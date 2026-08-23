"""
Who is allowed to use the bot, and until when.
===============================================

The bot already had a gate: a fixed list of Telegram user IDs from the
environment. A subscription is that list with an expiry date attached, so
this module replaces the set with a store and keeps the same question —
`active(user_id)` — as the only thing callers ask.

Three rules that matter more than the code:

  THE OWNER NEVER EXPIRES. The person running the bot is not a customer.
  Their access does not depend on a file that could be deleted, corrupted
  or mis-edited, so it is resolved from configuration and checked first.

  EXPIRY IS CHECKED, NOT SCHEDULED. There is no job that sweeps the store
  and removes lapsed rows. A row simply stops counting the moment its date
  passes, which means a missed cron, a sleeping worker or a restarted
  process can never accidentally extend someone's access.

  GRANTS ARE ADDITIVE AND AUDITED. Extending an active subscription adds to
  the time remaining rather than overwriting it, so renewing early does not
  cost the subscriber days. Every grant records who issued it and when.

WHAT THIS MODULE DOES NOT DO is take money. It is the access half only.
Collecting payment — Telegram Stars, a transfer, anything — happens
elsewhere and ends in a call to grant(). Keeping the two apart means the
payment method can change without touching the thing that guards the bot.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import config as C

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "subscriptions.json")

FREE_TRIAL_DAYS = 0          # 0 = no automatic trial; grant() is the only door


# --------------------------------------------------------------------------- #
#  Storage
# --------------------------------------------------------------------------- #
def _load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    d = os.path.dirname(os.path.abspath(STORE)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, STORE)               # atomic; never a half file
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
#  Owner
# --------------------------------------------------------------------------- #
def owner_ids() -> set:
    """Accounts that are never billed and never expire.

    OWNER_IDS if set; otherwise the legacy allowlist, so an existing
    deployment does not lock its own operator out the moment this ships.
    """
    return set(getattr(C, "OWNER_IDS", None) or C.ALLOWED_USER_IDS or set())


def is_owner(user_id: int) -> bool:
    return int(user_id) in owner_ids()


# --------------------------------------------------------------------------- #
#  Queries
# --------------------------------------------------------------------------- #
def _parse(ts):
    try:
        d = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def record(user_id: int) -> dict:
    return _load().get(str(int(user_id))) or {}


def expires_at(user_id: int):
    return _parse(record(user_id).get("until"))


def active(user_id: int, now=None) -> bool:
    """The only question the rest of the bot needs answered."""
    if is_owner(user_id):
        return True
    until = expires_at(user_id)
    return bool(until and until > (now or datetime.now(timezone.utc)))


def days_left(user_id: int, now=None) -> float:
    if is_owner(user_id):
        return float("inf")
    until = expires_at(user_id)
    if not until:
        return 0.0
    secs = (until - (now or datetime.now(timezone.utc))).total_seconds()
    return max(0.0, secs / 86400.0)


# --------------------------------------------------------------------------- #
#  Changes
# --------------------------------------------------------------------------- #
def grant(user_id: int, days: float, plan: str = "standard",
          granted_by: int = 0, note: str = "", now=None) -> dict:
    """Add time. Renewing early extends rather than replaces.

    Someone who pays for another month on the 28th day should end up with
    33 days, not 30 — overwriting would quietly charge them for the days
    they had already bought.
    """
    now = now or datetime.now(timezone.utc)
    data = _load()
    key = str(int(user_id))
    cur = _parse((data.get(key) or {}).get("until"))
    base = cur if (cur and cur > now) else now
    until = base + timedelta(days=float(days))

    data[key] = {
        "until": until.isoformat(timespec="seconds"),
        "plan": str(plan)[:32],
        "granted_by": int(granted_by),
        "granted_at": now.isoformat(timespec="seconds"),
        "note": str(note)[:120],
        "history": ((data.get(key) or {}).get("history") or [])[-9:] + [
            {"at": now.isoformat(timespec="seconds"), "days": float(days),
             "by": int(granted_by)}
        ],
    }
    _save(data)
    return data[key]


def revoke(user_id: int) -> bool:
    """End access now. Returns whether there was anything to end."""
    data = _load()
    key = str(int(user_id))
    if key not in data:
        return False
    data.pop(key)
    _save(data)
    return True


def everyone(now=None) -> list[dict]:
    """Every stored subscription, soonest to expire first."""
    now = now or datetime.now(timezone.utc)
    out = []
    for key, rec in _load().items():
        until = _parse(rec.get("until"))
        out.append({
            "user_id": int(key) if key.lstrip("-").isdigit() else key,
            "until": until,
            "active": bool(until and until > now),
            "days_left": max(0.0, (until - now).total_seconds() / 86400.0)
            if until else 0.0,
            "plan": rec.get("plan") or "standard",
            "note": rec.get("note") or "",
        })
    return sorted(out, key=lambda r: (r["until"] or datetime.max.replace(
        tzinfo=timezone.utc)))
