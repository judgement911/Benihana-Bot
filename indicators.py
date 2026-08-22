"""Pure-pandas technical indicators. No TA-Lib, no compilation headaches."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing — the original RSI/ATR/ADX average."""
    return series.ewm(alpha=1.0 / n, adjust=False).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, n)
    avg_loss = _wilder(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return _wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Returns DataFrame with adx, plus_di, minus_di."""
    up = df["high"].diff()
    down = -df["low"].diff()

    plus_dm = pd.Series(
        np.where((up > down) & (up > 0), up, 0.0), index=df.index, dtype="float64"
    )
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=df.index, dtype="float64"
    )

    atr_ = _wilder(true_range(df), n).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, n) / atr_
    minus_di = 100.0 * _wilder(minus_dm, n) / atr_

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom

    return pd.DataFrame(
        {
            "adx": _wilder(dx.fillna(0.0), n),
            "plus_di": plus_di.fillna(0.0),
            "minus_di": minus_di.fillna(0.0),
        }
    )


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "hist": macd_line - signal_line,
        }
    )


def pivots(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.DataFrame:
    """Fractal swing points. A pivot high has `left` lower highs before it and
    `right` lower highs after it. Confirmed pivots only (hence the shift)."""
    highs = df["high"]
    lows = df["low"]
    n = len(df)

    ph = np.zeros(n, dtype=bool)
    pl = np.zeros(n, dtype=bool)

    h = highs.to_numpy()
    l = lows.to_numpy()

    for i in range(left, n - right):
        window_h = h[i - left : i + right + 1]
        window_l = l[i - left : i + right + 1]
        if h[i] == window_h.max() and (window_h.argmax() == left):
            ph[i] = True
        if l[i] == window_l.min() and (window_l.argmin() == left):
            pl[i] = True

    return pd.DataFrame({"pivot_high": ph, "pivot_low": pl}, index=df.index)


def last_swings(df: pd.DataFrame, left: int = 2, right: int = 2, count: int = 3):
    """Most recent confirmed swing highs and lows, newest first.
    Returns (highs, lows) as lists of (timestamp, price)."""
    p = pivots(df, left, right)
    highs = [(ts, df.at[ts, "high"]) for ts in df.index[p["pivot_high"].to_numpy()]]
    lows = [(ts, df.at[ts, "low"]) for ts in df.index[p["pivot_low"].to_numpy()]]
    return highs[::-1][:count], lows[::-1][:count]


def candle_shape(df: pd.DataFrame) -> dict:
    """Classify the most recent CLOSED candle."""
    if len(df) < 2:
        return {"kind": "none", "bullish": False, "bearish": False, "close_pos": 0.5}

    c = df.iloc[-1]
    p = df.iloc[-2]

    rng = max(c["high"] - c["low"], 1e-9)
    body = abs(c["close"] - c["open"])
    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]
    close_pos = (c["close"] - c["low"]) / rng  # 1.0 = closed on the high

    bull = c["close"] > c["open"]
    bear = c["close"] < c["open"]

    prev_body_low = min(p["open"], p["close"])
    prev_body_high = max(p["open"], p["close"])

    kind = "neutral"
    if bull and c["close"] > prev_body_high and c["open"] <= prev_body_low:
        kind = "bullish_engulfing"
    elif bear and c["close"] < prev_body_low and c["open"] >= prev_body_high:
        kind = "bearish_engulfing"
    elif lower_wick > body * 1.8 and close_pos > 0.6:
        kind = "bullish_pin"
    elif upper_wick > body * 1.8 and close_pos < 0.4:
        kind = "bearish_pin"
    elif body / rng > 0.65:
        kind = "bullish_marubozu" if bull else "bearish_marubozu"

    return {
        "kind": kind,
        "bullish": bull,
        "bearish": bear,
        "close_pos": float(close_pos),
        "body_ratio": float(body / rng),
    }


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Build a higher timeframe from a lower one (used by the backtester)."""
    out = df.resample(rule, label="left", closed="left", origin="start_day").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    return out.dropna()


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Highest high and lowest low of the last n COMPLETED bars.

    Shifted by one deliberately: a channel that includes the current bar can
    never be broken by it, and a backtest that forgets the shift reports
    breakouts it could not have traded.
    """
    return pd.DataFrame({
        "upper": df["high"].rolling(n).max().shift(1),
        "lower": df["low"].rolling(n).min().shift(1),
    })


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame({
        "mid": mid, "upper": upper, "lower": lower,
        # Width relative to price, so it is comparable across instruments.
        "width": (upper - lower) / mid.replace(0, pd.NA),
    })


def sr_levels(df: pd.DataFrame, atr_val: float, left: int = 3, right: int = 3,
              lookback: int = 200, tol_atr: float = 0.45,
              min_touches: int = 2) -> list[dict]:
    """Support and resistance as PRICE CLUSTERS, not single pivots.

    A lone swing point is noise; a level is a price the market has turned at
    more than once. Pivots within `tol_atr` ATR of each other are merged, and
    the count of merged pivots is the level's touch count — the thing that
    makes it worth watching, and the thing stops pile up behind.

    Returns levels newest-first with: price, touches, kind, last_index.
    """
    piv = pivots(df.tail(lookback), left=left, right=right)
    tol = max(tol_atr * float(atr_val), 1e-12)

    raw = []
    for i, row in enumerate(piv.itertuples()):
        if getattr(row, "pivot_high", False):
            raw.append((float(df["high"].tail(lookback).iloc[i]), "resistance", i))
        if getattr(row, "pivot_low", False):
            raw.append((float(df["low"].tail(lookback).iloc[i]), "support", i))

    clusters: list[dict] = []
    for price, kind, idx in raw:
        for c in clusters:
            if abs(c["price"] - price) <= tol:
                # Weighted mean keeps the level where the touches actually are.
                c["price"] = (c["price"] * c["touches"] + price) / (c["touches"] + 1)
                c["touches"] += 1
                c["last_index"] = max(c["last_index"], idx)
                c["kinds"].add(kind)
                break
        else:
            clusters.append({"price": price, "touches": 1, "last_index": idx,
                             "kinds": {kind}})

    out = [c for c in clusters if c["touches"] >= min_touches]
    for c in out:
        c["kind"] = "both" if len(c["kinds"]) > 1 else next(iter(c["kinds"]))
        c.pop("kinds")
    return sorted(out, key=lambda c: -c["last_index"])


def sweep(bar, level: float, direction: int, atr_val: float,
          min_depth_atr: float = 0.15) -> dict | None:
    """Did this bar run the stops beyond `level` and then reject?

    The pattern being detected is a liquidity grab: price trades through a
    level where resting stops sit, triggers them, and closes back on the
    original side. A bar that merely closes through the level is a breakout,
    not a sweep, and returns None.

    direction is the trade being considered: LONG sweeps below support.
    """
    depth_needed = min_depth_atr * float(atr_val)
    if direction > 0:
        pierced = float(level) - float(bar["low"])
        reclaimed = float(bar["close"]) > float(level)
    else:
        pierced = float(bar["high"]) - float(level)
        reclaimed = float(bar["close"]) < float(level)

    if pierced < depth_needed or not reclaimed:
        return None

    rng = float(bar["high"] - bar["low"]) or 1e-12
    close_pos = ((float(bar["close"]) - float(bar["low"])) / rng if direction > 0
                 else (float(bar["high"]) - float(bar["close"])) / rng)
    return {
        "depth": pierced,
        "depth_atr": pierced / float(atr_val) if atr_val else 0.0,
        "close_pos": close_pos,          # 1.0 = closed at the rejecting extreme
        "range_atr": rng / float(atr_val) if atr_val else 0.0,
        "extreme": float(bar["low"]) if direction > 0 else float(bar["high"]),
    }
