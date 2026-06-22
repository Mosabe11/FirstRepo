"""
core/notify.py — Centralized Telegram notification helper.
"""
import logging
import requests

logger = logging.getLogger(__name__)


def tg_send(message: str, category: str = "general"):
    """
    Send a Telegram message. category controls which NOTIFY_* flag gates it.
    Categories: pulse, ai, open_close, discovery, cleaner, signal, general
    """
    from config import settings

    enabled = {
        "pulse": getattr(settings, "NOTIFY_PULSE", True),
        "ai": getattr(settings, "NOTIFY_AI", True),
        "open_close": getattr(settings, "NOTIFY_OPEN_CLOSE", True),
        "discovery": getattr(settings, "NOTIFY_DISCOVERY", True),
        "cleaner": getattr(settings, "NOTIFY_CLEANER", True),
        "signal": getattr(settings, "NOTIFY_SIGNAL", True),
        "general": True,
    }
    if not enabled.get(category, True):
        return

    if not settings.TELEGRAM_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": settings.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=5,
        )
        # Fallback: if Markdown fails, retry as plain text
        if r.status_code == 400:
            requests.post(
                url,
                json={
                    "chat_id": settings.TELEGRAM_CHAT_ID,
                    "text": message,
                    "disable_web_page_preview": True,
                },
                timeout=5,
            )
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
