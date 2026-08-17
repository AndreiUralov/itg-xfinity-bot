#!/usr/bin/env python3
"""Hourly cron entry — runs Miami-time scheduled tasks (check-in, summary, PDF, backup)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from bot.scheduler_tasks import (  # noqa: E402
    run_evening_summary,
    run_monday_pdf,
    run_morning_checkin,
    run_weekly_backup,
)
from bot.settings_store import ensure_schema  # noqa: E402
from datetime_miami import miami_now  # noqa: E402


def main() -> None:
    ensure_schema()
    now = miami_now()
    hour = now.hour
    weekday = now.weekday()
    ran: list[str] = []

    if hour == 7:
        if run_morning_checkin():
            ran.append("morning")
        if weekday == 0 and run_monday_pdf():
            ran.append("monday_pdf")

    if hour == 21:
        if run_evening_summary():
            ran.append("evening")
        if weekday == 6 and run_weekly_backup():
            ran.append("weekly_backup")

    if "--force" in sys.argv:
        task = next((a for a in sys.argv[1:] if not a.startswith("-")), "")
        if task == "morning":
            run_morning_checkin(force=True)
        elif task == "evening":
            run_evening_summary(force=True)
        elif task == "monday":
            run_monday_pdf(force=True)
        elif task == "backup":
            run_weekly_backup(force=True)
        else:
            print("Usage: scheduled_tasks.py --force [morning|evening|monday|backup]")
        return

    if ran:
        print(f"[{now.isoformat()}] Ran: {', '.join(ran)}")
    else:
        print(f"[{now.isoformat()}] Nothing to run (hour={hour}, weekday={weekday})")


if __name__ == "__main__":
    main()
