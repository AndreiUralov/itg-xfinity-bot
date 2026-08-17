#!/usr/bin/env python3
"""
Monday morning scheduler — generates previous week's ATN-format payroll invoice (ITG project).

Run via Windows Task Scheduler every Monday at 7:00 AM Eastern, or:
  python scripts/monday_scheduler.py

For Telegram delivery, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from weekly_report import generate_weekly_report, previous_payroll_week

TZ = "America/New_York"
LOG_PATH = ROOT / "output" / "scheduler.log"


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{timestamp}] {message}\n"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def send_telegram_pdf(pdf_path: Path, week_label: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). PDF saved locally only.")
        return False

    try:
        import requests

        url = f"https://api.telegram.org/bot{token}/sendDocument"
        with pdf_path.open("rb") as doc:
            response = requests.post(
                url,
                data={"chat_id": chat_id, "caption": f"📋 {week_label}\nITG — расчётный лист (формат ATN)"},
                files={"document": (pdf_path.name, doc, "application/pdf")},
                timeout=60,
            )
        response.raise_for_status()
        log(f"Sent PDF to Telegram chat {chat_id}")
        return True
    except Exception as exc:
        log(f"Telegram send failed: {exc}")
        return False


def run_monday_report(force: bool = False) -> dict:
    now = datetime.now(ZoneInfo(TZ))
    week_start, week_end = previous_payroll_week(now.date())

    if not force and now.weekday() != 0:
        log(f"Skipped — today is not Monday ({now.strftime('%A')}). Use --force to run anyway.")

    log(f"Generating invoice for week {week_start} to {week_end}")

    result = generate_weekly_report(week_start=week_start, week_end=week_end)

    with result["summary"].open(encoding="utf-8") as f:
        summary = json.load(f)

    log(
        f"Done — {summary['line_count']} lines, "
        f"production ${summary['production']:,.2f}, net ${summary['net']:,.2f}"
    )
    log(f"PDF: {result['pdf']}")

    week_label = f"WEEK {week_start} to {week_end}"
    send_telegram_pdf(result["pdf"], week_label)

    return summary


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_monday_report(force=force)
