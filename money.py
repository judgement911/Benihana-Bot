"""
RISK AMOUNTS, CURRENCIES AND LOT SIZES
======================================

Three jobs, all of which the bot used to fudge.

Parsing:   "20$", "100 USD", "300k IDR", "17000000 IDR", "1.5m idr" all mean
           a number and a currency. Anything unparseable is rejected rather
           than guessed at.

Conversion: IDR is converted with a rate fetched from the same market data
           provider the signals use, cached for an hour. There is deliberately
           no fallback constant. A stale hardcoded rate would silently
           mis-size every position, and being told the rate is unavailable is
           strictly better than being lied to about the risk.

Lots:      brokers accept discrete lot steps, so 0.0457 is not an order. Sizes
           round DOWN to the step — rounding up would risk more than asked —
           and if the honest size falls under the broker minimum the caller is
           told, because trading the minimum instead means taking more risk
           than requested.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import config as C

USD, IDR = "USD", "IDR"
SUPPORTED = (USD, IDR)

_rate_cache: dict[str, tuple[float, float]] = {}   # ccy -> (fetched_at, rate)


class MoneyError(ValueError):
    """Something the user typed could not be read as an amount."""


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #
_SUFFIX = {"k": 1_000.0, "rb": 1_000.0, "ribu": 1_000.0,
           "m": 1_000_000.0, "jt": 1_000_000.0, "juta": 1_000_000.0}

_AMOUNT = re.compile(
    r"^(?P<pre>\$|rp)?\s*"
    r"(?P<num>\d+(?:[.,]\d+)*)\s*"
    r"(?P<suffix>k|rb|ribu|m|jt|juta)?\s*"
    r"(?P<post>\$|usd|idr|rp)?$",
    re.IGNORECASE,
)


def parse_amount(text: str) -> tuple[float, str]:
    """'300k IDR' -> (300000.0, 'IDR'). Raises MoneyError if unreadable."""
    raw = (text or "").strip().replace("_", "")
    m = _AMOUNT.match(raw)
    if not m:
        raise MoneyError(text)

    num = m.group("num")
    # 1.000.000 and 1,000,000 are thousands separators; 1.5 is a decimal.
    # More than one separator can only be grouping. A single separator with
    # exactly three trailing digits is ambiguous, and in both the Indonesian
    # and English conventions it means thousands far more often than it means
    # a fractional currency unit, so read it that way.
    seps = len(re.findall(r"[.,]", num))
    if seps > 1 or re.fullmatch(r"\d+[.,]\d{3}", num):
        value = float(re.sub(r"[.,]", "", num))
    else:
        value = float(num.replace(",", "."))

    if m.group("suffix"):
        value *= _SUFFIX[m.group("suffix").lower()]

    tag = (m.group("post") or m.group("pre") or "").lower()
    if tag in ("$", "usd"):
        ccy = USD
    elif tag in ("idr", "rp"):
        ccy = IDR
    else:
        # No currency given. A bare number that large is not dollars.
        ccy = IDR if value >= 100_000 else USD

    if value <= 0:
        raise MoneyError(text)
    return value, ccy


def parse_risk_args(parts: list[str]) -> tuple[Optional[tuple[float, str]], list[str]]:
    """Pull a 'risk <amount>' clause out of a command's words.

    Returns ((value, currency) or None, the remaining words). The amount may
    be split across tokens — 'risk 300k IDR' arrives as two — so up to three
    following words are tried longest-first.
    """
    lowered = [p.lower() for p in parts]
    for i, word in enumerate(lowered):
        if word not in ("risk", "resiko", "risiko"):
            continue
        for span in (3, 2, 1):
            chunk = " ".join(parts[i + 1:i + 1 + span]).strip()
            if not chunk:
                continue
            try:
                amount = parse_amount(chunk)
            except MoneyError:
                continue
            return amount, parts[:i] + parts[i + 1 + span:]
        raise MoneyError(" ".join(parts[i + 1:i + 4]) or "risk")
    return None, parts


# --------------------------------------------------------------------------- #
#  Conversion
# --------------------------------------------------------------------------- #
def usd_rate(currency: str, fetch=None) -> Optional[float]:
    """How many USD one unit of `currency` is worth. None if unknown.

    No hardcoded fallback on purpose: sizing a position from a rate that was
    true a year ago is worse than declining to size it.
    """
    currency = currency.upper()
    if currency == USD:
        return 1.0
    if currency not in SUPPORTED:
        return None

    hit = _rate_cache.get(currency)
    if hit and (time.time() - hit[0]) < C.FX_RATE_TTL:
        return hit[1]

    if fetch is None:
        from data import fetch_ohlc as fetch          # noqa: PLC0415
    try:
        df = fetch(f"USD/{currency}", "1day", 5)
        per_usd = float(df["close"].iloc[-1])         # e.g. 16,200 IDR per USD
    except Exception:
        return hit[1] if hit else None                # stale beats nothing
    if per_usd <= 0:
        return None
    rate = 1.0 / per_usd
    _rate_cache[currency] = (time.time(), rate)
    return rate


def to_usd(value: float, currency: str, fetch=None) -> Optional[float]:
    r = usd_rate(currency, fetch)
    return None if r is None else value * r


def fmt(value: float, currency: str) -> str:
    if currency == IDR:
        if value >= 1_000_000:
            return f"{value / 1_000_000:g}M IDR"
        if value >= 1_000:
            return f"{value / 1_000:g}K IDR"
        return f"{value:,.0f} IDR"
    return f"${value:,.0f}" if abs(value) >= 10 else f"${value:,.2f}"


# --------------------------------------------------------------------------- #
#  Lot sizes
# --------------------------------------------------------------------------- #
def round_lots(raw: float) -> tuple[Optional[float], bool]:
    """Round a computed size to something a broker will accept.

    Returns (lots, below_minimum). Rounds DOWN so the trade never risks more
    than asked. below_minimum is True when the honest size is smaller than the
    broker's smallest lot — the caller must say so rather than quietly
    substituting the minimum, which would risk more than requested.
    """
    if raw is None or raw <= 0:
        return None, False
    step = C.LOT_STEP
    lots = int(raw / step) * step
    lots = round(lots, 3)
    if lots < C.LOT_MIN:
        return None, True
    return lots, False


def fmt_lots(lots: Optional[float]) -> str:
    if lots is None:
        return "—"
    text = f"{lots:.3f}".rstrip("0")
    if text.endswith("."):
        text += "00"
    if len(text.split(".")[1]) < 2:
        text += "0"
    return text
