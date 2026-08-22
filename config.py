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

# ---------------------------------------------------------------- risk config
ACCOUNT_BALANCE = float(_get("ACCOUNT_BALANCE", "5000"))
RISK_PCT = float(_get("RISK_PCT", "1.0"))          # % of balance per trade
CONTRACT_SIZE = float(_get("CONTRACT_SIZE", "100"))

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
        tp_multiples=(1.0, 2.0, 3.0), bars=300,
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
# A sweep costs 3 requests per instrument. Against a free tier of 8/min, keep
# the default list short and let the deadline stop it rather than the API.
SCAN_DEADLINE_S = float(_get("SCAN_DEADLINE_S", "45"))
CRAZY_MAX_SYMBOLS = int(_get("CRAZY_MAX_SYMBOLS", "8"))
_raw_scan = _get("SCAN_SYMBOLS", "xauusd,eurusd,gbpusd,usdjpy,audusd,xagusd")
SCAN_SYMBOLS = [s for s in (x.strip().lower() for x in _raw_scan.split(",")) if s]

ALERTS_FILE = _get("ALERTS_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "alerts.json"
)
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
# Written by:  python backtest.py --mode intraday --csv FILE --calibrate
CALIBRATION_FILE = _get("CALIBRATION_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "calibration.json"
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
