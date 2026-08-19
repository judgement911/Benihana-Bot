"""
Market data from Yahoo Finance.

Two reasons this replaces Twelve Data on PythonAnywhere:
  1. `.yahoo.com` is on PythonAnywhere's free-account allowlist. Twelve Data is not.
  2. No API key at all, so there's one less secret to manage.

Yahoo has no native 4h candles, so 4h is resampled from 1h here.
"""
from __future__ import annotations

import os
import time
from typing import Dict, Tuple

import pandas as pd
import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo rejects the default python-requests user agent often enough to matter.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# our timeframe -> (yahoo interval, yahoo range, resample rule or None)
TF_MAP = {
    "5min": ("5m", "1mo", None),
    "15min": ("15m", "1mo", None),
    "30min": ("30m", "2mo", None),
    "1h": ("1h", "6mo", None),
    "4h": ("1h", "2y", "4h"),      # Yahoo has no 4h; build it from 1h
    "1day": ("1d", "5y", None),
    "1week": ("1wk", "10y", None),
}

SYMBOLS = {
    "XAU/USD": ["XAUUSD=X", "GC=F"],   # spot first, gold futures as fallback
    "XAG/USD": ["XAGUSD=X", "SI=F"],
    "EUR/USD": ["EURUSD=X"],
    "GBP/USD": ["GBPUSD=X"],
    "USD/JPY": ["JPY=X"],
    "BTC/USD": ["BTC-USD"],
}

CACHE_TTL = {"5min": 60, "15min": 180, "1h": 600, "4h": 1800,
             "1day": 3600, "1week": 7200}

_cache: Dict[Tuple[str, str], Tuple[float, pd.DataFrame]] = {}


class DataError(RuntimeError):
    pass


def _proxies() -> dict:
    """PythonAnywhere free accounts reach the internet through a proxy.
    It's normally in the environment already; this makes it explicit."""
    p = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    return {"http": p, "https": p} if p else {}


def _parse_chart(payload: dict) -> pd.DataFrame:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise DataError(f"Yahoo error: {chart['error'].get('description', 'unknown')}")

    results = chart.get("result")
    if not results:
        raise DataError("Yahoo returned no result block.")

    res = results[0]
    stamps = res.get("timestamp")
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    if not stamps or not quote:
        raise DataError("Yahoo returned an empty candle series.")

    df = pd.DataFrame(
        {
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
        },
        index=pd.to_datetime(stamps, unit="s", utc=True),
    )
    return df.dropna().sort_index()


def _fetch_one(yahoo_symbol: str, interval: str, rng: str) -> pd.DataFrame:
    url = CHART_URL.format(symbol=yahoo_symbol)
    params = {"interval": interval, "range": rng, "includePrePost": "false"}

    try:
        resp = requests.get(
            url, params=params, headers=HEADERS, timeout=25, proxies=_proxies()
        )
    except requests.RequestException as exc:
        raise DataError(f"Could not reach Yahoo Finance: {exc}") from exc

    if resp.status_code == 429:
        raise DataError("Yahoo is rate limiting. Wait a minute and retry.")
    if resp.status_code >= 400:
        raise DataError(f"Yahoo returned HTTP {resp.status_code} for {yahoo_symbol}.")

    try:
        return _parse_chart(resp.json())
    except ValueError as exc:
        raise DataError("Yahoo sent a non-JSON response.") from exc


def fetch_ohlc(symbol: str, tf: str, bars: int = 300) -> pd.DataFrame:
    """symbol uses our internal naming ('XAU/USD'); tf is one of TF_MAP."""
    key = (symbol, tf)
    now = time.time()

    hit = _cache.get(key)
    if hit and (now - hit[0]) < CACHE_TTL.get(tf, 180):
        return hit[1]

    if tf not in TF_MAP:
        raise DataError(f"Unsupported timeframe {tf}.")
    interval, rng, resample_rule = TF_MAP[tf]

    candidates = SYMBOLS.get(symbol, [symbol])
    last_error = None
    df = None

    for candidate in candidates:
        try:
            df = _fetch_one(candidate, interval, rng)
            if len(df) >= 60:
                break
            last_error = DataError(f"{candidate} only returned {len(df)} candles.")
            df = None
        except DataError as exc:
            last_error = exc
            df = None

    if df is None:
        raise last_error or DataError(f"No data available for {symbol} {tf}.")

    if resample_rule:
        df = (
            df.resample(resample_rule, label="left", closed="left", origin="start_day")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )

    df = df.tail(bars)
    if len(df) < 60:
        raise DataError(f"Only {len(df)} candles for {symbol} {tf}; need 60+.")

    _cache[key] = (now, df)
    return df


def clear_cache() -> None:
    _cache.clear()
