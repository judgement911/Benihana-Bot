"""
THE SIGNAL JOURNAL — what the bot said, and what the market did next
====================================================================

/stats is only worth reading if the numbers behind it were recorded before
the outcome was known. So every ENTRY the bot issues is written down at the
moment it is issued — entry, stop, targets, score, and the probability it
claimed — and resolved later against candles it had never seen.

Resolution uses the same convention as backtest.py: a bar that touches both
the target and the stop is scored as the stop. Pessimistic on purpose. A bot
grading its own homework should mark itself down on ties.

Realised R follows the plan the signal quoted: equal slices to each target,
stop to breakeven once the first is filled. So a trade that reaches TP1 and
then retraces is +0.5R, not +1R, and not a "win" worth a full unit. Costs
come out at COST_R per trade, the same figure the signal's expectancy uses,
so /stats and the number on the signal are directly comparable.

Nothing here is a backtest. It is the live record, it starts empty, and it
stays honest by being boring.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import config as C
import instruments as I
from strategy import LONG

WIN, LOSS, OPEN, EXPIRED = "win", "loss", "open", "expired"

TF_SECONDS = {"5min": 300, "15min": 900, "30min": 1800, "1h": 3600,
              "2h": 7200, "4h": 14400, "1day": 86400, "1week": 604800}


# --------------------------------------------------------------------------- #
#  Storage
# --------------------------------------------------------------------------- #
def _load() -> list[dict]:
    try:
        with open(C.JOURNAL_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    rows = rows[-C.JOURNAL_MAX_ENTRIES:]
    d = os.path.dirname(os.path.abspath(C.JOURNAL_FILE)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        os.replace(tmp, C.JOURNAL_FILE)          # atomic; never a half file
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _iso(dt) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  Recording
# --------------------------------------------------------------------------- #
def signal_id(res: dict) -> str:
    """One id per (instrument, mode, signal bar). Asking twice for the same
    setup must not count it twice."""
    return f"{res.get('instrument')}:{res['mode']}:{_iso(res['as_of'])}"

def record(res: dict) -> str | None:
    """Write down an ENTRY at the moment it is issued. Returns its id, or None
    if this was not an ENTRY or was already recorded."""
    if res.get("decision") != "ENTRY" or not res.get("levels"):
        return None

    sid = signal_id(res)
    rows = _load()
    if any(r["id"] == sid for r in rows):
        return None

    lv = res["levels"]
    pr = res.get("probability") or {}
    conf = res.get("confidence") or {}
    rows.append({
        "id": sid,
        "ts": _iso(res["as_of"]),
        "instrument": res.get("instrument"),
        "mode": res["mode"],
        "entry_tf": res["timeframes"]["entry"],
        "direction": res["direction"],
        "entry": lv["entry"],
        "stop": lv["stop"],
        "tps": lv["tps"],
        "tp_multiples": list(lv["tp_multiples"]),
        "score": res.get("score"),
        "confidence": conf.get("value"),
        # what the bot promised, kept so /stats can grade the promise
        "p_tp1": (pr.get("targets") or [{}])[0].get("p"),
        "outcome": OPEN,
        "hit_tp1": False,
        "hit_final": False,
        "r": None,
        "resolved_ts": None,
    })
    _save(rows)
    return sid


# --------------------------------------------------------------------------- #
#  Resolution
# --------------------------------------------------------------------------- #
def _walk(row: dict, bars) -> tuple[str, bool, bool]:
    """Replay candles after the signal. Same rule as the backtester: a bar
    touching both levels counts as the stop."""
    long_ = row["direction"] == LONG
    sl, tp1 = row["stop"], row["tps"][0]
    final = row["tps"][-1]
    hit_tp1 = hit_final = False

    for _, bar in bars.iterrows():
        if long_:
            hit_sl = bar["low"] <= sl
            t1 = bar["high"] >= tp1
            tf = bar["high"] >= final
        else:
            hit_sl = bar["high"] >= sl
            t1 = bar["low"] <= tp1
            tf = bar["low"] <= final

        if t1 and not hit_sl:
            hit_tp1 = True
        if hit_sl:
            return (WIN if hit_tp1 else LOSS), hit_tp1, hit_final
        if tf and hit_tp1:
            return WIN, True, True
    return OPEN, hit_tp1, hit_final


def _realised_r(row: dict) -> float:
    """Equal slices to each target, breakeven on the rest once TP1 fills."""
    if not row["hit_tp1"]:
        return -1.0 - C.COST_R
    mults = row["tp_multiples"] or [1.0]
    n = len(mults)
    r = mults[0] / n
    if row["hit_final"] and n > 1:
        r += sum(mults[1:]) / n
    return r - C.COST_R


def resolve(fetch, now: datetime = None) -> dict:
    """Settle every open signal that has had time to play out.

    `fetch(symbol, timeframe, bars)` is injected so this works against a live
    provider, a CSV, or a test fixture.
    """
    now = now or datetime.now(timezone.utc)
    rows = _load()
    todo = [r for r in rows if r["outcome"] == OPEN]
    if not todo:
        return {"checked": 0, "resolved": 0, "expired": 0}

    resolved = expired = 0
    by_symbol: dict[tuple, list] = {}
    for r in todo:
        by_symbol.setdefault((r["instrument"], r["entry_tf"]), []).append(r)

    for (key, tf), group in by_symbol.items():
        inst = I.BY_KEY.get(key)
        if inst is None:
            continue
        try:
            df = fetch(inst.symbol, tf, C.JOURNAL_MAX_BARS + 50)
        except Exception:
            continue                      # provider down; try again next time

        for r in group:
            ts = datetime.fromisoformat(r["ts"])
            after = df[df.index > ts]
            if after.empty:
                continue
            outcome, t1, tfin = _walk(r, after)
            age = (now - ts).total_seconds()
            deadline = TF_SECONDS.get(tf, 900) * C.JOURNAL_MAX_BARS

            if outcome == OPEN and age > deadline:
                # Never resolved either way. Not a win, not a loss, not counted.
                r.update(outcome=EXPIRED, hit_tp1=t1, hit_final=tfin,
                         r=None, resolved_ts=_iso(now))
                expired += 1
            elif outcome != OPEN:
                r.update(outcome=outcome, hit_tp1=t1, hit_final=tfin,
                         resolved_ts=_iso(now))
                r["r"] = _realised_r(r)
                resolved += 1

    _save(rows)
    return {"checked": len(todo), "resolved": resolved, "expired": expired}


# --------------------------------------------------------------------------- #
#  Aggregation
# --------------------------------------------------------------------------- #
def stats(key: str = None, mode: str = None) -> dict:
    rows = [r for r in _load()
            if (key is None or r["instrument"] == key)
            and (mode is None or r["mode"] == mode)]
    settled = [r for r in rows if r["outcome"] in (WIN, LOSS)]
    wins = [r for r in settled if r["outcome"] == WIN]
    losses = [r for r in settled if r["outcome"] == LOSS]

    gains = sum(r["r"] for r in wins if r["r"] is not None)
    pains = abs(sum(r["r"] for r in losses if r["r"] is not None))
    rs = [r["r"] for r in settled if r["r"] is not None]

    planned = [max(r["tp_multiples"]) for r in rows if r.get("tp_multiples")]
    promised = [r["p_tp1"] for r in settled if r.get("p_tp1") is not None]

    return {
        "total": len(rows),
        "open": sum(1 for r in rows if r["outcome"] == OPEN),
        "expired": sum(1 for r in rows if r["outcome"] == EXPIRED),
        "settled": len(settled),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(settled)) if settled else None,
        "expectancy_r": (sum(rs) / len(rs)) if rs else None,
        "total_r": sum(rs) if rs else 0.0,
        "profit_factor": (gains / pains) if pains > 0 else (None if not gains else float("inf")),
        "avg_rr": (sum(planned) / len(planned)) if planned else None,
        # did the claimed probability match reality?
        "promised": (sum(promised) / len(promised)) if promised else None,
        "recent": [r["outcome"] for r in settled[-20:]],
        "first_ts": rows[0]["ts"] if rows else None,
    }


def format_stats(key: str = None, mode: str = None) -> str:
    inst = I.BY_KEY.get(key or "")
    title = f"{inst.display} — BENIHANA PERFORMANCE" if inst else "BENIHANA PERFORMANCE"
    if mode:
        title += f" ({mode})"
    s = stats(key, mode)

    if not s["total"]:
        return (f"<b>{title}</b>\n\n"
                "No signals recorded yet.\n\n"
                "<i>Every ENTRY the bot issues gets written down before the "
                "outcome is known, then graded against candles it had not seen. "
                "That takes real ENTRYs and real time — the bot will not invent "
                "a track record to fill this screen.</i>\n\n"
                "For a historical read instead: <code>/backtest intraday</code>")

    lines = [f"<b>{title}</b>", ""]
    lines.append(f"📈 Signals: <b>{s['total']}</b>")
    if not s["settled"]:
        lines.append(f"Still open: {s['open']}")
        lines.append("")
        lines.append("<i>Nothing has resolved yet — no win rate to report.</i>")
        return "\n".join(lines)

    lines.append(f"✅ Wins: <b>{s['wins']}</b>")
    lines.append(f"❌ Losses: <b>{s['losses']}</b>")
    lines.append(f"🎯 Win Rate: <b>{s['win_rate']:.1%}</b>")
    if s["avg_rr"]:
        lines.append(f"⚖️ Avg RR: 1:{s['avg_rr']:.2g}")
    if s["profit_factor"] is not None:
        pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
        lines.append(f"💹 Profit Factor: <b>{pf}</b>")
    lines.append(f"📊 Expectancy: {s['expectancy_r']:+.2f}R per signal")
    lines.append(f"💰 Total: {s['total_r']:+.1f}R")

    if s["recent"]:
        strip = " ".join("🟩" if o == WIN else "🟥" for o in s["recent"])
        lines += ["", f"Last {len(s['recent'])}:", strip]

    tail = []
    if s["open"]:
        tail.append(f"{s['open']} still open")
    if s["expired"]:
        tail.append(f"{s['expired']} expired unresolved")
    if tail:
        lines += ["", "<i>" + ", ".join(tail) + ".</i>"]

    # The bot graded its own forecast. Say how that went.
    if s["promised"] is not None and s["settled"] >= 5:
        gap = s["win_rate"] - s["promised"]
        verdict = ("about right" if abs(gap) < 0.05
                   else ("optimistic" if gap < 0 else "pessimistic"))
        lines += ["", f"<i>Claimed {s['promised']:.0%} on these, paid "
                      f"{s['win_rate']:.0%} — the model was {verdict}.</i>"]

    if s["settled"] < 20:
        lines += ["", f"<i>{s['settled']} settled signals is far too few to "
                      "judge anything. Treat this as a running tally, not "
                      "evidence.</i>"]
    return "\n".join(lines)
