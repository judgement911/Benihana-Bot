"""
Measure what the target ladder actually pays, and write calibration.json.
==========================================================================

The bot quotes a probability for every target it prints. Until this file is
run those numbers come from the barrier model alone — a closed-form guess
that knows the reward-to-risk geometry and nothing about whether these
particular rules, on this particular market, reach those particular levels.

This script replaces the guess with a count. It replays each strategy over
cached candles using the ladder the live bot actually trades (TP1/TP2/TP3 at
1R/2R/3R, stop to breakeven after TP1, and the strategy's own clock exit
where it has one) and records how often each rung was reached before the
stop. probability.py then shrinks the model toward those rates.

Three things this deliberately does NOT do:

  * It does not calibrate on the same trades used to pick the strategies.
    A rate measured on the data that selected the rules is optimistic. The
    split is chronological, and only the earlier portion is used, so the
    numbers here can be checked against the later portion.
  * It does not invent buckets it cannot fill. A score bucket with fewer
    than CALIBRATION_MIN_TRADES observations is written as absent rather
    than as a noisy rate, and the bot then falls back a level.
  * It does not pool strategies that behave differently. Each gets its own
    table, because a rate measured on a breakout says nothing useful about
    a mean-reversion fade.

Run: python build_calibration.py            (writes calibration.json)
     python build_calibration.py --dry-run  (prints, writes nothing)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "research"))

import config as C
import instruments as I
import probability as prob
from fullgrid import MODE_TF, collect_entries, load, simulate_exits
from strategies import REGISTRY

# The live ladder. Anything else would calibrate a trade the bot never makes.
LADDER = (1.0, 2.0, 3.0)

PAIRS = ("xauusd", "eurusd")
MODES = ("scalp", "intraday", "swing")
# Strategies with their own rules. "auto" is a router — it hands the trade to
# one of these, and that one's table is the one that applies.
KEYS = tuple(k for k in REGISTRY if k != "auto")

# Bars fed to each combination. 4h history is shorter than 5min history, so
# the cap is per timeframe rather than a single number.
BARS = {"5min": 20000, "15min": 20000, "4h": 11000}

# Only the earlier part of the series calibrates; the rest is left alone so
# the result can be tested on data it never saw.
CALIBRATION_FRACTION = 0.75


def _rate(flags) -> float:
    return sum(1 for f in flags if f) / len(flags) if flags else 0.0


def one_combo(args) -> tuple:
    """Replay one (pair, mode, strategy) and return its per-trade outcomes."""
    pair, mode, key = args
    tf = MODE_TF[mode]
    inst = I.find(pair)
    if inst is None or not os.path.exists(f"data/{pair}_{tf}.csv"):
        return (pair, mode, key, [])

    df = load(pair, tf)
    if len(df) < 1500:
        return (pair, mode, key, [])

    cap = min(BARS[tf], len(df))
    df, entries = collect_entries(df, mode, key, inst, max_bars=cap)
    if not entries:
        return (pair, mode, key, [])

    # Chronological, never random: a shuffled split would let the model see
    # the future of its own training window through overlapping bars.
    cut = int(len(df) * CALIBRATION_FRACTION)
    entries = [e for e in entries if e["i"] < cut]
    if not entries:
        return (pair, mode, key, [])

    spread = inst.spread
    slip = spread * 0.5
    trades = simulate_exits(df, entries,
                            LADDER, inst,
                            lambda risk: (spread + slip) / risk if risk > 0 else 0.0)

    out = []
    for t in trades:
        hit = int(t["targets_hit"])
        out.append({
            "score": float(t["score"] or 0),
            "tp1": hit >= 1,
            "tp2": hit >= 2,
            "final": hit >= 3,
            "r": float(t["r"]),
        })
    return (pair, mode, key, out)


def _table(rows: list[dict]) -> dict:
    """Rates for one population, plus per-bucket rates where there is data."""
    entry = {
        "trades": len(rows),
        "tp1": round(_rate([r["tp1"] for r in rows]), 4),
        "tp2": round(_rate([r["tp2"] for r in rows]), 4),
        "final": round(_rate([r["final"] for r in rows]), 4),
        "avg_r": round(sum(r["r"] for r in rows) / len(rows), 4) if rows else 0.0,
        "buckets": {},
    }
    by_bucket = defaultdict(list)
    for r in rows:
        by_bucket[prob.bucket_of(r["score"])].append(r)
    for name, rs in by_bucket.items():
        # Below the floor the rate is noise dressed as a measurement. Leaving
        # the bucket out makes the bot fall back to the mode-wide rate, which
        # is the honest answer.
        if len(rs) < C.CALIBRATION_MIN_TRADES:
            continue
        entry["buckets"][name] = {
            "n": len(rs),
            "tp1": round(_rate([r["tp1"] for r in rs]), 4),
            "tp2": round(_rate([r["tp2"] for r in rs]), 4),
            "final": round(_rate([r["final"] for r in rs]), 4),
        }
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=C.CALIBRATION_FILE)
    args = ap.parse_args()

    combos = [(p, m, k) for p in PAIRS for m in MODES for k in KEYS]
    print(f"Replaying {len(combos)} combinations "
          f"({len(KEYS)} strategies x {len(MODES)} modes x {len(PAIRS)} pairs)")
    print(f"Ladder {LADDER}, first {CALIBRATION_FRACTION:.0%} of each series\n")

    per_mode = defaultdict(list)
    per_strategy = defaultdict(lambda: defaultdict(list))

    workers = max(1, (os.cpu_count() or 2) - 1)
    done = 0
    with cf.ProcessPoolExecutor(max_workers=workers) as pool:
        for pair, mode, key, rows in pool.map(one_combo, combos):
            done += 1
            print(f"  [{done:>2}/{len(combos)}] {pair} {mode:<9} "
                  f"{REGISTRY[key].name:<15} {len(rows):>5} trades", flush=True)
            if rows:
                per_mode[mode].extend(rows)
                per_strategy[key][mode].extend(rows)

    cal = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": (f"replay of cached candles, ladder {LADDER}, "
                   f"first {CALIBRATION_FRACTION:.0%} of each series, "
                   f"pairs {'+'.join(p.upper() for p in PAIRS)}"),
        "ladder": list(LADDER),
        "modes": {m: _table(rows) for m, rows in per_mode.items() if rows},
        "strategies": {
            k: {m: _table(rows) for m, rows in modes.items() if rows}
            for k, modes in per_strategy.items()
        },
    }

    print("\nMeasured rates, all strategies pooled:")
    print(f"  {'mode':<10}{'n':>7}{'TP1':>7}{'TP2':>7}{'TP3':>7}{'avg R':>8}")
    for m in MODES:
        e = cal["modes"].get(m)
        if not e:
            continue
        print(f"  {m:<10}{e['trades']:>7}{e['tp1']:>7.1%}{e['tp2']:>7.1%}"
              f"{e['final']:>7.1%}{e['avg_r']:>8.3f}")

    print("\nPer strategy:")
    print(f"  {'strategy':<16}{'mode':<10}{'n':>6}{'TP1':>7}{'TP2':>7}"
          f"{'TP3':>7}{'avg R':>8}{'buckets':>9}")
    for k in KEYS:
        for m in MODES:
            e = (cal["strategies"].get(k) or {}).get(m)
            if not e:
                continue
            print(f"  {REGISTRY[k].name:<16}{m:<10}{e['trades']:>6}"
                  f"{e['tp1']:>7.1%}{e['tp2']:>7.1%}{e['final']:>7.1%}"
                  f"{e['avg_r']:>8.3f}{len(e['buckets']):>9}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(cal, fh, indent=1)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
