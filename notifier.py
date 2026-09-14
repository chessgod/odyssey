"""Telegram send. Token/chat ids come from env vars only."""

import logging
import os

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_PHOTO_API_URL = "https://api.telegram.org/bot{token}/sendPhoto"

# Telegram rejects sendMessage with 400 Bad Request above this many
# characters. /status snapshots and parser-broken notices can embed several
# long BFI search URLs and blow past it, silently dropping the whole message.
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_TRUNCATION_SUFFIX = "\n… [truncated]"


def get_chat_ids() -> list:
    """TELEGRAM_CHAT_IDS is a comma-separated list (one per recipient who
    should get alerts and be able to send commands - e.g. you and a friend).
    A single value with no comma still works fine. Shared with control.py so
    the command listener authorizes the same set of chats this sends to."""
    raw = os.environ.get("TELEGRAM_CHAT_IDS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _fit_to_telegram_limit(text: str) -> str:
    if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return text
    logger.warning(
        "Telegram message is %d chars, truncating to fit the %d-char limit",
        len(text),
        TELEGRAM_MAX_MESSAGE_LENGTH,
    )
    return text[: TELEGRAM_MAX_MESSAGE_LENGTH - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def send_message(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = get_chat_ids()
    if not token or not chat_ids:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_IDS not set")
        return False

    text = _fit_to_telegram_limit(text)

    any_ok = False
    for chat_id in chat_ids:
        try:
            resp = requests.post(
                TELEGRAM_API_URL.format(token=token),
                data={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=10,
            )
            resp.raise_for_status()
            any_ok = True
        except requests.RequestException:
            logger.exception("Failed to send Telegram message to chat %s", chat_id)
    return any_ok


def send_photo(photo_bytes: bytes, caption: str = None) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids = get_chat_ids()
    if not token or not chat_ids:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_IDS not set")
        return False

    any_ok = False
    for chat_id in chat_ids:
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
            any_ok = True
        except requests.RequestException:
            logger.exception("Failed to send Telegram photo to chat %s", chat_id)
    return any_ok
