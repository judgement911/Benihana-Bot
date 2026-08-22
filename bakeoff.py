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

WHAT IT WILL NOT DO
-------------------
It will not invent numbers. If the data is too short, too coarse, or the
provider will not serve it, this prints why and exits non-zero. An
unvalidated strategy stays unvalidated; that is a fact about your data, not
a problem to be smoothed over with a plausible-looking table.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

import candidates as CAND
import config as C
from backtest import run as backtest_run

MIN_TRADES = 20            # below this a result is reported but not ranked


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


def run_bakeoff(entry_df: pd.DataFrame, mode: str) -> list[tuple[str, dict]]:
    results = []
    for key, meta in CAND.CANDIDATES.items():
        try:
            stats, _ = backtest_run(entry_df, mode, strategy=meta["fn"])
            m = _metrics(stats.get("trade_log") or [])
        except Exception as exc:                    # noqa: BLE001
            m = {"trades": 0, "error": f"{type(exc).__name__}: {exc}"}
        m["name"] = meta["name"]
        m["idea"] = meta["idea"]
        m["score"] = _score(m)
        results.append((key, m))
    results.sort(key=lambda kv: kv[1]["score"], reverse=True)
    return results


def format_table(results, bars: int, mode: str) -> str:
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

    rankable = [r for r in results if r[1]["score"] != float("-inf")]
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
    args = p.parse_args()

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

    results = run_bakeoff(df, args.mode)
    print(format_table(results, len(df), args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
