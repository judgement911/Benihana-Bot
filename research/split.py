"""
THE SPLIT, DECIDED BEFORE ANYTHING IS MEASURED
==============================================

Chronological, never random: shuffling bars would let the model learn from
the future of its own training set, which is the subtlest form of
look-ahead and the hardest to spot afterwards.

    IN-SAMPLE    50%   develop here, look freely
    VALIDATION   25%   choose between variants here
    OUT-OF-SAMPLE 25%  do not open until the strategy is final

The out-of-sample slice is the only honest estimate of live performance, and
it is only honest ONCE. Every peek followed by an adjustment spends it, and
nothing puts it back. This module exists so the boundaries are defined by
code written before any result was seen, rather than by a judgement call
made afterwards with numbers already in view.
"""
from __future__ import annotations

import pandas as pd

IS, VAL, OOS = "in_sample", "validation", "out_of_sample"
FRACTIONS = {IS: 0.50, VAL: 0.25, OOS: 0.25}


def bounds(n: int) -> dict:
    a = int(n * FRACTIONS[IS])
    b = a + int(n * FRACTIONS[VAL])
    return {IS: (0, a), VAL: (a, b), OOS: (b, n)}


def slice_df(df: pd.DataFrame, part: str) -> pd.DataFrame:
    lo, hi = bounds(len(df))[part]
    return df.iloc[lo:hi]


def describe(df: pd.DataFrame) -> str:
    out = []
    for part, (lo, hi) in bounds(len(df)).items():
        s = df.index[lo]
        e = df.index[hi - 1]
        out.append(f"  {part:<14}{hi - lo:>7} bars  {s:%Y-%m-%d} to {e:%Y-%m-%d}")
    return "\n".join(out)


def walk_forward(n: int, folds: int = 4, train_frac: float = 0.6):
    """Rolling TRAIN -> TEST windows for Phase 6.

    Anchored to a fixed window length rather than an expanding one, so every
    fold is trained on the same amount of data and the later folds are not
    quietly advantaged.
    """
    win = int(n / (folds * (1 - train_frac) + train_frac))
    tr = int(win * train_frac)
    step = win - tr
    out = []
    start = 0
    while start + win <= n:
        out.append(((start, start + tr), (start + tr, start + win)))
        start += step
    return out
