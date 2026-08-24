"""All tunable knobs live here. Change numbers here, not in strategy.py."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:                        # optional; absent on PythonAnywhere free accounts
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:                        # PythonAnywhere: a plain python file holding your keys
    import pa_config as _local
except ImportError:
    _local = None


def _get(name: str, default: str = "") -> str:
    """Look in pa_config.py first, then environment variables."""
    if _local is not None and hasattr(_local, name):
        return str(getattr(_local, name))
    return os.getenv(name, default)

# ---------------------------------------------------------------- credentials
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TWELVEDATA_API_KEY = _get("TWELVEDATA_API_KEY")
WEBHOOK_SECRET = _get("WEBHOOK_SECRET", "change-me")

# Comma-separated Telegram user IDs allowed to use the bot. Empty = allow all.
_raw_allowed = _get("ALLOWED_USER_IDS").strip()
ALLOWED_USER_IDS = {
    int(x) for x in _raw_allowed.replace(" ", "").split(",") if x.isdigit()
} if _raw_allowed else set()

# Accounts that are never billed and never expire — the operator's own.
# Falls back to ALLOWED_USER_IDS so an existing deployment keeps working.
_raw_owner = _get("OWNER_IDS").strip()
OWNER_IDS = {
    int(x) for x in _raw_owner.replace(" ", "").split(",") if x.isdigit()
} if _raw_owner else set()

# Subscriptions are OFF unless switched on. A deployment that upgrades to
# this version must not suddenly start refusing its existing users, so the
# gate keeps its old behaviour until the operator opts in.
SUBSCRIPTIONS_ENABLED = _get("SUBSCRIPTIONS_ENABLED", "0") not in (
    "0", "false", "False", "")

# ---------------------------------------------------------------- scanning
# These three were deleted with the paid-plan features but /scan, scan_job
# and scanner all still read them, so /scan raised AttributeError before it
# reached its own error handling and answered with nothing at all.
#
# The default list is only what a free data plan actually serves: FX majors
# and the two metals. Indices and energy need a paid plan, and a symbol the
# provider refuses just burns one of the day's 800 requests.
SCAN_DEADLINE_S = float(_get("SCAN_DEADLINE_S", "45"))
# Each symbol costs one request per timeframe, so a scan of 8 is ~24 of the
# daily 800. Raising this is the fastest way to exhaust the quota.
CRAZY_MAX_SYMBOLS = int(_get("CRAZY_MAX_SYMBOLS", "6"))
_raw_scan = _get("SCAN_SYMBOLS", "xauusd,eurusd,gbpusd,usdjpy,audusd,xagusd")
SCAN_SYMBOLS = [s for s in (x.strip().lower() for x in _raw_scan.split(",")) if s]

# ---------------------------------------------------------------- risk config
ACCOUNT_BALANCE = float(_get("ACCOUNT_BALANCE", "5000"))
RISK_PCT = float(_get("RISK_PCT", "1.0"))          # % of balance per trade
CONTRACT_SIZE = float(_get("CONTRACT_SIZE", "100"))

# The most a trade may pay in dealing cost, as a share of its own risk.
# Cost in R is spread / stop distance, so a tight stop is expensive: the
# recorded backtests contain trades paying 2.14 R in spread alone to open,
# which need a 214% edge to break even. Those come from bars where the tape
# is frozen and ATR collapses, and the existing "dead tape" veto misses them
# because it is relative — over a long freeze the rolling median ATR falls
# too, so the ratio looks normal. This limit is absolute and does not care
# why the stop is small.
MAX_COST_R = float(_get("MAX_COST_R", "0.20"))

# ---------------------------------------------------------------- data source
DATA_PROVIDER = _get("DATA_PROVIDER", "twelvedata").lower()
OANDA_TOKEN = _get("OANDA_TOKEN")
OANDA_ENV = _get("OANDA_ENV", "practice")  # oz per 1.00 lot XAUUSD

# ---------------------------------------------------------------- symbol map
SYMBOL_ALIASES = {
    "xauusd": "XAU/USD",
    "gold": "XAU/USD",
    "xau": "XAU/USD",
    "xagusd": "XAG/USD",
    "silver": "XAG/USD",
    "eurusd": "EUR/USD",
    "gbpusd": "GBP/USD",
    "usdjpy": "USD/JPY",
    "btcusd": "BTC/USD",
    "btc": "BTC/USD",
}


@dataclass(frozen=True)
class ModeSpec:
    name: str
    entry_tf: str        # where the trigger is read
    trend_tf: str        # where direction is decided
    bias_tf: str         # where the veto lives
    sessions_utc: tuple  # ((start_hour, start_min, end_hour, end_min), ...)
    min_rr: float        # below this, an ENTRY is downgraded to WAIT
    atr_sl_mult: float   # stop floor, in ATR: never tighter than this
    max_sl_mult: float   # stop ceiling, in ATR: never wider than this
    tp_multiples: tuple  # take-profit levels in R
    bars: int            # history to request per timeframe


# Three targets on every mode, capped at 3R. Nothing beyond 3R is ever
# quoted: the barrier model puts P(4R) near a fifth even before costs, and
# a target that far out is a lottery ticket dressed as a plan.
#
# Gold's liquidity: London open 07:00 UTC, NY open 13:30 UTC,
# LDN/NY overlap 12:00-16:00 UTC is where the real range happens.
MODES = {
    "scalp": ModeSpec(
        "scalp", "5min", "15min", "1h",
        ((7, 0, 11, 0), (12, 30, 16, 30)),
        min_rr=1.3, atr_sl_mult=0.8, max_sl_mult=1.6,
        # 576 bars = 48h of 5min. Kage reads the PRIOR calendar day's high
        # and low, and 300 bars is only 25h, so the prior day arrives
        # truncated — median 163 of its 288 bars — and the level is simply
        # wrong on half the signals. Measured: 463 real signals lost, 684
        # phantom ones gained, t 3.09 -> 2.10. Every other ruleset is
        # indifferent to the extra history.
        tp_multiples=(1.0, 2.0, 3.0), bars=576,
    ),
    "intraday": ModeSpec(
        "intraday", "15min", "1h", "4h",
        ((6, 0, 20, 0),),
        min_rr=1.5, atr_sl_mult=1.0, max_sl_mult=2.2,
        tp_multiples=(1.0, 2.0, 3.0), bars=300,
    ),
    "swing": ModeSpec(
        "swing", "4h", "1day", "1week",
        ((0, 0, 23, 59),),
        min_rr=1.8, atr_sl_mult=1.5, max_sl_mult=2.8,
        tp_multiples=(1.0, 2.0, 3.0), bars=300,
    ),
}

# -------------------------------------------------------------- stop sizing
# The stop used to be "whichever is further: the ATR floor, or the last swing
# beyond it". Nothing bounded the second half, so a swing low 90 points away
# became a 90-point stop on gold. Structure still places the stop, but only
# inside a band the mode's volatility justifies: never tighter than
# atr_sl_mult x ATR, never wider than max_sl_mult x ATR.
#
# Tightening a stop is not free. It raises the share of the stop eaten by the
# spread, which the probability model now charges for, and it puts the stop
# closer to ordinary noise. These are deliberately not tiny.
SL_STRUCT_BUFFER = float(_get("SL_STRUCT_BUFFER", "0.35"))   # ATR beyond the swing

# ---------------------------------------------------------------- thresholds
ENTRY_MIN_SCORE = 70      # >= this AND a trigger present -> ENTRY
WAIT_MIN_SCORE = 50       # >= this -> WAIT (setup forming)
                          # below -> NO TRADE

ADX_GATE = 15.0           # below this the market is chop; hard veto
ADX_GOOD = 22.0           # full marks at/above this
ADX_STRONG = 32.0

RSI_LONG_RECROSS = 45.0   # momentum turning back up inside an uptrend
RSI_SHORT_RECROSS = 55.0
RSI_OVERBOUGHT = 78.0     # chasing a vertical move is not a pullback entry
RSI_OVERSOLD = 22.0

PULLBACK_IDEAL = (0.382, 0.618)   # golden pocket of the last impulse leg
PULLBACK_ACCEPT = (0.20, 0.75)

VOL_SPIKE_MULT = 2.5      # ATR vs its own median -> news spike, stand aside
VOL_DEAD_MULT = 0.40      # ATR vs its own median -> dead tape, stand aside

# Weights must total 100.
WEIGHTS = {
    "bias_align": 20,
    "trend_align": 15,
    "adx_strength": 12,
    "pullback_quality": 15,
    "momentum_trigger": 13,
    "candle_confirm": 10,
    "structure": 10,
    "session_vol": 5,
}

# High-impact releases that routinely gap gold. The bot cannot see the calendar
# on the free tier, so it warns you by clock instead. All times UTC.
NEWS_WARNING_HOURS_UTC = {12, 13, 14, 18}  # NFP/CPI 12:30, FOMC 18:00

# /news reads the real Forex Factory calendar. Times are printed in your
# timezone — a release time you have to convert in your head is one you will
# get wrong. Default UTC+7 (WIB).
NEWS_TZ_OFFSET = float(_get("NEWS_TZ_OFFSET", "7"))
NEWS_TZ_LABEL = _get("NEWS_TZ_LABEL", "WIB")
NEWS_CACHE_TTL = int(_get("NEWS_CACHE_TTL", "3600"))

# ------------------------------------------------------ the three percentages
# The signal prints three numbers and they mean three different things. Keep
# them straight or the whole thing is decoration:
#
#   Confluence  — the 100-point scorecard above. Rule agreement, nothing more.
#   Confidence  — conviction in THIS read: the confluence score adjusted for
#                 hazards the scorecard cannot see (news hour, dead session,
#                 truncated history, no room to the next swing).
#   Probability — P(target is reached before the stop), from barrier maths plus
#                 a score-driven edge term, then corrected by real backtest
#                 results when calibration.json exists.

# Spread + commission expressed as a fraction of the 1R stop distance. Gold on
# a retail account is commonly 20-30 cents of spread; against a 3-dollar stop
# that is ~0.08R. Measure yours and set it — it moves the odds more than any
# indicator setting here.
COST_R = float(_get("COST_R", "0.05"))

# Edge term. logit(p) gets PROB_EDGE_GAIN * (score - PROB_EDGE_PIVOT) / 100
# added to it. At the pivot the model assumes the strategy has NO edge and the
# odds are pure barrier maths. Gain 1.2 means a perfect 100 score buys about
# +12 percentage points on a 1R target — deliberately modest, because nobody
# has proven this strategy beats a coin flip yet.
PROB_EDGE_GAIN = float(_get("PROB_EDGE_GAIN", "1.2"))
PROB_EDGE_PIVOT = float(_get("PROB_EDGE_PIVOT", "60"))

# Log-odds removed when the target sits beyond the next opposing swing. Price
# has to chew through that level to pay you.
PROB_ROOM_PENALTY = float(_get("PROB_ROOM_PENALTY", "0.8"))

# Hard limits on anything printed as a probability. A bot quoting 95% is lying.
PROB_FLOOR = 0.05
PROB_CEIL = 0.85

# ------------------------------------------------------------- scanning/alerts
# A sweep costs 3 requests per instrument. Against a free tier of 8/min, keep)
# Only an ENTRY this confident is worth interrupting someone for.
ALERT_MIN_CONFIDENCE = int(_get("ALERT_MIN_CONFIDENCE", "55"))

# -------------------------------------------------------------- signal journal
# Every ENTRY is written down when issued and graded later against candles the
# bot had not seen. This is what /stats reads.
JOURNAL_FILE = _get("JOURNAL_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "journal.json"
)
JOURNAL_MAX_ENTRIES = int(_get("JOURNAL_MAX_ENTRIES", "2000"))
# Bars after entry to wait before giving up on a signal that never resolved.
JOURNAL_MAX_BARS = int(_get("JOURNAL_MAX_BARS", "120"))

# ----------------------------------------------------------- auto-signals
# §12: a setup this confident is sent without being asked. It still has to
# satisfy the strategy's real entry conditions — a market merely moving is
# not a signal. Per-user /setconf overrides this.
AUTO_SIGNAL_CONFIDENCE = int(_get("AUTO_SIGNAL_CONFIDENCE", "85"))

# -------------------------------------------------------------- lifecycle
# Once the first target pays, the rest of the position rides at zero risk.
# This is the plan the expectancy figure already assumes, so the lifecycle
# tracker has to follow it or /stats and the signal disagree.
MOVE_TO_BREAKEVEN_AFTER_TP1 = _get("MOVE_TO_BREAKEVEN_AFTER_TP1", "1") not in ("0", "false", "False")

# ------------------------------------------------------------- strategies
# Crimson Flow — momentum breakout. Weights total 100.
CRIMSON_CHANNEL = int(_get("CRIMSON_CHANNEL", "20"))     # Donchian lookback
CRIMSON_CLOSE_POS = float(_get("CRIMSON_CLOSE_POS", "0.66"))  # close in own range
CRIMSON_ATR_EXPANSION = float(_get("CRIMSON_ATR_EXPANSION", "1.05"))
CRIMSON_WEIGHTS = {
    "bias_align": 20, "breakout": 30, "adx_rising": 18,
    "close_position": 17, "atr_expansion": 10, "session": 5,
}

# Kage Protocol — volatility squeeze. Weights total 100.
# Kage Protocol — prior-day extension breakout. Replaced the Bollinger
# squeeze in Aug 2026 because the squeeze measured -0.357 R per trade at
# scalp over 871 trades and could not be rescued by tuning.
#
# Price must break k ATR CLEAR of yesterday's high or low and close there.
# The distance matters: at k=0 the same rule earns +0.015 R and at k=1.5 it
# earns +0.090, because a level is only meaningful once price has committed
# past it rather than merely touched it. Fading the touch instead loses
# money in all four quarters (-0.077 R, t -2.45), so the direction of this
# rule is not a coin flip dressed up.
KAGE_BUFFER_ATR = float(_get("KAGE_BUFFER_ATR", "1.5"))
# The stop the rule was measured with. Cost in R is spread over stop
# distance, so scalp's 1.6 ATR ceiling would nearly double the dealing cost
# and take net from +0.115 to +0.053 — the same edge, priced out.
KAGE_STOP_ATR = float(_get("KAGE_STOP_ATR", "3.0"))
# Bars before the trade is abandoned. Flat from 12 to 36; 6 breaks it.
KAGE_HOLD = int(_get("KAGE_HOLD", "18"))
# Only where the spread is thin. Gold costs 0.25-0.35 through London and NY
# and 1.20 at the 21:00-23:00 rollover; the rule's profit is concentrated in
# exactly the hours a retail broker charges most for, so trading it around
# the clock hands the edge back. 07:00-21:00 UTC keeps it.
KAGE_HOURS_UTC = (7, 21)
# The prior day must be at least half present in the data before its high
# and low mean anything. Also kills the frozen weekend, where Saturday's
# "range" is a third of a point.
KAGE_MIN_PRIOR_FRACTION = float(_get("KAGE_MIN_PRIOR_FRACTION", "0.5"))
KAGE_WEIGHTS = {
    "break": 34, "commitment": 22, "clean_level": 16,
    "room": 12, "session": 16,
}

# Zanshin Sweep — liquidity grab at a tested level. Weights total 100.
# Every threshold is a round number chosen from the shape of the pattern,
# NOT tuned against a backtest: the first measurement has to be a test, not
# a memory of one.
ZANSHIN_LEVEL_TOL_ATR = float(_get("ZANSHIN_LEVEL_TOL_ATR", "0.45"))
ZANSHIN_LEVEL_LOOKBACK = int(_get("ZANSHIN_LEVEL_LOOKBACK", "200"))
ZANSHIN_MIN_TOUCHES = int(_get("ZANSHIN_MIN_TOUCHES", "2"))
ZANSHIN_MAX_DISTANCE_ATR = float(_get("ZANSHIN_MAX_DISTANCE_ATR", "2.0"))
ZANSHIN_MIN_DEPTH_ATR = float(_get("ZANSHIN_MIN_DEPTH_ATR", "0.15"))
ZANSHIN_GOOD_DEPTH_ATR = float(_get("ZANSHIN_GOOD_DEPTH_ATR", "0.35"))
ZANSHIN_MIN_CLOSE_POS = float(_get("ZANSHIN_MIN_CLOSE_POS", "0.60"))
ZANSHIN_MIN_RANGE_ATR = float(_get("ZANSHIN_MIN_RANGE_ATR", "1.00"))
ZANSHIN_VOL_MIN = float(_get("ZANSHIN_VOL_MIN", "0.60"))
ZANSHIN_VOL_MAX = float(_get("ZANSHIN_VOL_MAX", "2.20"))
ZANSHIN_WEIGHTS = {
    "level": 22, "sweep": 20, "reclaim": 20,
    "expansion": 13, "liquidity": 10, "context": 15,
}

# Shogun Pulse — the one strategy here found by measurement. Phase 2 put the
# mean-reversion signal at 6-12 bars with p<0.01 on two instruments, so the
# hold is 12 bars and the exit is the clock, not a target. Thresholds are the
# ones tested; changing them changes a validated result into an untested one.
SHOGUN_LOOKBACK = int(_get("SHOGUN_LOOKBACK", "20"))
SHOGUN_Z = float(_get("SHOGUN_Z", "2.5"))
SHOGUN_HOLD = int(_get("SHOGUN_HOLD", "12"))
SHOGUN_STOP_ATR = float(_get("SHOGUN_STOP_ATR", "2.0"))
SHOGUN_WEIGHTS = {"stretch": 45, "volatility": 20, "session": 15,
                  "not_trending": 20}

# ------------------------------------------------------------- volatility
# ATR against its own recent median on the entry timeframe. Real measurement,
# bucketed for display; nothing here is assigned by mood.
VOL_HIGH_RATIO = float(_get("VOL_HIGH_RATIO", "1.35"))
VOL_LOW_RATIO = float(_get("VOL_LOW_RATIO", "0.80"))

# ----------------------------------------------------------------- money
# Lot sizes are rounded DOWN to this step so a trade never risks more than the
# user asked for. LOT_MIN is the smallest order a broker will take; a size
# below it is reported rather than silently rounded up.
LOT_STEP = float(_get("LOT_STEP", "0.01"))
LOT_MIN = float(_get("LOT_MIN", "0.01"))

# Non-USD risk amounts are converted with a live rate, cached this long. There
# is no hardcoded fallback: a stale rate mis-sizes every position silently.
FX_RATE_TTL = float(_get("FX_RATE_TTL", "3600"))

# Fallback rate in IDR per USD, used only when the data provider cannot
# quote USD/IDR — free plans usually carry the majors and not this pair.
# Empty by default: an absent rate makes the bot decline to size a position,
# which is the honest answer, but a trader who knows today's rate should be
# able to say so rather than lose the feature entirely.
USD_IDR_RATE = _get("USD_IDR_RATE", "")

# ------------------------------------------------------------- user settings
# Language, strategy, confidence floor and the risk-management envelope, keyed
# by Telegram user id. Anchored to the code directory for the same reason as
# the calibration file: a WSGI worker does not run from the app directory.
USERS_FILE = _get("USERS_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "users.json"
)

# ------------------------------------------------------------------ backtest
# 1200 bars of 15min is twelve days, and the 4h bias needs 960 of them before
# it even has 60 candles to read — so the old budget evaluated a couple of
# hundred bars and produced two trades, permanently under the calibration
# minimum. /backtest calibrate could not have worked at that size.
#
# 2500 bars is ~26 days and yields enough trades to calibrate, at roughly 25
# CPU-seconds. That matters: a free PythonAnywhere account gets 100 a day, and
# a webhook has about 60 seconds before Telegram gives up and retries. For a
# deeper sample run backtest.py from a console, where neither limit applies.
BACKTEST_BARS = int(_get("BACKTEST_BARS", "2500"))
BACKTEST_BARS_CLI = int(_get("BACKTEST_BARS_CLI", "5000"))

# ---------------------------------------------------------------- calibration
# Written on this host by:  python build_calibration.py
# Deliberately gitignored: it is measured on whatever data this machine has,
# and a deploy must never overwrite a calibration the operator generated.
CALIBRATION_FILE = _get("CALIBRATION_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "calibration.json"
)

# ...which leaves the problem that the deployment host cannot generate one.
# Measuring the ladder takes half an hour of CPU across every strategy and
# both instruments; a free hosting tier has about a hundred seconds a day.
# So the repository also ships a calibration built from the committed
# candles, and it is read only when the host has no calibration of its own.
# A local measurement always wins — this is a floor, not an override.
CALIBRATION_FALLBACK = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "calibration.default.json"
)

# Empirical-Bayes shrinkage: how many "pseudo-trades" of model prior a real
# backtest sample has to outvote. 25 means a 25-trade bucket is weighted 50/50
# against the model. Lower it only if you have thousands of trades.
CALIBRATION_PRIOR_WEIGHT = float(_get("CALIBRATION_PRIOR_WEIGHT", "25"))
CALIBRATION_MIN_TRADES = int(_get("CALIBRATION_MIN_TRADES", "8"))

# ----------------------------------------------------------------- confidence
# Points knocked off the confluence score for hazards the scorecard misses.
# These stack; the result is clamped to 5-99.
CONFIDENCE_PENALTIES = {
    "outside_session": 8,   # liquidity is thin, spreads are wide
    "news_hour": 6,         # a release can erase the whole technical picture
    "short_history": 6,     # bias EMA degraded because history was too short
    "no_room": 8,           # nearest opposing swing is inside the target
    "half_trigger": 5,      # only one of RSI / MACD turned
    "weak_structure": 5,    # swing structure not fully intact
    "odd_volatility": 5,    # ATR outside the 0.7-1.8x comfort band
    "ageing_data": 4,       # last candle older than 1.5 bars
    "weak_candle": 6,       # confirming candle unremarkable or absent
}
CONFIDENCE_CLEAN_SWEEP_BONUS = 6   # every scorecard line passed
