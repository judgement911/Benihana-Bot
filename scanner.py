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
    """A scan is mostly "no" — usually every symbol but one. So it is laid
    out to be read from the top: the answer first, then the shortlist, and
    the rejections compressed to a single line rather than a paragraph each.
    """
    rows, mode = result["rows"], result["mode"]
    spec = C.MODES[mode]

    out = "🔭 <b>MARKET SCAN</b>\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n"
    out += (f"<i>{mode} · {spec.entry_tf} chart · "
            f"{result['as_of'].strftime('%H:%M')} UTC</i>\n\n")

    if not rows:
        out += ("⚠️ No data came back for any symbol.\n"
                "<i>Usually the daily request quota. Try again later.</i>")
        return out

    entries = [r for r in rows if r.get("decision") == "ENTRY"
               and not r.get("vetoes")]
    waits = [r for r in rows if r.get("decision") == "WAIT"
             and not r.get("vetoes")]
    quiet = [r for r in rows if r not in entries and r not in waits]

    # The headline: how many of the markets looked at are actually offering
    # something. Everything below is detail on that number.
    if entries:
        out += f"🎯 <b>{len(entries)} ready to trade</b>"
    elif waits:
        out += f"⏳ <b>{len(waits)} forming, none ready</b>"
    else:
        out += "😴 <b>Nothing here right now</b>"
    out += f"  <i>· {len(rows)} scanned</i>\n\n"

    def _line(res, icon):
        inst = I.BY_KEY.get(res.get("instrument") or "", I.GOLD)
        d = DIR_NAME.get(res.get("direction"), "")
        conf = (res.get("confidence") or {}).get("value")
        line = (f"{icon} <b>{inst.display}</b> {DIR_DOT.get(d, '')} {d}"
                f"  <b>{conf}%</b>\n")
        lv = res.get("levels")
        if lv:
            order = (res.get("order") or {}).get("label", "")
            line += f"   📍 {inst.fmt(lv['entry'])}"
            line += f" <i>{order}</i>" if order else ""
            line += (f"\n   🛑 {inst.fmt(lv['stop'])}"
                     f"   🎯 {inst.fmt(lv['tps'][0])}\n")
        return line

    for res in entries[:limit]:
        out += _line(res, "🟩")
    if entries and waits:
        out += "\n"
    for res in waits[:max(0, limit - len(entries))]:
        out += _line(res, "🟨")

    # One line for everything that said no, instead of one line each.
    if quiet:
        names = ", ".join(
            I.BY_KEY.get(r.get("instrument") or "", I.GOLD).display
            for r in quiet[:10])
        out += f"\n⚪ <i>Quiet: {names}"
        out += f" +{len(quiet) - 10} more" if len(quiet) > 10 else ""
        out += "</i>\n"

    best = tradeable(rows)
    if best:
        b = best[0]
        inst = I.BY_KEY.get(b["instrument"], I.GOLD)
        pr = b.get("probability")
        st = strategies.REGISTRY.get(b.get("strategy") or "")
        out += "\n━━━━━━━━━━━━━━━━━━━━\n"
        out += "🏆 <b>PICK OF THE SCAN</b>\n"
        out += (f"{DIR_DOT.get(DIR_NAME[b['direction']], '')} "
                f"<b>{inst.display} {DIR_NAME[b['direction']]}</b>"
                f"  ·  {(b.get('confidence') or {}).get('value')}% sure\n")
        if st:
            out += f"{st.icon} {st.name}\n"
        if pr:
            out += (f"🎲 {pr['targets'][0]['p']:.0%} chance of reaching TP1  ·  "
                    f"expects {pr['expectancy_r']:+.2f}R\n")
        out += (f"⚖️ Risking 1 to make {max(b['levels']['tp_multiples']):g}\n"
                f"<i>Run /signal {b['instrument']} {mode} for the full plan.</i>\n")
    elif waits:
        out += ("\n<i>Setups are building but none has triggered. "
                "Waiting is the position.</i>\n")
    else:
        out += ("\n<i>No trade anywhere is a normal answer — most of the time "
                "the market is not offering one.</i>\n")

    notes = []
    if result["skipped"]:
        notes.append(f"{len(result['skipped'])} ran out of time")
    if result["errors"]:
        notes.append(f"{len(result['errors'])} had no data")
    if notes:
        out += f"\n<i>Took {result['elapsed']:.0f}s · {' · '.join(notes)}.</i>"
    return out
