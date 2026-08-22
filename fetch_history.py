"""
DOWNLOAD HISTORY SO IT CAN BE BACKTESTED SOMEWHERE ELSE
=======================================================

Fetching candles is network waiting, not computation, so this costs almost
nothing against a free PythonAnywhere CPU quota — unlike the backtest it
feeds, which costs a great deal. Run this here, commit the CSVs, and the
heavy work can happen anywhere with spare CPU.

    python3 fetch_history.py
    git add data/ && git commit -m "history" && git push

One file per mode, on that mode's entry timeframe. The backtester resamples
the trend and bias timeframes from the entry frame itself, so nothing else
needs downloading.

    scalp     5min
    intraday  15min
    swing     4h

Twelve Data's free tier serves 8 requests a minute and 5000 candles per
request, so three symbols is three requests and finishes in seconds.
"""
from __future__ import annotations

import argparse
import os
import sys

import config as C
import instruments as I

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def main() -> int:
    p = argparse.ArgumentParser(description="Download candles for offline backtesting")
    p.add_argument("--symbol", default="xauusd", help="instrument key, e.g. xauusd")
    p.add_argument("--bars", type=int, default=5000, help="candles per timeframe")
    p.add_argument("--modes", default="scalp,intraday,swing")
    p.add_argument("--out", default=OUT_DIR)
    args = p.parse_args()

    inst = I.find(args.symbol)
    if inst is None:
        print(f"Unknown symbol {args.symbol!r}. Try /symbols in the bot.",
              file=sys.stderr)
        return 2

    from data import fetch_ohlc

    os.makedirs(args.out, exist_ok=True)
    written = []
    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        if mode not in C.MODES:
            print(f"  skipping unknown mode {mode!r}")
            continue
        tf = C.MODES[mode].entry_tf
        try:
            df = fetch_ohlc(inst.symbol, tf, args.bars)
        except Exception as exc:                    # noqa: BLE001
            print(f"  {mode:<9} {tf:<6} FAILED: {exc}", file=sys.stderr)
            continue
        path = os.path.join(args.out, f"{inst.key}_{tf}.csv")
        df.to_csv(path, index_label="datetime")
        span = f"{df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}"
        print(f"  {mode:<9} {tf:<6} {len(df):>5} bars  {span}  -> {os.path.relpath(path)}")
        written.append(path)

    if not written:
        print("Nothing downloaded.", file=sys.stderr)
        return 1
    print(f"\n{len(written)} file(s) in {args.out}")
    print("Now commit them:\n  git add data/ && git commit -m \"history\" && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
