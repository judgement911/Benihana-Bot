"""
CANDIDATE FEATURES — measured, not assumed
==========================================

A wide net, deliberately including things I expect to fail. The brief was to
start from zero and keep only what earns its place, so the standard
indicators are in here as candidates on equal footing with everything else,
not as a starting point.

Every feature is causal: computed from closed bars only. The one-bar shift
that makes a feature usable as a signal is applied centrally in engine.py,
so nothing here needs to remember to do it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"],
                      (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df, n=14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def build(df: pd.DataFrame) -> pd.DataFrame:
    """Every candidate feature for one instrument, one timeframe."""
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    f = pd.DataFrame(index=df.index)
    a = atr(df, 14)
    f["atr"] = a
    f["atr_pct"] = a / c

    # ---- volatility regime ------------------------------------------------
    f["atr_ratio"] = a / a.rolling(100).median()
    ret = np.log(c / c.shift(1))
    f["rvol_20"] = ret.rolling(20).std()
    f["rvol_ratio"] = f["rvol_20"] / ret.rolling(100).std()
    # Contraction: today's range against the recent typical range.
    f["range_ratio"] = (h - l) / a.shift(1)
    f["squeeze"] = (h - l).rolling(6).max() / (h - l).rolling(60).median()

    # ---- trend / momentum -------------------------------------------------
    for n in (5, 10, 20, 50):
        f[f"ret_{n}"] = c.pct_change(n)
        f[f"z_{n}"] = (c - c.rolling(n).mean()) / c.rolling(n).std()
    # Kaufman efficiency: net travel over gross travel. 1 = clean trend,
    # 0 = noise. The cleanest trending-vs-ranging discriminator I know that
    # does not need a threshold baked into it.
    for n in (10, 30):
        net = (c - c.shift(n)).abs()
        gross = c.diff().abs().rolling(n).sum()
        f[f"er_{n}"] = net / gross.replace(0, np.nan)
    # Slope of a least-squares line through the last n closes, in ATR units.
    for n in (10, 30):
        x = np.arange(n)
        xm = x.mean()
        denom = ((x - xm) ** 2).sum()
        f[f"slope_{n}"] = (c.rolling(n).apply(
            lambda w: ((np.arange(len(w)) - xm) * (w - w.mean())).sum() / denom,
            raw=True) / a)

    # ---- location within recent range -------------------------------------
    for n in (20, 50):
        hi, lo = h.rolling(n).max(), l.rolling(n).min()
        f[f"pos_{n}"] = (c - lo) / (hi - lo).replace(0, np.nan)
        f[f"dist_hi_{n}"] = (hi - c) / a
        f[f"dist_lo_{n}"] = (c - lo) / a

    # ---- bar shape --------------------------------------------------------
    rng = (h - l).replace(0, np.nan)
    f["body"] = (c - o).abs() / rng
    f["close_pos"] = (c - l) / rng
    f["upper_wick"] = (h - np.maximum(c, o)) / rng
    f["lower_wick"] = (np.minimum(c, o) - l) / rng
    f["gap"] = (o - c.shift(1)) / a

    # ---- persistence ------------------------------------------------------
    up = (c > c.shift(1)).astype(int)
    f["up_streak"] = up.groupby((up != up.shift()).cumsum()).cumsum() * up
    f["autocorr_20"] = ret.rolling(20).apply(
        lambda w: pd.Series(w).autocorr(1) if np.std(w) > 0 else 0.0, raw=True)

    # ---- calendar ---------------------------------------------------------
    f["hour"] = df.index.hour
    f["dow"] = df.index.dayofweek
    # Prior completed day's extremes — a level everybody can see.
    day = df.index.normalize()
    pdh = h.groupby(day).max().shift(1).reindex(day).to_numpy()
    pdl = l.groupby(day).min().shift(1).reindex(day).to_numpy()
    f["pd_high_dist"] = (pdh - c.to_numpy()) / a.to_numpy()
    f["pd_low_dist"] = (c.to_numpy() - pdl) / a.to_numpy()

    # ---- round numbers ----------------------------------------------------
    # Distance to the nearest round level, scaled so it is comparable across
    # instruments quoted at wildly different magnitudes.
    step = 10 ** np.floor(np.log10(c.abs().median())) / 100.0
    f["round_dist"] = ((c / step) % 1.0 - 0.5).abs() * 2.0

    return f
