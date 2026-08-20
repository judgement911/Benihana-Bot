"""
Which data sources can this server actually reach?

Run:  python3 check_sources.py

Tests each candidate and prints a verdict. Doesn't need any API keys —
a 401 still proves the domain is reachable, which is what we're checking.
"""
from __future__ import annotations

import os

import requests


def proxies() -> dict:
    p = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    return {"http": p, "https": p} if p else {}


TESTS = [
    ("Binance  (PAXG gold, no key)",
     "https://api.binance.com/api/v3/klines",
     {"symbol": "PAXGUSDT", "interval": "15m", "limit": 3}),

    ("OANDA    (true XAUUSD, needs token)",
     "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles",
     {"granularity": "M15", "count": "3"}),

    ("Twelve Data (needs key)",
     "https://api.twelvedata.com/time_series",
     {"symbol": "XAU/USD", "interval": "15min", "outputsize": "3"}),

    ("Yahoo    (known blocked)",
     "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
     {"interval": "15m", "range": "5d"}),
]


def main() -> None:
    p = proxies()
    print(f"\nProxy in use: {p.get('https') or 'none'}\n")
    print(f"{'source':<38}{'status':<10}verdict")
    print("-" * 74)

    for name, url, params in TESTS:
        try:
            r = requests.get(url, params=params, timeout=20, proxies=p)
            code = r.status_code

            if code == 200:
                verdict = "WORKS"
            elif code == 401:
                verdict = "reachable — just needs a valid token"
            elif code == 403:
                verdict = "blocked (not on the allowlist?)"
            elif code == 429:
                verdict = "rate limited / IP refused"
            else:
                verdict = r.text[:60].replace("\n", " ")

            print(f"{name:<38}{code:<10}{verdict}")

        except requests.RequestException as exc:
            msg = str(exc)
            hint = "not allowlisted" if "403" in msg or "proxy" in msg.lower() else msg[:50]
            print(f"{name:<38}{'-':<10}{hint}")

    print("\nAny source marked WORKS (or 'needs a valid token') can be used.")
    print("Set DATA_PROVIDER in pa_config.py to 'binance' or 'oanda'.\n")


if __name__ == "__main__":
    main()
