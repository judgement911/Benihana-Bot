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
    atr_sl_mult: float   # stop buffer beyond structure, in ATR
    tp_multiples: tuple  # take-profit levels in R
    bars: int            # history to request per timeframe


# Gold's liquidity: London open 07:00 UTC, NY open 13:30 UTC,
# LDN/NY overlap 12:00-16:00 UTC is where the real range happens.
MODES = {
    "scalp": ModeSpec(
        "scalp", "5min", "15min", "1h",
        ((7, 0, 11, 0), (12, 30, 16, 30)),
        min_rr=1.3, atr_sl_mult=1.0, tp_multiples=(1.0, 2.0), bars=300,
    ),
    "intraday": ModeSpec(
        "intraday", "15min", "1h", "4h",
        ((6, 0, 20, 0),),
        min_rr=1.5, atr_sl_mult=1.3, tp_multiples=(1.0, 2.0), bars=300,
    ),
    "swing": ModeSpec(
        "swing", "4h", "1day", "1week",
        ((0, 0, 23, 59),),
        min_rr=1.8, atr_sl_mult=1.8, tp_multiples=(1.0, 2.5), bars=300,
    ),
}

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
