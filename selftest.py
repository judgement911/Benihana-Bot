"""Offline sanity check — no API key needed. Run: python selftest.py"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config as C
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
        print(f"  {m:<9} -> {res['decision']:<9} score {res['score']}")
