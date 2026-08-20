# XAUUSD Signal Bot

Type `/signal xauusd scalp` in Telegram. Get back **ENTRY**, **WAIT**, or **NO TRADE**
with three percentages, a stop, targets, and position size.

```
✅ ENTRY — SELL
Confluence    82%  ███████████░░░
Confidence    68%  ██████████░░░░
P(TP1 1R)     55%  ████████░░░░░░
P(TP2 2R)     37%  █████░░░░░░░░░

Expectancy  +0.16R  half at TP1, rest to TP2
Breakeven      52%  win rate needed at 1R
```

---

## Read this first

**The three numbers are not the same number.** Most bots print one figure and let you
assume it means whatever you want. This one separates them:

| Number | Question it answers | Where it comes from |
|---|---|---|
| **Confluence** | How many of my rules agree? | The 100-point scorecard. Nothing more. |
| **Confidence** | How much should I trust this read? | Confluence minus hazards the scorecard structurally cannot see — news hours, dead sessions, a bias EMA built on short history, no room to the next swing. Every deduction is listed under the signal. |
| **Probability** | How often does this actually pay? | Barrier maths, plus a capped edge for the score, minus costs — then overwritten by your own backtest results once you calibrate. |

Confluence 82% has never meant "82% of these win." Probability is the number that tries
to mean that, and it starts out honest about being a model: until you run
`--calibrate`, every signal says *"model estimate — no backtest calibration yet."*
`/calibration` tells you which one you're reading at any time.

Nobody has verified these rules make money. I wrote a backtester so you can find out
before you risk anything — see step 6. If the backtest shows negative expectancy, the bot
is working correctly and the strategy is wrong. Those are different problems.

I'm not a financial advisor and this isn't financial advice.

---

## Where the probability comes from

No magic, and deliberately no optimism.

**1. Start from the barriers, not from hope.** Stop 1R away, target *k*R away: a
driftless market touches the target first `1/(1+k)` of the time. At 1R that's 50%, at 2R
it's 33%. That same fraction is *also* the win rate you need to break even at that
payoff — so the null hypothesis is "expectancy exactly zero," and every point above it
has to be earned rather than assumed.

**2. Subtract what the trade costs.** `COST_R` (default 0.05) is your spread plus
commission as a fraction of the stop distance. Paying it puts you 0.05R closer to the
stop and 0.05R further from the target before price does anything at all. Measure yours
— on a scalp it moves the odds more than any indicator setting in this repo.

**3. Add a capped edge for the score.** The confluence score buys a log-odds
adjustment, pivoting at `PROB_EDGE_PIVOT` (60 = the model assumes no edge). A perfect
100 is worth roughly **twelve** percentage points on a 1R target, not fifty. Targets
that sit beyond the next opposing swing lose points instead — price has to chew through
that level to pay you.

**4. Then let the data overrule all of it.** `backtest.py --calibrate` replays the
strategy over your history, measures how often each score bucket actually reached TP1
and the final target, and writes `calibration.json`. Live signals shrink the model
toward those measurements, weighted by sample size: eight trades barely move the number,
four hundred bury the model. Buckets thinner than `CALIBRATION_MIN_TRADES` are ignored
and it falls back to the mode-wide rate, then to the model.

Nothing is ever quoted above `PROB_CEIL` (85%). A bot printing 95% is lying.

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

**Confidence deductions** (`CONFIDENCE_PENALTIES` in `config.py`) — each one is printed
under the signal so you can see why confidence sits below confluence:

| Hazard | Cost |
|---|---|
| Outside the session window | −8 |
| Nearest opposing swing inside the target | −8 |
| High-impact news hour | −6 |
| Bias EMA degraded by short history | −6 |
| Confirming candle weak or absent | −6 |
| Only one of RSI / MACD turned | −5 |
| Swing structure not fully intact | −5 |
| ATR outside the 0.7–1.8× band | −5 |
| Last candle ageing | −4 |
| *Every scorecard line passed* | *+6* |

A hard veto returns confidence 0% and quotes no probability at all.

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
Look at the bucket table. If expectancy doesn't rise with confluence, ignore the
percentage and treat ENTRY as a plain yes/no. If total R is negative, don't trade it.

The report also grades the probability model against itself:

```
Was the probability honest?
  TP1    promised 52%   paid 53%
  final  promised 36%   paid 45%
  model is 9 pts too pessimistic
```

### 6b. Calibrate, so the odds stop being guesses
```bash
python backtest.py --mode intraday --csv XAUUSD_M15.csv --calibrate
```
Same run, but it writes `calibration.json`. From then on every signal quotes measured
hit rates instead of modelled ones and says so, and `/calibration` shows the sample it
is leaning on. Re-run it per mode — the file holds all three. The bot notices the new
file without a restart.

No laptop? `/backtest intraday calibrate` in Telegram does the same thing, on whatever
history your data provider will hand over.

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
| `/backtest intraday calibrate` | same run, and the measured odds replace the model's guess from then on |
| `/calibration` | are the odds measured or modelled? shows the sample behind them |
| `/whoami` | your Telegram user ID |

Every reply has Scalp / Intraday / Swing buttons to re-run instantly.

Other symbols work too: `gold`, `silver`, `eurusd`, `gbpusd`, `usdjpy`, `btc`.
Add more in `SYMBOL_ALIASES` in `config.py`.

---

## Honest limitations

- **Twelve Data prices ≠ your broker's prices.** Spot gold is OTC; every broker's feed
  differs slightly. Levels will be a few cents off. For scalping, that matters.
- **Spread and commission are one number, not a simulation.** `COST_R` shifts the
  probability and the breakeven line, but the backtester still fills at the exact level.
  On a 5min scalp with a 3-point spread, a 1R target can be structurally unprofitable.
  Check your broker's typical gold spread against the ATR the bot reports and set
  `COST_R` from it.
- **An uncalibrated probability is arithmetic, not evidence.** The barrier maths is
  sound and the edge term is a guess with a cap on it. It only becomes a measurement
  after `--calibrate`, and then only for the symbol, timeframe and period you fed it.
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
