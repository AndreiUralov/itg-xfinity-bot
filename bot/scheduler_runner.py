"""Run due scheduled tasks (Miami time) with deduplication."""

from __future__ import annotations

from bot.scheduler_tasks import (
    run_evening_summary,
    run_monday_pdf,
    run_morning_checkin,
    run_weekly_backup,
)
from bot.settings_store import ensure_schema
from datetime_miami import miami_now


def run_due_tasks() -> list[str]:
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

    return ran
