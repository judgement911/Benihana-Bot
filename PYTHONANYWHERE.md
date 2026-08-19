# PythonAnywhere setup — free, no card, 24/7

Your bot runs as a **website** instead of a background process. Telegram calls it
when you send a command. That means no sleeping, no cold starts, and no keep-alive
pinger — better than the Render setup would have been.

**What changed from the original build:**

- Data now comes from **Yahoo Finance** instead of Twelve Data. PythonAnywhere's free
  tier only allows outbound connections to an approved list, and `.yahoo.com` is on it
  while Twelve Data isn't. Bonus: no API key needed at all.
- The bot uses **plain `requests`** instead of the python-telegram-bot library, so
  there's nothing to `pip install` — PythonAnywhere ships Flask, pandas, numpy and
  requests already.
- You need **one secret**, your BotFather token. That's it.

---

## Step 1 — Push the new files to GitHub

Upload these to your `Benihana-Bot` repo the same way as before:

```
flask_app.py  yahoo_data.py  set_webhook.py
pa_config_example.py  config.py  PYTHONANYWHERE.md  .gitignore
```

`config.py` replaces the existing one. The others are new.

## Step 2 — Make a PythonAnywhere account

**pythonanywhere.com** → **Pricing & signup** → **Create a Beginner account** (free).
No card. Pick a username carefully — it becomes your web address,
`yourname.pythonanywhere.com`.

## Step 3 — Pull your code

Dashboard → **Consoles** → **Bash**. Then:

```bash
git clone https://github.com/YOURNAME/Benihana-Bot.git
cd Benihana-Bot
```

GitHub is on the allowlist, so this works on a free account.

## Step 4 — Add your token

```bash
cp pa_config_example.py pa_config.py
nano pa_config.py
```

Fill in `TELEGRAM_BOT_TOKEN`, set `PA_USERNAME` to your PythonAnywhere username, and
change `WEBHOOK_SECRET` to any random string you invent.

Save with **Ctrl+X**, then **Y**, then **Enter**.

> `pa_config.py` is gitignored, so your token never reaches GitHub.

## Step 5 — Create the web app

**Web** tab → **Add a new web app** → **Next** → **Flask** → **Python 3.10** →
accept the default path → **Next**.

Then on that same Web tab, change two things:

**Source code** → set to:
```
/home/YOURNAME/Benihana-Bot
```

**WSGI configuration file** → click it, delete everything inside, paste this:
```python
import sys

path = '/home/YOURNAME/Benihana-Bot'
if path not in sys.path:
    sys.path.insert(0, path)

from flask_app import app as application
```

Replace `YOURNAME` in both places with your actual username. Save, then hit the big
green **Reload** button.

Check it worked: visit `https://YOURNAME.pythonanywhere.com` — you should see
*"Signal bot is running."*

## Step 6 — Connect Telegram

Back in the Bash console:

```bash
cd ~/Benihana-Bot
python3 set_webhook.py set
```

You want `"ok": true`. Then message your bot:

```
/start
/signal xauusd intraday
```

## Step 7 — Lock it to you

Send `/whoami` to the bot, copy the number, then:

```bash
nano pa_config.py     # put the number in ALLOWED_USER_IDS
```

Web tab → **Reload**. Now only you can use it.

## Step 8 — Check the strategy actually works

```
/backtest intraday
```

Read the bucket table. Total R negative means the strategy loses money — don't trade
it. If expectancy doesn't climb as confidence climbs, ignore the percentage and treat
ENTRY as a plain yes/no.

---

## Things that will bite you eventually

**Renew every 3 months.** Free web apps expire. PythonAnywhere emails you; there's a
button on the Web tab. Miss it and the bot goes quiet until you click it.

**100 CPU-seconds a day.** A `/signal` costs well under a second, so you can run
hundreds. `/backtest` costs roughly 15 — fine occasionally, but don't sit there
running it ten times in a row.

**One worker.** Commands queue rather than running in parallel. Send one, wait for the
reply, send the next.

**Yahoo prices are not your broker's prices.** It quotes spot gold (`XAUUSD=X`, falling
back to `GC=F` futures). Your broker's feed will differ by a few cents to a few dollars,
and it doesn't model your spread. For scalping especially, compare your broker's typical
gold spread against the ATR figure the bot reports before trusting a 1R target.

**Yahoo throttles.** Hit it too fast and you'll see a rate-limit message. Responses are
cached — 3 minutes for 15m candles, longer for higher timeframes — so normal use is fine.

---

## If it doesn't work

| Symptom | Fix |
|---|---|
| Site shows "Something went wrong" | Web tab → Error log. Usually a typo in the WSGI file path |
| Bot silent | `python3 set_webhook.py info` — check `last_error_message` |
| `TELEGRAM_BOT_TOKEN is empty` | `pa_config.py` missing or in the wrong folder |
| `Cannot connect to ...` | That domain isn't allowlisted. Tell me which one |
| setWebhook rejected | Reload the web app first, then retry. If it still refuses, tell me — there's a workaround |
| "No data" on weekends | Gold closes Friday ~21:00 UTC to Sunday ~22:00 UTC |
