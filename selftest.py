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


def check_instruments() -> bool:
    """Sizing and formatting are per-instrument, and getting either wrong is
    silent — a wrong lot size still looks like a number."""
    import instruments as I
    ok, fails = True, []

    for inst in I.all_instruments():
        if I.find(inst.key) is not inst or I.find(inst.display) is not inst:
            fails.append(f"{inst.display} does not resolve to itself")
        if inst.contract_size <= 0 or inst.pip <= 0 or inst.digits < 0:
            fails.append(f"{inst.display} has nonsense contract/pip/digits")
        # Price decimals must be fine enough to show one unit of movement.
        if inst.pip < 10 ** -inst.digits:
            fails.append(f"{inst.display} pip {inst.pip} finer than {inst.digits}dp")

    # Quote conversion: exact where it can be, absent where it cannot.
    cases = [("eurusd", 1.085, 1.0), ("xauusd", 4500.0, 1.0),
             ("us500", 5900.0, 1.0), ("usdjpy", 157.2, 1 / 157.2)]
    for key, px, want in cases:
        got = I.BY_KEY[key].usd_per_quote(px)
        if got is None or abs(got - want) > 1e-9:
            fails.append(f"{key} usd_per_quote {got} != {want}")
    for key in ("eurjpy", "gbpaud"):
        if I.BY_KEY[key].usd_per_quote(1.5) is not None:
            fails.append(f"{key} claims a USD rate it cannot know")

    # Every alias is unambiguous.
    seen = {}
    for inst in I.all_instruments():
        for a in inst.aliases:
            if a in seen:
                fails.append(f"alias {a!r} claimed by {seen[a]} and {inst.display}")
            seen[a] = inst.display

    for f in fails[:6]:
        print(f"  FAIL {f}")
        ok = False
    if ok:
        print(f"  instruments: {len(I.all_instruments())} defined, sizing and "
              "aliases consistent")
    return ok


def check_sizing() -> bool:
    """The printed lot size must actually risk the money it claims."""
    import instruments as I
    from indicators import resample_ohlc
    ok = True
    prices = {"xauusd": 4500.0, "eurusd": 1.0850, "usdjpy": 157.2,
              "us500": 5900.0, "xagusd": 31.2}
    for key, base in prices.items():
        inst = I.BY_KEY[key]
        df = synth(kind="uptrend", n=1200)
        df = df / df["close"].iloc[0] * base
        e = resample_ohlc(df, "5min"); t = resample_ohlc(df, "15min")
        b = resample_ohlc(df, "1h")
        spec = C.MODES["scalp"]
        for i in range(400, len(e), 5):
            now = e.index[i]
            res = evaluate(e.iloc[:i + 1], t[t.index <= now], b[b.index <= now],
                           spec, now.to_pydatetime(), instrument=inst)
            lv = res.get("levels")
            if not lv or lv.get("lots") is None:
                continue
            # printed entry - printed stop == printed risk
            if abs(abs(lv["entry"] - lv["stop"]) - lv["risk_points"]) > 10 ** -inst.digits:
                print(f"  FAIL {key}: printed levels disagree with printed risk")
                ok = False
            usd = (lv["lots"] * inst.contract_size * lv["risk_points"]
                   * inst.usd_per_quote(lv["entry"]))
            if abs(usd - lv["risk_cash"]) / lv["risk_cash"] > 0.02:
                print(f"  FAIL {key}: sized for ${usd:,.0f}, asked for ${lv['risk_cash']:,.0f}")
                ok = False
            break
    if ok:
        print("  sizing: every asset class risks what it says it risks")
    return ok


def check_journal() -> bool:
    """The journal grades ties as losses, same as the backtester."""
    import os, tempfile
    import pandas as pd
    C.JOURNAL_FILE = os.path.join(tempfile.mkdtemp(), "journal.json")
    import importlib
    import journal as J
    importlib.reload(J)

    def row(**kw):
        base = dict(id="t", ts="2026-08-20T10:00:00+00:00", instrument="xauusd",
                    mode="scalp", entry_tf="5min", direction=1, entry=4500.0,
                    stop=4490.0, tps=[4510.0, 4520.0], tp_multiples=[1.0, 2.0],
                    score=75, confidence=60, p_tp1=0.55, outcome="open",
                    hit_tp1=False, hit_final=False, r=None, resolved_ts=None)
        base.update(kw)
        return base

    def bars(seq):
        idx = pd.date_range("2026-08-20T10:05:00Z", periods=len(seq),
                            freq="5min", tz="UTC")
        return pd.DataFrame(
            [{"open": o, "high": h, "low": l, "close": c} for o, h, l, c in seq],
            index=idx)

    cases = [
        ("stop first", [(4500, 4505, 4489, 4492)], "loss"),
        ("tp1 then stop", [(4500, 4512, 4499, 4510), (4510, 4511, 4489, 4490)], "win"),
        ("both same bar", [(4500, 4515, 4485, 4500)], "loss"),   # tie -> stop
        ("neither", [(4500, 4503, 4498, 4501)], "open"),
    ]
    ok = True
    for name, seq, want in cases:
        J._save([row(id=name)])
        J.resolve(lambda s, tf, n: bars(seq))
        got = J._load()[0]["outcome"]
        if got != want:
            print(f"  FAIL journal {name}: {got}, expected {want}")
            ok = False
    # An empty journal must say so rather than invent a record.
    C.JOURNAL_FILE = os.path.join(tempfile.mkdtemp(), "empty.json")
    if "No signals recorded" not in J.format_stats("xauusd"):
        print("  FAIL empty journal does not say it is empty")
        ok = False
    if ok:
        print("  journal: ties score as losses, empty stays empty")
    return ok


def check_views() -> bool:
    """Both detail levels, every decision, inside Telegram's size limit."""
    import view
    from indicators import resample_ohlc
    ok, n, worst = True, 0, 0
    for kind in ("uptrend", "downtrend", "chop"):
        df = synth(kind=kind, n=2500)
        e = resample_ohlc(df, "5min"); t = resample_ohlc(df, "15min")
        b = resample_ohlc(df, "1h")
        for i in range(400, len(e), 37):
            now = e.index[i]
            res = evaluate(e.iloc[:i + 1], t[t.index <= now], b[b.index <= now],
                           C.MODES["scalp"], now.to_pydatetime())
            for verbose in (False, True):
                try:
                    out = view.render("XAUUSD", res, verbose)
                except Exception as exc:  # noqa: BLE001
                    print(f"  FAIL render raised {type(exc).__name__}: {exc}")
                    return False
                n += 1
                worst = max(worst, len(out))
                if len(out) > 4096:
                    print(f"  FAIL message {len(out)} chars, Telegram caps at 4096")
                    ok = False
    if ok:
        print(f"  view: {n} renders clean, longest {worst} chars of 4096")
    return ok


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

    print("\nUniverse, sizing and presentation:")
    ok &= check_instruments() & check_sizing() & check_views()

    print("\nSignal journal:")
    ok &= check_journal()
    raise SystemExit(0 if ok else 1)
