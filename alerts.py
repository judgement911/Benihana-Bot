"""
ALERT SUBSCRIPTIONS
===================

/alert records that a chat wants to be told when an instrument produces a
high-quality ENTRY. It does not, by itself, make anything happen: the bot is
a webhook, it only runs when Telegram pokes it. Something has to call
scan_job.py on a schedule.

That constraint is real and worth stating plainly rather than discovering
later — a PythonAnywhere free account gets ONE scheduled task per day, so
alerts fire once daily unless you upgrade or run the job somewhere else. See
PHONE-SETUP.md.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import config as C
import instruments as I


def _load() -> dict:
    try:
        with open(C.ALERTS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {"subs": [], "sent": []}
    except (OSError, ValueError):
        return {"subs": [], "sent": []}


def _save(data: dict) -> None:
    data["sent"] = data.get("sent", [])[-500:]
    d = os.path.dirname(os.path.abspath(C.ALERTS_FILE)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, C.ALERTS_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add(chat_id: int, key: str, mode: str) -> bool:
    """True if newly added, False if it was already there."""
    data = _load()
    for s in data["subs"]:
        if s["chat_id"] == chat_id and s["key"] == key and s["mode"] == mode:
            return False
    data["subs"].append({"chat_id": chat_id, "key": key, "mode": mode,
                         "since": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    _save(data)
    return True


def remove(chat_id: int, key: str = None, mode: str = None) -> int:
    data = _load()
    before = len(data["subs"])
    data["subs"] = [
        s for s in data["subs"]
        if not (s["chat_id"] == chat_id
                and (key is None or s["key"] == key)
                and (mode is None or s["mode"] == mode))
    ]
    _save(data)
    return before - len(data["subs"])


def for_chat(chat_id: int) -> list[dict]:
    return [s for s in _load()["subs"] if s["chat_id"] == chat_id]


def all_subs() -> list[dict]:
    return _load()["subs"]


def already_sent(signal_id: str, chat_id: int) -> bool:
    return f"{chat_id}:{signal_id}" in set(_load().get("sent", []))


def mark_sent(signal_id: str, chat_id: int) -> None:
    data = _load()
    data.setdefault("sent", []).append(f"{chat_id}:{signal_id}")
    _save(data)


def format_list(chat_id: int) -> str:
    subs = for_chat(chat_id)
    if not subs:
        return ("<b>No alerts set.</b>\n\n"
                "<code>/alert xauusd scalp</code>\n"
                "<code>/alert eurusd intraday</code>\n\n"
                "<code>/alert off</code> clears them all.")
    out = "<b>Your alerts</b>\n\n"
    for s in subs:
        inst = I.BY_KEY.get(s["key"])
        out += f"• {inst.display if inst else s['key']} · {s['mode']}\n"
    out += (f"\n<i>Alerting at confidence {C.ALERT_MIN_CONFIDENCE}%+ on an "
            "ENTRY. Delivery depends on the scan job running — see "
            "/alerthelp.</i>")
    return out
