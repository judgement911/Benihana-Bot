"""
THE ALERT DELIVERY JOB
======================

Run this on a schedule. It sweeps every instrument someone has subscribed
to, and pushes a message for each high-confidence ENTRY it finds.

    python scan_job.py                # every subscription
    python scan_job.py --mode scalp   # just one mode
    python scan_job.py --dry-run      # print, send nothing

PythonAnywhere: Tasks tab → add `python3 /home/YOU/Benihana-Bot/scan_job.py`.
A free account gets ONE task a day, so alerts fire once daily. Hourly needs a
paid plan, or run this from any machine that is already on.

Every ENTRY it finds is also written to the journal, so /stats grades alerts
the same way it grades signals you asked for.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

import alerts
import config as C
import instruments as I
import journal
import scanner
import view

log = logging.getLogger("scanjob")


def _fetch():
    if C.DATA_PROVIDER == "twelvedata":
        from data import fetch_ohlc
    else:
        from market_data import fetch_ohlc
    return fetch_ohlc


def run(send, mode: str = None, dry_run: bool = False, now: datetime = None) -> dict:
    """`send(chat_id, html)` delivers one alert. Returns a summary."""
    now = now or datetime.now(timezone.utc)
    subs = alerts.all_subs()
    if mode:
        subs = [s for s in subs if s["mode"] == mode]
    if not subs:
        return {"subs": 0, "scanned": 0, "alerts": 0, "recorded": 0}

    fetch = _fetch()
    # One scan per mode covers every chat watching that mode.
    by_mode: dict[str, list[str]] = {}
    for s in subs:
        by_mode.setdefault(s["mode"], [])
        if s["key"] not in by_mode[s["mode"]]:
            by_mode[s["mode"]].append(s["key"])

    sent = recorded = scanned = 0
    for m, keys in by_mode.items():
        result = scanner.scan(keys, m, fetch, now=now, log=log.warning)
        scanned += len(result["rows"])

        for res in scanner.tradeable(result["rows"]):
            conf = (res.get("confidence") or {}).get("value", 0)
            if conf < C.ALERT_MIN_CONFIDENCE:
                continue
            if journal.record(res):
                recorded += 1

            sid = journal.signal_id(res)
            inst = I.BY_KEY.get(res["instrument"], I.GOLD)
            body = ("🔔 <b>SETUP FOUND</b>\n\n"
                    + view.render(inst.display, res))
            for s in subs:
                if s["mode"] != m or s["key"] != res["instrument"]:
                    continue
                if alerts.already_sent(sid, s["chat_id"]):
                    continue
                if dry_run:
                    print(f"--- would alert {s['chat_id']} ---\n{body}\n")
                else:
                    send(s["chat_id"], body)
                    alerts.mark_sent(sid, s["chat_id"])
                sent += 1

    return {"subs": len(subs), "scanned": scanned, "alerts": sent,
            "recorded": recorded}


def main() -> int:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",
                        level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=sorted(C.MODES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not C.TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN missing — cannot send alerts.", file=sys.stderr)
        return 2

    def send(chat_id: int, html: str) -> None:
        from flask_app import tg
        tg("sendMessage", chat_id=chat_id, text=html, parse_mode="HTML",
           disable_web_page_preview=True)

    summary = run(send, mode=args.mode, dry_run=args.dry_run)
    log.info("subs=%(subs)d scanned=%(scanned)d alerts=%(alerts)d "
             "recorded=%(recorded)d", summary)

    # Settle anything the journal is still holding open.
    try:
        got = journal.resolve(_fetch())
        log.info("journal: %(resolved)d resolved, %(expired)d expired", got)
    except Exception as exc:  # noqa: BLE001
        log.warning("journal resolve failed: %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
