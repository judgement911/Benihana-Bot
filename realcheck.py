"""
A SANITY CHECK ON REAL PRICE ACTION
===================================

Runs all three strategies at all three reward targets against 5000 hourly
EUR/USD bars that ship inside the `backtesting` package — real market data,
requiring no API key and no network, so it runs anywhere.

    pip install backtesting && python3 realcheck.py

WHAT IT IS FOR
--------------
Confirming the rulesets behave sanely on real price action: that they fire,
that trade counts are plausible, and that the relative ordering of reward
targets holds up on something with genuine autocorrelation rather than the
random walk in selftest.py.

WHAT IT IS NOT FOR
------------------
Deciding anything. The sample is EUR/USD in 2017-18, which rose 14.6% almost
without pausing, and the strategies take long trades in it at roughly forty
to one. Every absolute number this prints is inflated by that: it measures a
bull market at least as much as it measures a strategy, and it says nothing
about how any of them behave in a downtrend.

It also cannot touch scalp or intraday, whose entry timeframes are finer
than the hourly source, nor swing, whose weekly bias needs years of history.
The mode used here is 1h/4h/1day — one step coarser than the bot's intraday.

For a decision, use real history for the instrument and timeframe you
actually trade: fetch_history.py then matrix.py.
"""
import contextlib, io, math
from dataclasses import replace
import pandas as pd
from backtesting.test import EURUSD

import config as C
from backtest import run as backtest_run
from bakeoff import MIN_TRADES, _metrics
from strategies import ORDER, REGISTRY

df = EURUSD.rename(columns=str.lower)[["open", "high", "low", "close"]]
df.index = pd.to_datetime(df.index, utc=True)

# One step coarser than the bot's intraday (15min/1h/4h). Same structure.
MODE = replace(C.MODES["intraday"], name="h1", entry_tf="1h",
               trend_tf="4h", bias_tf="1day")
C.MODES["h1"] = MODE

RR = {"1:1": (1.0,), "1:2": (1.0, 2.0), "1:3": (1.0, 2.0, 3.0)}

rows = []
for key in ORDER:
    for label, tps in RR.items():
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                stats, _ = backtest_run(df, "h1", strategy=REGISTRY[key].evaluate,
                                        tp_multiples=tps)
            m = _metrics(stats.get("trade_log") or [])
        except Exception as exc:
            m = {"trades": 0, "error": f"{type(exc).__name__}: {exc}"}
        m.update(name=REGISTRY[key].name, rr=label)
        rows.append(m)

print(f"REAL DATA — EUR/USD 1h, {len(df)} bars, "
      f"{df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d}")
print("mode 1h/4h/1day (one step coarser than the bot's intraday)\n")
print(f"{'strategy':<16}{'R:R':<6}{'n':>5}{'win':>7}{'PF':>7}{'exp R':>8}"
      f"{'net R':>8}{'maxDD':>8}  rankable")
print("-" * 74)
for m in rows:
    if not m.get("trades"):
        print(f"{m['name']:<16}{m['rr']:<6}{'0':>5}   {str(m.get('error','no trades'))[:38]}")
        continue
    pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    print(f"{m['name']:<16}{m['rr']:<6}{m['trades']:>5}{m['win_rate']:>6.0%}{pf:>7}"
          f"{m['expectancy']:>+8.2f}{m['net_r']:>+8.1f}{m['max_dd_r']:>+8.1f}  "
          f"{'yes' if m['trades'] >= MIN_TRADES else 'too few'}")

rank = [m for m in rows if m.get("trades", 0) >= MIN_TRADES]
print()
if not rank:
    print(f"Nothing reached {MIN_TRADES} trades — no ranking is possible.")
else:
    pos = [m for m in rank if m["expectancy"] > 0]
    print(f"{len(rank)} of {len(rows)} combinations are rankable; "
          f"{len(pos)} have positive expectancy after costs.")
    for m in sorted(rank, key=lambda r: -r["expectancy"])[:4]:
        se = 1.0 / math.sqrt(m["trades"])
        sig = "significant" if m["expectancy"] > 1.96 * se else "within noise"
        print(f"  {m['name']:<15} {m['rr']}  {m['expectancy']:+.3f}R over "
              f"{m['trades']:>3} trades   ({sig}, ±{1.96*se:.2f}R)")
