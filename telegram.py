"""Telegram Bot API bilan ishlash (faqat standart kutubxona)."""
import json
import os
import urllib.request

_username = None


def token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def enabled():
    return bool(token())


def call(method, http_timeout=15, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token()}/{method}",
        data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=http_timeout) as resp:
        data = json.load(resp)
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram xatosi"))
    return data["result"]


def bot_username():
    """Bot nomi (t.me havolasi uchun); bir marta so'raladi va eslab qolinadi."""
    global _username
    if _username is None and enabled():
        try:
            _username = call("getMe", http_timeout=5)["username"]
        except Exception:
            return None
    return _username


def send_message(chat_id, text):
    call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
