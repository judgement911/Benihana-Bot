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
import probability as prob
from strategy import DIR_NAME, evaluate
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


def send(chat_id: int, text: str, buttons: bool = False, symbol_key: str = "xauusd"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "Scalp", "callback_data": f"s|{symbol_key}|scalp"},
                {"text": "Intraday", "callback_data": f"s|{symbol_key}|intraday"},
                {"text": "Swing", "callback_data": f"s|{symbol_key}|swing"},
            ]]
        }
    return tg("sendMessage", **payload)


def edit(chat_id: int, message_id: int, text: str,
         buttons: bool = False, symbol_key: str = "xauusd"):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[
                {"text": "Scalp", "callback_data": f"s|{symbol_key}|scalp"},
                {"text": "Intraday", "callback_data": f"s|{symbol_key}|intraday"},
                {"text": "Swing", "callback_data": f"s|{symbol_key}|swing"},
            ]]
        }
    return tg("editMessageText", **payload)


def _ok(resp) -> bool:
    return bool(resp and resp.get("ok"))


def deliver(chat_id: int, message_id: int | None, text: str,
            symbol_key: str = "xauusd") -> None:
    """Get `text` in front of the user, whatever it takes.

    Editing the placeholder is the nice outcome. If Telegram refuses — a stray
    angle bracket in a provider's error message is enough to fail HTML parsing
    — retry without markup, then as a fresh message. Anything is better than
    leaving the chat on "Reading…" with the reason buried in a log file.
    """
    if message_id and _ok(edit(chat_id, message_id, text, True, symbol_key)):
        return

    plain = re.sub(r"<[^>]+>", "", text)
    if message_id:
        log.error("edit failed, retrying without HTML")
        if _ok(tg("editMessageText", chat_id=chat_id, message_id=message_id,
                  text=plain, disable_web_page_preview=True)):
            return

    if _ok(send(chat_id, text, buttons=True, symbol_key=symbol_key)):
        return
    log.error("send failed too, falling back to plain text")
    tg("sendMessage", chat_id=chat_id, text=plain)


def allowed(user_id: int) -> bool:
    return (not C.ALLOWED_USER_IDS) or (user_id in C.ALLOWED_USER_IDS)


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def parse_args(parts: list[str]) -> tuple[str, str]:
    symbol_key, mode = "xauusd", "intraday"
    for raw in parts:
        a = raw.lower().strip().lstrip("/")
        if a in C.MODES:
            mode = a
        elif a in ("day", "daytrade", "intra"):
            mode = "intraday"
        elif a in ("scalping", "scalper"):
            mode = "scalp"
        elif a in C.SYMBOL_ALIASES:
            symbol_key = a
    return symbol_key, mode


def render(symbol: str, res: dict) -> str:
    out = f"<b>{symbol} · {res['mode'].upper()}</b>\n"
    out += f"<code>{res['price']:,.2f}</code> · candle closed "
    out += f"{res['as_of'].strftime('%H:%M')} UTC\n"
    tfs = res["timeframes"]
    out += f"<i>{tfs['entry']} trigger / {tfs['trend']} trend / {tfs['bias']} bias</i>\n\n"

    if res["vetoes"]:
        out += "🚫 <b>NO TRADE</b>\n"
        out += "Confidence <b>0%</b> · no probability quoted\n"
        out += "<i>Hard filter blocked this — no score calculated.</i>\n\n"
        for v in res["vetoes"]:
            out += f"• {html.escape(v)}\n"
        return out

    dec = res["decision"]
    icon = {"ENTRY": "✅", "WAIT": "⏳", "NO TRADE": "🚫"}[dec]
    out += f"{icon} <b>{dec}"
    if dec == "ENTRY":
        out += f" — {DIR_NAME[res['direction']]}"
    out += "</b>\n"
    out += f"<pre>{html.escape(prob.read_block(res))}</pre>\n"

    drags = prob.drag_note(res)
    if drags:
        out += f"<i>{html.escape(drags)}</i>\n"
    basis = prob.basis_note(res)
    if basis:
        out += f"<i>Odds: {html.escape(basis)}</i>\n"
    out += "\n"

    lv = res["levels"]
    if dec == "ENTRY" and lv:
        out += "<pre>"
        out += f"Entry  {lv['entry']:,.2f}  (market)\n"
        out += f"Stop   {lv['stop']:,.2f}  ({lv['risk_points']} pts)\n"
        for n, (tp, m) in enumerate(zip(lv["tps"], lv["tp_multiples"]), start=1):
            out += f"TP{n}    {tp:,.2f}  ({m}R)\n"
        out += f"Size   {lv['lots']} lots = ${lv['risk_cash']}\n"
        out += f"ATR    {lv['atr']} pts</pre>\n"
    elif lv:
        out += f"<i>If it triggers, stop would sit near {lv['stop']:,.2f}.</i>\n\n"

    out += "<b>Scorecard</b>\n"
    for r in res["reasons"]:
        out += f"{'✓' if r['ok'] else '✗'} {html.escape(r['text'])} "
        out += f"<code>[{r['points']:.0f}/{r['max']}]</code>\n"

    if dec != "ENTRY" and res["missing"]:
        out += "\n<b>Waiting on</b>\n"
        for m in res["missing"][:4]:
            out += f"• {html.escape(m)}\n"

    if res.get("news_warning"):
        out += "\n⚠️ <i>High-impact US data often lands this hour.</i>\n"

    return out


# --------------------------------------------------------------------------- #
#  Commands
# --------------------------------------------------------------------------- #
HELP = (
    "<b>Signal bot online.</b>\n\n"
    "<code>/signal xauusd scalp</code>\n"
    "<code>/signal xauusd intraday</code>\n"
    "<code>/signal xauusd swing</code>\n\n"
    "<code>/backtest intraday</code> — does the strategy actually make money?\n"
    "<code>/backtest intraday calibrate</code> — same run, and the measured odds "
    "replace the model's guess from then on\n"
    "<code>/calibration</code> — is the probability measured or guessed?\n"
    "<code>/strategy</code> — what the bot checks\n"
    "<code>/whoami</code> — your Telegram ID\n\n"
    "Answers are <b>ENTRY</b>, <b>WAIT</b>, or <b>NO TRADE</b> with three "
    "percentages: <b>confluence</b> (how many rules agree), <b>confidence</b> "
    "(that score minus hazards the rules cannot see) and <b>probability</b> "
    "(odds the target trades before the stop)."
)

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
    "Nobody has verified these rules are profitable. Run /backtest first."
)


def do_signal(chat_id: int, symbol_key: str, mode: str, message_id: int | None = None):
    symbol = C.SYMBOL_ALIASES.get(symbol_key, "XAU/USD")
    spec = C.MODES[mode]

    if message_id is None:
        placeholder = send(chat_id, f"⏳ Reading {symbol} {mode}…")
        message_id = (placeholder or {}).get("result", {}).get("message_id")

    try:
        entry_df = fetch_ohlc(symbol, spec.entry_tf, spec.bars)
        trend_df = fetch_ohlc(symbol, spec.trend_tf, spec.bars)
        bias_df = fetch_ohlc(symbol, spec.bias_tf, spec.bars)
        res = evaluate(entry_df, trend_df, bias_df, spec, datetime.now(timezone.utc))
        text = render(symbol, res)
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.error("signal failed: %s", traceback.format_exc())
        text = f"⚠️ Error: <code>{html.escape(type(exc).__name__)}</code>"

    deliver(chat_id, message_id, text, symbol_key)


def do_backtest(chat_id: int, symbol_key: str, mode: str, calibrate: bool = False):
    symbol = C.SYMBOL_ALIASES.get(symbol_key, "XAU/USD")
    spec = C.MODES[mode]

    send(chat_id, f"⏱ Backtesting {symbol} {mode}. Takes about a minute.")
    try:
        from backtest import build_calibration, write_calibration
        from backtest import run as backtest_run

        # Deliberately modest: a free PythonAnywhere account gets 100 CPU-seconds
        # a day, and this is the only thing here that eats a real share of them.
        df = fetch_ohlc(symbol, spec.entry_tf, 1200)
        stats, report = backtest_run(df, mode)
        text = (f"<b>{symbol} {mode} backtest</b>\n{len(df)} × {spec.entry_tf} bars\n"
                f"<pre>{html.escape(report)}</pre>")

        if calibrate:
            trades = stats.get("trade_log") or []
            if len(trades) < C.CALIBRATION_MIN_TRADES:
                text += (f"\nNot calibrated: {len(trades)} trades is under the "
                         f"{C.CALIBRATION_MIN_TRADES}-trade minimum.")
            else:
                entry = build_calibration(trades, mode, symbol, len(df), "telegram")
                write_calibration(entry, mode, symbol)
                text += (f"\n✅ Calibrated {mode} on {len(trades)} trades. Signals "
                         f"now quote measured odds — see /calibration.")
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.error("backtest failed: %s", traceback.format_exc())
        text = f"⚠️ Backtest error: <code>{html.escape(type(exc).__name__)}</code>"

    send(chat_id, text)


def do_calibration(chat_id: int):
    send(chat_id, "<b>Probability calibration</b>\n"
                  f"<pre>{html.escape(prob.calibration_report())}</pre>")


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

    if not allowed(user_id):
        send(chat_id, "Not authorised.")
        return

    if cmd in ("/start", "/help"):
        send(chat_id, HELP)
    elif cmd == "/strategy":
        send(chat_id, STRATEGY)
    elif cmd == "/signal":
        symbol_key, mode = parse_args(args)
        do_signal(chat_id, symbol_key, mode)
    elif cmd == "/backtest":
        calibrate = any(a.lower().lstrip("-") == "calibrate" for a in args)
        symbol_key, mode = parse_args(args)
        do_backtest(chat_id, symbol_key, mode, calibrate)
    elif cmd == "/calibration":
        do_calibration(chat_id)
    else:
        send(chat_id, "Unknown command. Try /help")


def handle_callback(cb: dict):
    tg("answerCallbackQuery", callback_query_id=cb["id"], text="Refreshing…")
    user_id = cb.get("from", {}).get("id", 0)
    if not allowed(user_id):
        return

    try:
        _, symbol_key, mode = cb["data"].split("|")
    except ValueError:
        return

    msg = cb.get("message", {})
    do_signal(msg["chat"]["id"], symbol_key, mode, message_id=msg.get("message_id"))


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
