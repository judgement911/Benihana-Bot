"""
Point Telegram at your PythonAnywhere site. Run once from a Bash console:

    python3 set_webhook.py set          # register
    python3 set_webhook.py info         # check what Telegram thinks
    python3 set_webhook.py delete       # unregister (back to polling)

Reads PA_USERNAME, TELEGRAM_BOT_TOKEN and WEBHOOK_SECRET from pa_config.py.
"""
from __future__ import annotations

import json
import sys

import requests

import config as C

try:
    import pa_config

    USERNAME = getattr(pa_config, "PA_USERNAME", "")
except ImportError:
    USERNAME = ""

API = "https://api.telegram.org/bot{token}/{method}"


def call(method: str, **params):
    if not C.TELEGRAM_BOT_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN is empty. Fill in pa_config.py first.")
    r = requests.get(API.format(token=C.TELEGRAM_BOT_TOKEN, method=method),
                     params=params, timeout=30)
    try:
        return r.json()
    except ValueError:
        sys.exit(f"Telegram sent a non-JSON reply (HTTP {r.status_code}).")


def show(label: str, payload: dict):
    print(f"\n{label}")
    print(json.dumps(payload, indent=2)[:1500])


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "set").lower()

    if action == "delete":
        show("deleteWebhook:", call("deleteWebhook", drop_pending_updates="true"))
        return

    if action == "info":
        show("getWebhookInfo:", call("getWebhookInfo"))
        return

    if not USERNAME:
        sys.exit("PA_USERNAME missing from pa_config.py — set it to your "
                 "PythonAnywhere username.")
    if C.WEBHOOK_SECRET in ("", "change-me"):
        sys.exit("WEBHOOK_SECRET in pa_config.py is still the placeholder. "
                 "Change it to any random string.")

    url = f"https://{USERNAME}.pythonanywhere.com/webhook/{C.WEBHOOK_SECRET}"

    me = call("getMe")
    if not me.get("ok"):
        sys.exit(f"Bot token rejected by Telegram: {me}")
    print(f"Bot: @{me['result'].get('username')}")

    result = call("setWebhook", url=url, drop_pending_updates="true")
    show("setWebhook:", result)

    if not result.get("ok"):
        print("\nWebhook rejected. Common causes:")
        print("  • The web app isn't running yet — open the Web tab and Reload.")
        print("  • Certificate refused. If so, tell me and we'll switch approach.")
        return

    show("getWebhookInfo:", call("getWebhookInfo"))
    print("\nNow message your bot with /start")


if __name__ == "__main__":
    main()
