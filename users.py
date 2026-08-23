"""
PER-USER SETTINGS THAT SURVIVE A RESTART
========================================

Everything the bot remembers about a person lives in one JSON file keyed by
Telegram user id: language, chosen strategy, confidence floor, risk defaults,
and the whole risk-management envelope with its running daily counters.

Writes are atomic — a temp file renamed over the target — because the web
worker can be killed mid-request on a free host and a half-written settings
file would lose every user's configuration at once.

Daily counters (trades taken, realised P/L, drawdown) roll over on the WIB
calendar day rather than UTC, since that is the day the user actually lives
in and the one the limits are meant to describe.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import config as C

_lock = threading.Lock()

WIB = timezone(timedelta(hours=7))

DEFAULTS: dict[str, Any] = {
    "language": "en",              # "en" | "id"
    "strategy": "ronin",           # key into strategies.REGISTRY
    "min_confidence": 0,           # /setconf, 0 = no floor
    "risk_amount": None,           # {"value": 50.0, "currency": "USD"} or None
    "management": None,            # see management_defaults()
    "day": None,                   # WIB date string the counters belong to
    "day_trades": 0,
    "day_pl_usd": 0.0,
    "day_peak_usd": 0.0,           # high-water mark for drawdown
    "day_trough_usd": 0.0,         # worst point today, for max drawdown
}


def today_wib() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d")


def _path() -> str:
    return C.USERS_FILE


def _load_all() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_all(data: dict) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)      # atomic; a killed worker cannot truncate it
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get(user_id: int) -> dict:
    """Settings for one user, defaults filled in, daily counters rolled over."""
    with _lock:
        allu = _load_all()
        u = dict(DEFAULTS)
        u.update(allu.get(str(user_id)) or {})

        today = today_wib()
        if u.get("day") != today:
            u["day"] = today
            u["day_trades"] = 0
            u["day_pl_usd"] = 0.0
            u["day_peak_usd"] = 0.0
            u["day_trough_usd"] = 0.0
        return u


def update(user_id: int, **changes) -> dict:
    with _lock:
        allu = _load_all()
        cur = dict(DEFAULTS)
        cur.update(allu.get(str(user_id)) or {})

        today = today_wib()
        if cur.get("day") != today:
            cur.update(day=today, day_trades=0, day_pl_usd=0.0,
                       day_peak_usd=0.0, day_trough_usd=0.0)

        cur.update(changes)
        allu[str(user_id)] = cur
        _save_all(allu)
        return cur


def all_users() -> dict[int, dict]:
    with _lock:
        out = {}
        for k, v in _load_all().items():
            try:
                u = dict(DEFAULTS)
                u.update(v or {})
                out[int(k)] = u
            except (TypeError, ValueError):
                continue
        return out


# --------------------------------------------------------------------------- #
#  Risk and money management
# --------------------------------------------------------------------------- #
def management_defaults(balance_usd: float, risk_pct: float, dd_pct: float,
                        max_trades: int, target_pct: float) -> dict:
    return {
        "enabled": True,
        "balance_usd": float(balance_usd),
        "start_balance_usd": float(balance_usd),
        "risk_pct": float(risk_pct),
        "daily_dd_pct": float(dd_pct),
        "max_daily_trades": int(max_trades),
        "profit_target_pct": float(target_pct),
        "currency": "USD",
        "since": today_wib(),
    }


def management_on(user_id: int, mgmt: dict) -> dict:
    return update(user_id, management=mgmt, day_trades=0, day_pl_usd=0.0,
                  day_peak_usd=0.0, day=today_wib())


def management_off(user_id: int) -> dict:
    """Turning it off DELETES the envelope, it does not park it.

    A stale balance and a half-spent daily allowance are worse than no
    settings at all — they would silently size the next trade from a number
    the user has forgotten agreeing to. Starting again means /management on
    with fresh figures, which is the honest default.
    """
    return update(user_id, management=None, day_trades=0, day_pl_usd=0.0,
                  day_peak_usd=0.0)


def risk_per_trade_usd(u: dict) -> float | None:
    """What one trade is allowed to lose, from management if it is on."""
    m = u.get("management") or {}
    if m.get("enabled"):
        return m["balance_usd"] * m["risk_pct"] / 100.0
    return None


def drawdown_now(u: dict) -> float:
    """How far below today's high-water mark the account currently sits."""
    return min(0.0, float(u.get("day_pl_usd", 0.0))
               - max(0.0, float(u.get("day_peak_usd", 0.0))))


def max_drawdown_today(u: dict) -> float:
    """The worst it got today, not just where it is now — the number that
    tells you whether the day was survivable."""
    return min(0.0, float(u.get("day_trough_usd", 0.0)))
