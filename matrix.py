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
import concurrent.futures as cf
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


MIN_SIDE_TRADES = 10       # below this, a side's expectancy is noise


def _direction(trades: list[dict]) -> dict:
    """Split a result by trade direction.

    Every finding in this project has turned on this. A strategy that only
    ever went long in a rising market looks identical to one with an edge
    until you separate the two, and four of the best-looking results here
    were exactly that.

    The verdict needs a minimum count PER SIDE. Calling something two-sided
    off two short trades is how a one-sided result sneaks through wearing
    the label that was supposed to catch it.
    """
    L = [t["r"] for t in trades if t["dir"] > 0]
    S = [t["r"] for t in trades if t["dir"] < 0]
    eL = sum(L) / len(L) if L else None
    eS = sum(S) / len(S) if S else None
    if not S:
        verdict = "no shorts"
    elif len(L) < MIN_SIDE_TRADES or len(S) < MIN_SIDE_TRADES:
        verdict = "one side thin"
    elif eL > 0 and eS > 0:
        verdict = "YES"
    else:
        verdict = "no"
    return {"nL": len(L), "nS": len(S), "eL": eL, "eS": eS, "two_sided": verdict}


def _one(job: tuple) -> dict:
    """One backtest. Module-level and self-contained so it can be sent to a
    worker process — the pool passes keys, not closures."""
    path, mode, pair, key, rr_label = job
    try:
        df = load(path)
        with contextlib.redirect_stdout(io.StringIO()):
            stats, _ = backtest_run(df, mode, strategy=REGISTRY[key].evaluate,
                                    tp_multiples=RR_SETS[rr_label])
        tl = stats.get("trade_log") or []
        m = _metrics(tl)
        m.update(_direction(tl))
    except Exception as exc:                        # noqa: BLE001
        m = {"trades": 0, "error": f"{type(exc).__name__}: {exc}"}
    m.update(strategy=key, name=REGISTRY[key].name, mode=mode, rr=rr_label,
             pair=pair)
    m["score"] = _score(m)
    return m


def sweep_all(jobs: list[tuple], workers: int, log=print) -> list[dict]:
    """Run every backtest, in parallel when there are cores to spare.

    Each run is independent and CPU-bound, so a process pool is close to a
    linear win. One core is left free by default: saturating every core on a
    shared machine makes the whole thing slower, not faster.
    """
    rows = []
    if workers <= 1:
        for n, job in enumerate(jobs, start=1):
            m = _one(job)
            log(f"    [{n}/{len(jobs)}] {m['pair']:<7}{m['name']:<15}"
                f"{m['mode']:<9}{m['rr']}  {m.get('trades', 0):>4} trades")
            rows.append(m)
        return rows

    with cf.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, j): j for j in jobs}
        for n, fut in enumerate(cf.as_completed(futures), start=1):
            m = fut.result()
            log(f"    [{n}/{len(jobs)}] {m['pair']:<7}{m['name']:<15}"
                f"{m['mode']:<9}{m['rr']}  {m.get('trades', 0):>4} trades")
            rows.append(m)
    return rows


def table(rows: list[dict]) -> str:
    out = [f"{'pair':<8}{'strategy':<16}{'mode':<10}{'R:R':<6}{'n':>5}{'win':>6}"
           f"{'PF':>7}{'exp R':>8}{'maxDD':>8}{'long':>6}{'short':>6}  two-sided"]
    out.append("-" * 100)
    for m in sorted(rows, key=lambda r: -r.get("score", float("-inf"))):
        if not m.get("trades"):
            out.append(f"{m.get('pair',''):<8}{m['name']:<16}{m['mode']:<10}"
                       f"{m['rr']:<6}{'0':>5}   "
                       + str(m.get("error", "no trades"))[:36])
            continue
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        out.append(f"{m.get('pair',''):<8}{m['name']:<16}{m['mode']:<10}"
                   f"{m['rr']:<6}{m['trades']:>5}{m['win_rate']:>5.0%}{pf:>7}"
                   f"{m['expectancy']:>+8.2f}{m['max_dd_r']:>+8.1f}"
                   f"{m.get('nL', 0):>6}{m.get('nS', 0):>6}"
                   f"  {m.get('two_sided', '?')}")
    return "\n".join(out)


def verdict(rows: list[dict]) -> str:
    # A one-sided result in a trending sample measures the trend, so it is
    # reported in the table but never ranked or recommended.
    rankable = [r for r in rows if r.get("trades", 0) >= MIN_TRADES
                and r.get("two_sided") == "YES"]
    if not rankable:
        return ("\nNOTHING IS RANKABLE. No combination both cleared "
                f"{MIN_TRADES} trades and made money in both directions. "
                "That is the finding: on this sample nothing here is "
                "distinguishable from riding the prevailing trend.")
    out = ["", "Ranked on expectancy after costs, under the partial-exit plan",
           "the bot actually trades. Excluded: fewer than "
           f"{MIN_TRADES} trades, or not profitable on BOTH sides with at",
           f"least {MIN_SIDE_TRADES} trades each — a strategy that only went",
           "long in a rising market has measured the market, not itself.", ""]
    best = sorted(rankable, key=lambda r: -r["score"])[:5]
    for n, m in enumerate(best, start=1):
        out.append(f"  {n}. {m['name']} · {m.get('pair','')} · {m['mode']} · "
                   f"{m['rr']} — {m['expectancy']:+.2f}R over {m['trades']} trades")

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
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                   help="parallel workers (default: cores minus one)")
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

    work = []
    for path, mode in jobs:
        df = load(path)
        pair = os.path.basename(path).split("_")[0].upper()
        print(f"{os.path.basename(path)} — {len(df)} bars, {mode}, "
              f"{df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}")
        if len(df) < 800:
            print(f"  only {len(df)} bars; too short to be worth ranking")
            continue
        for key in ORDER:
            for rr in RR_SETS:
                work.append((path, mode, pair, key, rr))

    print(f"\n{len(work)} backtests on {args.jobs} worker(s)\n")
    rows = sweep_all(work, args.jobs)

    print()
    print(table(rows))
    print(verdict(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
