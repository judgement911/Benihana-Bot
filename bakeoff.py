"""
THE BAKE-OFF
============

Backtests all ten candidate rulesets over the same history and ranks them on
the metrics §14 asks for: win rate, profit factor, net return, maximum
drawdown, average R, expectancy, trade count, and consistency across the
periods within the sample.

    python3 bakeoff.py --live --symbol XAU/USD --mode intraday
    python3 bakeoff.py --csv XAUUSD_M15.csv --mode intraday

WHY RANKING IS NOT ONE NUMBER
-----------------------------
The highest net return in a sample is usually the most overfit thing in it.
The score here deliberately refuses to reward that alone:

  * a candidate with fewer than MIN_TRADES trades is reported but never
    ranked — you cannot measure an edge from six trades
  * expectancy is weighted above raw return, so a strategy that made its
    money in one lucky run does not win
  * drawdown is a penalty, not a footnote
  * consistency across the sample's halves is scored, so something that
    worked only in the first month scores below something that worked in
    both

IT RUNS IN BATCHES, BECAUSE IT HAS TO
-------------------------------------
Ten backtests cost roughly 190 CPU-seconds. A free PythonAnywhere account
gets 100 a day, so the whole thing in one go would be killed halfway and
leave you nothing. Results accumulate into a JSON file instead, so you can
run a few candidates a day and rank them once they are all in:

    python3 bakeoff.py --live --resume --budget 80     # day one
    python3 bakeoff.py --live --resume --budget 80     # day two, the rest
    python3 bakeoff.py --report                        # the table

--budget stops starting new candidates once the estimate says the next one
would not finish inside the allowance. On a paid plan or your own machine,
drop it and run the lot.

WHAT IT WILL NOT DO
-------------------
It will not invent numbers. If the data is too short, too coarse, or the
provider will not serve it, this prints why and exits non-zero. An
unvalidated strategy stays unvalidated; that is a fact about your data, not
a problem to be smoothed over with a plausible-looking table.

A partial table says which candidates are still missing. Ranking six of ten
and calling it a winner would be exactly the kind of quiet overreach the
scoring above is designed to avoid.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import resource
import sys

import pandas as pd

import candidates as CAND
import config as C
from backtest import run as backtest_run

MIN_TRADES = 20            # below this a result is reported but not ranked
RESULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "bakeoff_results.json")


def _cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def load_results(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_results(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    rs = [float(t["r"]) for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    half = len(rs) // 2 or 1
    first, second = sum(rs[:half]), sum(rs[half:])
    both_positive = first > 0 and second > 0

    return {
        "trades": len(rs),
        "win_rate": len(wins) / len(rs),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0
                         else (float("inf") if gross_win > 0 else 0.0),
        "net_r": sum(rs),
        "avg_r": sum(rs) / len(rs),
        "expectancy": sum(rs) / len(rs),
        "max_dd_r": max_dd,
        "first_half_r": first,
        "second_half_r": second,
        "consistent": both_positive,
    }


def _score(m: dict) -> float:
    """Robustness, not raw return. Unrankable candidates score -inf."""
    if m.get("trades", 0) < MIN_TRADES:
        return float("-inf")
    exp = m["expectancy"]
    dd = abs(m["max_dd_r"])
    pf = min(m["profit_factor"], 5.0)          # cap: 12.0 and 5.0 are both "good"
    consistency = 1.0 if m["consistent"] else 0.0
    # Expectancy carries the weight; drawdown subtracts; consistency breaks ties.
    return (exp * 100.0) + (pf * 8.0) - (dd * 4.0) + (consistency * 15.0)


def run_bakeoff(entry_df: pd.DataFrame, mode: str, only=None, done=None,
                budget: float = None, log=print) -> dict:
    """Backtest the requested candidates, respecting a CPU budget.

    Returns {key: metrics}. Anything already in `done` is skipped, and once
    the elapsed CPU plus one more candidate's estimate would exceed
    `budget`, it stops rather than being killed mid-run by the host.
    """
    done = dict(done or {})
    todo = [k for k in (only or CAND.CANDIDATES)
            if k in CAND.CANDIDATES and k not in done]
    if not todo:
        return done

    started = _cpu()
    per_candidate = None
    for key in todo:
        if budget and per_candidate:
            spent = _cpu() - started
            if spent + per_candidate > budget:
                log(f"  stopping: {spent:.0f}s spent, the next candidate needs "
                    f"about {per_candidate:.0f}s and the budget is {budget:.0f}s. "
                    f"Re-run with --resume to continue.")
                break

        meta = CAND.CANDIDATES[key]
        t0 = _cpu()
        try:
            # backtest_run narrates a full report per candidate. Ten of those
            # bury the comparison this script exists to print.
            with contextlib.redirect_stdout(io.StringIO()):
                stats, _ = backtest_run(entry_df, mode, strategy=meta["fn"])
            m = _metrics(stats.get("trade_log") or [])
        except Exception as exc:                    # noqa: BLE001
            m = {"trades": 0, "error": f"{type(exc).__name__}: {exc}"}
        cost = _cpu() - t0
        per_candidate = cost if per_candidate is None else max(per_candidate, cost)

        m["name"] = meta["name"]
        m["idea"] = meta["idea"]
        m["score"] = _score(m)
        m["cpu_seconds"] = round(cost, 1)
        done[key] = m
        log(f"  {meta['name']:<17} {m.get('trades', 0):>4} trades  "
            f"{cost:.0f}s CPU")
    return done


def ranked(done: dict) -> list[tuple[str, dict]]:
    out = [(k, v) for k, v in done.items()]
    out.sort(key=lambda kv: kv[1].get("score", float("-inf")), reverse=True)
    return out


def format_table(results, bars: int, mode: str) -> str:
    missing = [CAND.CANDIDATES[k]["name"] for k in CAND.CANDIDATES
               if k not in dict(results)]
    out = [f"BAKE-OFF — {mode}, {bars} bars", ""]
    out.append(f"{'strategy':<17}{'n':>5}{'win':>7}{'PF':>7}{'net R':>8}"
               f"{'avg R':>8}{'maxDD':>8}  consistent")
    out.append("-" * 72)
    for _, m in results:
        if m.get("trades", 0) == 0:
            out.append(f"{m['name']:<17}{'0':>5}   "
                       + (m.get("error", "no trades")[:44]))
            continue
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        flag = "" if m["trades"] >= MIN_TRADES else "  (too few to rank)"
        out.append(f"{m['name']:<17}{m['trades']:>5}{m['win_rate']:>6.0%}"
                   f"{pf:>7}{m['net_r']:>+8.1f}{m['avg_r']:>+8.2f}"
                   f"{m['max_dd_r']:>+8.1f}  "
                   f"{'yes' if m['consistent'] else 'no':<4}{flag}")
    out.append("")

    if missing:
        out.append(f"NOT YET RUN ({len(missing)}): " + ", ".join(missing))
        out.append("Re-run with --resume to finish. The ranking below is "
                   "partial until they are all in.")
        out.append("")

    rankable = [r for r in results if r[1].get("score", float("-inf")) != float("-inf")]
    if not rankable:
        out.append(f"NOTHING IS RANKABLE. Every candidate produced fewer than "
                   f"{MIN_TRADES} trades on this sample, which is too few to "
                   f"measure an edge from. Use more history before drawing a "
                   f"conclusion — this is not a result, it is an absence of one.")
        return "\n".join(out)

    out.append("Ranked on expectancy, profit factor, drawdown and consistency —")
    out.append("deliberately NOT on net return alone, which favours overfitting.")
    out.append("")
    for n, (key, m) in enumerate(rankable[:3], start=1):
        out.append(f"  {n}. {m['name']} ({m['idea']}) — score {m['score']:.1f}")
    out.append("")
    out.append(f"Sample: {bars} bars. Treat anything under a few hundred trades")
    out.append("as indicative, not settled.")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description="Rank the ten candidate strategies")
    p.add_argument("--mode", default="intraday", choices=list(C.MODES))
    p.add_argument("--csv", help="broker-exported OHLC on the entry timeframe")
    p.add_argument("--live", action="store_true", help="pull history from the provider")
    p.add_argument("--symbol", default="XAU/USD")
    p.add_argument("--bars", type=int, default=C.BACKTEST_BARS_CLI)
    p.add_argument("--only", help="comma-separated candidate keys")
    p.add_argument("--resume", action="store_true",
                   help="skip candidates already in the results file")
    p.add_argument("--budget", type=float,
                   help="stop before exceeding this many CPU-seconds")
    p.add_argument("--out", default=RESULTS_FILE)
    p.add_argument("--report", action="store_true",
                   help="print the table from saved results and exit")
    args = p.parse_args()

    if args.report:
        done = load_results(args.out)
        if not done:
            print(f"No saved results at {args.out}. Run the bake-off first.",
                  file=sys.stderr)
            return 1
        print(format_table(ranked(done), done.get("_bars", 0),
                           done.get("_mode", args.mode)))
        return 0

    spec = C.MODES[args.mode]
    if args.csv:
        from data import load_csv
        df = load_csv(args.csv)
    elif args.live:
        from data import fetch_ohlc
        df = fetch_ohlc(args.symbol, spec.entry_tf, args.bars)
    else:
        print("Give me data: --csv FILE or --live", file=sys.stderr)
        return 2

    if len(df) < 800:
        print(f"Only {len(df)} bars. That is not enough history to rank ten "
              f"strategies against each other, and a table built on it would "
              f"be noise wearing a suit. Get more data.", file=sys.stderr)
        return 1

    saved = load_results(args.out) if args.resume else {}
    saved.pop("_bars", None)
    saved.pop("_mode", None)
    only = [k.strip() for k in args.only.split(",")] if args.only else None

    todo = len([k for k in (only or CAND.CANDIDATES) if k not in saved])
    print(f"Running {todo} candidate(s) over {len(df)} bars. "
          f"Roughly {todo * 19} CPU-seconds; a free PythonAnywhere account "
          f"gets 100 a day.\n")

    done = run_bakeoff(df, args.mode, only=only, done=saved, budget=args.budget)
    done["_bars"], done["_mode"] = len(df), args.mode
    save_results(args.out, done)
    done.pop("_bars"); done.pop("_mode")

    print()
    print(format_table(ranked(done), len(df), args.mode))
    print(f"\nSaved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
