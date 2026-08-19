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
CONTRACT_SIZE = float(_get("CONTRACT_SIZE", "100"))  # oz per 1.00 lot XAUUSD

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
