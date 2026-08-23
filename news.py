"""
Event risk without a news feed.
================================

The honest position first: this bot has no news provider. A headline feed
that is both free and reachable from a PythonAnywhere free account is not
something I could find and verify, and a calendar the bot cannot fetch is
worse than no calendar at all — it would go stale silently and the user
would trust it anyway.

So this module claims only what a clock can prove. Two kinds of event:

  RULE-BASED   Non-farm payrolls lands on the first Friday of the month at
               08:30 New York time. That is a calendar rule, not a forecast,
               so it can be computed years ahead with no network. New York
               time is used rather than a fixed UTC offset because the US
               moves its clocks and Indonesia does not — hardcoding 13:30
               UTC would be wrong for eight months of the year.

  USER-ADDED   Everything else — CPI, FOMC, GDP — moves around and is
               published by the agency, not derivable. Those live in
               events.json, which the user fills from any calendar site.
               The bot never invents one.

What this deliberately does NOT do is guess. There is no hardcoded table of
FOMC dates in here, because I would be recalling them rather than reading
them, and a confidently wrong blackout window is worse than none.

Times shown to the user are UTC+7, matching the rest of the bot.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _new_york():
    """The US moves its clocks, so payrolls is 13:30 UTC in winter and 12:30
    in summer. That needs a real zone.

    If the host has no tz database this must not take the bot down with it:
    a missing calendar is a lost feature, an ImportError is a dead bot. The
    fallback is US Eastern Standard, which is correct in winter and a full
    hour early once daylight saving starts. TZ_EXACT records which one is in
    use, so a screen can say so rather than quietly mislead.
    """
    try:
        return ZoneInfo("America/New_York"), True
    except (ZoneInfoNotFoundError, KeyError, OSError):
        return timezone(timedelta(hours=-5)), False


NY, TZ_EXACT = _new_york()
WIB = timezone(timedelta(hours=7))

EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "events.json")

# Minutes either side of a high-impact release where spreads widen, stops get
# run and the strategies' assumptions stop holding.
BLACKOUT_BEFORE = 30
BLACKOUT_AFTER = 30

HIGH, MEDIUM = "high", "medium"


@dataclass(frozen=True)
class Event:
    name: str
    when_utc: datetime
    impact: str
    source: str          # "rule" or "user"
    note: str = ""
    # Rule-based events carry an i18n key so the renderer can translate them.
    # User events keep whatever text the user typed — translating someone's
    # own calendar entry would be presumptuous and usually wrong.
    key: str = ""

    @property
    def wib(self) -> datetime:
        return self.when_utc.astimezone(WIB)


# --------------------------------------------------------------------------- #
#  Rule-based events
# --------------------------------------------------------------------------- #
def _first_friday(year: int, month: int) -> datetime:
    """08:30 New York on the first Friday, returned in UTC."""
    d = datetime(year, month, 1, 8, 30, tzinfo=NY)
    while d.weekday() != 4:                      # 4 = Friday
        d += timedelta(days=1)
    return d.astimezone(timezone.utc)


def next_nfp(now_utc: Optional[datetime] = None) -> Event:
    """The next non-farm payrolls release.

    Caveat worth stating in the output rather than hiding here: when the
    first Friday falls on a public holiday the BLS shifts the release. That
    is rare, and this returns the unshifted date, so a user seeing a holiday
    should check.
    """
    now = now_utc or datetime.now(timezone.utc)
    y, m = now.year, now.month
    when = _first_friday(y, m)
    if when <= now:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        when = _first_friday(y, m)
    return Event("Non-farm payrolls", when, HIGH, "rule",
                 "first Friday, 08:30 New York", key="nfp")


def _friday_close(now_utc: datetime) -> Event:
    """17:00 New York on Friday — the week's last liquidity."""
    d = now_utc.astimezone(NY)
    ahead = (4 - d.weekday()) % 7
    close = (d + timedelta(days=ahead)).replace(hour=17, minute=0, second=0,
                                                microsecond=0)
    if close <= d:
        close += timedelta(days=7)
    return Event("Weekly close", close.astimezone(timezone.utc), MEDIUM,
                 "rule", "spreads widen, then the weekend gap",
                 key="weekly_close")


# --------------------------------------------------------------------------- #
#  User-supplied events
# --------------------------------------------------------------------------- #
def _load_user_events() -> list[Event]:
    """events.json, if the user made one. A malformed file is ignored rather
    than raised — a broken calendar must not take the bot down."""
    try:
        with open(EVENTS_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []

    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            when = datetime.fromisoformat(str(item["utc"]))
        except (KeyError, TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        name = str(item.get("name") or "Event")[:60]
        impact = HIGH if str(item.get("impact", "high")).lower() == "high" \
            else MEDIUM
        out.append(Event(name, when.astimezone(timezone.utc), impact, "user",
                         str(item.get("note") or "")[:80]))
    return out


# --------------------------------------------------------------------------- #
#  Public
# --------------------------------------------------------------------------- #
def upcoming(now_utc: Optional[datetime] = None, days: int = 14) -> list[Event]:
    now = now_utc or datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    events = [next_nfp(now), _friday_close(now)] + _load_user_events()
    return sorted((e for e in events if now <= e.when_utc <= horizon),
                  key=lambda e: e.when_utc)


def _nfp_around(now: datetime) -> list[Event]:
    """Last month's, this month's and next month's release.

    next_nfp alone is not enough to answer "are we in a window right now":
    the moment the release happens it rolls forward to the following month,
    so the event actually in progress stops being considered and the blackout
    reads clear exactly when it matters most.
    """
    out = []
    for shift in (-1, 0, 1):
        m = now.month + shift
        y = now.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        out.append(Event("Non-farm payrolls", _first_friday(y, m), HIGH,
                         "rule", "first Friday, 08:30 New York", key="nfp"))
    return out


def blackout(now_utc: Optional[datetime] = None) -> Optional[Event]:
    """The high-impact event we are currently inside the window of, if any."""
    now = now_utc or datetime.now(timezone.utc)
    for e in _nfp_around(now) + _load_user_events():
        if e.impact != HIGH:
            continue
        if (e.when_utc - timedelta(minutes=BLACKOUT_BEFORE) <= now
                <= e.when_utc + timedelta(minutes=BLACKOUT_AFTER)):
            return e
    return None


def delta_parts(now: datetime, when: datetime) -> tuple:
    """(key, fields) for the renderer to translate. Returning formatted
    English here is what leaked "in 5d 6h" into the Indonesian screen."""
    secs = (when - now).total_seconds()
    if secs < 0:
        return "dt_now", {}
    d, rem = divmod(int(secs), 86400)
    h, m = divmod(rem // 60, 60)
    if d:
        return "dt_in_dh", {"d": d, "h": h}
    if h:
        return "dt_in_hm", {"h": h, "m": m}
    return "dt_in_m", {"m": m}
