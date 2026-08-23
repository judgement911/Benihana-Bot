"""
PER-MODE PARAMETER TUNING — carefully
=====================================

Every strategy currently uses one parameter set at every timeframe. A 20-bar
Donchian channel is 100 minutes on the scalp chart and three days on the
swing chart; asking one number to mean both is why four of five strategies
collapse toward whichever timeframe their fixed settings happen to suit.

So: tune each strategy for the mode it should own. The danger is obvious —
this is optimisation, and optimisation finds noise if you let it. Three
rules keep it honest:

  IN-SAMPLE ONLY.   Validation stays shut until a winner is chosen.
  PLATEAU, NOT PEAK. A parameter is only accepted if its NEIGHBOURS also
                    work. One good cell surrounded by bad ones is a fluke,
                    and picking it is how backtests get fitted to noise.
  GROSS FIRST.      A setting that only wins because a cost assumption
                    flattered it has not found anything.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import instruments as I
import split
from fullgrid import MODE_TF, collect_entries, load, simulate_exits

BARS = 20000          # enough for a few hundred trades, fast enough to sweep


def trial(pair, mode, key, overrides, tps=(1.0, 2.0, 3.0)):
    """Run one parameter set. Overrides are applied to config for the run."""
    old = {k: getattr(C, k) for k in overrides}
    for k, v in overrides.items():
        setattr(C, k, v)
    try:
        inst = I.find(pair)
        df = load(pair, MODE_TF[mode])
        lo, hi = split.bounds(len(df))[split.IS]
        df = df.iloc[lo:hi]
        df, entries = collect_entries(df, mode, key, inst, max_bars=BARS)
        if not entries:
            return None
        spread = inst.spread
        cost = lambda r: (spread + spread * 0.5) / r if r > 0 else 0.0
        tr = simulate_exits(df, entries, tps, inst, cost)
        if len(tr) < 40:
            return {"n": len(tr)}
        r = np.array([t["r"] for t in tr])
        g = np.array([t["r_gross"] for t in tr])
        se = r.std(ddof=1) / np.sqrt(len(r))
        return {"n": len(r), "net": r.mean(), "gross": g.mean(),
                "t": r.mean() / se if se > 0 else 0.0}
    finally:
        for k, v in old.items():
            setattr(C, k, v)


def sweep(pair, mode, key, param, values, tps=(1.0, 2.0, 3.0)):
    print(f"\n{key} on {mode} — sweeping {param}")
    print(f"  {'value':>10}{'n':>7}{'gross':>9}{'net':>9}{'t':>7}")
    out = []
    for v in values:
        res = trial(pair, mode, key, {param: v}, tps)
        if not res or "net" not in res:
            print(f"  {str(v):>10}{(res or {}).get('n', 0):>7}   too few trades")
            out.append((v, None))
            continue
        print(f"  {str(v):>10}{res['n']:>7}{res['gross']:>+9.3f}"
              f"{res['net']:>+9.3f}{res['t']:>+7.2f}")
        out.append((v, res))
    good = [(v, r) for v, r in out if r and r["net"] > 0]
    if len(good) >= 2:
        print(f"  -> {len(good)} of {len(out)} values positive: a plateau, worth keeping")
    elif len(good) == 1:
        print(f"  -> only {good[0][0]} works, neighbours do not. A spike. Reject.")
    else:
        print("  -> nothing positive at any setting")
    return out
