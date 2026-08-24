"""
Copy this to pa_config.py on PythonAnywhere and fill it in.
pa_config.py is gitignored — your keys never reach GitHub.
"""

# From @BotFather
TELEGRAM_BOT_TOKEN = "123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Your PythonAnywhere username (the one in yourname.pythonanywhere.com)
PA_USERNAME = "yourname"

# Any random string you invent. Stops strangers POSTing to your webhook.
WEBHOOK_SECRET = "pick-something-random-here-9f3k2"

# Lock the bot to you. Send /whoami to the bot to get your ID, then paste it.
# Leave as "" while testing.
ALLOWED_USER_IDS = ""

# Where price data comes from.
#   "twelvedata" — true XAU/USD. Free key at twelvedata.com. Works on PythonAnywhere.
#   "oanda"      — true XAU/USD from a real broker. Free practice account + token.
#   "binance"    — PAXG gold token, no key. Geo-blocked on PythonAnywhere (451).
DATA_PROVIDER = "twelvedata"

# Needed if DATA_PROVIDER = "twelvedata". Free at twelvedata.com (800 calls/day).
TWELVEDATA_API_KEY = ""

# Only needed if DATA_PROVIDER = "oanda".
# Free practice account at oanda.com -> Manage API Access -> generate token.
OANDA_TOKEN = ""
OANDA_ENV = "practice"

# Used only for position-size maths. No money moves.
ACCOUNT_BALANCE = "5000"
RISK_PCT = "1.0"
CONTRACT_SIZE = "100"

# --------------------------------------------------------------------- ACCESS
# Subscriptions. Leave OFF ("0") and the bot behaves exactly as before:
# whoever is in ALLOWED_USER_IDS can use it, nobody else.
#
# Set to "1" and the rules change:
#   - anyone in OWNER_IDS gets in, always, and never expires
#   - everyone else needs unexpired days, given with /grant
#   - everyone else is refused, and told their own ID so they can ask you
SUBSCRIPTIONS_ENABLED = "0"

# Your own Telegram ID (send /whoami to the bot). Comma-separate for several.
# If you leave this blank it falls back to ALLOWED_USER_IDS, so you cannot
# accidentally lock yourself out of your own bot.
OWNER_IDS = ""

# The most a single trade may pay in spread, as a share of its own risk.
# 0.20 means "refuse anything where the spread is more than a fifth of the
# stop". Guards against signalling on a dead or frozen market, where the
# stop collapses and the spread becomes most of the trade.
MAX_COST_R = "0.20"
