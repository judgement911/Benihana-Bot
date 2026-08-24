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
from datetime import datetime, timedelta, timezone

import config as C
import instruments as I
from strategy import LONG

WIN, LOSS, OPEN, EXPIRED = "win", "loss", "open", "expired"
CANCELLED = "cancelled"

# §18 lifecycle. `outcome` stays the settled verdict for /stats; `state` is
# where the trade is right now, which is what the user gets notified about.
WAITING, ACTIVE, BREAKEVEN = "waiting", "active", "breakeven"
TP1, TP2, TP3 = "tp1", "tp2", "tp3"
STOPPED, DONE = "stopped", "completed"

# A signal in one of these is finished and no longer blocks a new one.
FINAL_STATES = {STOPPED, DONE, EXPIRED, CANCELLED}

STATUS_LABEL = {
    WAITING: "WAIT", ACTIVE: "ACTIVE", BREAKEVEN: "BREAKEVEN",
    TP1: "NEAR", TP2: "NEAR", TP3: "COMPLETED",
    STOPPED: "STOPPED", DONE: "COMPLETED", EXPIRED: "EXPIRED",
    CANCELLED: "CANCELLED",
}

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
        # ---- lifecycle -------------------------------------------------- #
        # A market order is live the moment it is issued; a pending one is
        # only waiting, and its entry has to trade before anything else can.
        "state": ACTIVE if (res.get("order") or {}).get("kind") == "market"
                 else WAITING,
        "order_kind": (res.get("order") or {}).get("kind", "market"),
        "tps_hit": [],
        "strategy": res.get("strategy", "ronin"),
        "user_id": res.get("user_id"),
        # ---- money, so /daily can report P/L and points ------------------ #
        "risk_cash": lv.get("risk_cash"),
        "risk_points": lv.get("risk_points"),
        "lots": lv.get("lots"),
        "currency": res.get("risk_currency", "USD"),
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
    """One shared definition with the backtester — see probability.realised_r.

    The old version here credited only TP1's slice unless every target
    filled, so a trade that banked TP1 and TP2 before reversing scored
    +0.33R instead of +1.00R. It also disagreed with the backtester, which
    scored that same trade as a full loss.
    """
    import probability as prob                     # noqa: PLC0415
    hit = len(row.get("tps_hit") or ([1] if row.get("hit_tp1") else []))
    if row.get("hit_final"):
        hit = len(row.get("tp_multiples") or [1.0])
    return prob.realised_r(hit, row.get("tp_multiples") or [1.0], C.COST_R)


def resolve(fetch, now: datetime = None) -> dict:
    """Settle every open signal that has had time to play out.

    `fetch(symbol, timeframe, bars)` is injected so this works against a live
    provider, a CSV, or a test fixture.
    """
    now = now or datetime.now(timezone.utc)
    rows = _load()
    todo = [r for r in rows if r.get("outcome") == OPEN]
    if not todo:
        return {"checked": 0, "resolved": 0, "expired": 0, "skipped": 0}

    resolved = expired = 0
    skipped = 0
    by_symbol: dict[tuple, list] = {}
    for r in todo:
        # A record written by an older version can be missing a field this
        # function needs. Indexing it directly threw before anything was
        # settled, and every caller catches the exception and logs it — so
        # one stale row silently froze /stats, /daily, /history and /update
        # for good, with no error shown to anyone. Skip the row instead.
        inst_key = r.get("instrument")
        tf = r.get("entry_tf")
        if not tf:
            spec = C.MODES.get(r.get("mode") or "")
            tf = spec.entry_tf if spec else None
        if not inst_key or not tf:
            skipped += 1
            continue
        by_symbol.setdefault((inst_key, tf), []).append(r)

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
    if skipped:
        print(f"journal.resolve: skipped {skipped} record(s) missing an "
              f"instrument or timeframe")
    return {"checked": len(todo), "resolved": resolved, "expired": expired,
            "skipped": skipped}


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


# --------------------------------------------------------------------------- #
#  Lifecycle (§18) — where each signal is right now
# --------------------------------------------------------------------------- #
def _touch(bar, level: float, direction: int, above: bool) -> bool:
    return bool(bar["high"] >= level) if above else bool(bar["low"] <= level)


def advance(row: dict, bars) -> list[dict]:
    """Walk a signal forward through the bars it has not seen yet.

    Returns the events that happened, in order, so the caller can notify.
    Pessimism is the same as everywhere else in this codebase: a bar that
    touches both a target and the stop is scored as the stop, because from
    a daily candle you cannot know which came first and assuming the good
    one is how backtests learn to lie.
    """
    events = []
    if row.get("state") in FINAL_STATES:
        return events

    d = int(row["direction"])
    long_ = d > 0
    entry, stop = float(row["entry"]), float(row["stop"])
    tps = [float(x) for x in row["tps"]]
    hit = set(row.get("tps_hit") or [])

    for ts, bar in bars.iterrows():
        # A pending order has to fill before anything else can happen to it.
        if row["state"] == WAITING:
            if row["order_kind"] == "limit":
                filled = bar["low"] <= entry if long_ else bar["high"] >= entry
            elif row["order_kind"] == "stop":
                filled = bar["high"] >= entry if long_ else bar["low"] <= entry
            else:
                filled = True
            if not filled:
                continue
            row["state"] = ACTIVE
            events.append({"kind": "entry", "ts": ts, "price": entry})

        stop_now = float(row.get("stop_moved") or stop)
        stopped = (bar["low"] <= stop_now) if long_ else (bar["high"] >= stop_now)

        # Targets first only to record them; the stop still wins a tie below.
        newly = []
        for n, tp in enumerate(tps, start=1):
            if n in hit:
                continue
            if (bar["high"] >= tp) if long_ else (bar["low"] <= tp):
                newly.append((n, tp))

        if stopped:
            # Tie goes to the stop. If TP1 had already filled on an earlier
            # bar and the stop has since moved to breakeven, this is a
            # scratch rather than a full loss — _realised_r works that out.
            row["state"] = STOPPED if not hit else DONE
            row["outcome"] = LOSS if not hit else WIN
            events.append({"kind": "stop", "ts": ts, "price": stop_now,
                           "breakeven": bool(row.get("stop_moved"))})
            break

        for n, tp in newly:
            hit.add(n)
            row["tps_hit"] = sorted(hit)
            row["hit_tp1"] = row["hit_tp1"] or n == 1
            events.append({"kind": f"tp{n}", "ts": ts, "price": tp, "n": n})
            if n == 1 and C.MOVE_TO_BREAKEVEN_AFTER_TP1:
                # The strategy's own rule, not a hunch: once the first target
                # pays, the rest of the position rides at zero risk.
                row["stop_moved"] = entry
                row["state"] = BREAKEVEN
                events.append({"kind": "breakeven", "ts": ts, "price": entry})
            if n == len(tps):
                row["state"] = DONE
                row["outcome"] = WIN
                row["hit_final"] = True
                events.append({"kind": "complete", "ts": ts, "price": tp})
                break
        if row["state"] in FINAL_STATES:
            break

    if row["state"] in FINAL_STATES and not row.get("resolved_ts"):
        row["r"] = _realised_r(row)
        row["resolved_ts"] = _iso(datetime.now(timezone.utc))
    return events


def active_signals(instrument: str = None, mode: str = None,
                   user_id: int = None) -> list[dict]:
    """Signals that have not finished. §19 identifies a flow by pair AND
    trading style, so a live swing never blocks a scalp on the same pair."""
    out = []
    for r in _load():
        if r.get("state") in FINAL_STATES:
            continue
        if r.get("state") is None and r.get("outcome") != OPEN:
            continue
        if instrument and r.get("instrument") != instrument:
            continue
        if mode and r.get("mode") != mode:
            continue
        if user_id is not None and r.get("user_id") not in (None, user_id):
            continue
        out.append(r)
    return out


def cancel(sid: str) -> bool:
    rows = _load()
    for r in rows:
        if r["id"] == sid and r.get("state") not in FINAL_STATES:
            r["state"] = CANCELLED
            r["outcome"] = CANCELLED
            r["resolved_ts"] = _iso(datetime.now(timezone.utc))
            _save(rows)
            return True
    return False


# --------------------------------------------------------------------------- #
#  Period performance (§11) — /daily, /weekly, /monthly
# --------------------------------------------------------------------------- #
PERIODS = {"daily": 1, "weekly": 7, "monthly": 30}


def period_stats(period: str = "daily", user_id: int = None) -> dict:
    """Settled trades inside a window, counted in R, points and money.

    The window is measured on the WIB calendar day, because that is the day
    the user's own limits and habits run on. Only trades this bot actually
    recorded and then resolved are counted — nothing is inferred, and open
    trades contribute nothing until they finish.
    """
    days = PERIODS.get(period, 1)
    now = datetime.now(users_tz())
    if days == 1:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=days)

    rows = []
    for r in _load():
        if r.get("r") is None or r.get("outcome") in (OPEN, EXPIRED, CANCELLED):
            continue
        if user_id is not None and r.get("user_id") not in (None, user_id):
            continue
        stamp = r.get("resolved_ts") or r.get("ts")
        try:
            when = datetime.fromisoformat(stamp).astimezone(users_tz())
        except (TypeError, ValueError):
            continue
        if when >= start:
            rows.append(r)

    pairs: dict[str, int] = {}
    profit = loss = 0.0
    total_r = total_pts = 0.0
    for r in rows:
        key = (r.get("instrument") or "?").upper()
        pairs[key] = pairs.get(key, 0) + 1
        rr = float(r["r"])
        total_r += rr
        cash = float(r.get("risk_cash") or 0.0) * rr
        if cash >= 0:
            profit += cash
        else:
            loss += cash
        total_pts += rr * float(r.get("risk_points") or 0.0)

    return {
        "period": period,
        "start": start,
        "trades": len(rows),
        "pairs": pairs,
        "profit": round(profit, 2),
        "loss": round(loss, 2),
        "net": round(profit + loss, 2),
        "total_r": round(total_r, 2),
        "total_points": round(total_pts, 1),
        "wins": sum(1 for r in rows if float(r["r"]) > 0),
        "losses": sum(1 for r in rows if float(r["r"]) <= 0),
    }


def users_tz():
    """UTC+7 — the clock the user's day, and therefore their limits, run on."""
    return timezone(timedelta(hours=7))


def format_period(period: str = "daily", user_id: int = None,
                  lang: str = "en") -> str:
    import i18n
    s = period_stats(period, user_id)
    title = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY"}[period]
    head = f"📊 <b>{title} {i18n.t('performance', lang)}</b>\n\n"

    if not s["trades"]:
        return head + f"<i>{i18n.t('no_trades_period', lang)}</i>"

    pairs = ", ".join(f"{k}·{v}" for k, v in
                      sorted(s["pairs"].items(), key=lambda x: -x[1]))
    money = lambda v: f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"
    return (
        head
        + f"{i18n.t('trades', lang)}: <b>{s['trades']}</b>\n"
        + f"{i18n.t('pairs', lang)}: {pairs}\n\n"
        + f"✅ {i18n.t('profit', lang)}: {money(s['profit'])}\n"
        + f"❌ {i18n.t('loss', lang)}: {money(s['loss'])}\n"
        + f"💰 {i18n.t('net', lang)}: <b>{money(s['net'])}</b>\n\n"
        + f"📈 {i18n.t('total_r', lang)}: <b>{s['total_r']:+.2f}R</b>\n"
        + f"📐 {i18n.t('total_points', lang)}: {s['total_points']:+,.1f} pts"
    )


def wipe(user_id: int = None) -> dict:
    """Erase recorded history. Returns what was removed.

    Scoped to one user when given an id, because a shared bot must never let
    one person's reset delete another's record.
    """
    rows = _load()
    if user_id is None:
        removed = len(rows)
        _save([])
        return {"removed": removed, "kept": 0}
    keep = [r for r in rows if r.get("user_id") not in (None, user_id)]
    removed = len(rows) - len(keep)
    _save(keep)
    return {"removed": removed, "kept": len(keep)}


def lifecycle_view(user_id: int, modes=None, exclude_modes=None) -> list[dict]:
    """Signals to report on, newest first.

    /update and /swingupdate split the same data by timeframe: a swing trade
    runs for days and does not belong in the same list as this morning's
    scalps, where it would be buried under them.
    """
    out = []
    for r in _load():
        if r.get("user_id") not in (None, user_id):
            continue
        if modes and r.get("mode") not in modes:
            continue
        if exclude_modes and r.get("mode") in exclude_modes:
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return out
