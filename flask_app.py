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
import alerts
import instruments as I
import journal
import news
import probability as prob
import scanner
import view
from strategy import evaluate
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
    return (not C.ALLOWED_USER_IDS) or (user_id in C.ALLOWED_USER_IDS)


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
              message_id: int | None = None, verbose: bool = False):
    inst = I.BY_KEY.get(symbol_key, I.GOLD)
    symbol = inst.symbol
    spec = C.MODES[mode]

    if message_id is None:
        placeholder = send(chat_id, f"⏳ Reading {inst.display} {mode}…")
        message_id = (placeholder or {}).get("result", {}).get("message_id")

    try:
        entry_df = fetch_ohlc(symbol, spec.entry_tf, spec.bars)
        trend_df = fetch_ohlc(symbol, spec.trend_tf, spec.bars)
        bias_df = fetch_ohlc(symbol, spec.bias_tf, spec.bars)
        res = evaluate(entry_df, trend_df, bias_df, spec,
                       datetime.now(timezone.utc), instrument=inst)
        text = view.render(inst.display, res, verbose)
        journal.record(res)   # graded later by /stats
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.error("signal failed: %s", traceback.format_exc())
        text = f"⚠️ Error: <code>{html.escape(type(exc).__name__)}</code>"

    deliver(chat_id, message_id, text, symbol_key, mode, verbose)


def do_backtest(chat_id: int, symbol_key: str, mode: str, calibrate: bool = False):
    inst = I.BY_KEY.get(symbol_key, I.GOLD)
    symbol = inst.symbol
    spec = C.MODES[mode]

    send(chat_id, f"⏱ Backtesting {symbol} {mode}. Takes about a minute.")
    try:
        from backtest import build_calibration, write_calibration
        from backtest import run as backtest_run

        # A free PythonAnywhere account gets 100 CPU-seconds a day and Telegram
        # gives a webhook about 60 seconds, so this is the one command here that
        # has to be sized against both. See config.BACKTEST_BARS.
        df = fetch_ohlc(symbol, spec.entry_tf, C.BACKTEST_BARS)
        stats, report = backtest_run(df, mode)
        text = (f"<b>{symbol} {mode} backtest</b>\n{len(df)} × {spec.entry_tf} bars\n"
                f"<pre>{html.escape(report)}</pre>")

        if calibrate:
            trades = stats.get("trade_log") or []
            if len(trades) < C.CALIBRATION_MIN_TRADES:
                text += (f"\n⚠️ Not calibrated: {len(trades)} trades is under the "
                         f"{C.CALIBRATION_MIN_TRADES}-trade minimum. This window "
                         f"was too quiet. For a bigger sample run it from a "
                         f"console:\n<code>python3 backtest.py --mode {mode} "
                         f"--live --calibrate</code>")
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
def do_news(chat_id: int, symbol_key: str):
    inst = I.BY_KEY.get(symbol_key, I.GOLD)
    try:
        send(chat_id, news.format_news(inst))
    except Exception:  # noqa: BLE001
        log.error("news failed: %s", traceback.format_exc())
        send(chat_id, "⚠️ Could not read the calendar just now.")


# --------------------------------------------------------------------------- #
#  /alert — subscribe to high-quality setups
# --------------------------------------------------------------------------- #
ALERT_HELP = (
    "<b>Alerts</b>\n\n"
    "<code>/alert xauusd scalp</code> — tell me when gold sets up\n"
    "<code>/alert</code> — what you are watching\n"
    "<code>/alert off</code> — stop everything\n\n"
    "You get a message the moment an ENTRY appears at "
    f"{C.ALERT_MIN_CONFIDENCE}%+ confidence, with entry, stop and targets.\n\n"
    "<b>One thing you need to know.</b> This bot is a webhook — it only runs "
    "when Telegram pokes it, so it cannot watch the market on its own. "
    "Something has to run <code>scan_job.py</code> on a schedule.\n\n"
    "On PythonAnywhere: <b>Tasks</b> tab → "
    "<code>python3 ~/Benihana-Bot/scan_job.py</code>. A free account gets one "
    "task a day, so alerts fire once daily. Hourly needs a paid plan, or run "
    "the job from any machine that stays on."
)


def do_alert(chat_id: int, args: list):
    words = [a.lower().lstrip("/") for a in args]

    if any(w in ("off", "stop", "clear", "none") for w in words):
        n = alerts.remove(chat_id)
        send(chat_id, f"Cleared {n} alert{'s' if n != 1 else ''}." if n
             else "You had no alerts set.")
        return
    if not args:
        send(chat_id, alerts.format_list(chat_id))
        return

    symbol_key, mode, bad = parse_args(args, default_mode="intraday")
    if bad:
        send(chat_id, f"Don't know <b>{html.escape(bad)}</b>. Try /symbols.")
        return
    inst = I.BY_KEY[symbol_key]
    added = alerts.add(chat_id, symbol_key, mode)
    if not added:
        send(chat_id, f"Already watching {inst.display} {mode}.")
        return
    send(chat_id,
         f"🔔 Watching <b>{inst.display}</b> on <b>{mode}</b>.\n\n"
         f"You will get the full signal — entry, stop, targets — as soon as "
         f"one appears at {C.ALERT_MIN_CONFIDENCE}%+ confidence.\n\n"
         f"<i>Delivery needs the scan job running. /alerthelp explains.</i>")


# --------------------------------------------------------------------------- #
#  /crazymode — sweep the board
# --------------------------------------------------------------------------- #
def do_crazymode(chat_id: int, mode: str, keys: list = None,
                 message_id: int | None = None):
    keys = keys or C.SCAN_SYMBOLS
    keys = [k for k in keys if k in I.BY_KEY][:C.CRAZY_MAX_SYMBOLS]
    if not keys:
        send(chat_id, "Nothing to scan — check SCAN_SYMBOLS in config.")
        return

    placeholder = send(chat_id, f"⚔️ Scanning {len(keys)} markets on {mode}…")
    mid = (placeholder or {}).get("result", {}).get("message_id")

    try:
        result = scanner.scan(keys, mode, fetch_ohlc, log=log.warning)
        text = scanner.format_scan(result)
        for res in scanner.tradeable(result["rows"]):
            journal.record(res)
    except Exception:  # noqa: BLE001
        log.error("crazymode failed: %s", traceback.format_exc())
        text = "⚠️ Scan failed. Check the log."
    deliver(chat_id, mid, text, "xauusd", mode)


# --------------------------------------------------------------------------- #
#  /symbols — what it can trade
# --------------------------------------------------------------------------- #
def do_symbols(chat_id: int):
    out = "<b>Tradeable universe</b>\n"
    for label, items in I.grouped().items():
        if not items:
            continue
        out += f"\n<b>{label}</b>\n"
        out += "<code>" + " ".join(i.display for i in items) + "</code>\n"
    out += ("\n<i>Type any of them, or a nickname: gold, cable, nas100, "
            "aussie. Free data plans usually cover FX and metals; indices "
            "and energy often need a paid plan — "
            "<code>python check_universe.py</code> tells you which.</i>")
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

    if not allowed(user_id):
        send(chat_id, "Not authorised.")
        return

    if cmd in ("/start", "/help"):
        send(chat_id, HELP)
    elif cmd == "/strategy":
        send(chat_id, STRATEGY)
    elif cmd == "/signal":
        symbol_key, mode, bad = parse_args(args)
        if bad:
            send(chat_id, f"Don't know <b>{html.escape(bad)}</b>. "
                          f"Try /symbols for the list.")
            return
        do_signal(chat_id, symbol_key, mode)
    elif cmd == "/backtest":
        calibrate = any(a.lower().lstrip("-") == "calibrate" for a in args)
        symbol_key, mode, _ = parse_args(args)
        do_backtest(chat_id, symbol_key, mode, calibrate)
    elif cmd == "/calibration":
        do_calibration(chat_id)
    elif cmd == "/stats":
        symbol_key, mode, _ = parse_args(args)
        do_stats(chat_id, symbol_key, mode if any(
            a.lower().lstrip("/") in C.MODES or a.lower() in MODE_WORDS
            for a in args) else None)
    elif cmd == "/news":
        symbol_key, _, _ = parse_args(args)
        do_news(chat_id, symbol_key)
    elif cmd in ("/alert", "/alerts"):
        do_alert(chat_id, args)
    elif cmd == "/alerthelp":
        send(chat_id, ALERT_HELP)
    elif cmd in ("/crazymode", "/scan"):
        _, mode, _ = parse_args(args, default_mode="intraday")
        do_crazymode(chat_id, mode)
    elif cmd == "/symbols":
        do_symbols(chat_id)
    else:
        send(chat_id, "Unknown command. Try /help")


def handle_callback(cb: dict):
    tg("answerCallbackQuery", callback_query_id=cb["id"], text="Refreshing…")
    user_id = cb.get("from", {}).get("id", 0)
    if not allowed(user_id):
        return

    try:
        parts = cb["data"].split("|")
        _, symbol_key, mode = parts[:3]
        verbose = len(parts) > 3 and parts[3] == "v"
    except ValueError:
        return

    msg = cb.get("message", {})
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
