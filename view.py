"""
HOW A SIGNAL LOOKS
==================

Two levels of detail, one renderer, both front ends.

The compact view answers the only question that matters when you open the
chat: is there a trade, which way, and at what prices. Everything that
explains *why* — the scorecard, the confidence deductions, how much of the
probability is measured rather than modelled — lives behind the Details
button. It is all still one tap away, and nothing was deleted; it just
stopped being the first thing you read.

What compact never drops: the honest numbers. Plenty of signal bots print a
confident-looking percentage with nothing behind it. The odds here stay on
the front page next to the expectancy, because a clean layout is not a
reason to start flattering the setup.
"""
from __future__ import annotations

import html

import i18n
import instruments as I
import money as M
from strategy import TF_SECONDS as base_TF_SECONDS
import probability as prob
import sessions as SESS
from strategy import DIR_NAME

# Compact needs a word, not a sentence. The scorecard in Details carries the
# full text; these are just labels for "what is still missing".
SHORT_LABEL = {
    "bias_align": "bias",
    "trend_align": "trend",
    "adx_strength": "ADX",
    "pullback_quality": "pullback",
    "momentum_trigger": "momentum",
    "candle_confirm": "candle",
    "structure": "structure",
    "session_vol": "session",
}

DIR_ICON = {"BUY": "🟢", "SELL": "🔴"}

# One emoji per target, escalating with how unlikely it is.
TP_ICON = ("🤡", "🥵", "💀")

# §22 lifecycle states. The signal carries whichever one it is actually in.
STATUS_ICON = {
    "WAIT": "⏳", "ACTIVE": "🟢", "NEAR": "⚠️", "BREAKEVEN": "🛡️",
    "COMPLETED": "✅", "STOPPED": "❌", "EXPIRED": "⌛", "CANCELLED": "🚫",
}


def _inst(res: dict) -> "I.Instrument":
    return I.BY_KEY.get(res.get("instrument") or "", I.GOLD)


def _p(x: float, inst: "I.Instrument" = None) -> str:
    return (inst or I.GOLD).fmt(x)


def _e(s) -> str:
    return html.escape(str(s))


def _cash(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 10 else f"${x:,.2f}"


def _subhead(res: dict, with_price: bool) -> str:
    """Mode, trigger timeframe, clock. Price too when no levels block follows,
    otherwise the Entry row already says it."""
    bits = [res["mode"].upper(), res["timeframes"]["entry"]]
    if with_price:
        bits.append(_p(res["price"], _inst(res)))
    bits.append(SESS.stamp(res["as_of"]))
    return " · ".join(bits)


def _levels_lines(res: dict, live: bool, lang: str = i18n.EN) -> str:
    """Entry, stop and every target, each labelled with what it is worth.

    A target line carries four facts: the price, how many R it is, how far
    away in the instrument's own units, and the modelled probability of
    getting there before the stop. The last of those comes from the barrier
    model corrected by backtest calibration — never from a number chosen to
    look good.
    """
    lv = res["levels"]
    inst = _inst(res)
    order = res.get("order") or {}
    entry_note = order.get("label") or (i18n.t("at_market", lang) if live
                                        else i18n.t("price_now", lang))
    if order.get("kind") == "market":
        entry_note = i18n.t("at_market", lang)

    lines = [
        f"📍 {i18n.t('entry', lang)} <b>{_e(_p(lv['entry'], inst))}</b> · {_e(entry_note)}",
        f"🛑 {i18n.t('stop', lang)} <b>{_e(_p(lv['stop'], inst))}</b> · "
        f"{_e(lv.get('risk_display') or lv['risk_points'])}",
        "",
    ]

    # A strategy that closes on the clock has no targets to show. Printing
    # TP1/2/3 for it would describe a trade it does not take — the position
    # is flat at the bell whatever the price is doing.
    bars = res.get("time_exit_bars")
    if bars:
        mins = bars * (base_TF_SECONDS.get(res["timeframes"]["entry"], 900) // 60)
        dur = f"{mins} min" if mins < 90 else f"{mins / 60:.1f} h"
        lines.append(f"⏱ {i18n.t('time_exit', lang, bars=bars, dur=dur)}")
        return "\n".join(lines)

    pr = res.get("probability") or {}
    odds = {round(float(row["r"]), 3): row["p"] for row in pr.get("targets", [])}
    pts = lv.get("tp_points_display") or []
    for n, (tp, m) in enumerate(zip(lv["tps"], lv["tp_multiples"])):
        icon = TP_ICON[n] if n < len(TP_ICON) else "🎯"
        bits = [f"{m:g}R"]
        if n < len(pts):
            bits.append(pts[n])
        p = odds.get(round(float(m), 3))
        if p is not None:
            bits.append(f"{p * 100:.0f}%")
        lines.append(f"{icon} TP{n + 1} <b>{_e(_p(tp, inst))}</b> · " + " · ".join(bits))
    return "\n".join(lines)


def _headline_numbers(res: dict, lang: str = i18n.EN) -> str:
    """One line. Confidence, the odds on the first target, expectancy."""
    conf = res.get("confidence") or {}
    pr = res.get("probability")
    bits = []
    if conf:
        bits.append(f"{i18n.t('confidence', lang)} <b>{int(conf.get('value', 0))}%</b>")
    if pr:
        bits.append(f"{i18n.t('odds', lang)} {pr['targets'][0]['p'] * 100:.0f}%")
        bits.append(f"{i18n.t('exp', lang)} {pr['expectancy_r']:+.2f}R")
    return " · ".join(bits)


def _context_line(res: dict, lang: str = i18n.EN) -> str:
    """Which book is open, and how much room the tape has. Both measured."""
    sess = SESS.current_session(res.get("as_of"))
    vol = SESS.classify_volatility(res.get("atr_ratio"))
    bits = []
    if sess:
        bits.append(f"{sess['emoji']} {i18n.label(sess['label'], lang)}")
    else:
        bits.append(f"🌙 {i18n.t('market_closed', lang)}")
    if vol:
        bits.append(f"{vol['emoji']} {i18n.label(vol['label'], lang)}")
    return " · ".join(bits)


def _disclaimer(lang: str = i18n.EN) -> str:
    """Bold on the word and on DYOR, underline on the sentence itself."""
    return (f"⚠️ <b>Disclaimer:</b> <u>{_e(i18n.t('disclaimer', lang))}</u> "
            f"<b>DYOR</b>.")


def _needs(res: dict, limit: int = 3) -> str:
    missing = [SHORT_LABEL.get(r["key"], r["key"])
               for r in res.get("reasons", []) if not r["ok"]]
    return " · ".join(missing[:limit])


# --------------------------------------------------------------------------- #
#  Compact — the default
# --------------------------------------------------------------------------- #
def _compact(symbol: str, res: dict, lang: str = i18n.EN) -> str:
    dec = res["decision"]
    status = res.get("status") or dec
    icon = STATUS_ICON.get(status, "⏳")

    if res["vetoes"]:
        out = f"🚫 <b>{i18n.t('no_trade', lang)} · {_e(symbol)}</b>\n"
        out += f"🕐 {_e(_subhead(res, with_price=True))}\n"
        out += _context_line(res, lang) + "\n\n"
        for v in res["vetoes"][:2]:
            out += f"• {_e(v)}\n"
        return out + "\n" + _disclaimer(lang) + "\n"

    direction = DIR_NAME[res["direction"]] if res["direction"] else ""

    if dec == "ENTRY" and status in ("ENTRY", "ACTIVE"):
        out = f"{DIR_ICON.get(direction, '🟢')} <b>{_e(direction)} · {_e(symbol)}</b>\n"
    elif dec == "WAIT":
        out = (f"{icon} <b>{i18n.t('wait', lang)} · {_e(direction)} "
               f"{i18n.t('setup', lang)} · {_e(symbol)}</b>\n")
    elif dec == "ENTRY":
        out = f"{icon} <b>{_e(status)} · {_e(direction)} · {_e(symbol)}</b>\n"
    else:
        out = f"🚫 <b>{i18n.t('no_trade', lang)} · {_e(symbol)}</b>\n"

    lv = res["levels"]
    has_plan = bool(lv) and dec in ("ENTRY", "WAIT")
    out += f"🕐 {_e(_subhead(res, with_price=not has_plan))}\n"
    out += _context_line(res, lang) + "\n\n"

    timed = bool(res.get("time_exit_bars"))
    if has_plan:
        out += _levels_lines(res, live=dec == "ENTRY", lang=lang) + "\n\n"
        risk_txt = res.get("risk_display") or _cash(lv["risk_cash"])
        # No reward-to-risk figure when there is no target to reach: the trade
        # is worth whatever the clock finds, and quoting 1:3 would describe a
        # payoff it never pursues.
        rr_txt = "" if timed else f" · 1:{max(lv['tp_multiples']):g} R:R"
        if lv.get("lots") is not None:
            out += (f"💰 <b>{M.fmt_lots(lv['lots'])} {i18n.t('lots', lang)}</b> · "
                    f"{_e(risk_txt)} {i18n.t('risk', lang)}{rr_txt}\n")
        else:
            out += f"💰 {_e(risk_txt)} {i18n.t('risk', lang)}{rr_txt}\n"
            if lv.get("lots_below_min"):
                out += (f"⚠️ <i>"
                        f"{_e(i18n.t('lot_too_small', lang, min=M.C.LOT_MIN))}</i>\n")

    if timed:
        # The probability model answers "does the target come before the
        # stop". With no target the question is meaningless, so confidence is
        # shown alone rather than dressed up with odds that describe a
        # different trade.
        conf = res.get("confidence") or {}
        if conf:
            out += (f"📊 {i18n.t('confidence', lang)} "
                    f"<b>{int(conf.get('value', 0))}%</b> · "
                    f"<i>{_e(i18n.t('no_odds_timed', lang))}</i>\n")
    else:
        nums = _headline_numbers(res, lang)
        if nums:
            out += f"📊 {nums}\n"

    # All-in-One must show its working: which strategy it handed off to and
    # what that choice was based on. A router that hides its reasoning is
    # just another opaque score.
    picked = res.get("auto_picked")
    if picked:
        m = res.get("auto_measured") or {}
        from strategies import REGISTRY as _REG
        name = _REG[picked].name if picked in _REG else picked
        out += (f"🤖 <i>{_e(name)} — measured {m.get('expectancy', 0):+.3f}R "
                f"over {m.get('trades', 0)} trades (t={m.get('t', 0):+.2f})</i>\n")

    order = res.get("order") or {}
    if dec != "ENTRY":
        needs = _needs(res)
        if needs:
            out += f"🔍 {i18n.t('needs', lang)}: {_e(needs)}\n"
    if dec == "WAIT" and (order.get("note_key") or order.get("note")):
        note = (i18n.t(order["note_key"], lang, **(order.get("note_args") or {}))
                if order.get("note_key") else order["note"])
        out += (f"ℹ️ <i>{_e(note[0].upper() + note[1:])}. "
                f"{_e(i18n.t('levels_move', lang))}</i>\n")
    elif dec == "WAIT":
        out += f"ℹ️ <i>{_e(i18n.t('not_live', lang))}</i>\n"

    if res.get("news_warning"):
        out += f"\n⚠️ <i>{_e(i18n.t('news_hour', lang))}</i>\n"

    return out + "\n" + _disclaimer(lang) + "\n"


# --------------------------------------------------------------------------- #
#  Verbose — everything, behind the Details button
# --------------------------------------------------------------------------- #
def _verbose(symbol: str, res: dict) -> str:
    mode = res["mode"].upper()
    tfs = res["timeframes"]
    out = f"<b>{_e(symbol)} · {_e(mode)}</b>\n"
    out += f"<code>{_p(res['price'], _inst(res))}</code> · candle closed "
    out += f"{res['as_of'].strftime('%H:%M')} UTC\n"
    out += (f"<i>{_e(tfs['entry'])} trigger / {_e(tfs['trend'])} trend / "
            f"{_e(tfs['bias'])} bias</i>\n\n")

    if res["vetoes"]:
        out += "🚫 <b>NO TRADE</b>\n"
        out += "Confidence <b>0%</b> · no probability quoted\n"
        out += "<i>Hard filter blocked this — no score calculated.</i>\n\n"
        for v in res["vetoes"]:
            out += f"• {_e(v)}\n"
        return out

    dec = res["decision"]
    icon = {"ENTRY": "✅", "WAIT": "⏳", "NO TRADE": "🚫"}[dec]
    out += f"{icon} <b>{dec}"
    if dec == "ENTRY":
        out += f" — {DIR_NAME[res['direction']]}"
    elif dec == "WAIT" and res["direction"]:
        out += f" — {DIR_NAME[res['direction']]} setup"
    out += "</b>\n"
    out += f"<pre>{_e(prob.read_block(res))}</pre>\n"

    drags = prob.drag_note(res)
    if drags:
        out += f"<i>{_e(drags)}</i>\n"
    basis = prob.basis_note(res)
    if basis:
        out += f"<i>Odds: {_e(basis)}</i>\n"
    out += "\n"

    lv = res["levels"]
    if lv and dec in ("ENTRY", "WAIT"):
        out += ("<b>Trade plan</b>\n" if dec == "ENTRY" else "<b>Provisional plan</b>\n")
        out += _levels_lines(res, live=dec == "ENTRY") + "\n"
        o = res.get("order") or {}
        if o.get("note"):
            out += f"<i>{_e(o['note'])}</i>\n"
        if lv.get("lots") is not None:
            out += f"💰 Size {lv['lots']} lots = {_cash(lv['risk_cash'])}\n"
        out += f"📐 ATR {lv['atr']} · stop {lv.get('stop_atr')}x ATR"
        if lv.get("risk_pct"):
            out += f", {lv['risk_pct']}% of price"
        out += "\n"
        if lv.get("stop_clamped"):
            out += ("<i>Stop capped at the mode's ceiling — the structural swing "
                    "sat further away than this timeframe justifies.</i>\n")
        if dec == "WAIT":
            out += ("<i>Not a live trade. These levels are recomputed from the "
                    "current price every time you ask, so they move until the "
                    "setup actually triggers.</i>\n")
        out += "\n"
    elif lv:
        out += (f"<i>If it triggers, stop would sit near {_p(lv['stop'], _inst(res))}, "
                f"{lv.get('risk_display') or lv['risk_points']} away.</i>\n\n")

    out += "<b>Scorecard</b>\n"
    for r in res["reasons"]:
        mark = "✓" if r["ok"] else "✗"
        out += f"{mark} {_e(r['text'])} <code>[{r['points']:.0f}/{r['max']}]</code>\n"

    if dec != "ENTRY" and res["missing"]:
        out += "\n<b>Waiting on</b>\n"
        for m in res["missing"][:4]:
            out += f"• {_e(m)}\n"

    if res.get("news_warning"):
        out += "\n⚠️ <i>High-impact US data often lands this hour. "
        out += "Check the calendar.</i>\n"

    return out


def render(symbol: str, res: dict, verbose: bool = False,
           lang: str = i18n.EN) -> str:
    return _verbose(symbol, res) if verbose else _compact(symbol, res, lang)


# --------------------------------------------------------------------------- #
#  Buttons. Returned as plain data so each front end can build its own type.
# --------------------------------------------------------------------------- #
def buttons(symbol_key: str, mode: str, verbose: bool = False) -> list[list[tuple[str, str]]]:
    """[(label, callback_data), ...] per row."""
    modes = [("Scalp", "scalp"), ("Intraday", "intraday"), ("Swing", "swing")]
    row1 = [(f"• {label} •" if m == mode else label, f"s|{symbol_key}|{m}|c")
            for label, m in modes]
    toggle = (("↩ Less", f"s|{symbol_key}|{mode}|c") if verbose
              else ("🔍 Details", f"s|{symbol_key}|{mode}|v"))
    row2 = [toggle, ("🔄 Refresh", f"s|{symbol_key}|{mode}|{'v' if verbose else 'c'}")]
    return [row1, row2]


# --------------------------------------------------------------------------- #
#  Command reference. One copy, both front ends.
# --------------------------------------------------------------------------- #
HELP = (
    "<b>BENIHANA — commands</b>\n\n"

    "<b>Signals</b>\n"
    "<code>/signal xauusd scalp</code> — one market, full read\n"
    "<code>/signal eurusd intraday</code> · <code>/signal gold swing</code>\n"
    "Modes: <b>scalp</b> 5m/15m/1h · <b>intraday</b> 15m/1h/4h · "
    "<b>swing</b> 4h/1D/1W\n"
    "<code>/crazymode</code> — scan every market and rank them\n"
    "<code>/symbols</code> — the 43 instruments it trades\n\n"

    "<b>Track record</b>\n"
    "<code>/stats xauusd</code> — what the bot has actually delivered\n"
    "<code>/calibration</code> — are the odds measured, or still guessed?\n"
    "<code>/backtest intraday</code> — replay the rules over history\n"
    "<code>/backtest intraday calibrate</code> — same run, and the measured "
    "odds replace the guess from then on\n\n"

    "<b>Alerts and news</b>\n"
    "<code>/alert xauusd scalp</code> — ping me when a setup appears\n"
    "<code>/alerts</code> — what you are subscribed to · "
    "<code>/alert clear</code> — stop them\n"
    "<code>/news xauusd</code> — calendar entries that move this market\n\n"

    "<b>Reference</b>\n"
    "<code>/strategy</code> — what the bot checks and why\n"
    "<code>/alerthelp</code> — why alerts need a scheduled task\n"
    "<code>/whoami</code> — your Telegram ID\n\n"

    "<b>Reading a signal</b>\n"
    "Three answers: <b>ENTRY</b> (executable now), <b>WAIT</b> (a setup "
    "forming), <b>NO TRADE</b>.\n\n"
    "The <b>Entry</b> row says how to place it:\n"
    "• <b>market</b> — every condition met, go now\n"
    "• <b>LIMIT</b> — rests in the pullback zone, fills if price comes back\n"
    "• <b>STOP</b> — beyond the last swing, fills only if the turn confirms\n\n"
    "Three percentages: <b>confluence</b> (how many rules agree), "
    "<b>confidence</b> (that score minus hazards the rules cannot see), "
    "<b>odds</b> (chance the first target trades before the stop).\n\n"
    "Tap <b>Details</b> on any signal for the full scorecard."
)
