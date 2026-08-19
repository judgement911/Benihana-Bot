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
import traceback
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

import config as C
from strategy import DIR_NAME, evaluate
from yahoo_data import DataError, fetch_ohlc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("signalbot")

app = Flask(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram re-sends an update if we're slow to answer. Without this you'd get
# the same signal twice.
_seen_updates: set[int] = set()


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


def bar(pct: int, width: int = 14) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def render(symbol: str, res: dict) -> str:
    out = f"<b>{symbol} · {res['mode'].upper()}</b>\n"
    out += f"<code>{res['price']:,.2f}</code> · candle closed "
    out += f"{res['as_of'].strftime('%H:%M')} UTC\n"
    tfs = res["timeframes"]
    out += f"<i>{tfs['entry']} trigger / {tfs['trend']} trend / {tfs['bias']} bias</i>\n\n"

    if res["vetoes"]:
        out += "🚫 <b>NO TRADE</b>\n<i>Hard filter blocked this — no score calculated.</i>\n\n"
        for v in res["vetoes"]:
            out += f"• {html.escape(v)}\n"
        return out

    dec = res["decision"]
    icon = {"ENTRY": "✅", "WAIT": "⏳", "NO TRADE": "🚫"}[dec]
    out += f"{icon} <b>{dec}"
    if dec == "ENTRY":
        out += f" — {DIR_NAME[res['direction']]}"
    out += "</b>\n"
    out += f"Confluence <b>{res['score']}%</b>  <code>{bar(res['score'])}</code>\n"
    out += "<i>rule agreement, not win probability</i>\n\n"

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
    "<code>/strategy</code> — what the bot checks\n"
    "<code>/whoami</code> — your Telegram ID\n\n"
    "Answers are <b>ENTRY</b>, <b>WAIT</b>, or <b>NO TRADE</b> with a confluence "
    "score. That score is rule agreement, <i>not</i> a probability of winning."
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
    "<b>Score</b>: 8 weighted conditions totalling 100. 70+ with a real trigger "
    "= ENTRY. 50–69 = WAIT. Under 50 = NO TRADE.\n\n"
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

    if message_id:
        edit(chat_id, message_id, text, buttons=True, symbol_key=symbol_key)
    else:
        send(chat_id, text, buttons=True, symbol_key=symbol_key)


def do_backtest(chat_id: int, symbol_key: str, mode: str):
    symbol = C.SYMBOL_ALIASES.get(symbol_key, "XAU/USD")
    spec = C.MODES[mode]

    send(chat_id, f"⏱ Backtesting {symbol} {mode}. Takes about a minute.")
    try:
        from backtest import run as backtest_run

        # Deliberately modest: a free PythonAnywhere account gets 100 CPU-seconds
        # a day, and this is the only thing here that eats a real share of them.
        df = fetch_ohlc(symbol, spec.entry_tf, 1200)
        _, report = backtest_run(df, mode)
        text = (f"<b>{symbol} {mode} backtest</b>\n{len(df)} × {spec.entry_tf} bars\n"
                f"<pre>{html.escape(report)}</pre>")
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.error("backtest failed: %s", traceback.format_exc())
        text = f"⚠️ Backtest error: <code>{html.escape(type(exc).__name__)}</code>"

    send(chat_id, text)


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
        symbol_key, mode = parse_args(args)
        do_backtest(chat_id, symbol_key, mode)
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

    if update_id in _seen_updates:
        return jsonify(ok=True)          # Telegram retry; already handled
    if update_id is not None:
        _seen_updates.add(update_id)
        if len(_seen_updates) > 500:
            _seen_updates.clear()

    try:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception:  # noqa: BLE001
        log.error("update handling failed: %s", traceback.format_exc())

    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
