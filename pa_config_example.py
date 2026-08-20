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
