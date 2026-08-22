"""
SCANNING MANY MARKETS AT ONCE
=============================

One signal costs three requests: entry, trend and bias timeframes. Twelve
Data's free tier allows eight requests a minute and 800 a day. So a full
43-instrument sweep is 129 requests — about sixteen minutes of waiting and a
sixth of the daily budget, which is why this module is built around a
deadline rather than a wish.

scan() stops when the clock runs out and reports exactly how far it got. A
partial scan that says it is partial beats a complete-looking scan that
quietly skipped nine symbols.

Ordering matters: the list is scanned in the order given, so put what you
care about first. Cached candles cost nothing, so a second scan inside the
cache window is almost instant.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import config as C
import instruments as I
import strategies
from strategy import DIR_NAME

DIR_DOT = {"BUY": "🟢", "SELL": "🔴"}


def scan(keys, mode: str, fetch, deadline_s: float = None,
         now: datetime = None, log=None, strategy: str = None) -> dict:
    """Evaluate each instrument in `keys`. Never raises for one bad symbol."""
    spec = C.MODES[mode]
    deadline_s = C.SCAN_DEADLINE_S if deadline_s is None else deadline_s
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)

    rows, errors, skipped = [], [], []
    for key in keys:
        inst = I.BY_KEY.get(key)
        if inst is None:
            errors.append((key, "unknown instrument"))
            continue
        if deadline_s and (time.monotonic() - started) > deadline_s:
            skipped.append(key)
            continue
        try:
            e = fetch(inst.symbol, spec.entry_tf, spec.bars)
            t = fetch(inst.symbol, spec.trend_tf, spec.bars)
            b = fetch(inst.symbol, spec.bias_tf, spec.bars)
            res = strategies.evaluate(strategy, e, t, b, spec, now, instrument=inst)
        except Exception as exc:  # noqa: BLE001 — one dead symbol must not kill the sweep
            errors.append((key, f"{type(exc).__name__}: {exc}"))
            if log:
                log(f"scan {key}: {exc}")
            continue
        rows.append(res)

    rows.sort(key=_rank, reverse=True)
    return {"rows": rows, "errors": errors, "skipped": skipped,
            "mode": mode, "elapsed": time.monotonic() - started,
            "as_of": now}


def _rank(res: dict) -> tuple:
    """ENTRY beats WAIT beats nothing; then confidence, then odds."""
    order = {"ENTRY": 2, "WAIT": 1}.get(res.get("decision"), 0)
    if res.get("vetoes"):
        order = -1
    conf = (res.get("confidence") or {}).get("value", 0)
    pr = res.get("probability")
    odds = pr["targets"][0]["p"] if pr else 0
    return (order, conf, odds)


def tradeable(rows) -> list[dict]:
    return [r for r in rows if r.get("decision") == "ENTRY" and r.get("levels")]


def format_scan(result: dict, limit: int = 12) -> str:
    rows, mode = result["rows"], result["mode"]
    spec = C.MODES[mode]
    out = "⚔️ <b>BENIHANA MARKET SCAN</b>\n"
    out += (f"<i>{mode} · {spec.entry_tf} trigger · "
            f"{result['as_of'].strftime('%H:%M')} UTC</i>\n\n")

    if not rows:
        out += "Nothing scanned — no data came back for any symbol.\n"
    for res in rows[:limit]:
        inst = I.BY_KEY.get(res.get("instrument") or "", I.GOLD)
        dec = res.get("decision")
        conf = (res.get("confidence") or {}).get("value")

        if res.get("vetoes") or dec == "NO TRADE":
            out += f"⚪ <b>{inst.display}</b> — NO TRADE\n"
            continue

        direction = DIR_NAME.get(res.get("direction"), "")
        dot = DIR_DOT.get(direction, "⚪")
        tag = "BUY" if direction == "BUY" else "SELL"
        if dec == "WAIT":
            out += f"{dot} <b>{inst.display}</b> — {tag} setup — {conf}% <i>(wait)</i>\n"
        else:
            out += f"{dot} <b>{inst.display}</b> — {tag} — {conf}%\n"

        lv = res.get("levels")
        if lv and dec in ("ENTRY", "WAIT"):
            order = (res.get("order") or {}).get("label", "")
            out += f"      📍 {inst.fmt(lv['entry'])}"
            out += f" · {order}" if order else ""
            out += (f"\n      🛑 {inst.fmt(lv['stop'])}"
                    f" · 🎯 {inst.fmt(lv['tps'][0])}\n")

    best = tradeable(rows)
    if best:
        b = best[0]
        inst = I.BY_KEY.get(b["instrument"], I.GOLD)
        pr = b.get("probability")
        out += "\n🏆 <b>Best setup</b>\n"
        out += f"{inst.display} {DIR_NAME[b['direction']]}\n"
        out += f"📊 Confidence: {(b.get('confidence') or {}).get('value')}%\n"
        if pr:
            out += (f"🎲 Odds: {pr['targets'][0]['p']:.0%} to TP1 · "
                    f"Exp {pr['expectancy_r']:+.2f}R\n")
        out += f"⚖️ RR: 1:{max(b['levels']['tp_multiples']):g}\n"
        out += f"🕐 TF: {b['timeframes']['entry']}\n"
    else:
        out += "\n<i>No ENTRY anywhere in this sweep. That is a normal result.</i>\n"

    notes = []
    if result["skipped"]:
        notes.append(f"{len(result['skipped'])} skipped (out of time)")
    if result["errors"]:
        notes.append(f"{len(result['errors'])} no data")
    if notes:
        out += f"\n<i>Scanned {len(rows)} in {result['elapsed']:.0f}s · " \
               f"{' · '.join(notes)}.</i>\n"
    if result["errors"]:
        first = result["errors"][:3]
        out += "<i>" + "; ".join(f"{k}: {m[:40]}" for k, m in first) + "</i>\n"
    return out
