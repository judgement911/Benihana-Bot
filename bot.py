"""Telegram front end. Usage: /signal xauusd scalp"""
from __future__ import annotations

import asyncio
import html
import logging
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
from backtest import run as backtest_run
from data import DataError, fetch_ohlc
from strategy import DIR_NAME, evaluate

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


def bar(pct: int, width: int = 14) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def fmt_price(x: float) -> str:
    return f"{x:,.2f}"


def render(symbol: str, res: dict) -> str:
    mode = res["mode"].upper()
    head = f"<b>{symbol} · {mode}</b>\n"
    head += f"<code>{fmt_price(res['price'])}</code> · candle closed "
    head += f"{res['as_of'].strftime('%H:%M')} UTC\n"
    tfs = res["timeframes"]
    head += f"<i>{tfs['entry']} trigger / {tfs['trend']} trend / {tfs['bias']} bias</i>\n\n"

    dec = res["decision"]

    # --- vetoed outright -------------------------------------------------- #
    if res["vetoes"]:
        body = "🚫 <b>NO TRADE</b>\n<i>Hard filter blocked this — score not even calculated.</i>\n\n"
        for v in res["vetoes"]:
            body += f"• {v}\n"
        return head + body

    icon = {"ENTRY": "✅", "WAIT": "⏳", "NO TRADE": "🚫"}[dec]
    label = f"{DIR_NAME[res['direction']]}" if dec == "ENTRY" else dec
    body = f"{icon} <b>{dec}"
    if dec == "ENTRY":
        body += f" — {label}"
    body += "</b>\n"
    body += f"Confluence <b>{res['score']}%</b>  <code>{bar(res['score'])}</code>\n"
    body += f"<i>rule agreement, not win probability</i>\n\n"

    lv = res["levels"]
    if dec == "ENTRY" and lv:
        body += "<b>Trade plan</b>\n<pre>"
        body += f"Entry  {fmt_price(lv['entry'])}  (market)\n"
        body += f"Stop   {fmt_price(lv['stop'])}  ({lv['risk_points']} pts risk)\n"
        for n, (tp, m) in enumerate(zip(lv["tps"], lv["tp_multiples"]), start=1):
            body += f"TP{n}    {fmt_price(tp)}  ({m}R)\n"
        body += f"Size   {lv['lots']} lots = ${lv['risk_cash']} risk\n"
        body += f"ATR    {lv['atr']} pts</pre>\n"
    elif lv:
        body += (
            f"<i>If it does trigger: stop would sit near {fmt_price(lv['stop'])}, "
            f"{lv['risk_points']} pts away.</i>\n\n"
        )

    body += "<b>Scorecard</b>\n"
    for r in res["reasons"]:
        mark = "✓" if r["ok"] else "✗"
        body += f"{mark} {r['text']} <code>[{r['points']:.0f}/{r['max']}]</code>\n"

    if dec != "ENTRY" and res["missing"]:
        body += "\n<b>Waiting on</b>\n"
        for m in res["missing"][:4]:
            body += f"• {m}\n"

    if res.get("news_warning"):
        body += "\n⚠️ <i>High-impact US data often lands this hour. Check the calendar.</i>\n"

    return head + body


def keyboard(symbol_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Scalp", callback_data=f"s|{symbol_key}|scalp"),
                InlineKeyboardButton("Intraday", callback_data=f"s|{symbol_key}|intraday"),
                InlineKeyboardButton("Swing", callback_data=f"s|{symbol_key}|swing"),
            ]
        ]
    )


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
        "with a confluence score.\n\n"
        "<code>/backtest intraday</code> — proves whether the rules make money. "
        "Run this before risking anything.\n"
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
        "<b>The score</b> is a weighted sum of 8 conditions totalling 100. "
        "70+ with a live trigger = ENTRY. 50–69 = WAIT. Under 50 = NO TRADE.\n\n"
        "It is agreement between rules. It is <b>not</b> a probability of winning, "
        "and nobody has verified the rules are profitable on your broker's data yet. "
        "Run <code>backtest.py</code> first.",
        parse_mode=ParseMode.HTML,
    )


async def do_signal(symbol: str, mode: str, send, edit=None) -> None:
    try:
        res = await analyse(symbol, mode)
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{exc}"
    except Exception as exc:  # noqa: BLE001
        log.exception("analysis failed")
        text = f"⚠️ Unexpected error: <code>{type(exc).__name__}</code>"
    else:
        text = render(symbol, res)

    key = symbol.replace("/", "").lower()
    if edit:
        await edit(text, parse_mode=ParseMode.HTML, reply_markup=keyboard(key))
    else:
        await send(text, parse_mode=ParseMode.HTML, reply_markup=keyboard(key))


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

    _, symbol_key, mode = q.data.split("|")
    symbol = C.SYMBOL_ALIASES.get(symbol_key, "XAU/USD")
    await do_signal(symbol, mode, q.message.reply_text, q.edit_message_text)


async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs the real strategy over history and reports expectancy. Slow on
    purpose — this is the step that tells you whether any of this works."""
    if not allowed(update):
        return

    symbol, mode, _ = parse_args(ctx.args or [])
    spec = C.MODES[mode]
    msg = await update.message.reply_text(
        f"⏱ Backtesting {symbol} {mode} on {spec.entry_tf} history.\n"
        f"This takes 1–4 minutes. I'll edit this message when done."
    )

    loop = asyncio.get_running_loop()
    try:
        df = await loop.run_in_executor(None, fetch_ohlc, symbol, spec.entry_tf, 5000)
        _, report = await loop.run_in_executor(None, backtest_run, df, mode)
        header = f"<b>{symbol} {mode} backtest</b>\n{len(df)} × {spec.entry_tf} bars\n"
        text = header + f"<pre>{html.escape(report)}</pre>"
    except DataError as exc:
        text = f"⚠️ <b>Data problem</b>\n{exc}"
    except Exception as exc:  # noqa: BLE001
        log.exception("backtest failed")
        text = f"⚠️ Backtest error: <code>{type(exc).__name__}</code>"

    await msg.edit_text(text, parse_mode=ParseMode.HTML)


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
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^s\|"))

    log.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
