# XAUUSD Signal Bot

Type `/signal xauusd scalp` in Telegram. Get back **ENTRY**, **WAIT**, or **NO TRADE**
with a confluence score, a stop, targets, and position size.

---

## Read this first

The percentage is **rule agreement, not win probability**. It says "7 of my 8 conditions
line up," not "76% of these trades win." Any bot claiming the second thing is lying to you.

Nobody has verified these rules make money. I wrote a backtester so you can find out
before you risk anything — see step 6. If the backtest shows negative expectancy, the bot
is working correctly and the strategy is wrong. Those are different problems.

I'm not a financial advisor and this isn't financial advice.

---

## The strategy

**Multi-timeframe trend-pullback confluence.** Only buys dips in an uptrend, only sells
rallies in a downtrend. Never breakouts, never counter-trend, never in a range.

Three timeframes per mode:

| Mode | Trigger | Trend | Bias | Session window (UTC) |
|---|---|---|---|---|
| scalp | 5min | 15min | 1h | 07:00–11:00, 12:30–16:30 |
| intraday | 15min | 1h | 4h | 06:00–20:00 |
| swing | 4h | 1day | 1week | always |

**Hard vetoes.** Any one of these returns NO TRADE with no score at all:

- Trend timeframe ADX below 15 — the market is ranging
- Bias timeframe pointing the opposite way to the trend timeframe
- ATR above 2.5× or below 0.4× its own 100-bar median — news spike or dead tape
- Entry RSI already above 78 / below 22 — that's chasing, not a pullback
- Last candle more than 4 timeframes old — market closed

**The 100-point scorecard** (weights in `config.py`):

| Condition | Points |
|---|---|
| Bias timeframe EMA50 vs EMA200 and price agree | 20 |
| Trend timeframe EMA 20/50/200 stacked and sloping | 15 |
| Pullback depth 38–62% of last leg, price near EMA20 | 15 |
| Momentum turning: RSI recross + MACD histogram flip | 13 |
| ADX strength, scaled 15 → 32 | 12 |
| Confirming candle: engulfing, pin bar, or strong body | 10 |
| Structure intact: HH+HL for longs, LH+LL for shorts | 10 |
| Inside session window, ATR in normal band | 5 |

**Decision:** 70+ **and** a genuine trigger (momentum ≥ 6, candle ≥ 4) → ENTRY.
50–69 → WAIT, and it tells you exactly what's missing. Under 50 → NO TRADE.

**Levels:** stop goes beyond the last swing point plus a 0.35 ATR buffer, with a minimum
distance of 1.0/1.3/1.8 ATR by mode. Targets at 1R and 2R (2.5R for swing). If the nearest
opposing swing is closer than the mode's minimum R:R, the ENTRY is downgraded to WAIT —
there's no point taking a trade with no room to run.

---

> **Phone only, no computer?** Open **PHONE-SETUP.md** instead of this section.
> Everything below assumes a desktop.

## Setup — about 15 minutes

### 1. Telegram bot token
Message **@BotFather** on Telegram → `/newbot` → pick a name → copy the token it gives you.

### 2. Market data key
Sign up at **twelvedata.com** (free tier: 800 requests/day, 8/min). Copy your API key.
One `/signal` call uses 3 requests, so roughly 260 signals a day. Responses are cached,
so hammering the same command doesn't burn quota.

### 3. Install
```bash
pip install -r requirements.txt
cp .env.example .env
```
Open `.env`, paste both keys, set your account balance.

### 4. Check the strategy runs (no keys needed)
```bash
python selftest.py
```
Runs the engine over synthetic trending and ranging data. You should see it going long in
uptrends, short in downtrends, and mostly standing aside.

### 5. Start the bot
```bash
python bot.py
```
In Telegram: `/whoami` → copy your user ID into `ALLOWED_USER_IDS` in `.env` → restart.
Without this, anyone who finds your bot can use your API quota.

### 6. Backtest before you trade it
```bash
python backtest.py --mode intraday --live
```
Better: export a few years of M15 history from your broker (MT4/MT5 → Tools → History
Center → Export) and run:
```bash
python backtest.py --mode intraday --csv XAUUSD_M15.csv
```
Look at the bucket table. If expectancy doesn't rise with confidence, ignore the
percentage and treat ENTRY as a plain yes/no. If total R is negative, don't trade it.

### 7. Keep it running
Your laptop closing kills the bot. Cheapest reliable options:
- **Railway** or **Render** — free/hobby tier, push this folder, set env vars in their dashboard
- **Any $5 VPS** — `screen -S bot` then `python bot.py`, or write a systemd unit
- **Raspberry Pi** at home works fine

---

## Commands

| Command | Effect |
|---|---|
| `/signal xauusd scalp` | full analysis |
| `/signal scalp` | same, gold is the default |
| `/signal xauusd` | same, intraday is the default |
| `/strategy` | what the bot checks, in-chat |
| `/backtest intraday` | runs the backtest and replies with the results in chat |
| `/whoami` | your Telegram user ID |

Every reply has Scalp / Intraday / Swing buttons to re-run instantly.

Other symbols work too: `gold`, `silver`, `eurusd`, `gbpusd`, `usdjpy`, `btc`.
Add more in `SYMBOL_ALIASES` in `config.py`.

---

## Honest limitations

- **Twelve Data prices ≠ your broker's prices.** Spot gold is OTC; every broker's feed
  differs slightly. Levels will be a few cents off. For scalping, that matters.
- **No spread or commission modelling.** On a 5min scalp with a 3-point spread, a 1R
  target can be structurally unprofitable. Check your broker's typical gold spread against
  the ATR the bot reports.
- **No economic calendar.** The ATR spike filter catches the aftermath of CPI or FOMC, not
  the two minutes before. The bot warns you by clock hour; check the calendar yourself.
- **It cannot place orders.** By design. Add execution once, and only once, the backtest
  is positive across several years and you've forward-tested on demo for a month.
- **A 300-bar weekly history isn't 200 weeks.** In swing mode the bias EMA degrades to a
  shorter period automatically and says so.

## Tuning
Everything adjustable is in `config.py` — weights, thresholds, session hours, ATR
multiples. Change one thing at a time and re-run the backtest. Changing five things at
once teaches you nothing.
