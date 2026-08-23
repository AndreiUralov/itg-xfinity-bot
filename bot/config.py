"""ITG Telegram bot — configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ALLOWED_USER_IDS = {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
    if x.strip().isdigit()
}
TELEGRAM_ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.getenv("TELEGRAM_ADMIN_USER_IDS", "").split(",")
    if x.strip().isdigit()
}
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip()

TECH_ID = os.getenv("TECH_ID", "I0KF").strip()
DEFAULT_WORK_AREA = os.getenv("DEFAULT_WORK_AREA", "Broward").strip()
PHOTO_WAIT_SECONDS = int(os.getenv("PHOTO_WAIT_SECONDS", "60"))


def _parse_tech_bindings(raw: str) -> dict[str, int]:
    """TECH_BINDINGS=I0KF:123456789,A1BC:987654321"""
    result: dict[str, int] = {}
    for part in raw.split(","):
        piece = part.strip()
        if ":" not in piece:
            continue
        tech, uid = piece.split(":", 1)
        tech = tech.strip().upper()
        uid = uid.strip()
        if tech and uid.isdigit():
            result[tech] = int(uid)
    return result


TECH_BINDINGS = _parse_tech_bindings(os.getenv("TECH_BINDINGS", ""))

# Cloud: set WEBHOOK_URL=https://your-app.onrender.com (Render sets RENDER_EXTERNAL_URL)
WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip()

JOB_LINES_CSV = ROOT / "data" / "job_lines" / "jobs.csv"
PAY_DB_PATH = ROOT / "config" / "pay_database.json"
