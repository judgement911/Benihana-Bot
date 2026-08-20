"""Offline sanity check — no API key needed. Run: python selftest.py"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config as C
import probability as prob
from indicators import resample_ohlc
from strategy import evaluate


def synth(n=4000, kind="uptrend", seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")

    if kind == "uptrend":
        drift = 0.045
        wave = 6.0 * np.sin(np.arange(n) / 55.0)   # pullbacks inside the trend
    elif kind == "downtrend":
        drift = -0.045
        wave = 6.0 * np.sin(np.arange(n) / 55.0)
    else:  # chop
        drift = 0.0
        wave = 14.0 * np.sin(np.arange(n) / 30.0)

    noise = rng.normal(0, 1.1, n).cumsum()
    close = 2600 + drift * np.arange(n) + wave + noise

    spread = rng.uniform(0.6, 2.6, n)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    open_ = np.concatenate([[close[0]], close[:-1]])

    return pd.DataFrame(
        {"open": open_, "high": np.maximum(high, np.maximum(open_, close)),
         "low": np.minimum(low, np.minimum(open_, close)), "close": close},
        index=idx,
    )


def check(kind: str, mode: str = "intraday"):
    spec = C.MODES[mode]
    df = synth(kind=kind)
    trend = resample_ohlc(df, "1h")
    bias = resample_ohlc(df, "4h")

    decisions = {"ENTRY": 0, "WAIT": 0, "NO TRADE": 0}
    dirs = {1: 0, -1: 0, 0: 0}
    scores = []

    # sample 300 points across the series
    for i in range(400, len(df), 12):
        now = df.index[i]
        res = evaluate(
            df.iloc[: i + 1],
            trend[trend.index <= now],
            bias[bias.index <= now],
            spec,
            now.to_pydatetime(),
        )
        decisions[res["decision"]] += 1
        dirs[res["direction"]] += 1
        if res["score"]:
            scores.append(res["score"])

    total = sum(decisions.values())
    print(f"{kind:<10} n={total:<4} "
          f"ENTRY {decisions['ENTRY'] / total:5.1%}  "
          f"WAIT {decisions['WAIT'] / total:5.1%}  "
          f"NOTRADE {decisions['NO TRADE'] / total:5.1%}  "
          f"| long {dirs[1]:<4} short {dirs[-1]:<4} "
          f"| mean score {np.mean(scores):.0f}" if scores else "")


def show_one():
    spec = C.MODES["intraday"]
    df = synth(kind="uptrend")
    trend = resample_ohlc(df, "1h")
    bias = resample_ohlc(df, "4h")

    for i in range(1200, len(df), 4):
        now = df.index[i]
        res = evaluate(df.iloc[: i + 1], trend[trend.index <= now],
                       bias[bias.index <= now], spec, now.to_pydatetime())
        if res["decision"] == "ENTRY":
            print("\n--- example ENTRY ------------------------------------")
            print(f"score {res['score']}  dir {res['direction']}  price {res['price']}")
            for r in res["reasons"]:
                print(f"  {'v' if r['ok'] else 'x'} {r['text']}  [{r['points']}/{r['max']}]")
            print(f"  levels: {res['levels']}")
            return
    print("no ENTRY found in sample")


def check_probability_model():
    """The probability number has to obey a few laws or it is worse than
    useless. Assert them rather than eyeball them."""
    failures = []

    def want(cond, msg):
        if not cond:
            failures.append(msg)

    # A driftless market hits +kR before -1R exactly 1/(1+k) of the time, and
    # that is also the breakeven win rate. Costs push them apart, one each way.
    want(abs(prob.null_probability(1.0, 0.0) - 0.5) < 1e-9, "1R null should be 50%")
    want(abs(prob.null_probability(2.0, 0.0) - 1 / 3) < 1e-9, "2R null should be 33%")
    want(prob.null_probability(1.0) < prob.breakeven_rate(1.0),
         "costs must make the target harder AND the bar higher")

    # Monotone in the score, and never wilder than the caps allow.
    prev = 0.0
    for s in range(0, 101, 5):
        p = prob.model_probability(s, 1.0)
        want(p >= prev - 1e-12, f"probability fell as score rose at {s}")
        want(C.PROB_FLOOR - 1e-9 <= p <= C.PROB_CEIL + 1e-9, f"{p} out of bounds at {s}")
        prev = p

    # Farther targets are strictly harder, always.
    for s in (40, 70, 100):
        e = prob.estimate(s, (1.0, 2.0, 3.0), cal={})
        ps = [t["p"] for t in e["targets"]]
        want(ps == sorted(ps, reverse=True), f"targets not monotone at score {s}: {ps}")

    # Swings sitting inside the target have to cost something.
    open_room = prob.model_probability(80, 2.0, room_rr=5.0)
    blocked = prob.model_probability(80, 2.0, room_rr=0.5)
    want(blocked < open_room, "an obstacle inside the target changed nothing")

    # A perfect score must not buy a fantasy. 12-ish points over the null.
    lift = prob.model_probability(100, 1.0) - prob.null_probability(1.0)
    want(0.0 < lift < 0.20, f"edge term is out of hand: +{lift:.1%}")

    # Calibration: a thin sample nudges, a thick one dominates.
    thin = {"modes": {"intraday": {"trades": 10, "tp1": 0.90, "final": 0.80,
                                   "buckets": {}}}}
    thick = {"modes": {"intraday": {"trades": 4000, "tp1": 0.90, "final": 0.80,
                                    "buckets": {}}}}
    base = prob.estimate(75, (1.0, 2.0), mode="intraday", cal={})["p_first"]
    p_thin = prob.estimate(75, (1.0, 2.0), mode="intraday", cal=thin)["p_first"]
    p_thick = prob.estimate(75, (1.0, 2.0), mode="intraday", cal=thick)["p_first"]
    want(base < p_thin < p_thick, f"shrinkage misordered: {base:.3f} {p_thin:.3f} {p_thick:.3f}")
    want(p_thick <= C.PROB_CEIL + 1e-9, "calibration escaped the ceiling")

    # Samples below the minimum are ignored outright.
    tiny = {"modes": {"intraday": {"trades": 2, "tp1": 0.9, "final": 0.9,
                                   "buckets": {}}}}
    want(prob.estimate(75, (1.0, 2.0), mode="intraday", cal=tiny)["source"] == "model",
         "a 2-trade sample was allowed to speak")

    # Confidence: penalties bite, bonus lifts, result stays in range.
    clean = prob.confidence(80, {}, clean_sweep=True)["value"]
    hurt = prob.confidence(80, {k: True for k in C.CONFIDENCE_PENALTIES})["value"]
    want(clean == 86, f"clean sweep should be 86, got {clean}")
    want(5 <= hurt < 80, f"stacked penalties should bite: {hurt}")
    want(prob.confidence(2, {})["value"] >= 5, "confidence went below the floor")
    want(prob.confidence(100, {}, clean_sweep=True)["value"] <= 99,
         "confidence hit 100 — no read is that good")

    for msg in failures:
        print(f"  FAIL {msg}")
    print(f"  probability model: {'all checks passed' if not failures else str(len(failures)) + ' FAILURES'}")
    return not failures


def check_signal_fields(mode: str = "intraday"):
    """Every non-vetoed evaluation must carry both numbers, and a vetoed one
    must say zero rather than nothing."""
    spec = C.MODES[mode]
    df = synth(kind="uptrend")
    trend = resample_ohlc(df, "1h")
    bias = resample_ohlc(df, "4h")

    seen = {"scored": 0, "vetoed": 0}
    failures = []

    for i in range(400, len(df), 17):
        now = df.index[i]
        res = evaluate(df.iloc[: i + 1], trend[trend.index <= now],
                       bias[bias.index <= now], spec, now.to_pydatetime())
        conf = res["confidence"]
        if res["vetoes"]:
            seen["vetoed"] += 1
            if conf["value"] != 0 or res["probability"] is not None:
                failures.append(f"veto at {now} quoted odds anyway")
            continue

        seen["scored"] += 1
        pr = res["probability"]
        if pr is None:
            failures.append(f"no probability at {now}")
            continue
        if not 0 <= conf["value"] <= 99:
            failures.append(f"confidence {conf['value']} out of range at {now}")
        if len(pr["targets"]) != len(spec.tp_multiples):
            failures.append(f"target count mismatch at {now}")
        if conf["value"] > res["score"] + C.CONFIDENCE_CLEAN_SWEEP_BONUS:
            failures.append(f"confidence exceeded score+bonus at {now}")
        if not prob.read_block(res):
            failures.append(f"empty read block at {now}")

    for msg in failures[:5]:
        print(f"  FAIL {msg}")
    print(f"  signal fields: {seen['scored']} scored, {seen['vetoed']} vetoed, "
          f"{'all present' if not failures else str(len(failures)) + ' FAILURES'}")
    return not failures


if __name__ == "__main__":
    print("\nBehaviour across market regimes (intraday mode):")
    for k in ("uptrend", "downtrend", "chop"):
        check(k)
    show_one()

    print("\nAll three modes run without error:")
    for m in C.MODES:
        df = synth(kind="uptrend")
        spec = C.MODES[m]
        rule = {"5min": "5min", "15min": "15min", "1h": "1h",
                "4h": "4h", "1day": "1D", "1week": "1W"}
        e = df if spec.entry_tf in ("15min", "5min") else resample_ohlc(df, rule[spec.entry_tf])
        t = resample_ohlc(df, rule[spec.trend_tf])
        b = resample_ohlc(df, rule[spec.bias_tf])
        if len(e) < 80 or len(t) < 60 or len(b) < 30:
            print(f"  {m:<9} skipped (synthetic history too short for {spec.bias_tf})")
            continue
        res = evaluate(e, t, b, spec, datetime.now(timezone.utc))
        pr = res["probability"]
        odds = f"P(TP1) {pr['p_first']:.0%}" if pr else "no odds (vetoed)"
        print(f"  {m:<9} -> {res['decision']:<9} score {res['score']:<4} "
              f"confidence {res['confidence']['value']:<4} {odds}")

    print("\nConfidence and probability:")
    ok = check_probability_model() & check_signal_fields()
    raise SystemExit(0 if ok else 1)
