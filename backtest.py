"""
Replays the LIVE strategy code over history, bar by bar, with no lookahead.

Two things matter in the output.

The first is expectancy: does this make money at all?

The second is the bucket table, which is the only thing that turns the
percentages in a signal from decoration into measurement. If the 80-100%
bucket does not beat the 70-79% bucket, the confluence score is noise and you
should treat ENTRY as a plain yes/no. And if the "pred" column — what the
probability model promised — sits far from the "final" column that the market
actually paid, the model is wrong and needs the correction that --calibrate
writes.

Usage
-----
  python backtest.py --mode intraday --csv XAUUSD_M15.csv
  python backtest.py --mode intraday --live      (pulls from Twelve Data)
  python backtest.py --mode intraday --csv F.csv --calibrate   (teach the bot)
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd

import config as C
import probability as prob
from data import load_csv

try:                        # honours DATA_PROVIDER (twelvedata / oanda / binance)
    from market_data import fetch_ohlc
except ImportError:         # market_data.py absent — fall back to Twelve Data
    from data import fetch_ohlc
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
            hit_tp1 = (
                bar["high"] >= open_trade["tp1"] if d == LONG
                else bar["low"] <= open_trade["tp1"]
            )

            # First target is recorded separately because it is what the
            # signal's headline probability is about. Same pessimism as below:
            # a bar that touches both is scored as the stop.
            if hit_tp1 and not hit_sl:
                open_trade["hit_tp1"] = True

            # Pessimistic: if both are touched in one bar, assume the stop first.
            if hit_sl:
                open_trade["r"] = -1.0
                open_trade["exit"] = now
                trades.append(open_trade)
                open_trade = None
            elif hit_tp:
                open_trade["r"] = open_trade["target_r"]
                open_trade["hit_final"] = True
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
        pr = res["probability"] or {}
        open_trade = {
            "entry_time": now,
            "dir": res["direction"],
            "entry": lv["entry"],
            "sl": lv["stop"],
            "tp1": lv["tps"][0],
            "tp": lv["tps"][-1],
            "target_r": spec.tp_multiples[-1],
            "score": res["score"],
            "confidence": (res.get("confidence") or {}).get("value", 0),
            # What the bot promised at the moment it fired. Kept so the table
            # below can hold the model to account.
            "pred_tp1": pr.get("p_first"),
            "pred_final": pr.get("p_final"),
            "hit_tp1": False,
            "hit_final": False,
        }

    return summarise(trades, mode)


def _rate(values: list) -> float | None:
    return (sum(1 for v in values if v) / len(values)) if values else None


def _mean(values: list) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float))]
    return (sum(clean) / len(clean)) if clean else None


def summarise(trades: list, mode: str) -> tuple:
    if not trades:
        txt = (f"No trades generated for {mode}.\nEither the history is too short "
               f"or the filters are too tight for this data.")
        print(txt)
        return {"trades": 0, "trade_log": []}, txt

    total_r = sum(t["r"] for t in trades)
    wins = [t for t in trades if t["r"] > 0]

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["r"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    tp1_rate = _rate([t["hit_tp1"] for t in trades])
    final_rate = _rate([t["hit_final"] for t in trades])
    pred_final = _mean([t.get("pred_final") for t in trades])
    pred_tp1 = _mean([t.get("pred_tp1") for t in trades])

    L = []
    L.append(f"{mode.upper()}  —  {len(trades)} trades")
    L.append(f"{trades[0]['entry_time'].date()} to {trades[-1]['entry_time'].date()}")
    L.append("")
    L.append(f"Win rate      {len(wins) / len(trades):.1%}")
    L.append(f"Total         {total_r:+.1f}R")
    L.append(f"Expectancy    {total_r / len(trades):+.3f}R/trade")
    L.append(f"Max drawdown  {max_dd:.1f}R")
    L.append(f"Reached TP1   {tp1_rate:.1%}")

    # ---- does the confluence score predict anything? ----------------------
    buckets = defaultdict(list)
    for t in trades:
        buckets[prob.bucket_of(t["score"])].append(t)

    L.append("")
    L.append("Does the confluence % mean anything?")
    L.append(f"{'bucket':<9}{'n':>4}{'TP1':>6}{'final':>7}{'pred':>6}{'exp R':>8}")
    for key in ("90-100", "80-89", "70-79"):
        ts = buckets.get(key, [])
        if not ts:
            L.append(f"{key:<9}{0:>4}{'-':>6}{'-':>7}{'-':>6}{'-':>8}")
            continue
        exp = sum(t["r"] for t in ts) / len(ts)
        pf = _mean([t.get("pred_final") for t in ts])
        L.append(
            f"{key:<9}{len(ts):>4}"
            f"{_rate([t['hit_tp1'] for t in ts]):>5.0%}"
            f"{_rate([t['hit_final'] for t in ts]):>7.0%}"
            f"{(pf if pf is not None else 0):>6.0%}"
            f"{exp:>+8.3f}"
        )
    L.append("Expectancy should FALL as you read down. If")
    L.append("it does not, ignore the % and treat ENTRY as")
    L.append("a plain yes/no.")

    # ---- and does the confidence number? ----------------------------------
    cbuckets = defaultdict(list)
    for t in trades:
        c = t.get("confidence", 0)
        cbuckets["80+" if c >= 80 else "65-79" if c >= 65 else "<65"].append(t)

    L.append("")
    L.append("Does the confidence % mean anything?")
    L.append(f"{'bucket':<9}{'n':>4}{'win':>6}{'exp R':>8}")
    for key in ("80+", "65-79", "<65"):
        ts = cbuckets.get(key, [])
        if not ts:
            L.append(f"{key:<9}{0:>4}{'-':>6}{'-':>8}")
            continue
        L.append(
            f"{key:<9}{len(ts):>4}"
            f"{_rate([t['r'] > 0 for t in ts]):>5.0%}"
            f"{sum(t['r'] for t in ts) / len(ts):>+8.3f}"
        )

    # ---- was the probability model honest? --------------------------------
    if None not in (pred_final, pred_tp1, final_rate, tp1_rate):
        gap = (final_rate - pred_final) * 100
        verdict = (
            "about right" if abs(gap) < 5
            else f"{abs(gap):.0f} pts too {'pessimistic' if gap > 0 else 'optimistic'}"
        )
        L.append("")
        L.append("Was the probability honest?")
        L.append(f"  TP1    promised {pred_tp1:.0%}   paid {tp1_rate:.0%}")
        L.append(f"  final  promised {pred_final:.0%}   paid {final_rate:.0%}")
        L.append(f"  model is {verdict}")
        L.append("  Add calibrate to the command to fold this in.")

    txt = "\n".join(L)
    print(txt)

    return {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "expectancy_r": total_r / len(trades),
        "max_dd_r": max_dd,
        "tp1_rate": tp1_rate,
        "final_rate": final_rate,
        "trade_log": trades,
    }, txt


# --------------------------------------------------------------------------- #
#  Calibration — turning measured results into the numbers the bot quotes
# --------------------------------------------------------------------------- #
def build_calibration(trades: list, mode: str, symbol: str, bars: int,
                      source: str) -> dict:
    """One mode's worth of measured hit rates, bucketed by confluence score."""
    grouped = defaultdict(list)
    for t in trades:
        grouped[prob.bucket_of(t["score"])].append(t)

    return {
        "trades": len(trades),
        "tp1": _rate([t["hit_tp1"] for t in trades]),
        "final": _rate([t["hit_final"] for t in trades]),
        "symbol": symbol,
        "bars": bars,
        "source": source,
        "from": str(trades[0]["entry_time"].date()) if trades else None,
        "to": str(trades[-1]["entry_time"].date()) if trades else None,
        "buckets": {
            key: {
                "n": len(ts),
                "tp1": _rate([t["hit_tp1"] for t in ts]),
                "final": _rate([t["hit_final"] for t in ts]),
            }
            for key, ts in grouped.items()
        },
    }


def write_calibration(entry: dict, mode: str, symbol: str,
                      path: str | None = None) -> str:
    """Merge one mode into calibration.json, leaving the other modes alone."""
    path = path or C.CALIBRATION_FILE

    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except (OSError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}

    data.setdefault("modes", {})
    data["modes"][mode] = entry
    data["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["symbol"] = symbol

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="intraday", choices=list(C.MODES))
    p.add_argument("--csv", help="broker-exported OHLC on the entry timeframe")
    p.add_argument("--live", action="store_true", help="pull history from Twelve Data")
    p.add_argument("--symbol", default="XAU/USD")
    p.add_argument("--bars", type=int, default=5000)
    p.add_argument(
        "--calibrate",
        action="store_true",
        help="write the measured hit rates to calibration.json so live signals "
             "quote them instead of the model's guess",
    )
    args = p.parse_args()

    spec = C.MODES[args.mode]

    if args.csv:
        df = load_csv(args.csv)
        source = f"csv:{os.path.basename(args.csv)}"
        print(f"Loaded {len(df)} bars from {args.csv} "
              f"({df.index[0].date()} → {df.index[-1].date()})")
    elif args.live:
        df = fetch_ohlc(args.symbol, spec.entry_tf, args.bars)
        source = "live"
        print(f"Fetched {len(df)} × {spec.entry_tf} bars of {args.symbol}")
    else:
        raise SystemExit("Pass --csv FILE or --live")

    stats, _ = run(df, args.mode)

    if not args.calibrate:
        return

    trades = stats.get("trade_log") or []
    if len(trades) < C.CALIBRATION_MIN_TRADES:
        print(f"\nNot calibrating: {len(trades)} trades is below the "
              f"{C.CALIBRATION_MIN_TRADES}-trade minimum. Feed it more history.")
        return

    entry = build_calibration(trades, args.mode, args.symbol, len(df), source)
    path = write_calibration(entry, args.mode, args.symbol)
    print(f"\nWrote {args.mode} calibration to {path} "
          f"({len(trades)} trades). Live signals will now quote measured rates, "
          f"shrunk toward the model by {C.CALIBRATION_PRIOR_WEIGHT:.0f} "
          f"pseudo-trades.")


if __name__ == "__main__":
    main()
