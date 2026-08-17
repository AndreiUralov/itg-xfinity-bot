#!/usr/bin/env python3
"""Hourly cron entry — runs Miami-time scheduled tasks (check-in, summary, PDF, backup)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from bot.scheduler_runner import run_due_tasks  # noqa: E402
from bot.scheduler_tasks import (  # noqa: E402
    run_evening_summary,
    run_monday_pdf,
    run_morning_checkin,
    run_weekly_backup,
)
from datetime_miami import miami_now  # noqa: E402


def main() -> None:
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

    ran = run_due_tasks()
    now = miami_now()
    if ran:
        print(f"[{now.isoformat()}] Ran: {', '.join(ran)}")
    else:
        print(f"[{now.isoformat()}] Nothing to run (hour={now.hour}, weekday={now.weekday()})")


if __name__ == "__main__":
    main()
