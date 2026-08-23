"""
PHASE 2 — FEATURE DISCOVERY
===========================

Phase 1 tested ten strategies I wrote in advance. That is a guess, ten times.
This asks the data a narrower question instead: does any measurable feature
carry information about what price does next?

Method. For every feature, the rank correlation (Spearman) with the forward
return over several horizons, measured in ATR units so it is comparable
across instruments and across volatility regimes. That statistic is the
information coefficient; in this field an IC of 0.03 is worth having and
0.10 is extraordinary.

THE MULTIPLE-TESTING PROBLEM, WHICH IS THE WHOLE DANGER HERE
------------------------------------------------------------
37 features x 4 horizons x 2 instruments is 296 tests. At p<0.05 roughly
fifteen will look significant with no signal present at all. Ranking them
and picking the top one is not research, it is drawing the target after
firing.

So the bar is deliberately harsh: a feature must clear significance on BOTH
instruments, at the SAME horizon, with the SAME sign. Two independent
markets agreeing by chance is far less likely than one, and the sign
requirement kills the case where a feature is merely volatile.

IN-SAMPLE ONLY. Validation is already partly spent; out-of-sample is sealed.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features
import split

HORIZONS = (6, 12, 24, 48)
PAIRS = ("xauusd", "eurusd")
TF = "5min"


def forward_return_atr(df: pd.DataFrame, atr: pd.Series, n: int) -> pd.Series:
    """Return over the next n bars, scaled by ATR at the decision point.

    Scaling matters: a 10-point gold move means something different in a
    quiet week than a violent one, and an unscaled correlation would mostly
    measure volatility clustering.
    """
    fwd = df["close"].shift(-n) - df["close"]
    return fwd / atr.replace(0, np.nan)


def ic_table(pair: str, tf: str = TF) -> pd.DataFrame:
    df = pd.read_csv(f"data/{pair}_{tf}.csv", parse_dates=["datetime"]).set_index("datetime")
    df = df[["open", "high", "low", "close"]]
    f = features.build(df)
    lo, hi = split.bounds(len(df))[split.IS]

    rows = []
    for n in HORIZONS:
        fwd = forward_return_atr(df, f["atr"], n)
        # Trim the tail: the last n bars have no forward return to compare to,
        # and including them as NaN-adjacent noise biases nothing but wastes
        # the alignment. Explicit is safer than implicit here.
        y = fwd.iloc[lo:hi]
        for col in f.columns:
            if col in ("atr",):
                continue
            x = f[col].iloc[lo:hi]
            ok = x.notna() & y.notna() & np.isfinite(x) & np.isfinite(y)
            if ok.sum() < 500 or x[ok].nunique() < 10:
                continue
            ic, p = stats.spearmanr(x[ok], y[ok])
            rows.append({"pair": pair, "feature": col, "horizon": n,
                         "ic": ic, "p": p, "n": int(ok.sum())})
    return pd.DataFrame(rows)


def main():
    tables = {p: ic_table(p) for p in PAIRS}
    a, b = tables[PAIRS[0]], tables[PAIRS[1]]
    m = a.merge(b, on=["feature", "horizon"], suffixes=("_a", "_b"))

    # Agreement on BOTH instruments, same sign, both significant.
    m["agree"] = np.sign(m.ic_a) == np.sign(m.ic_b)
    m["both_sig"] = (m.p_a < 0.01) & (m.p_b < 0.01)
    m["min_abs_ic"] = np.minimum(m.ic_a.abs(), m.ic_b.abs())
    keep = m[m.agree & m.both_sig].sort_values("min_abs_ic", ascending=False)

    print(f"PHASE 2 — feature discovery, {TF}, IN-SAMPLE only")
    print(f"  {len(a)} tests per instrument, {len(m)} paired\n")
    print(f"  {'feature':<16}{'horiz':>6}{'IC gold':>9}{'IC eur':>9}"
          f"{'weaker':>9}  both p<0.01 and same sign")
    print("  " + "-" * 72)
    if keep.empty:
        print("  NOTHING. No feature is significant on both instruments with")
        print("  the same sign. That is the result.")
    else:
        for _, r in keep.head(15).iterrows():
            print(f"  {r.feature:<16}{int(r.horizon):>6}{r.ic_a:>+9.4f}"
                  f"{r.ic_b:>+9.4f}{r.min_abs_ic:>+9.4f}")
        print(f"\n  {len(keep)} of {len(m)} feature/horizon pairs survive.")
        exp = len(m) * 0.01 * 0.01 * 0.5
        print(f"  Expected by chance if nothing predicts anything: ~{exp:.1f}")
    return keep


if __name__ == "__main__":
    k = main()
    if not k.empty:
        k.to_csv("research/phase2_ic.csv", index=False)
