"""Send Telegram messages from bot handlers and scheduled scripts."""

from __future__ import annotations

import os
from pathlib import Path

import requests

from bot.settings_store import get_chat_id


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def resolve_chat_id() -> str | None:
    stored = get_chat_id()
    if stored:
        return stored
    env_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return env_chat or None


def send_message(text: str, *, parse_mode: str = "HTML", reply_markup: dict | None = None) -> bool:
    token = _token()
    chat_id = resolve_chat_id()
    if not token or not chat_id:
        return False
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return True
    except Exception:
        return False


def send_document(path: Path, caption: str = "") -> bool:
    token = _token()
    chat_id = resolve_chat_id()
    if not token or not chat_id or not path.exists():
        return False
    try:
        with path.open("rb") as doc:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (path.name, doc)},
                timeout=120,
            )
        response.raise_for_status()
        return True
    except Exception:
        return False
