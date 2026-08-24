"""
Telegram bot as a Flask web app, for PythonAnywhere free accounts.

Why webhooks instead of polling: a free account can't run a process forever, but
it CAN run a website forever. So we let Telegram call us. No sleeping, no cold
starts, no keep-alive pinger.

Deliberately uses only requests + flask + pandas + numpy, all preinstalled on
PythonAnywhere, so there is nothing to pip install.
"""
from __future__ import annotations

import html
import logging
import re
import time
import traceback
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

import config as C
import instruments as I
import journal
import probability as prob
import scanner
import i18n
import money
import motivation
import news
import strategies
import strategy as base
import subscriptions
import users
import view
from market_data import DataError, fetch_ohlc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("signalbot")

app = Flask(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram re-sends an update if we're slow to answer. Without this you'd get
# the same signal twice.
_seen_updates: set[int] = set()

# ...but marking an update seen BEFORE handling it is how a chat gets stuck on
# "⏳ Reading…" forever: PythonAnywhere kills a request that outruns its limits,
# the reply is never sent, Telegram retries, and the retry is thrown away as a
# duplicate. So an update in flight is tracked separately and expires. A retry
# that arrives after the worker died gets to run; a genuine double-delivery
# while we are still working does not.
_inflight: dict[int, float] = {}
INFLIGHT_TTL = 90.0        # seconds; Telegram gives up long before this


# --------------------------------------------------------------------------- #
#  Telegram helpers
# --------------------------------------------------------------------------- #
def tg(method: str, **payload):
    if not C.TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is empty — check pa_config.py")
        return None
    try:
        r = requests.post(
            API.format(token=C.TELEGRAM_BOT_TOKEN, method=method),
            json=payload,
            timeout=25,
        )
        if not r.ok:
            log.error("Telegram %s failed: %s", method, r.text[:300])
        return r.json()
    except requests.RequestException as exc:
        log.error("Telegram %s network error: %s", method, exc)
        return None


def _meter(used: float, total: float, width: int = 10,
           goal: bool = False) -> str:
    """A bar built from block characters. Telegram has no progress widget, and
    a number beside a filled bar is read at a glance where a bare number is
    read only if you stop to think about it.

    `goal` flips what a full bar means. Filling a drawdown limit is bad and
    filling a profit target is good, so the same bar cannot use the same
    colour for both without telling the user the opposite of the truth.
    """
    if total <= 0:
        return ""
    frac = max(0.0, min(1.0, used / total))
    filled = int(round(frac * width))
    if goal:
        dot = "🏁" if frac >= 1.0 else "🟢" if frac >= 0.6 else "⚪"
    else:
        dot = "🔴" if frac >= 0.85 else "🟠" if frac >= 0.6 else "🟢"
    return "▓" * filled + "░" * (width - filled) + " " + dot


def _pct_bar(frac: float, width: int = 10) -> str:
    filled = int(round(max(0.0, min(1.0, frac)) * width))
    return "▓" * filled + "░" * (width - filled)


def lang_of(user_id: int) -> str:
    return users.get(user_id).get("language", i18n.EN)


def _markup(symbol_key: str, mode: str, verbose: bool) -> dict:
    return {"inline_keyboard": [
        [{"text": lbl, "callback_data": cb} for lbl, cb in row]
        for row in view.buttons(symbol_key, mode, verbose)
    ]}


def send(chat_id: int, text: str, buttons: bool = False, symbol_key: str = "xauusd",
         mode: str = "intraday", verbose: bool = False):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = _markup(symbol_key, mode, verbose)
    return tg("sendMessage", **payload)


def edit(chat_id: int, message_id: int, text: str,
         buttons: bool = False, symbol_key: str = "xauusd",
         mode: str = "intraday", verbose: bool = False):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = _markup(symbol_key, mode, verbose)
    return tg("editMessageText", **payload)


def _ok(resp) -> bool:
    return bool(resp and resp.get("ok"))


def deliver(chat_id: int, message_id: int | None, text: str,
            symbol_key: str = "xauusd", mode: str = "intraday",
            verbose: bool = False) -> None:
    """Get `text` in front of the user, whatever it takes.

    Editing the placeholder is the nice outcome. If Telegram refuses — a stray
    angle bracket in a provider's error message is enough to fail HTML parsing
    — retry without markup, then as a fresh message. Anything is better than
    leaving the chat on "Reading…" with the reason buried in a log file.
    """
    if message_id and _ok(edit(chat_id, message_id, text, True, symbol_key,
                           mode, verbose)):
        return

    plain = re.sub(r"<[^>]+>", "", text)
    if message_id:
        log.error("edit failed, retrying without HTML")
        if _ok(tg("editMessageText", chat_id=chat_id, message_id=message_id,
                  text=plain, disable_web_page_preview=True)):
            return

    if _ok(send(chat_id, text, buttons=True, symbol_key=symbol_key,
            mode=mode, verbose=verbose)):
        return
    log.error("send failed too, falling back to plain text")
    tg("sendMessage", chat_id=chat_id, text=plain)


def allowed(user_id: int) -> bool:
    """The access gate.

    With subscriptions off this is exactly what it always was, so upgrading
    cannot lock out an existing deployment. With them on, the owner is
    always in and everyone else needs unexpired time on the clock.
    """
    if not C.SUBSCRIPTIONS_ENABLED:
        return (not C.ALLOWED_USER_IDS) or (user_id in C.ALLOWED_USER_IDS)
    return subscriptions.active(user_id)


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
MODE_WORDS = {"day": "intraday", "daytrade": "intraday", "intra": "intraday",
              "scalping": "scalp", "scalper": "scalp", "swings": "swing"}


def parse_args(parts: list[str], default_symbol: str = "xauusd",
               default_mode: str = "intraday") -> tuple[str, str, str | None]:
    """Any order, any spelling: '/signal gold scalp', '/signal nas100'.
    Third element is a complaint about an unrecognised word, or None."""
    symbol_key, mode, unknown = default_symbol, default_mode, None
    for raw in parts:
        a = raw.lower().strip().lstrip("/")
        if not a or a in ("calibrate", "off", "on", "clear", "stop"):
            continue
        if a in C.MODES:
            mode = a
        elif a in MODE_WORDS:
            mode = MODE_WORDS[a]
        else:
            inst = I.find(a)
            if inst:
                symbol_key = inst.key
            elif len(a) >= 2:
                unknown = raw
    return symbol_key, mode, unknown


HELP = view.HELP

STRATEGY = (
    "<b>What the bot checks</b>\n\n"
    "Only pullbacks inside an established trend. Three timeframes:\n"
    "• <b>Bias</b> — holds a veto. Wrong side, no trade.\n"
    "• <b>Trend</b> — picks direction; ADX must confirm a trend exists.\n"
    "• <b>Entry</b> — the trigger: pullback into the EMA20 zone, RSI or MACD "
    "turning back, confirming candle close.\n\n"
    "<b>Hard vetoes</b> (NO TRADE, no score):\n"
    "ADX under 15 · timeframe conflict · ATR over 2.5x or under 0.4x normal · "
    "RSI already extreme · stale data\n\n"
    "<b>Confluence</b>: 8 weighted conditions totalling 100. 70+ with a real "
    "trigger = ENTRY. 50–69 = WAIT. Under 50 = NO TRADE. Rule agreement, "
    "nothing more.\n\n"
    "<b>Confidence</b>: that score minus what the scorecard cannot see — news "
    "hours, dead sessions, short bias history, no room to the next swing. The "
    "signal lists every deduction.\n\n"
    "<b>Probability</b>: odds the target trades before the stop. Barrier maths "
    "first — a 1R stop against a kR target pays 1/(1+k) of the time in a "
    "driftless market, which is also your breakeven win rate — then a capped "
    "edge for the confluence score, minus costs and minus any swing sitting in "
    "the way. Backtest results override the model once you run "
    "/backtest intraday calibrate. Check /calibration to see which one you are "
    "reading.\n\n"
    "<b>The stop</b>: placed at the swing the trade is wrong beneath, but only "
    "inside the band the timeframe justifies — scalp 0.8-1.6x ATR, intraday "
    "1.0-2.2x, swing 1.5-2.8x. Structure further away than that gets capped, "
    "and the signal says so.\n\n"
    "<b>The order</b>: an ENTRY is executable at market. A WAIT is a plan for "
    "a price we are not at, so it quotes the order that gets you there — a "
    "LIMIT resting in the pullback zone, or a STOP beyond the last swing that "
    "fills only if the turn confirms. Stop and targets are measured from that "
    "order price, not from wherever price happens to be when you asked.\n\n"
    "Nobody has verified these rules are profitable. Run /backtest first."
)


def do_signal(chat_id: int, symbol_key: str, mode: str,
              message_id: int | None = None, verbose: bool = False,
              user_id: int = 0, risk_usd: float = None,
              risk_display: str = None):
    inst = I.BY_KEY.get(symbol_key, I.GOLD)
    symbol = inst.symbol
    spec = C.MODES[mode]
    lang = lang_of(user_id) if user_id else i18n.EN
    u = users.get(user_id) if user_id else users.DEFAULTS

    if message_id is None:
        placeholder = send(chat_id, f"⏳ Reading {inst.display} {mode}…")
        message_id = (placeholder or {}).get("result", {}).get("message_id")

    try:
        entry_df = fetch_ohlc(symbol, spec.entry_tf, spec.bars)
        trend_df = fetch_ohlc(symbol, spec.trend_tf, spec.bars)
        bias_df = fetch_ohlc(symbol, spec.bias_tf, spec.bars)
        res = strategies.evaluate(u["strategy"], entry_df, trend_df, bias_df,
                                  spec, datetime.now(timezone.utc),
                                  instrument=inst, risk_usd=risk_usd)
        res["user_id"] = user_id or None
        if risk_display:
            res["risk_display"] = risk_display

        # §13: a confidence floor suppresses the signal rather than dressing
        # it up. The user asked not to see these.
        floor = int(u.get("min_confidence") or 0)
        got = int((res.get("confidence") or {}).get("value") or 0)
        if floor and res["decision"] == "ENTRY" and got < floor:
            text = i18n.t("conf_below", lang, got=got, want=floor)
            deliver(chat_id, message_id, text, symbol_key, mode, verbose)
            return

        text = view.render(inst.display, res, verbose, lang)
        if journal.record(res) and user_id:
            users.update(user_id, day_trades=u["day_trades"] + 1)
    except DataError as exc:
        text = f"⚠️ <b>{i18n.t('data_problem', lang)}</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.error("signal failed: %s", traceback.format_exc())
        text = (f"⚠️ {i18n.t('unexpected', lang)}: "
                f"<code>{html.escape(type(exc).__name__)}</code>")

    deliver(chat_id, message_id, text, symbol_key, mode, verbose)


def _backtest_text(stats: dict, inst, mode: str, bars: int, tf: str,
                   lang: str) -> str:
    """The Telegram view of a backtest.

    backtest.summarise() also returns a monospace report, which is right for
    a console and wrong here: a beginner reading a column headed "pred" or
    "exp R" learns nothing. This keeps the same numbers and says what they
    mean, and it drops the two diagnostic tables entirely — whether the
    confidence score is monotone is a question for /calibration, not for
    somebody who just wants to know if the thing works.
    """
    n = stats["trades"]
    if not n:
        return f"{i18n.t('bt_title', lang)}\n━━━━━━━━━━━━━━━━━━━━\n\n" \
               + i18n.t("bt_none", lang)

    exp = stats["expectancy_r"]
    total = exp * n
    out = f"{i18n.t('bt_title', lang)}  ·  <b>{inst.display} {mode}</b>\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n"
    out += i18n.t("bt_intro", lang, bars=bars, tf=tf) + "\n\n"
    out += (i18n.t("bt_good", lang) if exp > 0 else i18n.t("bt_bad", lang)) + "\n\n"
    out += f"🔢 {i18n.t('bt_trades', lang)}: <b>{n}</b>\n"
    out += (f"🎯 {i18n.t('bt_win', lang)}: <b>{stats['win_rate']:.0%}</b>"
            f"  {_pct_bar(stats['win_rate'])}\n")
    out += f"📊 {i18n.t('bt_exp', lang)}: <b>{exp:+.2f}R</b>\n"
    out += f"💰 {i18n.t('bt_total', lang)}: <b>{total:+.1f}R</b>\n"
    out += f"📉 {i18n.t('bt_dd', lang)}: <b>{stats['max_dd_r']:.1f}R</b>\n"
    out += f"🤡 {i18n.t('bt_tp1', lang)}: <b>{stats['tp1_rate']:.0%}</b>\n\n"
    out += i18n.t("bt_r_note", lang)
    if n < 30:
        out += "\n\n" + i18n.t("bt_thin", lang, n=n)
    return out


def do_backtest(chat_id: int, symbol_key: str, mode: str,
                calibrate: bool = False, user_id: int = 0):
    inst = I.BY_KEY.get(symbol_key, I.GOLD)
    symbol = inst.symbol
    spec = C.MODES[mode]
    lang = lang_of(user_id)

    send(chat_id, i18n.t("bt_running", lang, sym=inst.display, mode=mode))
    try:
        from backtest import build_calibration, write_calibration
        from backtest import run as backtest_run

        # A free PythonAnywhere account gets 100 CPU-seconds a day and Telegram
        # gives a webhook about 60 seconds, so this is the one command here that
        # has to be sized against both. See config.BACKTEST_BARS.
        df = fetch_ohlc(symbol, spec.entry_tf, C.BACKTEST_BARS)
        stats, _report = backtest_run(df, mode)
        text = _backtest_text(stats, inst, mode, len(df), spec.entry_tf, lang)

        if calibrate:
            trades = stats.get("trade_log") or []
            if len(trades) < C.CALIBRATION_MIN_TRADES:
                text += (f"\n\n⚠️ Not calibrated: {len(trades)} trades is under "
                         f"the {C.CALIBRATION_MIN_TRADES}-trade minimum. This "
                         f"window was too quiet. For a bigger sample run it "
                         f"from a console:\n<code>python3 backtest.py --mode "
                         f"{mode} --live --calibrate</code>")
            else:
                entry = build_calibration(trades, mode, symbol, len(df), "telegram")
                write_calibration(entry, mode, symbol)
                text += (f"\n\n✅ Calibrated {mode} on {len(trades)} trades. "
                         f"Signals now quote measured odds — see /calibration.")
    except DataError as exc:
        text = f"⚠️ <b>{i18n.t('data_problem', lang)}</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.error("backtest failed: %s", traceback.format_exc())
        text = f"⚠️ Backtest error: <code>{html.escape(type(exc).__name__)}</code>"

    send(chat_id, text)


def do_calibration(chat_id: int, user_id: int):
    send(chat_id, prob.calibration_report(lang_of(user_id)))


# --------------------------------------------------------------------------- #
#  /stats — what the bot actually delivered
# --------------------------------------------------------------------------- #
def do_stats(chat_id: int, symbol_key: str, mode: str = None):
    # Settle anything outstanding first, so the numbers are current. Cheap
    # when nothing is open; skipped entirely if the provider is unhappy.
    try:
        journal.resolve(fetch_ohlc)
    except Exception:  # noqa: BLE001
        log.warning("journal resolve failed: %s", traceback.format_exc())
    send(chat_id, journal.format_stats(symbol_key, mode))


# --------------------------------------------------------------------------- #
#  /news — the real calendar, not a guess by the clock
# --------------------------------------------------------------------------- #
def do_scan(chat_id: int, user_id: int, mode: str, keys: list = None,
            message_id: int | None = None):
    keys = keys or C.SCAN_SYMBOLS
    keys = [k for k in keys if k in I.BY_KEY][:C.CRAZY_MAX_SYMBOLS]
    if not keys:
        send(chat_id, "Nothing to scan — check SCAN_SYMBOLS in config.")
        return

    placeholder = send(chat_id, f"⚔️ Scanning {len(keys)} markets on {mode}…")
    mid = (placeholder or {}).get("result", {}).get("message_id")

    try:
        strat = users.get(user_id)["strategy"]
        result = scanner.scan(keys, mode, fetch_ohlc, log=log.warning,
                              strategy=strat)
        text = scanner.format_scan(result)
        for res in scanner.tradeable(result["rows"]):
            journal.record(res)
    except Exception:  # noqa: BLE001
        log.error("scan failed: %s", traceback.format_exc())
        text = "⚠️ Scan failed. Check the log."
    deliver(chat_id, mid, text, "xauusd", mode)


# --------------------------------------------------------------------------- #
#  /symbols — what it can trade
# --------------------------------------------------------------------------- #
def do_symbols(chat_id: int, lang: str = i18n.EN):
    """Grouped, with what each one costs to trade — because the spread is the
    number that decides whether a pair is worth taking at all."""
    ICON = {"Forex": "💱", "Metals": "🥇"}
    out = i18n.t("sym_title", lang) + "\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n\n"
    for label, items in I.grouped().items():
        if not items:
            continue
        out += f"{ICON.get(label, '•')} <b>{label}</b>  <i>({len(items)})</i>\n"
        names = [i.display for i in items]
        for n in range(0, len(names), 4):
            out += "   " + " · ".join(names[n:n + 4]) + "\n"
        out += "\n"
    g = I.GOLD
    out += (f"<b>── {i18n.t('sym_cost', lang)} ──</b>\n"
            f"🥇 {g.display}  spread {g.spread:g} pts\n"
            f"💱 majors  ~{I.BY_KEY['eurusd'].spread / I.BY_KEY['eurusd'].pip:.1f} pips\n\n")
    out += i18n.t("sym_nicknames", lang)
    send(chat_id, out)



# --------------------------------------------------------------------------- #
#  Settings commands
# --------------------------------------------------------------------------- #
def do_language(chat_id: int, user_id: int, args: list[str]):
    want = (args[0].lower() if args else "")
    if want in ("english", "en", "inggris"):
        users.update(user_id, language=i18n.EN)
        send(chat_id, i18n.t("lang_set", i18n.EN))
    elif want in ("bahasa", "indonesia", "id", "indonesian"):
        users.update(user_id, language=i18n.ID)
        send(chat_id, i18n.t("lang_set", i18n.ID))
    else:
        send(chat_id, i18n.t("lang_usage", lang_of(user_id)))


def do_setconf(chat_id: int, user_id: int, args: list[str]):
    lang = lang_of(user_id)
    raw = (args[0].lower().rstrip("%") if args else "")
    if raw in ("off", "0", "none"):
        users.update(user_id, min_confidence=0)
        send(chat_id, f"{i18n.t('conf_off_title', lang)}\n"
                      f"━━━━━━━━━━━━━━━━━━━━\n"
                      f"{i18n.t('conf_cleared', lang)}")
        return
    try:
        n = int(raw)
        if not 0 <= n <= 99:
            raise ValueError
    except ValueError:
        send(chat_id, i18n.t("conf_usage", lang))
        return
    users.update(user_id, min_confidence=n)
    band = i18n.t("conf_band_relaxed" if n < 50 else
                  "conf_band_balanced" if n < 70 else
                  "conf_band_strict" if n < 85 else
                  "conf_band_very_strict", lang)
    send(chat_id, f"{i18n.t('conf_title', lang)}\n"
                  f"━━━━━━━━━━━━━━━━━━━━\n"
                  f"<b>{n}%</b>  {_pct_bar(n / 100)}\n"
                  f"{band}\n\n{i18n.t('conf_set', lang, n=n)}")


def do_strategy(chat_id: int, user_id: int, args: list[str]):
    lang = lang_of(user_id)
    u = users.get(user_id)
    pick = (args[0].strip().lower() if args else "")

    chosen = None
    if pick.isdigit() and 1 <= int(pick) <= len(strategies.ORDER):
        chosen = strategies.ORDER[int(pick) - 1]
    elif pick in strategies.REGISTRY:
        chosen = pick
    elif pick:
        for k, s in strategies.REGISTRY.items():
            if pick in s.name.lower():
                chosen = k
                break

    if chosen:
        users.update(user_id, strategy=chosen)
        s = strategies.REGISTRY[chosen]
        send(chat_id, f"⚔️ <b>{html.escape(s.name)}</b> "
                      f"{i18n.t('strategy_selected', lang)}")
        return

    out = f"⚔️ <b>{i18n.t('strategies_title', lang)}</b>\n\n"
    for n, key in enumerate(strategies.ORDER, start=1):
        s = strategies.REGISTRY[key]
        mark = "  ✅" if key == u["strategy"] else ""
        blurb = s.blurb_id if lang == i18n.ID else s.blurb_en
        best = s.best_id if lang == i18n.ID else s.best_en
        out += f"{s.icon} <b>{n}. {html.escape(s.name)}</b>{mark}\n"
        if best:
            out += f"   <i>{html.escape(best)}</i>\n"
        out += f"   {html.escape(blurb)}\n\n"
    out += i18n.t("strategy_howto", lang)
    send(chat_id, out)


def do_motivation(chat_id: int, user_id: int):
    send(chat_id, f"💭 <i>{html.escape(motivation.pick(user_id, lang_of(user_id)))}</i>")


def do_settings(chat_id: int, user_id: int):
    lang = lang_of(user_id)
    u = users.get(user_id)
    s = strategies.REGISTRY[u["strategy"]]
    m = u.get("management") or {}
    lines = [f"⚙️ <b>{i18n.t('settings_title', lang)}</b>", ""]
    lines.append(f"🌐 {i18n.t('language', lang)}: "
                 f"{'English' if u['language'] == i18n.EN else 'Bahasa Indonesia'}")
    lines.append(f"⚔️ {i18n.t('strategy', lang)}: {html.escape(s.name)}")
    lines.append(f"📊 {i18n.t('min_conf', lang)}: "
                 + (f"{u['min_confidence']}%" if u["min_confidence"] else i18n.t("off", lang)))
    risk = u.get("risk_amount")
    lines.append(f"💰 {i18n.t('default_risk', lang)}: "
                 + (money.fmt(risk["value"], risk["currency"]) if risk
                    else i18n.t("not_set", lang)))
    lines.append(f"🛡️ {i18n.t('management', lang)}: "
                 + (i18n.t("on", lang) if m.get("enabled") else i18n.t("off", lang)))
    send(chat_id, "\n".join(lines))


def do_status(chat_id: int, user_id: int):
    lang = lang_of(user_id)
    u = users.get(user_id)
    m = u.get("management") or {}
    live = journal.active_signals(user_id=user_id)
    s = strategies.REGISTRY.get(u["strategy"])
    today = journal.period_stats("daily", user_id)

    out = "📡 <b>BENIHANA STATUS</b>\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n\n"
    out += f"{s.icon} <b>{html.escape(s.name)}</b>\n"
    out += (f"🌐 {'English' if u['language'] == i18n.EN else 'Bahasa Indonesia'}"
            f"   ·   🎯 " +
            (f"{u['min_confidence']}%+" if u["min_confidence"]
             else i18n.t("st_no_filter", lang)) +
            "\n\n")

    out += f"<b>── {i18n.t('upd_live', lang)} ──</b>\n"
    out += f"📶 {i18n.t('active_signals', lang)}: <b>{len(live)}</b>\n"
    if today["trades"]:
        net = today["net"]
        out += (f"{'🟩' if net >= 0 else '🟥'} "
                + i18n.t("st_today", lang, n=today["trades"])
                + f" · {today['total_r']:+.2f}R · {'+' if net >= 0 else '-'}"
                  f"${abs(net):,.2f}\n")
    else:
        out += f"⚪ {i18n.t('st_nothing_today', lang)}\n"

    if m.get("enabled"):
        used, cap = u["day_trades"], m["max_daily_trades"]
        limit = abs(m["balance_usd"] * m["daily_dd_pct"] / 100.0)
        worst = abs(users.max_drawdown_today(u))
        gained = m["balance_usd"] - m["start_balance_usd"]
        target = m["start_balance_usd"] * m["profit_target_pct"] / 100.0
        out += f"\n<b>── 🛡️ {i18n.t('management', lang)} ──</b>\n"
        out += f"💰 {i18n.t('balance', lang)}: <b>${m['balance_usd']:,.2f}</b>\n"
        out += (f"🔢 {i18n.t('st_trades', lang)}  {used}/{cap}   "
                f"{_meter(used, cap)}\n")
        out += (f"📉 {i18n.t('st_drawdown', lang)}  "
                f"${worst:,.0f}/${limit:,.0f}   {_meter(worst, limit)}\n")
        out += (f"🎯 {i18n.t('st_target', lang)}  "
                f"${max(gained, 0):,.0f}/${target:,.0f}   "
                f"{_meter(max(gained, 0), target, goal=True)}\n")
    else:
        out += f"\n🛡️ {i18n.t('management', lang)}: {i18n.t('off', lang)}\n"
        out += i18n.t("st_set_limits", lang) + "\n"
    send(chat_id, out)


def do_signals_list(chat_id: int, user_id: int):
    lang = lang_of(user_id)
    live = journal.active_signals(user_id=user_id)
    if not live:
        send(chat_id, i18n.t("no_active", lang))
        return
    out = f"📡 <b>{i18n.t('active_signals', lang)}</b>\n\n"
    for r in live:
        inst = I.BY_KEY.get(r.get("instrument") or "")
        name = inst.display if inst else (r.get("instrument") or "?").upper()
        state = journal.STATUS_LABEL.get(r.get("state"), "WAIT")
        icon = view.STATUS_ICON.get(state, "⏳")
        out += (f"{icon} <b>{name}</b> · {r['mode'].upper()} · "
                f"{base.DIR_NAME.get(r['direction'], '?')}\n"
                f"   {i18n.t('entry', lang)} {r['entry']} · "
                f"{i18n.t('stop', lang)} {r['stop']}\n")
    send(chat_id, out)


def do_history(chat_id: int, user_id: int):
    lang = lang_of(user_id)
    rows = [r for r in journal._load()
            if r.get("r") is not None and r.get("user_id") in (None, user_id)]
    if not rows:
        send(chat_id, f"📜 <b>{i18n.t('history_title', lang)}</b>\n\n"
                      f"{i18n.t('no_history', lang)}")
        return
    rows = rows[-12:][::-1]
    rs = [float(r["r"]) for r in rows]
    strip = "".join("🟩" if r > 0 else "🟥" for r in rs)

    out = f"📜 <b>{i18n.t('history_title', lang)}</b>\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n"
    out += f"{strip}\n"
    out += i18n.t("hist_summary", lang,
                   w=sum(1 for r in rs if r > 0),
                   l=sum(1 for r in rs if r <= 0),
                   r=f"{sum(rs):+.2f}", n=len(rs)) + "\n\n"
    for r in rows:
        inst = I.BY_KEY.get(r.get("instrument") or "")
        name = inst.display if inst else (r.get("instrument") or "?").upper()
        rr = float(r["r"])
        st = strategies.REGISTRY.get(r.get("strategy") or "")
        when = str(r.get("resolved_ts") or r.get("ts") or "")[:10]
        out += (f"{'🟩' if rr > 0 else '🟥'} <b>{name}</b> {r['mode'].upper()}"
                f" · <b>{rr:+.2f}R</b>\n"
                f"   {st.icon if st else '·'} {st.name if st else ''} · {when}\n")
    send(chat_id, out)


def do_period(chat_id: int, user_id: int, period: str):
    send(chat_id, journal.format_period(period, user_id, lang_of(user_id)))


# --------------------------------------------------------------------------- #
#  Risk and money management (§17)
# --------------------------------------------------------------------------- #
def do_management(chat_id: int, user_id: int, args: list[str]):
    lang = lang_of(user_id)
    sub = (args[0].lower() if args else "")

    if sub == "off":
        users.management_off(user_id)
        send(chat_id, f"🛡️ {i18n.t('mgmt_off', lang)}")
        return

    if sub != "on":
        send(chat_id, i18n.t("mgmt_form", lang))
        return

    rest = args[1:]
    if len(rest) < 5:
        send(chat_id, i18n.t("mgmt_form", lang))
        return

    try:
        bal_value, bal_ccy = money.parse_amount(rest[0])
        risk_pct = float(rest[1].rstrip("%"))
        dd_pct = float(rest[2].rstrip("%"))
        max_trades = int(float(rest[3]))
        target_pct = float(rest[4].rstrip("%"))
    except (money.MoneyError, ValueError):
        send(chat_id, i18n.t("mgmt_form", lang))
        return

    balance_usd = money.to_usd(bal_value, bal_ccy)
    if balance_usd is None:
        send(chat_id, i18n.t("fx_unavailable", lang, ccy=bal_ccy))
        return
    if not (0 < risk_pct <= 100 and 0 < dd_pct <= 100
            and 0 < max_trades <= 100 and 0 < target_pct <= 1000):
        send(chat_id, i18n.t("mgmt_form", lang))
        return

    mgmt = users.management_defaults(balance_usd, risk_pct, dd_pct,
                                     max_trades, target_pct)
    mgmt["currency"] = bal_ccy
    mgmt["balance_display"] = money.fmt(bal_value, bal_ccy)
    users.management_on(user_id, mgmt)
    per_trade = balance_usd * risk_pct / 100.0
    send(chat_id, i18n.t("mgmt_on", lang,
                         balance=money.fmt(bal_value, bal_ccy),
                         risk=f"{risk_pct:g}", per_trade=money.fmt(per_trade, "USD"),
                         dd=f"{dd_pct:g}",
                         dd_cash=money.fmt(balance_usd * dd_pct / 100.0, "USD"),
                         trades=max_trades, target=f"{target_pct:g}",
                         target_cash=money.fmt(balance_usd * target_pct / 100.0,
                                               "USD")))


def management_gate(chat_id: int, user_id: int) -> bool:
    """True if a signal may be issued. Refuses and explains when it may not."""
    lang = lang_of(user_id)
    u = users.get(user_id)
    m = u.get("management") or {}
    if not m.get("enabled"):
        return True

    if u["day_trades"] >= m["max_daily_trades"]:
        send(chat_id, i18n.t("mgmt_max_trades", lang,
                             n=u["day_trades"], max=m["max_daily_trades"]))
        return False

    dd_limit = -abs(m["balance_usd"] * m["daily_dd_pct"] / 100.0)
    if u["day_pl_usd"] <= dd_limit:
        send(chat_id, i18n.t("mgmt_drawdown", lang,
                             pl=f"{u['day_pl_usd']:+,.2f}",
                             limit=f"{dd_limit:,.2f}"))
        return False
    return True


def check_profit_target(user_id: int) -> str | None:
    """§17: hitting the target switches management off and says so. It is
    never switched back on automatically — that is the user's decision."""
    u = users.get(user_id)
    m = u.get("management") or {}
    if not m.get("enabled"):
        return None
    target = m["start_balance_usd"] * m["profit_target_pct"] / 100.0
    gained = m["balance_usd"] - m["start_balance_usd"]
    if gained < target:
        return None
    # Target reached: the envelope is deleted, not disabled. Re-enabling
    # would need /management on with fresh numbers, which is the point —
    # the old balance is out of date the moment the target is hit.
    users.management_off(user_id)
    return i18n.t("mgmt_target_hit", lang_of(user_id),
                  target=f"{m['profit_target_pct']:g}",
                  profit=f"{gained:,.2f}",
                  start=f"{m['start_balance_usd']:,.2f}")


# --------------------------------------------------------------------------- #
#  /signal — the command, with everything that guards it
# --------------------------------------------------------------------------- #
def do_signal_command(chat_id: int, user_id: int, args: list[str]):
    lang = lang_of(user_id)
    u = users.get(user_id)

    # 1. an explicit risk clause wins over anything stored
    try:
        risk, rest = money.parse_risk_args(list(args))
    except money.MoneyError as exc:
        send(chat_id, i18n.t("risk_unreadable", lang, raw=html.escape(str(exc))))
        return

    symbol_key, mode, bad = parse_args(rest)
    if bad:
        send(chat_id, i18n.t("unknown_symbol", lang, what=html.escape(bad)))
        return

    # 2. risk management can refuse outright
    if not management_gate(chat_id, user_id):
        return

    # 3. one live signal per pair AND style — a swing never blocks a scalp
    live = journal.active_signals(instrument=symbol_key, mode=mode,
                                  user_id=user_id)
    if live:
        r = live[0]
        inst = I.BY_KEY.get(symbol_key)
        send(chat_id, i18n.t("cooldown", lang,
                             mode=mode, pair=inst.display if inst else symbol_key,
                             state=journal.STATUS_LABEL.get(r.get("state"), "WAIT"),
                             sid=r["id"]))
        return

    # 4. resolve the risk amount into USD, refusing rather than guessing
    risk_usd = None
    risk_display = None
    if risk is None and u.get("risk_amount"):
        risk = (u["risk_amount"]["value"], u["risk_amount"]["currency"])
    if risk is None:
        mgmt_risk = users.risk_per_trade_usd(u)
        if mgmt_risk is not None:
            risk_usd, risk_display = mgmt_risk, money.fmt(mgmt_risk, "USD")
    else:
        value, ccy = risk
        risk_usd = money.to_usd(value, ccy, fetch=fetch_ohlc)
        if risk_usd is None:
            send(chat_id, i18n.t("fx_unavailable", lang, ccy=ccy))
            return
        risk_display = money.fmt(value, ccy)
        users.update(user_id, risk_amount={"value": value, "currency": ccy})

    do_signal(chat_id, symbol_key, mode, user_id=user_id, risk_usd=risk_usd,
              risk_display=risk_display)


def do_start(chat_id: int, user_id: int):
    """The first screen. Deliberately reachable without a subscription.

    A stranger's very first message is /start, and at that point they have no
    access and no idea what the bot is. Answering "not authorised" teaches
    them nothing and gives them no way to ask. So: pick a language, then a
    welcome that says what this is and — if they cannot use it yet — the one
    piece of information they need, which is their own ID.
    """
    tg("sendMessage", chat_id=chat_id,
       text=i18n.t("start_pick", lang_of(user_id)), parse_mode="HTML",
       reply_markup={"inline_keyboard": [[
           {"text": "🇬🇧 English", "callback_data": "lang|en"},
           {"text": "🇮🇩 Bahasa Indonesia", "callback_data": "lang|id"},
       ]]})


def send_welcome(chat_id: int, user_id: int, lang: str):
    out = i18n.t("welcome", lang) + "\n\n"
    out += (i18n.t("welcome_open", lang) if allowed(user_id)
            else i18n.t("welcome_locked", lang, uid=user_id))
    send(chat_id, out)


def help_text(lang: str = i18n.EN) -> str:
    """Every command with one line saying what it is for.

    A bare list of slash commands tells a new user nothing — they can see the
    names, what they cannot see is which one to type first or what /setconf
    even means. So each line is command + purpose, in their language.
    """
    t = lambda k: i18n.t(k, lang)                    # noqa: E731
    L = ["⚔️ <b>BENIHANA COMMANDS</b>", ""]

    def block(title, rows):
        L.append(f"<b>{title}</b>")
        for cmd, key in rows:
            L.append(f"<code>{cmd}</code>\n   <i>{t(key)}</i>")
        L.append("")

    block(f"📡 {t('sec_signals')}", [
        ("/signal xauusd intraday", "h_signal"),
        ("/signal xauusd scalp risk 20$", "h_signal_risk"),
        ("/scan", "h_scan"),
        ("/strategy", "h_strategy"),
        ("/setconf 80", "h_setconf"),
        ("/symbols", "h_symbols"),
        ("/cancel &lt;id&gt;", "h_cancel"),
    ])
    block(f"📶 {t('sec_open')}", [
        ("/update", "h_update"),
        ("/swingupdate", "h_swingupdate"),
        ("/signals", "h_signals"),
    ])
    block(f"📊 {t('sec_performance')}", [
        ("/daily  /weekly  /monthly", "h_periods"),
        ("/history", "h_history"),
        ("/stats xauusd", "h_stats"),
        ("/backtest intraday", "h_backtest"),
        ("/calibration", "h_calibration"),
    ])
    block(f"🛡️ {t('sec_risk')}", [
        ("/management on 1000$ 1 5 5 5", "h_management_on"),
        ("/management off", "h_management_off"),
    ])
    block(f"⚙️ {t('sec_settings')}", [
        ("/language english", "h_language"),
        ("/settings", "h_settings"),
        ("/status", "h_status"),
        ("/subscription", "h_subscription"),
        ("/resetdata", "h_resetdata"),
    ])
    block(f"💬 {t('sec_other')}", [
        ("/news", "h_news"),
        ("/motivation", "h_motivation"),
        ("/help", "h_help"),
    ])
    L.append(f"<i>{t('help_footer')}</i>")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
#  /update and /swingupdate — where the live signals stand
# --------------------------------------------------------------------------- #
STATE_ICON = {"waiting": "⏳", "active": "🟢", "breakeven": "🛡️",
              "tp1": "🤡", "tp2": "🥵", "tp3": "💀",
              "stopped": "❌", "completed": "✅",
              "expired": "⌛", "cancelled": "🚫"}
# Lifecycle wording lives in the translation table; this maps a state to its
# key so an Indonesian user does not get an English status line.
STATE_KEY = {"waiting": "upd_st_waiting", "active": "upd_st_active",
             "breakeven": "upd_st_breakeven", "stopped": "upd_st_stopped",
             "completed": "upd_st_completed", "expired": "upd_st_expired",
             "cancelled": "upd_st_cancelled", "tp1": "upd_st_tp1",
             "tp2": "upd_st_tp2", "tp3": "upd_st_tp3"}


def _signal_line(r, lang) -> str:
    inst = I.BY_KEY.get(r.get("instrument") or "")
    name = inst.display if inst else str(r.get("instrument", "?")).upper()
    st = r.get("state") or "waiting"
    icon = STATE_ICON.get(st, "•")
    side = "BUY" if r.get("direction", 0) > 0 else "SELL"
    hit = r.get("tps_hit") or []
    strat = strategies.REGISTRY.get(r.get("strategy") or "")
    sname = f" · {strat.icon} {strat.name}" if strat else ""

    out = f"{icon} <b>{html.escape(name)}</b> {side} · {r.get('mode','').upper()}{sname}\n"
    key = STATE_KEY.get(st)
    out += f"   {i18n.t(key, lang) if key else st}"
    if hit:
        out += (" · " + " ".join(STATE_ICON.get(f"tp{n}", "🎯") for n in hit)
                + " " + i18n.t("upd_hit", lang))
    out += "\n"

    fmt = inst.fmt if inst else (lambda v: f"{v}")
    # Whether the stop has been moved to entry is the single most useful fact
    # about a running trade — it is the difference between still risking a
    # full R and risking nothing — so it gets its own line rather than being
    # inferred from a stop price that happens to equal the entry.
    moved = r.get("stop_moved")
    out += f"   📍 {fmt(r['entry'])}  🛑 {fmt(moved or r['stop'])}\n"
    if moved and st not in journal.FINAL_STATES:
        out += (f"   🛡️ <b>{i18n.t('upd_be_moved', lang)}</b>"
                f"  <i>({i18n.t('upd_be_orig', lang)} {fmt(r['stop'])})</i>\n")
    tps = r.get("tps") or []
    nxt = [n for n in range(1, len(tps) + 1) if n not in hit]
    if nxt and st not in journal.FINAL_STATES:
        n = nxt[0]
        out += (f"   {i18n.t('upd_next', lang)} "
                    f"{STATE_ICON.get(f'tp{n}','🎯')} TP{n} {fmt(tps[n-1])}\n")
    if r.get("r") is not None:
        rr = float(r["r"])
        out += f"   {'🟩' if rr > 0 else '🟥'} <b>{rr:+.2f}R</b>\n"
    return out


def do_update(chat_id: int, user_id: int, swing: bool):
    """Swing runs for days; scalps resolve in minutes. Mixing them in one
    list buries the swing trade under this morning's noise, so they get
    separate commands."""
    lang = lang_of(user_id)
    rows = journal.lifecycle_view(
        user_id, modes=["swing"] if swing else None,
        exclude_modes=None if swing else ["swing"])
    title = i18n.t("upd_swing_title" if swing else "upd_title", lang)
    head = f"{'🐢' if swing else '📡'} <b>{title}</b>\n\n"

    if not rows:
        send(chat_id, head + i18n.t("upd_none_swing" if swing else "upd_none", lang))
        return

    live = [r for r in rows if r.get("state") not in journal.FINAL_STATES]
    done = [r for r in rows if r.get("state") in journal.FINAL_STATES][:6]

    out = head
    if live:
        out += f"<b>── {i18n.t('upd_live', lang)} ({len(live)}) ──</b>\n\n"
        out += "\n".join(_signal_line(r, lang) for r in live) + "\n"
    if done:
        out += f"<b>── {i18n.t('upd_done', lang)} ──</b>\n\n"
        out += "\n".join(_signal_line(r, lang) for r in done)
    out += f"\n<i>{i18n.t('upd_hint', lang)}</i>"
    send(chat_id, out)


def do_resetdata(chat_id: int, user_id: int, args):
    lang = lang_of(user_id)
    confirmed = args and args[0].lower() in ("yes", "confirm", "ya")
    if not confirmed:
        n = len(journal.lifecycle_view(user_id))
        if not n:
            send(chat_id, i18n.t("reset_empty", lang))
            return
        send(chat_id, i18n.t("reset_confirm", lang))
        return
    res = journal.wipe(user_id)
    users.update(user_id, day_trades=0, day_pl_usd=0.0, day_peak_usd=0.0)
    if not res["removed"]:
        send(chat_id, i18n.t("reset_empty", lang))
        return
    send(chat_id, i18n.t("reset_done", lang, n=res["removed"]))


def do_news(chat_id: int, user_id: int):
    """Event risk from calendar rules plus whatever the user listed.

    Deliberately small. A bot that cannot fetch a calendar should say what it
    knows and say what it does not, rather than imply coverage it has no way
    to provide.
    """
    lang = lang_of(user_id)
    now = datetime.now(timezone.utc)

    out = f"{i18n.t('news_title', lang)}\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n\n"

    hot = news.blackout(now)
    if hot:
        hot_name = i18n.t(f"ev_{hot.key}", lang) if hot.key else hot.name
        out += i18n.t("news_blackout", lang, name=html.escape(hot_name),
                      before=news.BLACKOUT_BEFORE,
                      after=news.BLACKOUT_AFTER) + "\n\n"
    else:
        out += i18n.t("news_clear", lang) + "\n\n"

    events = news.upcoming(now, days=14)
    if not events:
        out += i18n.t("news_none", lang, days=14) + "\n\n"
    else:
        for e in events:
            dot = "🔴" if e.impact == news.HIGH else "🟠"
            tag = "" if e.source == "rule" else " ·<i>yours</i>"
            name = i18n.t(f"ev_{e.key}", lang) if e.key else e.name
            note = i18n.t(f"ev_{e.key}_note", lang) if e.key else e.note
            dkey, dfields = news.delta_parts(now, e.when_utc)
            out += (f"{dot} <b>{html.escape(name)}</b>{tag}\n"
                    f"   {i18n.date_short(e.wib, lang)} · "
                    f"{e.wib:%H:%M} UTC+7  "
                    f"<i>({i18n.t(dkey, lang, **dfields)})</i>\n")
            if note:
                out += f"   <i>{html.escape(note)}</i>\n"
        out += "\n"

    if not news.TZ_EXACT:
        out += i18n.t("news_tz_approx", lang) + "\n\n"
    out += i18n.t("news_howto", lang)
    send(chat_id, out)


# --------------------------------------------------------------------------- #
#  Subscriptions — access, not payment
# --------------------------------------------------------------------------- #
def do_subscription(chat_id: int, user_id: int):
    """What the caller's own access looks like."""
    lang = lang_of(user_id)
    out = f"{i18n.t('sub_yours', lang)}\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n\n"

    if subscriptions.is_owner(user_id):
        out += i18n.t("sub_owner", lang) + "\n"
    else:
        until = subscriptions.expires_at(user_id)
        left = subscriptions.days_left(user_id)
        if until and left > 0:
            out += i18n.t("sub_active", lang,
                          until=i18n.date_long(
                              until.astimezone(news.WIB), lang)
                          + until.astimezone(news.WIB).strftime(", %H:%M")
                          + " UTC+7",
                          days=f"{left:.1f}") + "\n"
            if left <= 5:
                out += "\n" + i18n.t("sub_soon", lang, days=f"{left:.1f}") + "\n"
        else:
            out += i18n.t("sub_expired", lang, uid=user_id) + "\n"

    if not C.SUBSCRIPTIONS_ENABLED:
        out += "\n" + i18n.t("sub_off", lang)
    send(chat_id, out)


def _owner_only(chat_id: int, user_id: int) -> bool:
    if subscriptions.is_owner(user_id):
        return True
    send(chat_id, i18n.t("sub_owner_only", lang_of(user_id)))
    return False


def do_grant(chat_id: int, user_id: int, args: list[str]):
    lang = lang_of(user_id)
    if not _owner_only(chat_id, user_id):
        return
    if len(args) < 2 or not args[0].lstrip("-").isdigit():
        send(chat_id, i18n.t("sub_grant_usage", lang))
        return
    try:
        days = float(args[1])
        if not 0 < days <= 3650:
            raise ValueError
    except ValueError:
        send(chat_id, i18n.t("sub_grant_usage", lang))
        return

    target = int(args[0])
    plan = args[2] if len(args) > 2 else "standard"
    rec = subscriptions.grant(target, days, plan, granted_by=user_id)
    until = datetime.fromisoformat(rec["until"]).astimezone(news.WIB)
    send(chat_id, i18n.t("sub_granted", lang, days=f"{days:g}", uid=target,
                         until=i18n.date_long(until, lang)
                         + until.strftime(", %H:%M") + " UTC+7"))


def do_revoke(chat_id: int, user_id: int, args: list[str]):
    lang = lang_of(user_id)
    if not _owner_only(chat_id, user_id):
        return
    if not args or not args[0].lstrip("-").isdigit():
        send(chat_id, "Usage: <code>/revoke &lt;user_id&gt;</code>")
        return
    target = int(args[0])
    key = "sub_revoked" if subscriptions.revoke(target) else "sub_nothing"
    send(chat_id, i18n.t(key, lang, uid=target))


def do_subs(chat_id: int, user_id: int):
    lang = lang_of(user_id)
    if not _owner_only(chat_id, user_id):
        return
    rows = subscriptions.everyone()
    if not rows:
        send(chat_id, i18n.t("sub_list_empty", lang))
        return

    live = sum(1 for r in rows if r["active"])
    out = "🎟 <b>SUBSCRIBERS</b>\n"
    out += "━━━━━━━━━━━━━━━━━━━━\n"
    out += f"<b>{live}</b> active · <b>{len(rows) - live}</b> lapsed\n\n"
    for r in rows[:40]:
        dot = "🟢" if r["active"] else "⚪"
        when = i18n.date_long(r["until"].astimezone(news.WIB), lang) \
            if r["until"] else "-"
        out += (f"{dot} <code>{r['user_id']}</code> · {html.escape(r['plan'])}\n"
                f"   {when}"
                + (f"  ({r['days_left']:.0f}d left)" if r["active"] else "")
                + "\n")
    if len(rows) > 40:
        out += f"\n<i>… and {len(rows) - 40} more</i>"
    send(chat_id, out)


def handle_message(msg: dict):
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id", 0)
    text = (msg.get("text") or "").strip()

    if not text.startswith("/"):
        return

    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd == "/whoami":
        send(chat_id, f"Your Telegram user ID: <code>{user_id}</code>")
        return

    # Reachable without access, deliberately: someone whose time ran out
    # needs to be able to see that that is what happened, and renewing
    # requires an ID they can only get from the bot.
    if cmd == "/subscription":
        do_subscription(chat_id, user_id)
        return

    # Also before the gate: a stranger's first message is /start, and they
    # need the welcome and their own ID far more than a refusal.
    if cmd == "/start":
        do_start(chat_id, user_id)
        return

    if not allowed(user_id):
        # Tell them their ID. Without it they cannot ask for access, and
        # "not authorised" on its own is a dead end.
        key = "sub_denied" if C.SUBSCRIPTIONS_ENABLED else "not_authorised"
        send(chat_id, i18n.t(key, lang_of(user_id), uid=user_id))
        return

    lang = lang_of(user_id)

    if cmd in ("/help", "/menu"):
        send(chat_id, help_text(lang))
    elif cmd == "/language":
        do_language(chat_id, user_id, args)
    elif cmd == "/setconf":
        do_setconf(chat_id, user_id, args)
    elif cmd == "/strategy":
        do_strategy(chat_id, user_id, args)
    elif cmd == "/motivation":
        do_motivation(chat_id, user_id)
    elif cmd == "/settings":
        do_settings(chat_id, user_id)
    elif cmd == "/status":
        do_status(chat_id, user_id)
    elif cmd == "/signals":
        do_signals_list(chat_id, user_id)
    elif cmd == "/history":
        do_history(chat_id, user_id)
    elif cmd in ("/daily", "/weekly", "/monthly"):
        do_period(chat_id, user_id, cmd.lstrip("/"))
    elif cmd == "/management":
        do_management(chat_id, user_id, args)
    elif cmd == "/update":
        do_update(chat_id, user_id, swing=False)
    elif cmd == "/swingupdate":
        do_update(chat_id, user_id, swing=True)
    elif cmd == "/resetdata":
        do_resetdata(chat_id, user_id, args)
    elif cmd == "/cancel":
        sid = args[0] if args else ""
        send(chat_id, i18n.t("cancelled_ok" if journal.cancel(sid)
                             else "cancel_notfound", lang))
    elif cmd == "/signal":
        do_signal_command(chat_id, user_id, args)
    elif cmd == "/scan":
        _, mode, _ = parse_args(args, default_mode="intraday")
        do_scan(chat_id, user_id, mode)
    elif cmd == "/backtest":
        calibrate = any(a.lower().lstrip("-") == "calibrate" for a in args)
        symbol_key, mode, _ = parse_args(args)
        do_backtest(chat_id, symbol_key, mode, calibrate, user_id)
    elif cmd == "/calibration":
        do_calibration(chat_id, user_id)
    elif cmd == "/stats":
        symbol_key, mode, _ = parse_args(args)
        do_stats(chat_id, symbol_key, mode if any(
            a.lower().lstrip("/") in C.MODES or a.lower() in MODE_WORDS
            for a in args) else None)
    elif cmd == "/news":
        do_news(chat_id, user_id)
    elif cmd == "/grant":
        do_grant(chat_id, user_id, args)
    elif cmd == "/revoke":
        do_revoke(chat_id, user_id, args)
    elif cmd == "/subs":
        do_subs(chat_id, user_id)
    elif cmd == "/symbols":
        do_symbols(chat_id, lang)
    else:
        send(chat_id, i18n.t("unknown_command", lang))


def handle_callback(cb: dict):
    user_id = cb.get("from", {}).get("id", 0)
    data = cb.get("data") or ""
    msg = cb.get("message", {})

    # The language picker is part of /start, so it has to work before the
    # user has any access at all.
    if data.startswith("lang|"):
        tg("answerCallbackQuery", callback_query_id=cb["id"])
        lang = i18n.ID if data.endswith("|id") else i18n.EN
        users.update(user_id, language=lang)
        send_welcome(msg.get("chat", {}).get("id", user_id), user_id, lang)
        return

    tg("answerCallbackQuery", callback_query_id=cb["id"], text="Refreshing…")
    if not allowed(user_id):
        return

    try:
        parts = cb["data"].split("|")
        _, symbol_key, mode = parts[:3]
        verbose = len(parts) > 3 and parts[3] == "v"
    except ValueError:
        return

    do_signal(msg["chat"]["id"], symbol_key, mode,
              message_id=msg.get("message_id"), verbose=verbose)


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #
@app.route("/", methods=["GET"])
def index():
    return "Signal bot is running. Webhook endpoint is private.", 200


@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret: str):
    if secret != C.WEBHOOK_SECRET:
        return jsonify(ok=False), 403

    update = request.get_json(force=True, silent=True) or {}
    update_id = update.get("update_id")

    if update_id is not None:
        now = time.time()
        for stale in [k for k, ts in _inflight.items() if now - ts > INFLIGHT_TTL]:
            _inflight.pop(stale, None)   # the worker that owned it is gone

        if update_id in _seen_updates or update_id in _inflight:
            return jsonify(ok=True)      # genuine duplicate; already answered
        _inflight[update_id] = now

    try:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception:  # noqa: BLE001
        log.error("update handling failed: %s", traceback.format_exc())
    finally:
        if update_id is not None:
            _inflight.pop(update_id, None)
            _seen_updates.add(update_id)
            if len(_seen_updates) > 500:
                _seen_updates.clear()

    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
