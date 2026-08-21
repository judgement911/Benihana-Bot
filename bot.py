"""Telegram front end. Usage: /signal xauusd scalp"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config as C
import probability as prob
from backtest import build_calibration, write_calibration
from backtest import run as backtest_run
from data import DataError, fetch_ohlc
import alerts
import instruments as I
import journal
import news
import scanner
import view
from strategy import evaluate

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("signalbot")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def allowed(update: Update) -> bool:
    if not C.ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in C.ALLOWED_USER_IDS)


MODE_WORDS = {"day": "intraday", "daytrade": "intraday", "intra": "intraday",
              "scalping": "scalp", "scalper": "scalp", "swings": "swing"}


def parse_args(args: list[str], default_symbol: str = "xauusd",
               default_mode: str = "intraday") -> tuple[str, str, str | None]:
    """Any order, any spelling. Third element names an unrecognised word."""
    symbol_key, mode, unknown = default_symbol, default_mode, None
    for raw in args:
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


async def analyse(symbol_key: str, mode: str) -> dict:
    spec = C.MODES[mode]
    inst = I.BY_KEY.get(symbol_key, I.GOLD)
    symbol = inst.symbol
    loop = asyncio.get_running_loop()

    entry_df, trend_df, bias_df = await asyncio.gather(
        loop.run_in_executor(None, fetch_ohlc, symbol, spec.entry_tf, spec.bars),
        loop.run_in_executor(None, fetch_ohlc, symbol, spec.trend_tf, spec.bars),
        loop.run_in_executor(None, fetch_ohlc, symbol, spec.bias_tf, spec.bars),
    )

    return evaluate(entry_df, trend_df, bias_df, spec,
                    datetime.now(timezone.utc), instrument=inst)


# --------------------------------------------------------------------------- #
#  Handlers
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.message.reply_text(view.HELP, parse_mode=ParseMode.HTML)


async def cmd_strategy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.message.reply_text(
        "<b>What the bot actually checks</b>\n\n"
        "It only trades pullbacks inside an established trend. Three timeframes:\n"
        "• <b>Bias TF</b> — holds a veto. Wrong side, no trade.\n"
        "• <b>Trend TF</b> — picks direction, and ADX must confirm a trend exists.\n"
        "• <b>Entry TF</b> — supplies the trigger: retracement into the EMA20 zone, "
        "RSI or MACD turning back, confirming candle close.\n\n"
        "<b>Hard vetoes</b> (answer is NO TRADE, no score given):\n"
        "ADX under 15 · timeframe conflict · ATR over 2.5x or under 0.4x normal · "
        "RSI already extreme · stale data\n\n"
        "<b>Confluence</b> is a weighted sum of 8 conditions totalling 100. "
        "70+ with a live trigger = ENTRY. 50–69 = WAIT. Under 50 = NO TRADE. "
        "It is agreement between rules — nothing more.\n\n"
        "<b>Confidence</b> starts at that score and loses points for what the "
        "scorecard cannot see: news hours, dead sessions, a bias EMA built on "
        "short history, no room to the next swing. The signal lists every "
        "deduction it made.\n\n"
        "<b>Probability</b> is the chance the target trades before the stop. It "
        "starts from barrier maths — with a 1R stop and a kR target a driftless "
        "market pays 1/(1+k) of the time, which is also the win rate you need to "
        "break even — then adds a capped edge for the confluence score and "
        "subtracts for costs and for swings sitting in the way. Once you run "
        "<code>/backtest intraday calibrate</code>, real results "
        "override the model. <code>/calibration</code> says which you are "
        "looking at.\n\n"
        "Nobody has verified these rules are profitable on your broker's data. "
        "Run <code>backtest.py</code> first.",
        parse_mode=ParseMode.HTML,
    )


def keyboard(symbol_key: str, mode: str, verbose: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row]
        for row in view.buttons(symbol_key, mode, verbose)
    ])


async def do_signal(symbol_key: str, mode: str, send, edit=None,
                    verbose: bool = False) -> None:
    inst = I.BY_KEY.get(symbol_key, I.GOLD)
    # render() used to sit in an else: clause, which is NOT covered by the
    # except handlers above it. Any error formatting the reply escaped, the
    # placeholder was never edited, and the chat sat on "Reading…" forever.
    # Rendering belongs inside the try.
    try:
        res = await analyse(symbol_key, mode)
        text = view.render(inst.display, res, verbose)
        journal.record(res)   # graded later by /stats
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.exception("signal failed")
        text = f"⚠️ Unexpected error: <code>{html.escape(type(exc).__name__)}</code>"

    key = inst.key
    deliver = edit or send

    # Last line of defence: if Telegram rejects the HTML — an unescaped angle
    # bracket in an error string is enough — resend as plain text rather than
    # leaving the placeholder stranded.
    try:
        await deliver(text, parse_mode=ParseMode.HTML, reply_markup=keyboard(key, mode, verbose))
    except Exception:  # noqa: BLE001
        log.exception("HTML delivery failed, falling back to plain text")
        stripped = re.sub(r"<[^>]+>", "", text)
        try:
            await deliver(stripped, reply_markup=keyboard(key, mode, verbose))
        except Exception:  # noqa: BLE001
            log.exception("plain-text delivery failed too")


async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await update.message.reply_text("Not authorised.")
        return

    symbol_key, mode, warning = parse_args(ctx.args or [])
    inst = I.BY_KEY[symbol_key]
    placeholder = await update.message.reply_text(
        f"⏳ Reading {inst.display} {mode}…"
    )
    if warning:
        await update.message.reply_text(warning)

    await do_signal(symbol_key, mode, update.message.reply_text, placeholder.edit_text)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer("Refreshing…")
    if not allowed(update):
        return

    parts = q.data.split("|")
    _, symbol_key, mode = parts[:3]
    verbose = len(parts) > 3 and parts[3] == "v"
    await do_signal(symbol_key, mode, q.message.reply_text, q.edit_message_text, verbose)


def save_calibration(stats: dict, mode: str, symbol: str, bars: int) -> str:
    """Fold a finished backtest into calibration.json so live signals quote
    measured hit rates. Returns the line to show the user."""
    trades = stats.get("trade_log") or []
    if len(trades) < C.CALIBRATION_MIN_TRADES:
        return (f"Not calibrated: {len(trades)} trades is under the "
                f"{C.CALIBRATION_MIN_TRADES}-trade minimum.")
    try:
        entry = build_calibration(trades, mode, symbol, bars, "telegram")
        write_calibration(entry, mode, symbol)
    except OSError as exc:
        return f"Could not write calibration: {exc}"
    return (f"✅ Calibrated {mode} on {len(trades)} trades. Signals now quote "
            f"measured odds — see /calibration.")


async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs the real strategy over history and reports expectancy. Slow on
    purpose — this is the step that tells you whether any of this works.

    Add the word `calibrate` to also teach the bot: the measured hit rates get
    written to calibration.json and every later signal quotes them instead of
    the model's guess."""
    if not allowed(update):
        return

    args = list(ctx.args or [])
    calibrate = any(a.lower().lstrip("-") == "calibrate" for a in args)
    args = [a for a in args if a.lower().lstrip("-") != "calibrate"]

    symbol_key, mode, _ = parse_args(args)
    inst = I.BY_KEY[symbol_key]
    symbol = inst.symbol
    spec = C.MODES[mode]
    msg = await update.message.reply_text(
        f"⏱ Backtesting {inst.display} {mode} on {spec.entry_tf} history.\n"
        f"This takes 1–4 minutes. I'll edit this message when done."
    )

    loop = asyncio.get_running_loop()
    try:
        df = await loop.run_in_executor(None, fetch_ohlc, symbol, spec.entry_tf, C.BACKTEST_BARS_CLI)
        stats, report = await loop.run_in_executor(None, backtest_run, df, mode)
        header = f"<b>{inst.display} {mode} backtest</b>\n{len(df)} × {spec.entry_tf} bars\n"
        text = header + f"<pre>{html.escape(report)}</pre>"
        if calibrate:
            text += "\n" + html.escape(
                save_calibration(stats, mode, symbol, len(df))
            )
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{exc}"
    except Exception as exc:  # noqa: BLE001
        log.exception("backtest failed")
        text = f"⚠️ Backtest error: <code>{type(exc).__name__}</code>"

    await msg.edit_text(text, parse_mode=ParseMode.HTML)


async def cmd_calibration(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Says plainly whether the probabilities are measured or modelled."""
    if not allowed(update):
        return
    await update.message.reply_text(
        f"<b>Probability calibration</b>\n<pre>{html.escape(prob.calibration_report())}</pre>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    args = list(ctx.args or [])
    symbol_key, mode, _ = parse_args(args)
    explicit_mode = any(a.lower().lstrip("/") in C.MODES or a.lower() in MODE_WORDS
                        for a in args)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, journal.resolve, fetch_ohlc)
    except Exception:  # noqa: BLE001
        log.warning("journal resolve failed", exc_info=True)
    await update.message.reply_text(
        journal.format_stats(symbol_key, mode if explicit_mode else None),
        parse_mode=ParseMode.HTML)


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    symbol_key, _, _ = parse_args(ctx.args or [])
    inst = I.BY_KEY[symbol_key]
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, news.format_news, inst)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


ALERT_HELP = (
    "<b>Alerts</b>\n\n"
    "<code>/alert xauusd scalp</code> — tell me when gold sets up\n"
    "<code>/alert</code> — what you are watching\n"
    "<code>/alert off</code> — stop everything\n\n"
    f"You get the full signal as soon as an ENTRY appears at "
    f"{C.ALERT_MIN_CONFIDENCE}%+ confidence.\n\n"
    "Polling mode watches continuously while this process is running, so "
    "alerts need <code>scan_job.py</code> on a schedule (cron, systemd timer, "
    "or the PythonAnywhere Tasks tab)."
)


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    args = list(ctx.args or [])
    chat_id = update.effective_chat.id
    words = [a.lower().lstrip("/") for a in args]

    if any(w in ("off", "stop", "clear", "none") for w in words):
        n = alerts.remove(chat_id)
        await update.message.reply_text(
            f"Cleared {n} alert{'s' if n != 1 else ''}." if n else "No alerts set.")
        return
    if not args:
        await update.message.reply_text(alerts.format_list(chat_id),
                                        parse_mode=ParseMode.HTML)
        return

    symbol_key, mode, bad = parse_args(args)
    if bad:
        await update.message.reply_text(f"Don't know '{bad}'. Try /symbols.")
        return
    inst = I.BY_KEY[symbol_key]
    if alerts.add(chat_id, symbol_key, mode):
        await update.message.reply_text(
            f"🔔 Watching <b>{inst.display}</b> on <b>{mode}</b>.\n\n"
            f"<i>Delivery needs the scan job running. /alerthelp explains.</i>",
            parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(f"Already watching {inst.display} {mode}.")


async def cmd_alerthelp(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if allowed(update):
        await update.message.reply_text(ALERT_HELP, parse_mode=ParseMode.HTML)


async def cmd_crazymode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    _, mode, _ = parse_args(ctx.args or [])
    keys = [k for k in C.SCAN_SYMBOLS if k in I.BY_KEY][:C.CRAZY_MAX_SYMBOLS]
    msg = await update.message.reply_text(f"⚔️ Scanning {len(keys)} markets on {mode}…")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: scanner.scan(keys, mode, fetch_ohlc, log=log.warning))
        text = scanner.format_scan(result)
        for res in scanner.tradeable(result["rows"]):
            journal.record(res)
    except Exception:  # noqa: BLE001
        log.exception("crazymode failed")
        text = "⚠️ Scan failed. Check the log."
    await msg.edit_text(text, parse_mode=ParseMode.HTML)


async def cmd_symbols(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    out = "<b>Tradeable universe</b>\n"
    for label, items in I.grouped().items():
        if items:
            out += f"\n<b>{label}</b>\n<code>" + " ".join(
                i.display for i in items) + "</code>\n"
    out += ("\n<i>Type any of them, or a nickname: gold, cable, nas100, aussie.</i>")
    await update.message.reply_text(out, parse_mode=ParseMode.HTML)


async def cmd_whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your Telegram user ID: {update.effective_user.id}")


def main() -> None:
    if not C.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing — fill in your .env file.")

    app = Application.builder().token(C.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("strategy", cmd_strategy))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("calibration", cmd_calibration))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler(["alert", "alerts"], cmd_alert))
    app.add_handler(CommandHandler("alerthelp", cmd_alerthelp))
    app.add_handler(CommandHandler(["crazymode", "scan"], cmd_crazymode))
    app.add_handler(CommandHandler("symbols", cmd_symbols))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^s\|"))

    log.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
