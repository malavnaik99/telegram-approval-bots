"""Tiny Telegram Bot API helper shared by the Gmail and blog bots."""
import json
import os
import urllib.parse
import urllib.request


def tg(method, **params):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"
    data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, dict) else v for k, v in params.items()}).encode()
    with urllib.request.urlopen(url, data, timeout=int(params.get("timeout", 0)) + 30) as r:
        return json.load(r)["result"]
