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

import instruments as I
import probability as prob
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
    bits.append(res["as_of"].strftime("%H:%M UTC"))
    return " · ".join(bits)


def _levels_block(res: dict, live: bool) -> str:
    """The part people actually came for."""
    lv = res["levels"]
    inst = _inst(res)
    rows = [("Entry", lv["entry"], "market" if live else "price now"),
            ("Stop Loss", lv["stop"],
             lv.get("risk_display") or f"{lv['risk_points']} pts")]
    for n, (tp, m) in enumerate(zip(lv["tps"], lv["tp_multiples"]), start=1):
        rows.append((f"TP {n}", tp, f"{m:g}R"))
    width = max(len(r[0]) for r in rows)
    money = max(len(_p(v, inst)) for _, v, _ in rows)
    return "\n".join(f"{name:<{width}}  {_p(val, inst):>{money}}  {note}"
                      for name, val, note in rows)


def _headline_numbers(res: dict) -> str:
    """One line. Confidence, the odds on the first target, expectancy."""
    conf = res.get("confidence") or {}
    pr = res.get("probability")
    bits = []
    if conf:
        bits.append(f"Confidence <b>{int(conf.get('value', 0))}%</b>")
    if pr:
        bits.append(f"Odds {pr['targets'][0]['p'] * 100:.0f}%")
        bits.append(f"Exp {pr['expectancy_r']:+.2f}R")
    return " · ".join(bits)


def _needs(res: dict, limit: int = 3) -> str:
    missing = [SHORT_LABEL.get(r["key"], r["key"])
               for r in res.get("reasons", []) if not r["ok"]]
    return " · ".join(missing[:limit])


# --------------------------------------------------------------------------- #
#  Compact — the default
# --------------------------------------------------------------------------- #
def _compact(symbol: str, res: dict) -> str:
    dec = res["decision"]

    if res["vetoes"]:
        out = f"🚫 <b>NO TRADE · {_e(symbol)}</b>\n"
        out += f"<i>{_e(_subhead(res, with_price=True))}</i>\n\n"
        for v in res["vetoes"][:2]:
            out += f"{_e(v)}\n"
        return out

    direction = DIR_NAME[res["direction"]] if res["direction"] else ""

    if dec == "ENTRY":
        out = f"{DIR_ICON.get(direction, '🟢')} <b>{_e(direction)} · {_e(symbol)}</b>\n"
    elif dec == "WAIT":
        out = f"⏳ <b>WAIT · {_e(direction)} setup · {_e(symbol)}</b>\n"
    else:
        out = f"🚫 <b>NO TRADE · {_e(symbol)}</b>\n"
    lv = res["levels"]
    has_plan = bool(lv) and dec in ("ENTRY", "WAIT")
    out += f"<i>{_e(_subhead(res, with_price=not has_plan))}</i>\n\n"

    if has_plan:
        out += f"<pre>{_e(_levels_block(res, live=dec == 'ENTRY'))}</pre>\n"
        rr = max(lv["tp_multiples"])
        if lv.get("lots") is None:
            # A cross pays out in a currency we have no USD rate for. The R
            # multiples still hold; only the lot size is unknowable here.
            out += f"<i>Size it yourself — {_e(_inst(res).quote)} payout</i> · 1:{rr:g} R:R\n"
        else:
            out += (f"<b>{lv['lots']} lots</b> · {_cash(lv['risk_cash'])} risk "
                    f"· 1:{rr:g} R:R\n")

    nums = _headline_numbers(res)
    if nums:
        out += nums + "\n"

    if dec != "ENTRY":
        needs = _needs(res)
        if needs:
            out += f"<i>Needs: {_e(needs)}</i>\n"
    if dec == "WAIT":
        out += "<i>Not live — levels move until it triggers.</i>\n"

    if res.get("news_warning"):
        out += "\n⚠️ <i>High-impact US data often lands this hour.</i>\n"

    return out


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
        out += f"<pre>{_e(_levels_block(res, live=dec == 'ENTRY'))}\n"
        size = (f"{lv['lots']:>10} lots = {_cash(lv['risk_cash'])}" if lv.get("lots") is not None
                else f"{'—':>10} (cross pair, size it yourself)")
        out += f"{'Size':<9}  {size}\n"
        out += f"{'ATR':<9}  {lv['atr']:>10} pts</pre>\n"
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


def render(symbol: str, res: dict, verbose: bool = False) -> str:
    return _verbose(symbol, res) if verbose else _compact(symbol, res)


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
