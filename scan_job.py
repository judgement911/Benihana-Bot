"""
THE BACKGROUND JOB
==================

The bot is a webhook: it only runs when someone messages it, so it cannot
watch the market or a live trade on its own. This script is the thing that
can, and it does two jobs each time it runs.

  1. Lifecycle (§18). Every unfinished signal is walked forward over the
     candles it has not seen. Entry fills, each target, the move to
     breakeven, the stop and expiry all produce a message to whoever the
     signal belongs to.

  2. Auto-signals (§12). Each user's chosen strategy is run over the free
     universe on their timeframe, and anything reaching their confidence
     floor — default 85% — is sent unprompted. A setup has to satisfy the
     strategy's real entry conditions; a market merely moving is not a
     signal.

RUN IT ON A SCHEDULE. How often is how responsive the bot is:

    python3 scan_job.py

A PythonAnywhere free account gets ONE scheduled task a day, so on that plan
both jobs run once daily — a TP1 that fills at noon is reported at whatever
hour the task runs. Any host with cron gives you minutes instead. This is a
property of the hosting, not of the bot, and the /help text says so rather
than letting you discover it from a notification that never came.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

import config as C
import i18n
import instruments as I
import journal
import scanner
import users
import view
from data import fetch_ohlc

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",
                    level=logging.INFO)
log = logging.getLogger("scanjob")


def _tg(method: str, **payload):
    import requests
    url = f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=20)
        return r.json()
    except Exception as exc:                       # noqa: BLE001
        log.error("telegram %s failed: %s", method, exc)
        return None


def send(chat_id: int, text: str):
    _tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
        disable_web_page_preview=True)


# --------------------------------------------------------------------------- #
#  1. Lifecycle
# --------------------------------------------------------------------------- #
EVENT_EMOJI = {"entry": "🎯", "tp1": "🤡", "tp2": "🥵", "tp3": "💀",
               "breakeven": "🛡️", "stop": "❌", "complete": "✅"}


def notify_lifecycle(fetch=fetch_ohlc) -> int:
    rows = journal._load()
    todo = [r for r in rows if r.get("state") not in journal.FINAL_STATES]
    if not todo:
        return 0

    sent = 0
    by_feed: dict[tuple, list] = {}
    for r in todo:
        by_feed.setdefault((r.get("instrument"), r.get("entry_tf")), []).append(r)

    for (key, tf), group in by_feed.items():
        inst = I.BY_KEY.get(key or "")
        if inst is None:
            continue
        try:
            df = fetch(inst.symbol, tf, C.JOURNAL_MAX_BARS + 50)
        except Exception as exc:                   # noqa: BLE001
            log.warning("no data for %s %s: %s", key, tf, exc)
            continue

        for r in group:
            try:
                since = datetime.fromisoformat(r["ts"])
            except (TypeError, ValueError):
                continue
            bars = df[df.index > since]
            if bars.empty:
                continue
            for ev in journal.advance(r, bars):
                chat = r.get("user_id")
                if not chat:
                    continue
                send(chat, format_event(r, ev, inst,
                                        users.get(chat).get("language", i18n.EN)))
                sent += 1
    journal._save(rows)
    return sent


def format_event(row: dict, ev: dict, inst, lang: str) -> str:
    kind = ev["kind"]
    side = "BUY" if row["direction"] > 0 else "SELL"
    emoji = EVENT_EMOJI.get(kind, "📣")
    head = {
        "entry": i18n.t("ev_entry", lang),
        "tp1": i18n.t("ev_tp", lang, n=1),
        "tp2": i18n.t("ev_tp", lang, n=2),
        "tp3": i18n.t("ev_tp", lang, n=3),
        "breakeven": i18n.t("ev_breakeven", lang),
        "stop": i18n.t("ev_stop", lang),
        "complete": i18n.t("ev_complete", lang),
    }.get(kind, kind.upper())

    out = f"{emoji} <b>{head}</b>\n\n{inst.display} {side}\n"
    out += f"{inst.fmt(ev['price'])}\n"

    if kind.startswith("tp"):
        n = ev["n"]
        out += f"\n{i18n.t('profit', lang)}: +{row['tp_multiples'][n - 1]:g}R\n"
        remaining = [i for i in range(1, len(row["tps"]) + 1)
                     if i not in (row.get("tps_hit") or [])]
        if remaining:
            nxt = remaining[0]
            out += (f"\n{i18n.t('next_target', lang)}\n"
                    f"{EVENT_EMOJI.get(f'tp{nxt}', '🎯')} TP{nxt} "
                    f"{inst.fmt(row['tps'][nxt - 1])}")
    elif kind == "breakeven":
        out += f"\n<i>{i18n.t('ev_breakeven_note', lang)}</i>"
    elif kind == "stop":
        out += (f"\n{i18n.t('result', lang)}: {row.get('r', 0):+.2f}R" if row.get("r")
                is not None else "")
    return out


# --------------------------------------------------------------------------- #
#  2. Auto-signals
# --------------------------------------------------------------------------- #
def auto_signals(fetch=fetch_ohlc) -> int:
    sent = 0
    for user_id, u in users.all_users().items():
        floor = int(u.get("min_confidence") or 0) or C.AUTO_SIGNAL_CONFIDENCE
        mode = u.get("auto_mode") or "intraday"
        keys = [k for k in C.SCAN_SYMBOLS if k in I.BY_KEY]
        if not keys:
            continue
        try:
            result = scanner.scan(keys, mode, fetch, log=log.warning,
                                  strategy=u.get("strategy"))
        except Exception as exc:                   # noqa: BLE001
            log.error("scan failed for %s: %s", user_id, exc)
            continue

        for res in result["rows"]:
            if res.get("decision") != "ENTRY":
                continue
            conf = int((res.get("confidence") or {}).get("value") or 0)
            if conf < floor:
                continue
            inst = I.BY_KEY.get(res.get("instrument") or "")
            if inst is None:
                continue
            # Respect the same one-per-flow rule the manual command obeys.
            if journal.active_signals(instrument=inst.key, mode=mode,
                                      user_id=user_id):
                continue
            res["user_id"] = user_id
            journal.record(res)
            send(user_id, view.render(inst.display, res,
                                      lang=u.get("language", i18n.EN)))
            sent += 1
    return sent


def main() -> int:
    if not C.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN missing")
        return 1
    events = notify_lifecycle()
    signals = auto_signals()
    log.info("lifecycle events sent: %d, auto-signals sent: %d", events, signals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
