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


def parse_args(args: list[str]) -> tuple[str, str, str | None]:
    """Accepts any order: '/signal xauusd scalp', '/signal scalp', '/signal gold'."""
    symbol_key, mode = "xauusd", "intraday"
    found_symbol = found_mode = False

    for raw in args:
        a = raw.lower().strip().replace("/", "").replace("-", "")
        if a in C.MODES and not found_mode:
            mode, found_mode = a, True
        elif a in C.SYMBOL_ALIASES and not found_symbol:
            symbol_key, found_symbol = a, True
        elif a in ("scalping", "scalper"):
            mode, found_mode = "scalp", True
        elif a in ("day", "daytrade", "intra"):
            mode, found_mode = "intraday", True

    for raw in args:
        a = raw.lower().strip()
        if not found_symbol and a not in C.MODES and len(a) >= 3:
            return C.SYMBOL_ALIASES.get(a, "XAU/USD"), mode, (
                None if a in C.SYMBOL_ALIASES else f"Unknown symbol '{raw}', used XAU/USD."
            )

    return C.SYMBOL_ALIASES[symbol_key], mode, None


# --------------------------------------------------------------------------- #
#  Core analysis (runs the blocking HTTP calls off the event loop)
# --------------------------------------------------------------------------- #
async def analyse(symbol: str, mode: str) -> dict:
    spec = C.MODES[mode]
    loop = asyncio.get_running_loop()

    entry_df, trend_df, bias_df = await asyncio.gather(
        loop.run_in_executor(None, fetch_ohlc, symbol, spec.entry_tf, spec.bars),
        loop.run_in_executor(None, fetch_ohlc, symbol, spec.trend_tf, spec.bars),
        loop.run_in_executor(None, fetch_ohlc, symbol, spec.bias_tf, spec.bars),
    )

    return evaluate(entry_df, trend_df, bias_df, spec, datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
#  Handlers
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.message.reply_text(
        "<b>Signal bot online.</b>\n\n"
        "<code>/signal xauusd scalp</code>\n"
        "<code>/signal xauusd intraday</code>\n"
        "<code>/signal xauusd swing</code>\n\n"
        "Short forms work too: <code>/signal scalp</code> defaults to gold.\n\n"
        "You get one of three answers — <b>ENTRY</b>, <b>WAIT</b>, or <b>NO TRADE</b> — "
        "with three percentages:\n"
        "• <b>Confluence</b> — how many rules agree.\n"
        "• <b>Confidence</b> — that score minus hazards the rules cannot see.\n"
        "• <b>Probability</b> — odds the target trades before the stop.\n\n"
        "<code>/backtest intraday</code> — proves whether the rules make money. "
        "Run this before risking anything.\n"
        "<code>/backtest intraday calibrate</code> — same run, but the measured "
        "odds replace the model's guess in every later signal.\n"
        "<code>/calibration</code> — is the probability measured or guessed?\n"
        "<code>/strategy</code> — what the bot checks.\n\n"
        "Read /strategy before you trust a single number in here.",
        parse_mode=ParseMode.HTML,
    )


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


async def do_signal(symbol: str, mode: str, send, edit=None,
                    verbose: bool = False) -> None:
    # render() used to sit in an else: clause, which is NOT covered by the
    # except handlers above it. Any error formatting the reply escaped, the
    # placeholder was never edited, and the chat sat on "Reading…" forever.
    # Rendering belongs inside the try.
    try:
        res = await analyse(symbol, mode)
        text = view.render(symbol, res, verbose)
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{html.escape(str(exc))}"
    except Exception as exc:  # noqa: BLE001
        log.exception("signal failed")
        text = f"⚠️ Unexpected error: <code>{html.escape(type(exc).__name__)}</code>"

    key = symbol.replace("/", "").lower()
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

    symbol, mode, warning = parse_args(ctx.args or [])
    placeholder = await update.message.reply_text(
        f"⏳ Reading {symbol} {mode}…"
    )
    if warning:
        await update.message.reply_text(warning)

    await do_signal(symbol, mode, update.message.reply_text, placeholder.edit_text)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer("Refreshing…")
    if not allowed(update):
        return

    parts = q.data.split("|")
    _, symbol_key, mode = parts[:3]
    verbose = len(parts) > 3 and parts[3] == "v"
    symbol = C.SYMBOL_ALIASES.get(symbol_key, "XAU/USD")
    await do_signal(symbol, mode, q.message.reply_text, q.edit_message_text, verbose)


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

    symbol, mode, _ = parse_args(args)
    spec = C.MODES[mode]
    msg = await update.message.reply_text(
        f"⏱ Backtesting {symbol} {mode} on {spec.entry_tf} history.\n"
        f"This takes 1–4 minutes. I'll edit this message when done."
    )

    loop = asyncio.get_running_loop()
    try:
        df = await loop.run_in_executor(None, fetch_ohlc, symbol, spec.entry_tf, 5000)
        stats, report = await loop.run_in_executor(None, backtest_run, df, mode)
        header = f"<b>{symbol} {mode} backtest</b>\n{len(df)} × {spec.entry_tf} bars\n"
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
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^s\|"))

    log.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
