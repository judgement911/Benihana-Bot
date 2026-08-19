"""Market data. Twelve Data free tier: 8 requests/min, 800/day.
One /signal call = 3 requests, so ~260 signals a day. Caching stretches that."""
from __future__ import annotations

import time
from typing import Dict, Tuple

import pandas as pd
import requests

from config import TWELVEDATA_API_KEY

BASE_URL = "https://api.twelvedata.com/time_series"

# How long a cached candle set stays fresh, in seconds. Roughly a third of the
# bar duration, so you never trade a stale picture.
CACHE_TTL = {
    "5min": 60,
    "15min": 180,
    "30min": 300,
    "1h": 600,
    "2h": 900,
    "4h": 1800,
    "1day": 3600,
    "1week": 7200,
}

_cache: Dict[Tuple[str, str], Tuple[float, pd.DataFrame]] = {}


class DataError(RuntimeError):
    pass


def fetch_ohlc(symbol: str, interval: str, bars: int = 300) -> pd.DataFrame:
    """Returns a UTC-indexed OHLC frame, oldest bar first."""
    key = (symbol, interval)
    now = time.time()

    hit = _cache.get(key)
    if hit and (now - hit[0]) < CACHE_TTL.get(interval, 180):
        return hit[1]

    if not TWELVEDATA_API_KEY:
        raise DataError("TWELVEDATA_API_KEY is not set — check your .env file.")

    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": bars,
        "apikey": TWELVEDATA_API_KEY,
        "timezone": "UTC",
        "format": "JSON",
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=20)
    except requests.RequestException as exc:
        raise DataError(f"Network error reaching Twelve Data: {exc}") from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        raise DataError(f"Bad response from Twelve Data (HTTP {resp.status_code}).") from exc

    if isinstance(payload, dict) and payload.get("status") == "error":
        code = payload.get("code")
        msg = payload.get("message", "unknown error")
        if code == 429:
            raise DataError("Rate limit hit (8 req/min on free tier). Wait a minute.")
        raise DataError(f"Twelve Data error {code}: {msg}")

    values = payload.get("values") if isinstance(payload, dict) else None
    if not values:
        raise DataError(f"No candles returned for {symbol} {interval}.")

    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[["open", "high", "low", "close"]].dropna()
    if len(df) < 60:
        raise DataError(f"Only {len(df)} candles for {symbol} {interval}; need 60+.")

    _cache[key] = (now, df)
    return df


def load_csv(path: str) -> pd.DataFrame:
    """For backtesting on broker-exported history.
    Expected columns: datetime/time/date, open, high, low, close."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    time_col = next(
        (c for c in ("datetime", "date", "time", "timestamp") if c in df.columns), None
    )
    if time_col is None:
        raise DataError(f"No datetime column found in {path}. Columns: {list(df.columns)}")

    df["datetime"] = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()

    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise DataError(f"Missing columns in {path}: {missing}")

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df[["open", "high", "low", "close"]].dropna()


def clear_cache() -> None:
    _cache.clear()
