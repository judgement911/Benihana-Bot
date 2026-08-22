"""
WHICH SESSION IS OPEN, AND HOW WILD IS IT
=========================================

Session is a clock fact: the four centres have fixed hours and the only real
decision is what to call an overlap. Rather than print "London/NY" and leave
the reader to work out which one matters, the session with the deepest
liquidity wins — New York over London over Asia over Sydney. That is the
book you are actually trading against.

Volatility is a market fact, not a mood. It is ATR against its own recent
median on the entry timeframe: 1.0 means today looks like the last few weeks,
1.6 means the range has half again as much room in it. Nothing here is
assigned, guessed, or randomised — if the ratio cannot be computed the caller
gets None and prints nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import config as C

WIB = timezone(timedelta(hours=7))

# (key, label, emoji, open_hour_utc, close_hour_utc). Windows wrap midnight
# where close < open. Priority is list order: first match wins, so the deepest
# book is the one named during an overlap.
SESSIONS = (
    ("ny",     "NY Session",     "🗽", 12, 21),
    ("london", "London Session", "🌃",  7, 16),
    ("asia",   "Asia Session",   "⛩️", 23,  8),
    ("sydney", "Sydney Session", "🦘", 21,  6),
)


def _in_window(hour: int, start: int, end: int) -> bool:
    return start <= hour < end if start < end else (hour >= start or hour < end)


def current_session(now_utc: Optional[datetime] = None) -> Optional[dict]:
    """The dominant open session, or None when every centre is shut."""
    now_utc = now_utc or datetime.now(timezone.utc)
    hour = now_utc.hour
    if now_utc.weekday() >= 5:          # Saturday, Sunday: FX is closed
        return None
    for key, label, emoji, start, end in SESSIONS:
        if _in_window(hour, start, end):
            return {"key": key, "label": label, "emoji": emoji}
    return None


def classify_volatility(atr_ratio: Optional[float]) -> Optional[dict]:
    """ATR against its own median, bucketed. None in, None out."""
    if atr_ratio is None:
        return None
    r = float(atr_ratio)
    if r >= C.VOL_HIGH_RATIO:
        return {"key": "high", "label": "High Volatile", "emoji": "🔴", "ratio": r}
    if r >= C.VOL_LOW_RATIO:
        return {"key": "medium", "label": "Medium Volatile", "emoji": "🟠", "ratio": r}
    return {"key": "low", "label": "Low Volatile", "emoji": "🟢", "ratio": r}


def to_wib(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB)


def stamp(dt: datetime) -> str:
    """Every clock the user sees. Labelled UTC+7, never 'WIB'."""
    return to_wib(dt).strftime("%H:%M UTC+7")
