"""
Market data with swappable providers.

Yahoo blocks PythonAnywhere's shared IPs (permanent 429), so this replaces it.
Both providers below are on PythonAnywhere's free-account allowlist.

  binance  — PAXG/USDT, a token redeemable for physical gold. No signup, no key,
             trades 24/7. Tracks spot gold closely but not exactly.
  oanda    — XAU_USD straight from a real forex broker. Needs a free practice
             account and API token, but the prices are true spot gold.

Pick one in pa_config.py:   DATA_PROVIDER = "binance"   (or "oanda")

Both expose native 5m/15m/1h/4h/1d/1w candles, so nothing is resampled.
"""
from __future__ import annotations

import os
import time
from typing import Dict, Tuple

import pandas as pd
import requests

import config as C

CACHE_TTL = {"5min": 60, "15min": 180, "1h": 600, "4h": 1800,
             "1day": 3600, "1week": 7200}

_cache: Dict[Tuple[str, str, str], Tuple[float, pd.DataFrame]] = {}


class DataError(RuntimeError):
    pass


def _proxies() -> dict:
    """PythonAnywhere free accounts reach the internet through a proxy."""
    p = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    return {"http": p, "https": p} if p else {}


def _get(url: str, **kwargs):
    kwargs.setdefault("timeout", 25)
    kwargs.setdefault("proxies", _proxies())
    try:
        return requests.get(url, **kwargs)
    except requests.RequestException as exc:
        raise DataError(f"Network error: {exc}") from exc


# --------------------------------------------------------------------------- #
#  Binance — PAXG/USDT, no API key required
# --------------------------------------------------------------------------- #
BINANCE_URL = "https://api.binance.com/api/v3/klines"

BINANCE_TF = {"5min": "5m", "15min": "15m", "30min": "30m", "1h": "1h",
              "4h": "4h", "1day": "1d", "1week": "1w"}

BINANCE_SYMBOLS = {
    "XAU/USD": "PAXGUSDT",
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
}


def _fetch_binance(symbol: str, tf: str, bars: int) -> pd.DataFrame:
    pair = BINANCE_SYMBOLS.get(symbol)
    if not pair:
        raise DataError(f"{symbol} isn't available on Binance. Try DATA_PROVIDER='oanda'.")
    if tf not in BINANCE_TF:
        raise DataError(f"Unsupported timeframe {tf}.")

    resp = _get(BINANCE_URL, params={
        "symbol": pair, "interval": BINANCE_TF[tf], "limit": min(bars, 1000)})

    if resp.status_code == 451:
        raise DataError("Binance is geo-blocked from this server.")
    if resp.status_code >= 400:
        raise DataError(f"Binance returned HTTP {resp.status_code}.")

    try:
        rows = resp.json()
    except ValueError as exc:
        raise DataError("Binance sent a non-JSON response.") from exc

    if not isinstance(rows, list) or not rows:
        raise DataError(f"Binance returned no candles for {pair} {tf}.")

    df = pd.DataFrame(
        {
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
        },
        index=pd.to_datetime([r[0] for r in rows], unit="ms", utc=True),
    )
    return df.sort_index()


# --------------------------------------------------------------------------- #
#  OANDA — true XAU_USD, needs a free practice token
# --------------------------------------------------------------------------- #
OANDA_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

OANDA_TF = {"5min": "M5", "15min": "M15", "30min": "M30", "1h": "H1",
            "4h": "H4", "1day": "D", "1week": "W"}


def _fetch_oanda(symbol: str, tf: str, bars: int) -> pd.DataFrame:
    token = getattr(C, "OANDA_TOKEN", "")
    if not token:
        raise DataError("OANDA_TOKEN missing from pa_config.py.")
    if tf not in OANDA_TF:
        raise DataError(f"Unsupported timeframe {tf}.")

    host = OANDA_HOSTS.get(getattr(C, "OANDA_ENV", "practice"), OANDA_HOSTS["practice"])
    instrument = symbol.replace("/", "_")
    url = f"{host}/v3/instruments/{instrument}/candles"

    resp = _get(
        url,
        params={"granularity": OANDA_TF[tf], "count": min(bars, 5000), "price": "M"},
        headers={"Authorization": f"Bearer {token}"},
    )

    if resp.status_code == 401:
        raise DataError("OANDA rejected the token. Check OANDA_TOKEN and OANDA_ENV.")
    if resp.status_code >= 400:
        raise DataError(f"OANDA returned HTTP {resp.status_code}.")

    try:
        candles = resp.json().get("candles", [])
    except ValueError as exc:
        raise DataError("OANDA sent a non-JSON response.") from exc

    rows = [c for c in candles if c.get("complete")]
    if not rows:
        raise DataError(f"OANDA returned no completed candles for {instrument} {tf}.")

    df = pd.DataFrame(
        {
            "open": [float(c["mid"]["o"]) for c in rows],
            "high": [float(c["mid"]["h"]) for c in rows],
            "low": [float(c["mid"]["l"]) for c in rows],
            "close": [float(c["mid"]["c"]) for c in rows],
        },
        index=pd.to_datetime([c["time"] for c in rows], format="ISO8601", utc=True),
    )
    return df.sort_index()


PROVIDERS = {"binance": _fetch_binance, "oanda": _fetch_oanda}


# --------------------------------------------------------------------------- #
#  Public interface — unchanged, so nothing else in the project cares
# --------------------------------------------------------------------------- #
def fetch_ohlc(symbol: str, tf: str, bars: int = 300) -> pd.DataFrame:
    provider = getattr(C, "DATA_PROVIDER", "binance").lower()
    fetch = PROVIDERS.get(provider)
    if fetch is None:
        raise DataError(f"Unknown DATA_PROVIDER '{provider}'. Use 'binance' or 'oanda'.")

    key = (provider, symbol, tf)
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < CACHE_TTL.get(tf, 180):
        return hit[1]

    df = fetch(symbol, tf, bars).tail(bars)
    if len(df) < 60:
        raise DataError(f"Only {len(df)} candles for {symbol} {tf}; need 60+.")

    _cache[key] = (now, df)
    return df


def clear_cache() -> None:
    _cache.clear()
