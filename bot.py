"""Telegram bot: akkauntni ulash (/start <kod>). Parol tiklash kodlarini sayt o'zi yuboradi."""
import html
import os
import time

import telegram
from app import link_telegram

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
HELP = ("Assalomu alaykum! Bu <b>Bilimlar manbai</b> platformasining boti.\n\n"
        "Akkauntingizni ulash uchun saytda <b>Profil → Telegramni ulash</b> tugmasini bosing. "
        "Shundan keyin parolni unutsangiz, tiklash kodi shu yerga keladi."
        + (f"\n\nSayt: {SITE_URL}" if SITE_URL else ""))


def handle(message):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if text.startswith("/start ") and message["chat"].get("type") == "private":
        name = link_telegram(text.split(maxsplit=1)[1], chat_id)
        if name:
            telegram.send_message(chat_id, f"✅ Akkaunt ulandi: <b>{html.escape(name)}</b>\n\n"
                                            "Endi parolni unutsangiz, saytda «Parolni unutdingizmi?» ni bosing, "
                                            "tiklash kodi shu yerga keladi.")
        else:
            telegram.send_message(chat_id, "❌ Havola eskirgan yoki noto'g'ri. Saytdagi Profil sahifasidan "
                                           "«Telegramni ulash» tugmasini qayta bosing.")
    else:
        telegram.send_message(chat_id, HELP)


def main():
    if not telegram.enabled():
        print("TELEGRAM_BOT_TOKEN berilmagan, bot ishlamaydi.", flush=True)
        while True:
            time.sleep(3600)
    print(f"Bot ishga tushdi: @{telegram.bot_username()}", flush=True)
    offset = None
    while True:
        try:
            updates = telegram.call("getUpdates", http_timeout=60, timeout=50, offset=offset,
                                    allowed_updates=["message"])
            for upd in updates:
                offset = upd["update_id"] + 1
                if "message" in upd:
                    try:
                        handle(upd["message"])
                    except Exception as exc:
                        print("Xabarni qayta ishlashda xato:", exc, flush=True)
        except Exception as exc:
            print("Telegram bilan aloqa xatosi:", exc, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
