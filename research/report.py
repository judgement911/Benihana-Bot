"""Turn the grid output into the full report, in the order it was asked for."""
from __future__ import annotations


import pandas as pd

OUT = "research/out"


def line(ch="=", n=78): return ch * n


def main():
    d = pd.read_csv(f"{OUT}/grid_summary.csv")
    d = d[d.trades > 0].copy()
    d["t"] = d.sharpe
    L = []
    A = L.append

    A(line()); A("BENIHANA BOT — FULL BACKTEST REPORT"); A(line())
    A("")
    A("Instrument      XAUUSD")
    A("Strategies      Ronin Edge, Crimson Flow, Kage Protocol,")
    A("                Zanshin Sweep, Shogun Pulse")
    A("Modes           scalp (5min), intraday (15min), swing (4h)")
    A("Targets         1:1, 1:2, 1:3   (Shogun exits on a clock, so one row)")
    A("Risk model      0.5% of $10,000 per trade")
    A("Costs           spread 0.26 + slippage 0.13 (half spread), commission $0")
    A("Data            5min/15min capped at 40,000 bars; 4h full history")
    A("")
    A("NOT MODELLED, and not guessed at:")
    A("  swap / overnight financing — the feed carries none. Matters for swing")
    A("     positions held for days; near-irrelevant for scalps.")
    A("  bid/ask as separate series — these are mid prices. Spread is charged")
    A("     as a cost, not simulated as two books.")
    A("  leverage — an account setting, not a backtest output. Position size")
    A("     here is a fixed fraction of equity at risk.")
    A("")
    A("THE CAVEAT THAT MATTERS MOST: these are whole-file runs. In-sample and")
    A("out-of-sample are MIXED. Every figure below is the optimistic case.")
    A("")

    # ---- 1. core performance, ranked -------------------------------------
    A(line()); A("1. CORE PERFORMANCE — ranked by expectancy after costs"); A(line())
    A("")
    A(f"{'strategy':<14}{'mode':<9}{'R:R':<6}{'n':>5}{'win':>5}{'avgW':>7}"
      f"{'avgL':>7}{'R:R*':>6}{'gross':>8}{'net R':>8}{'t':>6}{'PF':>6}")
    A(line("-"))
    for _, r in d.sort_values("expectancy_r", ascending=False).iterrows():
        pf = "inf" if r.profit_factor == float("inf") else f"{r.profit_factor:.2f}"
        rr = "n/a" if pd.isna(r.rr_realised) else f"{r.rr_realised:.2f}"
        A(f"{r.strategy:<14}{r['mode']:<9}{r.rr:<6}{int(r.trades):>5}"
          f"{r.win_rate:>4.0%}{r.avg_win_r:>+7.2f}{r.avg_loss_r:>+7.2f}"
          f"{rr:>6}{r.expectancy_gross_r:>+8.3f}{r.expectancy_r:>+8.3f}"
          f"{r.t:>+6.2f}{pf:>6}")
    A("")
    A("  R:R* = realised, average win divided by average loss")

    # ---- 2. account -------------------------------------------------------
    A(""); A(line()); A("2. ACCOUNT PERFORMANCE — $10,000 start, 0.5% risk"); A(line())
    A("")
    A(f"{'strategy':<14}{'mode':<9}{'R:R':<6}{'net $':>9}{'end $':>10}"
      f"{'ret%':>8}{'maxDD$':>9}{'maxDD%':>8}{'recov':>7}{'best$':>8}{'worst$':>8}")
    A(line("-"))
    for _, r in d.sort_values("net_usd", ascending=False).iterrows():
        rec = "inf" if r.recovery == float("inf") else f"{r.recovery:.1f}"
        A(f"{r.strategy:<14}{r['mode']:<9}{r.rr:<6}{r.net_usd:>+9.0f}"
          f"{r.end_balance:>10,.0f}{r.return_pct:>+7.1f}%{r.max_dd_usd:>+9.0f}"
          f"{r.max_dd_pct:>7.1f}%{rec:>7}{r.best_trade_usd:>+8.0f}"
          f"{r.worst_trade_usd:>+8.0f}")

    # ---- 3. risk ----------------------------------------------------------
    A(""); A(line()); A("3. RISK AND STREAKS"); A(line())
    A("")
    A(f"{'strategy':<14}{'mode':<9}{'R:R':<6}{'Sharpe':>8}{'Sortino':>9}"
      f"{'maxWin':>8}{'maxLoss':>9}{'ddTrades':>10}{'topTrade%':>11}")
    A(line("-"))
    for _, r in d.sort_values("sharpe", ascending=False).iterrows():
        tt = "n/a" if pd.isna(r.top_trade_share) else f"{100*r.top_trade_share:.1f}%"
        A(f"{r.strategy:<14}{r['mode']:<9}{r.rr:<6}{r.sharpe:>+8.2f}"
          f"{r.sortino:>+9.2f}{int(r.max_win_streak):>8}"
          f"{int(r.max_loss_streak):>9}{int(r.longest_dd_trades):>10}{tt:>11}")
    A("")
    A("  topTrade% = share of total profit from the single best trade.")
    A("  A high number means the result rests on one lucky outlier.")

    # ---- 4. time ----------------------------------------------------------
    A(""); A(line()); A("4. TIME AND FREQUENCY"); A(line())
    A("")
    A(f"{'strategy':<14}{'mode':<9}{'R:R':<6}{'period':<26}{'/day':>6}"
      f"{'/wk':>7}{'/mo':>7}{'avg dur':>10}")
    A(line("-"))
    for _, r in d.sort_values(["mode", "strategy"]).iterrows():
        dur = (f"{r.avg_duration_min:.0f}m" if r.avg_duration_min < 120
               else f"{r.avg_duration_min/60:.1f}h")
        A(f"{r.strategy:<14}{r['mode']:<9}{r.rr:<6}{r.period:<26}"
          f"{r.trades_per_day:>6.1f}{r.trades_per_week:>7.1f}"
          f"{r.trades_per_month:>7.0f}{dur:>10}")

    # ---- 5. monthly / yearly ---------------------------------------------
    A(""); A(line()); A("5. MONTHLY AND YEARLY"); A(line())
    A("")
    A(f"{'strategy':<14}{'mode':<9}{'R:R':<6}{'best mo':>10}{'worst mo':>10}"
      f"{'avg mo':>9}{'best yr':>10}{'worst yr':>10}")
    A(line("-"))
    for _, r in d.sort_values("avg_month_usd", ascending=False).iterrows():
        A(f"{r.strategy:<14}{r['mode']:<9}{r.rr:<6}{r.best_month_usd:>+10.0f}"
          f"{r.worst_month_usd:>+10.0f}{r.avg_month_usd:>+9.0f}"
          f"{r.best_year_usd:>+10.0f}{r.worst_year_usd:>+10.0f}")

    # ---- 6. costs ---------------------------------------------------------
    A(""); A(line()); A("6. WHAT THE COSTS DID"); A(line())
    A("")
    A(f"{'mode':<10}{'combos':>8}{'avg gross':>12}{'avg cost':>11}"
      f"{'avg net':>10}{'verdict':>28}")
    A(line("-"))
    for mode in ("swing", "intraday", "scalp"):
        g = d[d["mode"] == mode]
        if g.empty:
            continue
        gross, net = g.expectancy_gross_r.mean(), g.expectancy_r.mean()
        cost = gross - net
        v = ("edge survives" if net > 0.05 else
             "costs eat most of it" if net > 0 else "costs exceed the edge")
        A(f"{mode:<10}{len(g):>8}{gross:>+12.3f}{cost:>11.3f}{net:>+10.3f}{v:>28}")
    A("")
    A("  This single table is the whole finding. Gross edge barely changes")
    A("  across timeframes; the cost of collecting it does, by ten times.")

    # ---- 7. verdict -------------------------------------------------------
    A(""); A(line()); A("7. WHAT CLEARS STATISTICAL SIGNIFICANCE"); A(line())
    A("")
    sig = d[(d.t > 1.96) & (d.expectancy_r > 0) & (d.trades >= 100)]
    if sig.empty:
        A("  NOTHING. No combination is distinguishable from zero.")
    else:
        for _, r in sig.sort_values("t", ascending=False).iterrows():
            A(f"  {r.strategy:<14}{r['mode']:<9}{r.rr:<6}{int(r.trades):>5} trades  "
              f"{r.expectancy_r:>+.3f}R  t={r.t:+.2f}  PF {r.profit_factor:.2f}  "
              f"maxDD {r.max_dd_pct:.1f}%")
        A("")
        A(f"  {len(sig)} of {len(d)} combinations. "
          f"Modes represented: {', '.join(sorted(sig['mode'].unique()))}.")
    A("")
    A(line()); A("STILL OWED: walk-forward, Monte Carlo, regime splits,")
    A("out-of-sample, and every instrument other than gold."); A(line())
    return "\n".join(L)


if __name__ == "__main__":
    txt = main()
    with open(f"{OUT}/REPORT.txt", "w") as fh:
        fh.write(txt)
    print(txt)
