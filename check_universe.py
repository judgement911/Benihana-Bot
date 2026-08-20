"""
WHICH SYMBOLS DOES YOUR DATA PLAN ACTUALLY SERVE?
=================================================

instruments.py lists 43 CFDs. Your provider almost certainly does not give
you all of them — free tiers generally cover FX and metals, while indices
and energy need a paid plan. Guessing wastes a scan; this asks.

    python check_universe.py                 # everything
    python check_universe.py --class index   # one asset class
    python check_universe.py --tf 1h         # probe a specific timeframe

It costs one request per instrument, so a full run is ~43 of your daily
budget. Results print as a list you can paste straight into SCAN_SYMBOLS.
"""
from __future__ import annotations

import argparse
import sys
import time

import config as C
import instruments as I


def _fetch():
    if C.DATA_PROVIDER == "twelvedata":
        from data import fetch_ohlc
    else:
        from market_data import fetch_ohlc
    return fetch_ohlc


def probe(inst: I.Instrument, fetch, tf: str) -> tuple[bool, str]:
    try:
        df = fetch(inst.symbol, tf, 80)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:60]}"
    if df is None or len(df) < 60:
        return False, f"only {0 if df is None else len(df)} candles"
    return True, f"{len(df)} candles, last {df['close'].iloc[-1]:,.5g}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--class", dest="cls", choices=sorted(I.CLASS_LABEL))
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--pause", type=float, default=8.0,
                    help="seconds between requests; the free tier allows 8/min")
    args = ap.parse_args()

    fetch = _fetch()
    items = I.all_instruments(asset_class=args.cls)
    print(f"Probing {len(items)} instruments on {args.tf} via {C.DATA_PROVIDER}.")
    print(f"About {len(items) * args.pause / 60:.0f} minutes at {args.pause}s apart.\n")

    ok, bad = [], []
    for n, inst in enumerate(items, 1):
        good, detail = probe(inst, fetch, args.tf)
        (ok if good else bad).append(inst)
        mark = "  ok " if good else "MISS "
        print(f"{mark}{inst.display:<8} {inst.name:<18} {detail}")
        sys.stdout.flush()
        if n < len(items) and args.pause:
            time.sleep(args.pause)

    print(f"\n{len(ok)} available, {len(bad)} unavailable.")
    if ok:
        print("\nPaste into pa_config.py or your environment:")
        print("SCAN_SYMBOLS = \"" + ",".join(i.key for i in ok) + "\"")
    if bad:
        print("\nNot on this plan: " + " ".join(i.display for i in bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
