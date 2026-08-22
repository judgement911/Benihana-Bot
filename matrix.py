"""
THE FULL SWEEP
==============

Every live strategy, against every trading style, at every reward target:

    3 strategies  x  3 modes  x  3 R:R  =  27 backtests

    python3 matrix.py --data data/xauusd_15min.csv --mode intraday
    python3 matrix.py --auto           # every CSV in data/, matched by name

It is deliberately separate from bakeoff.py. That script asks "which of ten
candidate rulesets deserves a place in the bot" and is built to run in
CPU-starved batches. This one asks "of the three already in the bot, which
style and target suits each" and expects to run somewhere with spare CPU —
which is the point of fetch_history.py committing the candles.

READING THE TABLE
-----------------
Expectancy is the column that matters: R per trade, after costs, under the
partial-exit plan the bot actually trades. Net R rewards whichever
combination happened to trade most, so it is shown but not ranked on.

A row with fewer than MIN_TRADES trades is printed and then ignored, because
an edge cannot be read off a dozen trades however good the numbers look.
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import io
import math
import os
import sys

import pandas as pd

import config as C
from backtest import run as backtest_run
from bakeoff import MIN_TRADES, _metrics, _score
from strategies import ORDER, REGISTRY

RR_SETS = {"1:1": (1.0,), "1:2": (1.0, 2.0), "1:3": (1.0, 2.0, 3.0)}


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    stamp = next((c for c in df.columns
                  if c.lower() in ("datetime", "date", "time", "timestamp")), None)
    if stamp is None:
        raise SystemExit(f"{path}: no datetime column found")
    df[stamp] = pd.to_datetime(df[stamp], utc=True)
    df = df.set_index(stamp).sort_index()
    need = ["open", "high", "low", "close"]
    df.columns = [c.lower() for c in df.columns]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise SystemExit(f"{path}: missing column(s) {missing}")
    return df[need].dropna()


def sweep(df: pd.DataFrame, mode: str, log=print) -> list[dict]:
    rows = []
    for key in ORDER:
        for rr_label, tps in RR_SETS.items():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    stats, _ = backtest_run(df, mode, strategy=REGISTRY[key].evaluate,
                                            tp_multiples=tps)
                m = _metrics(stats.get("trade_log") or [])
            except Exception as exc:                # noqa: BLE001
                m = {"trades": 0, "error": f"{type(exc).__name__}: {exc}"}
            m.update(strategy=key, name=REGISTRY[key].name, mode=mode, rr=rr_label)
            m["score"] = _score(m)
            rows.append(m)
            log(f"    {REGISTRY[key].name:<15} {mode:<9} {rr_label}  "
                f"{m.get('trades', 0):>4} trades")
    return rows


def table(rows: list[dict]) -> str:
    out = [f"{'strategy':<16}{'mode':<10}{'R:R':<6}{'n':>5}{'win':>7}"
           f"{'PF':>7}{'exp R':>8}{'net R':>8}{'maxDD':>8}  ok"]
    out.append("-" * 82)
    for m in sorted(rows, key=lambda r: -r.get("score", float("-inf"))):
        if not m.get("trades"):
            out.append(f"{m['name']:<16}{m['mode']:<10}{m['rr']:<6}{'0':>5}   "
                       + str(m.get("error", "no trades"))[:40])
            continue
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        out.append(f"{m['name']:<16}{m['mode']:<10}{m['rr']:<6}{m['trades']:>5}"
                   f"{m['win_rate']:>6.0%}{pf:>7}{m['expectancy']:>+8.2f}"
                   f"{m['net_r']:>+8.1f}{m['max_dd_r']:>+8.1f}  "
                   + ("yes" if m["trades"] >= MIN_TRADES else "few"))
    return "\n".join(out)


def verdict(rows: list[dict]) -> str:
    rankable = [r for r in rows if r.get("trades", 0) >= MIN_TRADES]
    if not rankable:
        return ("\nNOTHING IS RANKABLE. Every combination produced fewer than "
                f"{MIN_TRADES} trades, which cannot show an edge. More history "
                "is the only fix — this is an absence of evidence, not a result.")
    out = ["", "Ranked on expectancy after costs, under the partial-exit plan",
           "the bot actually trades. Combinations under "
           f"{MIN_TRADES} trades are excluded.", ""]
    best = sorted(rankable, key=lambda r: -r["score"])[:5]
    for n, m in enumerate(best, start=1):
        out.append(f"  {n}. {m['name']} · {m['mode']} · {m['rr']} — "
                   f"{m['expectancy']:+.2f}R over {m['trades']} trades")

    positive = [r for r in rankable if r["expectancy"] > 0]
    out.append("")
    if not positive:
        out.append("NOT ONE combination has positive expectancy after costs on")
        out.append("this sample. That is the finding. Do not trade it because a")
        out.append("row sits at the top of a table of losses.")
    else:
        n_sig = sum(1 for r in positive
                    if r["expectancy"] > 1.96 * 1.0 / math.sqrt(r["trades"]))
        out.append(f"{len(positive)} of {len(rankable)} combinations are positive; "
                   f"{n_sig} clear a rough 95% significance bar")
        out.append("(expectancy > 1.96/sqrt(n), taking 1R as the per-trade spread).")
        if not n_sig:
            out.append("None are statistically distinguishable from breakeven yet.")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description="Every strategy x mode x reward target")
    p.add_argument("--data", help="one CSV of entry-timeframe candles")
    p.add_argument("--mode", choices=list(C.MODES), help="mode that CSV belongs to")
    p.add_argument("--auto", action="store_true",
                   help="every data/*.csv, mode inferred from the filename")
    p.add_argument("--dir", default="data")
    args = p.parse_args()

    jobs = []
    if args.auto:
        tf_to_mode = {spec.entry_tf: name for name, spec in C.MODES.items()}
        for path in sorted(glob.glob(os.path.join(args.dir, "*.csv"))):
            tf = os.path.splitext(os.path.basename(path))[0].split("_")[-1]
            mode = tf_to_mode.get(tf)
            if mode:
                jobs.append((path, mode))
            else:
                print(f"  skipping {path}: cannot tell which mode {tf!r} is")
    elif args.data and args.mode:
        jobs.append((args.data, args.mode))
    else:
        print("Give me --data FILE --mode MODE, or --auto", file=sys.stderr)
        return 2
    if not jobs:
        print(f"No usable CSVs in {args.dir}/. Run fetch_history.py first.",
              file=sys.stderr)
        return 1

    rows = []
    for path, mode in jobs:
        df = load(path)
        print(f"\n{os.path.basename(path)} — {len(df)} bars, {mode}, "
              f"{df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}")
        if len(df) < 800:
            print(f"  only {len(df)} bars; too short to be worth ranking")
            continue
        rows += sweep(df, mode)

    print()
    print(table(rows))
    print(verdict(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
