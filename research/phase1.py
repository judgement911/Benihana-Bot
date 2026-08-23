"""PHASE 1 — do any of the families show anything at all? IN-SAMPLE ONLY."""
from __future__ import annotations

import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import families
import features
import metrics
import split
from engine import Costs, shift_causal, simulate

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import instruments as I

TARGETS = {"1:2": (1.0, 2.0), "1:3": (1.0, 2.0, 3.0)}


def load(path):
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
    return df[["open", "high", "low", "close"]]


def run_one(df, inst, fam_fn, tps, part=split.IS):
    """One family, one instrument, one target set, on one slice."""
    f = features.build(df)                      # features over the FULL series
    sig, stop = fam_fn(df, f)                   # so warmup is not wasted,
    sig = shift_causal(sig).astype(float)       # then shifted and sliced
    stop = shift_causal(stop)
    lo, hi = split.bounds(len(df))[part]
    sub = df.iloc[lo:hi]
    s = pd.Series(sig).iloc[lo:hi].fillna(0).astype(int).to_numpy()
    d = pd.Series(stop).iloc[lo:hi].to_numpy()
    res = simulate(sub, s, d, tps, Costs.for_instrument(inst))
    return res


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "4h"
    rows = []
    for path in sorted(glob.glob(f"data/*_{tf}.csv")):
        pair = os.path.basename(path).split("_")[0]
        inst = I.find(pair)
        if inst is None:
            print(f"  skipping {pair}: unknown instrument")
            continue
        df = load(path)
        if len(df) < 1000:
            print(f"  skipping {pair} {tf}: only {len(df)} bars")
            continue
        for name, (fn, desc) in families.FAMILIES.items():
            for tname, tps in TARGETS.items():
                res = run_one(df, inst, fn, tps)
                rs = res.r_series(net=True)
                m = metrics.summarise(rs, [t.bars_held for t in res.trades], tf)
                g = metrics.summarise(res.r_series(net=False))
                m.update(pair=pair.upper(), family=name, rr=tname, desc=desc,
                         gross_exp=g.get("expectancy", 0.0))
                if m["trades"]:
                    m.update(metrics.significance(rs))
                rows.append(m)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    d = main()
    d.to_csv("research/phase1_results.csv", index=False)
    ok = d[d.trades >= 30].copy()
    print(f"PHASE 1 — {len(d)} runs, IN-SAMPLE only, net of spread+slippage\n")
    if ok.empty:
        print("Nothing reached 30 trades in-sample. No family is testable here.")
        raise SystemExit(0)

    # Rank families by MEDIAN expectancy across instruments: a family that
    # works everywhere beats one carried by a single pair.
    agg = (ok.groupby(["family", "rr"])
             .agg(pairs=("pair", "nunique"), trades=("trades", "sum"),
                  med_exp=("expectancy", "median"),
                  min_exp=("expectancy", "min"), max_exp=("expectancy", "max"),
                  med_pf=("profit_factor", "median"),
                  gross=("gross_exp", "median"))
             .reset_index().sort_values("med_exp", ascending=False))
    print(f"{'family':<14}{'R:R':<6}{'pairs':>6}{'trades':>8}{'gross':>8}"
          f"{'net exp':>9}{'worst':>8}{'best':>8}{'PF':>7}")
    print("-" * 74)
    for _, r in agg.iterrows():
        print(f"{r.family:<14}{r.rr:<6}{r.pairs:>6}{int(r.trades):>8}"
              f"{r.gross:>+8.3f}{r.med_exp:>+9.3f}{r.min_exp:>+8.3f}"
              f"{r.max_exp:>+8.3f}{r.med_pf:>7.2f}")
    pos = agg[agg.med_exp > 0]
    print(f"\n{len(pos)} of {len(agg)} family/target combinations have positive")
    print("median net expectancy in-sample. In-sample positive is the WEAKEST")
    print("possible evidence — it is the bar to clear before testing, not a result.")
