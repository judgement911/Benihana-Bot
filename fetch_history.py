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
import time

import pandas as pd

import config as C
import instruments as I

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def main() -> int:
    p = argparse.ArgumentParser(description="Download candles for offline backtesting")
    p.add_argument("--symbol", default="xauusd", help="instrument key, e.g. xauusd")
    p.add_argument("--bars", type=int, default=5000, help="candles per timeframe")
    p.add_argument("--modes", default="scalp,intraday,swing")
    p.add_argument("--out", default=OUT_DIR)
    p.add_argument("--deep", type=int, default=1,
                   help="how many 5000-bar windows to walk back (1 = latest only)")
    p.add_argument("--pause", type=float, default=8.5,
                   help="seconds between requests; 8.5 keeps under 8/min")
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
        # Walk backwards a window at a time. The provider caps one response
        # at 5000 candles, so deep history is several dated requests stitched
        # together — paced to stay inside the free tier's 8 per minute.
        frames, end = [], None
        for w in range(args.deep):
            try:
                chunk = fetch_ohlc(inst.symbol, tf, args.bars, end_date=end)
            except Exception as exc:                # noqa: BLE001
                print(f"  {mode:<9} {tf:<6} window {w + 1} failed: {exc}",
                      file=sys.stderr)
                break
            frames.append(chunk)
            # Next window ends one bar before the oldest we just received.
            end = (chunk.index[0] - pd.Timedelta(seconds=1)).strftime(
                "%Y-%m-%d %H:%M:%S")
            if len(chunk) < args.bars:
                break                               # provider ran out of history
            if w + 1 < args.deep:
                time.sleep(args.pause)
        if not frames:
            continue
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        path = os.path.join(args.out, f"{inst.key}_{tf}.csv")
        # Merge with anything already on disk so repeated runs deepen the file
        # rather than replacing it.
        if os.path.exists(path):
            try:
                old = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
                df = pd.concat([old, df]).sort_index()
                df = df[~df.index.duplicated(keep="last")]
            except Exception:                       # noqa: BLE001
                pass
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
