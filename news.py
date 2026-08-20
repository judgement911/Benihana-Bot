"""
THE ECONOMIC CALENDAR
=====================

config.NEWS_WARNING_HOURS_UTC warns you by the clock: "US data often lands
this hour." Useful, and completely blind — it fires on a quiet Tuesday and
stays silent for an unscheduled ECB press conference.

This reads the actual Forex Factory calendar and filters it to the
currencies that move the instrument you asked about. Gold answers to USD.
EUR/USD answers to both its legs. GER40 answers to EUR.

Times are shown in your timezone, set by NEWS_TZ_OFFSET / NEWS_TZ_LABEL in
config (default UTC+7, WIB), because a release time you have to convert in
your head is a release time you will get wrong.

A NOTE ON PYTHONANYWHERE FREE ACCOUNTS: outbound HTTP is restricted to a
whitelist, and the Forex Factory feed is not on it. /news will report that
plainly rather than hanging. Twelve Data is whitelisted, which is why price
data works and this may not.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import requests

import config as C
import instruments as I

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

IMPACT_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡", "holiday": "⚪"}
IMPACT_RANK = {"high": 3, "medium": 2, "low": 1, "holiday": 0}

# Which currencies actually move an instrument.
INDEX_CCY = {"us30": "USD", "us500": "USD", "ustec": "USD", "ger40": "EUR",
             "uk100": "GBP", "jp225": "JPY", "fra40": "EUR", "aus200": "AUD"}

_cache: tuple[float, list] | None = None


class NewsError(RuntimeError):
    pass


def _proxies() -> dict:
    p = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    return {"http": p, "https": p} if p else {}


def currencies_for(inst: I.Instrument) -> set[str]:
    if inst.asset_class == I.FX:
        return {inst.base, inst.quote}
    if inst.asset_class == I.INDEX:
        return {INDEX_CCY.get(inst.key, "USD")}
    return {"USD"}          # metals and energy are priced in dollars


def fetch(force: bool = False) -> list[dict]:
    """This week's calendar. Cached, because it changes about never."""
    global _cache
    if _cache and not force and (time.time() - _cache[0]) < C.NEWS_CACHE_TTL:
        return _cache[1]

    try:
        resp = requests.get(FEED, timeout=15, proxies=_proxies(),
                            headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as exc:
        raise NewsError(
            f"Could not reach Forex Factory ({type(exc).__name__}).\n\n"
            "On a PythonAnywhere free account this is expected: outbound "
            "traffic is limited to a whitelist and this feed is not on it. "
            "Price data still works because Twelve Data is whitelisted."
        ) from exc

    if resp.status_code != 200:
        raise NewsError(f"Forex Factory returned HTTP {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError as exc:
        raise NewsError("Forex Factory sent something that is not JSON.") from exc
    if not isinstance(data, list):
        raise NewsError("Unexpected calendar format.")

    _cache = (time.time(), data)
    return data


def _parse_when(raw: str):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local(dt: datetime) -> str:
    """Render in the user's timezone, the way their broker's clock reads."""
    shifted = dt.astimezone(timezone(timedelta(hours=C.NEWS_TZ_OFFSET)))
    stamp = shifted.strftime("%-d %b %Y at %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    return f"{stamp} ({C.NEWS_TZ_LABEL})"


def upcoming(inst: I.Instrument, days: int = 7, min_impact: str = "medium",
             now: datetime = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    want = currencies_for(inst)
    floor = IMPACT_RANK.get(min_impact, 2)

    out = []
    for row in fetch():
        ccy = (row.get("country") or "").upper()
        if ccy not in want:
            continue
        impact = (row.get("impact") or "").lower()
        if IMPACT_RANK.get(impact, 0) < floor:
            continue
        when = _parse_when(row.get("date") or "")
        if not when or not (now <= when <= horizon):
            continue
        out.append({"title": row.get("title") or "Unnamed release",
                    "currency": ccy, "impact": impact, "when": when,
                    "forecast": row.get("forecast") or "",
                    "previous": row.get("previous") or ""})
    return sorted(out, key=lambda r: r["when"])


def format_news(inst: I.Instrument, days: int = 7, limit: int = 8,
                now: datetime = None) -> str:
    head = f"<b>{inst.display} — ECONOMIC CALENDAR</b>\n"
    head += f"<i>{' · '.join(sorted(currencies_for(inst)))} · next {days} days</i>\n\n"
    try:
        rows = upcoming(inst, days=days, now=now)
    except NewsError as exc:
        return head + f"⚠️ {exc}"

    if not rows:
        return head + ("Nothing medium or high impact scheduled.\n\n"
                       "<i>A clear calendar is not a promise of a quiet tape — "
                       "unscheduled headlines do not appear here.</i>")

    body = ""
    for r in rows[:limit]:
        icon = IMPACT_ICON.get(r["impact"], "⚪")
        body += f"{icon} <b>{r['title']}</b> · {r['currency']}\n"
        body += f"    {local(r['when'])}\n"
        if r["forecast"] or r["previous"]:
            body += (f"    <i>forecast {r['forecast'] or '—'} · "
                     f"previous {r['previous'] or '—'}</i>\n")
    if len(rows) > limit:
        body += f"\n<i>+{len(rows) - limit} more this week.</i>\n"

    nxt = rows[0]
    if nxt["impact"] == "high":
        mins = (nxt["when"] - (now or datetime.now(timezone.utc))).total_seconds() / 60
        if mins < 120:
            body += (f"\n⚠️ <b>{nxt['title']} in {mins:.0f} minutes.</b> "
                     "Spreads widen and stops get run either side of it.\n")
    return head + body
