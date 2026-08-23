"""
CANDIDATE STRATEGY FAMILIES
===========================

Every family the brief asks for, each as the simplest honest expression of
its idea. Simple on purpose: a family is being asked "is there anything here
at all", and a baseline loaded with filters cannot answer that — you can no
longer tell whether the idea works or one of the filters is carrying it.

Each generator returns (signal, stop_distance) aligned to the bar whose
CLOSE produced the decision. engine.simulate applies the one-bar delay and
fills at the next open, so nothing here needs to remember the shift.

Parameters are round numbers, not tuned. Phase 7 sweeps them to see whether
performance sits on a plateau or a spike; a spike means the number was
fitted to noise and the family should be distrusted.
"""
from __future__ import annotations

import numpy as np

from engine import LONG, SHORT


def _blank(n):
    return np.zeros(n, dtype=int), np.full(n, np.nan)


# --------------------------------------------------------------------------- #
#  A. Trend following
# --------------------------------------------------------------------------- #
def donchian_breakout(df, f, n=20, atr_mult=2.0):
    """Close beyond the n-bar extreme of the PRIOR bars."""
    sig, stop = _blank(len(df))
    hi = df["high"].rolling(n).max().shift(1)
    lo = df["low"].rolling(n).min().shift(1)
    c = df["close"]
    sig[(c > hi).to_numpy()] = LONG
    sig[(c < lo).to_numpy()] = SHORT
    stop = (f["atr"] * atr_mult).to_numpy()
    return sig, stop


def trend_pullback(df, f, n=50, atr_mult=1.5, depth=0.5):
    """In an established trend, buy a dip toward the mean."""
    sig, stop = _blank(len(df))
    c = df["close"]
    ma = c.rolling(n).mean()
    up = (c > ma) & (ma > ma.shift(10))
    dn = (c < ma) & (ma < ma.shift(10))
    near = (c - ma).abs() < depth * f["atr"]
    sig[(up & near).to_numpy()] = LONG
    sig[(dn & near).to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


def channel_ride(df, f, n=30, atr_mult=2.0, thresh=0.80):
    """Price pinned to the top or bottom of its own recent range."""
    sig, stop = _blank(len(df))
    pos = f[f"pos_{20 if n <= 20 else 50}"]
    sig[(pos > thresh).to_numpy()] = LONG
    sig[(pos < 1 - thresh).to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


# --------------------------------------------------------------------------- #
#  B. Mean reversion
# --------------------------------------------------------------------------- #
def zscore_fade(df, f, n=20, z=2.0, atr_mult=1.5):
    """Fade a statistical extreme against its own recent distribution."""
    sig, stop = _blank(len(df))
    zz = f[f"z_{n}"] if f"z_{n}" in f else f["z_20"]
    sig[(zz < -z).to_numpy()] = LONG
    sig[(zz > z).to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


def range_reversion(df, f, n=50, edge=0.05, atr_mult=1.5):
    """Buy the bottom of a range, sell the top — only when NOT trending."""
    sig, stop = _blank(len(df))
    pos, er = f["pos_50"], f["er_30"]
    quiet = er < 0.30                      # low efficiency = ranging
    sig[((pos < edge) & quiet).to_numpy()] = LONG
    sig[((pos > 1 - edge) & quiet).to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


# --------------------------------------------------------------------------- #
#  C. Market structure
# --------------------------------------------------------------------------- #
def failed_breakout(df, f, n=20, atr_mult=1.5):
    """Price breaks the n-bar extreme intrabar, then closes back inside."""
    sig, stop = _blank(len(df))
    hi = df["high"].rolling(n).max().shift(1)
    lo = df["low"].rolling(n).min().shift(1)
    poked_up = (df["high"] > hi) & (df["close"] < hi)
    poked_dn = (df["low"] < lo) & (df["close"] > lo)
    sig[poked_dn.to_numpy()] = LONG        # failed break DOWN -> long
    sig[poked_up.to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


def prior_day_level(df, f, atr_mult=1.5, tol=0.25):
    """React at yesterday's high or low — a level everyone can see."""
    sig, stop = _blank(len(df))
    at_high = f["pd_high_dist"].abs() < tol
    at_low = f["pd_low_dist"].abs() < tol
    reject_up = at_high & (f["close_pos"] < 0.4)
    reject_dn = at_low & (f["close_pos"] > 0.6)
    sig[reject_dn.to_numpy()] = LONG
    sig[reject_up.to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


# --------------------------------------------------------------------------- #
#  D. Volatility
# --------------------------------------------------------------------------- #
def squeeze_expansion(df, f, pct=0.25, atr_mult=1.5):
    """A quiet stretch resolves; take the direction of the resolution."""
    sig, stop = _blank(len(df))
    width = f["rvol_20"]
    rank = width.rolling(120).apply(lambda w: (w.iloc[-1] >= w).mean(), raw=False)
    tight_before = rank.shift(1) < pct
    expanding = f["range_ratio"] > 1.2
    c, o = df["close"], df["open"]
    sig[(tight_before & expanding & (c > o)).to_numpy()] = LONG
    sig[(tight_before & expanding & (c < o)).to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


def vol_regime_trend(df, f, atr_mult=2.0):
    """Trend continuation, but only while volatility is expanding."""
    sig, stop = _blank(len(df))
    rising = f["atr_ratio"] > 1.1
    sig[((f["er_30"] > 0.35) & (f["ret_20"] > 0) & rising).to_numpy()] = LONG
    sig[((f["er_30"] > 0.35) & (f["ret_20"] < 0) & rising).to_numpy()] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


# --------------------------------------------------------------------------- #
#  E. Session
# --------------------------------------------------------------------------- #
def session_breakout(df, f, open_hour=7, window=4, atr_mult=1.5):
    """Break of the range built in the first hours of the session."""
    sig, stop = _blank(len(df))
    idx = df.index
    day = idx.normalize()
    in_win = (idx.hour >= open_hour) & (idx.hour < open_hour + window)
    hi = df["high"].where(in_win).groupby(day).cummax().groupby(day).ffill()
    lo = df["low"].where(in_win).groupby(day).cummin().groupby(day).ffill()
    after = idx.hour >= open_hour + window
    sig[(after & (df["close"] > hi).to_numpy())] = LONG
    sig[(after & (df["close"] < lo).to_numpy())] = SHORT
    return sig, (f["atr"] * atr_mult).to_numpy()


FAMILIES = {
    "A_donchian":     (donchian_breakout, "trend: n-bar breakout"),
    "A_pullback":     (trend_pullback,    "trend: dip to the mean"),
    "A_channel":      (channel_ride,      "trend: pinned to range edge"),
    "B_zfade":        (zscore_fade,       "reversion: fade a z-score extreme"),
    "B_range":        (range_reversion,   "reversion: range edge when quiet"),
    "C_failbreak":    (failed_breakout,   "structure: failed breakout"),
    "C_priorday":     (prior_day_level,   "structure: prior-day high/low"),
    "D_squeeze":      (squeeze_expansion, "volatility: quiet then expanding"),
    "D_voltrend":     (vol_regime_trend,  "volatility: trend while vol rises"),
    "E_session":      (session_breakout,  "session: opening-range break"),
}
