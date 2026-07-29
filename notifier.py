"""Telegram send. Token/chat id come from env vars only."""

import logging
import os

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_PHOTO_API_URL = "https://api.telegram.org/bot{token}/sendPhoto"


def send_message(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False

    try:
        resp = requests.post(
            TELEGRAM_API_URL.format(token=token),
            data={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Failed to send Telegram message")
        return False


def send_photo(photo_bytes: bytes, caption: str = None) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False

    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption[:1024]  # Telegram's caption length limit

    try:
        resp = requests.post(
            TELEGRAM_PHOTO_API_URL.format(token=token),
            data=data,
            files={"photo": ("screenshot.png", photo_bytes, "image/png")},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Failed to send Telegram photo")
        return False
