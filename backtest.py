"""
Replays the LIVE strategy code over history, bar by bar, with no lookahead.

The single most important output is the confidence-bucket table. If the 80-100%
bucket does not beat the 70-79% bucket, your confidence number is decoration and
you should not size trades by it.

Usage
-----
  python backtest.py --mode intraday --csv XAUUSD_M15.csv
  python backtest.py --mode intraday --live      (pulls from Twelve Data)
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import pandas as pd

import config as C
from data import fetch_ohlc, load_csv
from indicators import resample_ohlc
from strategy import LONG, evaluate

RESAMPLE_RULE = {"5min": "5min", "15min": "15min", "1h": "1h",
                 "4h": "4h", "1day": "1D", "1week": "1W"}
WARMUP = 260


def run(entry_df: pd.DataFrame, mode: str, window: int = 500) -> tuple:
    """window = how many bars of history each evaluation sees. 500 is plenty of
    warmup for an EMA200 and keeps this O(n) instead of O(n^2)."""
    spec = C.MODES[mode]
    trend_full = resample_ohlc(entry_df, RESAMPLE_RULE[spec.trend_tf])
    bias_full = resample_ohlc(entry_df, RESAMPLE_RULE[spec.bias_tf])

    trades = []
    open_trade = None
    start = WARMUP

    for i in range(start, len(entry_df)):
        now = entry_df.index[i]
        bar = entry_df.iloc[i]

        # ---- manage an open position on this bar's range -------------------
        if open_trade:
            d = open_trade["dir"]
            hit_sl = bar["low"] <= open_trade["sl"] if d == LONG else bar["high"] >= open_trade["sl"]
            hit_tp = bar["high"] >= open_trade["tp"] if d == LONG else bar["low"] <= open_trade["tp"]

            # Pessimistic: if both are touched in one bar, assume the stop first.
            if hit_sl:
                open_trade["r"] = -1.0
                open_trade["exit"] = now
                trades.append(open_trade)
                open_trade = None
            elif hit_tp:
                open_trade["r"] = open_trade["target_r"]
                open_trade["exit"] = now
                trades.append(open_trade)
                open_trade = None
            else:
                continue

        # ---- evaluate using only closed data up to and including bar i -----
        e_slice = entry_df.iloc[max(0, i - window) : i + 1]
        t_slice = trend_full[trend_full.index <= now].tail(300)
        b_slice = bias_full[bias_full.index <= now].tail(300)
        if len(t_slice) < 60 or len(b_slice) < 60:
            continue

        res = evaluate(e_slice, t_slice, b_slice, spec, now.to_pydatetime())
        if res["decision"] != "ENTRY":
            continue

        lv = res["levels"]
        open_trade = {
            "entry_time": now,
            "dir": res["direction"],
            "entry": lv["entry"],
            "sl": lv["stop"],
            "tp": lv["tps"][-1],
            "target_r": spec.tp_multiples[-1],
            "score": res["score"],
        }

    return summarise(trades, mode)


def summarise(trades: list, mode: str) -> tuple:
    if not trades:
        txt = (f"No trades generated for {mode}.\nEither the history is too short "
               f"or the filters are too tight for this data.")
        print(txt)
        return {"trades": 0}, txt

    total_r = sum(t["r"] for t in trades)
    wins = [t for t in trades if t["r"] > 0]

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["r"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    L = []
    L.append(f"{mode.upper()}  —  {len(trades)} trades")
    L.append(f"{trades[0]['entry_time'].date()} to {trades[-1]['entry_time'].date()}")
    L.append("")
    L.append(f"Win rate      {len(wins) / len(trades):.1%}")
    L.append(f"Total         {total_r:+.1f}R")
    L.append(f"Expectancy    {total_r / len(trades):+.3f}R/trade")
    L.append(f"Max drawdown  {max_dd:.1f}R")

    buckets = defaultdict(list)
    for t in trades:
        s = t["score"]
        key = "90-100" if s >= 90 else "80-89" if s >= 80 else "70-79"
        buckets[key].append(t["r"])

    L.append("")
    L.append("Does the confidence % mean anything?")
    L.append(f"{'bucket':<9}{'n':>4}{'win%':>7}{'exp R':>9}")
    for key in ("90-100", "80-89", "70-79"):
        rs = buckets.get(key, [])
        if not rs:
            L.append(f"{key:<9}{0:>4}{'-':>7}{'-':>9}")
            continue
        w = sum(1 for r in rs if r > 0) / len(rs)
        L.append(f"{key:<9}{len(rs):>4}{w:>6.0%}{sum(rs) / len(rs):>+9.3f}")

    L.append("")
    L.append("Expectancy should RISE down this table.")
    L.append("If it does not, ignore the % and treat")
    L.append("ENTRY as a plain yes/no.")

    txt = "\n".join(L)
    print(txt)

    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "expectancy_r": total_r / len(trades),
        "max_dd_r": max_dd,
    }, txt


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="intraday", choices=list(C.MODES))
    p.add_argument("--csv", help="broker-exported OHLC on the entry timeframe")
    p.add_argument("--live", action="store_true", help="pull history from Twelve Data")
    p.add_argument("--symbol", default="XAU/USD")
    p.add_argument("--bars", type=int, default=5000)
    args = p.parse_args()

    spec = C.MODES[args.mode]

    if args.csv:
        df = load_csv(args.csv)
        print(f"Loaded {len(df)} bars from {args.csv} "
              f"({df.index[0].date()} → {df.index[-1].date()})")
    elif args.live:
        df = fetch_ohlc(args.symbol, spec.entry_tf, args.bars)
        print(f"Fetched {len(df)} × {spec.entry_tf} bars of {args.symbol}")
    else:
        raise SystemExit("Pass --csv FILE or --live")

    run(df, args.mode)


if __name__ == "__main__":
    main()
