# Phone-only setup

No computer needed. Two routes:

- **Route A — Cloud (any phone, iPhone or Android).** Runs 24/7 even when your phone
  is off. This is the one you want.
- **Route B — Termux (Android only).** Runs on the phone itself. Free, no signup, good
  for testing. Dies when the phone sleeps.

---

# Route A — Cloud, 100% in your phone browser

### 1. Bot token
Telegram → search **@BotFather** → `/newbot` → name it → username must end in `bot`.
Copy the token it gives you.

### 2. Data key
Browser → **twelvedata.com** → sign up free → copy your API key.

### 3. Save the files to your phone
Tap each file I sent and save it. On iPhone they land in **Files**, on Android in
**Downloads**. You need all 12:

```
bot.py  web.py  strategy.py  indicators.py  data.py
config.py  backtest.py  selftest.py  requirements.txt
README.md  PHONE-SETUP.md  .env.example
```

You do **not** need `.env` — on the cloud, keys go in the dashboard instead.

### 4. Put them on GitHub
Browser → **github.com** → sign up → **+** → **New repository**
- Name: `signalbot`
- **Private** (your keys never go here, but keep it private anyway)
- Create

Then: **Add file → Upload files** → select all the files you saved → **Commit changes**.

> If the upload button is hidden, tap the desktop-site toggle in your browser menu.
> GitHub's mobile layout hides it sometimes.

### 5. Deploy on Render
Browser → **render.com** → **Sign up with GitHub** → authorise it.

**New → Web Service** → pick your `signalbot` repo → then set:

| Field | Value |
|---|---|
| Runtime / Language | **Python 3** |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python web.py` |
| Instance Type | **Free** |

⚠️ It must be a **Web Service**, not a Background Worker. Render's free tier doesn't
include workers. `web.py` exists purely to satisfy that requirement — it opens a tiny
health-check port and runs the bot alongside it.

### 6. Add your keys as environment variables
Scroll to **Environment Variables** → **Add** these four:

```
TELEGRAM_BOT_TOKEN   = your BotFather token
TWELVEDATA_API_KEY   = your Twelve Data key
ACCOUNT_BALANCE      = your real balance, e.g. 5000
RISK_PCT             = 1.0
```

Tap **Create Web Service**. Watch the log. When you see `Bot starting…`, it's live.

### 7. Test it
Telegram → your bot → `/start` → then `/signal xauusd intraday`.

Then `/whoami`, copy the number, and add one more environment variable:
```
ALLOWED_USER_IDS = 123456789
```
Save — Render redeploys automatically. Now nobody else can spend your API quota.

### 8. Stop it falling asleep
Free web services sleep after 15 minutes idle, and take 30–60 seconds to wake. For a
signal bot that delay is painful. Fix it with a free pinger:

Browser → **cron-job.org** → sign up → **Create cronjob**
- URL: your Render URL (looks like `https://signalbot-xxxx.onrender.com`)
- Schedule: **every 10 minutes**

That keeps it warm. Free tier gives 750 instance-hours a month; a single always-on
service uses about 744, so one bot fits — but don't run a second one.

### 9. Validate before risking money
In Telegram:
```
/backtest intraday
```
Takes 1–4 minutes and replies with win rate, expectancy in R, max drawdown, and a
confidence-bucket table. Read it before you place a single trade.

- **Total R negative?** The strategy loses. Don't trade it.
- **Expectancy not rising down the bucket table?** Ignore the percentage; treat ENTRY
  as a plain yes/no.

### Editing code later, still phone-only
GitHub → open the file → pencil icon → edit → commit. Render redeploys by itself.
All your tuning lives in `config.py`.

---

# Route B — Termux (Android only)

Runs on your handset. Nothing to sign up for beyond the two keys.

### 1. Install Termux from F-Droid
Get **f-droid.org** in your browser → install F-Droid → search **Termux** → install.

> Don't use the Play Store version. It's abandoned and breaks on package installs.

### 2. Set it up
```bash
pkg update && pkg upgrade -y
pkg install python python-numpy python-pandas git -y
```
Installing numpy and pandas as **Termux packages** matters — `pip install pandas` tries
to compile from source on Android and usually fails.

### 3. Get the code
If you did Route A step 4:
```bash
git clone https://github.com/YOURNAME/signalbot.git
cd signalbot
```
Otherwise move the saved files into Termux:
```bash
termux-setup-storage        # grant the permission prompt
mkdir ~/signalbot && cd ~/signalbot
cp /sdcard/Download/*.py /sdcard/Download/requirements.txt .
cp /sdcard/Download/.env.example .
```

### 4. Install and configure
```bash
pip install python-telegram-bot==21.9 requests python-dotenv
cp .env.example .env
nano .env
```
In `nano`: paste your keys, then **Ctrl+X**, **Y**, **Enter**.
(Volume-Down + X gives you Ctrl+X if the key row is hidden.)

### 5. Run
```bash
python selftest.py    # check the strategy engine works
python bot.py         # start the bot
```

### 6. Stop Android killing it
```bash
termux-wake-lock
```
Also: Android Settings → Apps → Termux → Battery → **Unrestricted**.

Even then, expect it to die eventually. Termux is great for testing and backtesting,
not for a bot you rely on. `python backtest.py --mode intraday --live` runs fine here
and takes a couple of minutes.

---

## Which to pick

| | Cloud (A) | Termux (B) |
|---|---|---|
| iPhone | ✅ | ❌ |
| Works with phone off | ✅ | ❌ |
| Free | ✅ | ✅ |
| Setup time | ~20 min | ~15 min |
| Survives a reboot | ✅ | ❌ |

Do **Route A**. Use Termux only if you're on Android and want to poke at the code
directly.

---

## If something breaks

| Symptom | Cause |
|---|---|
| `TELEGRAM_BOT_TOKEN missing` | Env var not saved, or a stray space in the value |
| `TWELVEDATA_API_KEY is not set` | Same, check the Render dashboard |
| Bot silent, log shows `Conflict` | Two copies running. Kill one — Termux and Render can't both poll |
| `Rate limit hit` | 8 requests/min ceiling. Wait 60 seconds |
| First `/signal` takes a minute | Service was asleep. Set up the cron pinger (step 8) |
| `No candles returned` | Weekend. Gold closes Friday ~21:00 UTC to Sunday ~22:00 UTC |
