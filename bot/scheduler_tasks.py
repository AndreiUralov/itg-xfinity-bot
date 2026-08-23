"""Scheduled notifications: morning check-in, evening summary, Monday PDF, weekly backup."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT))

from bot.goals import goals_progress_block
from bot.jobs_manager import get_today_jobs, today_totals
from bot.settings_store import count_work_days, get_work_day, mark_task_ran, task_already_ran
from bot.storage import week_bounds, week_totals
from bot.telegram_notify import send_document, send_message
from bot.users import UserProfile, ensure_legacy_migration, list_active_users
from datetime_miami import miami_now


def _workday_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🟢 На работе", "callback_data": "work:on"},
                {"text": "🏖 Выходной", "callback_data": "work:off"},
            ]
        ]
    }


def run_morning_checkin_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    tech_id = user.tech_id
    if not force and task_already_ran("morning", today, tech_id=tech_id):
        return False
    if get_work_day(today, tech_id):
        mark_task_ran("morning", today, tech_id=tech_id)
        return False

    send_message(
        "🌅 <b>Доброе утро!</b>\n\n"
        "Работаешь сегодня?\n"
        "Нажми кнопку или отправь /on /off",
        chat_id=user.chat_id,
        reply_markup=_workday_keyboard(),
    )
    mark_task_ran("morning", today, tech_id=tech_id)
    return True


def run_evening_summary_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    tech_id = user.tech_id
    if not force and task_already_ran("evening", today, tech_id=tech_id):
        return False

    status = get_work_day(today, tech_id)
    day = today_totals(tech_id, today)
    if status == "off" and day["job_count"] == 0:
        mark_task_ran("evening", today, tech_id=tech_id)
        return False

    jobs = get_today_jobs(tech_id, today)
    lines = [
        f"🌙 <b>Итог дня — {today.strftime('%d.%m.%Y')}</b>\n",
    ]
    if status == "working":
        lines.append("Статус: 🟢 рабочий день\n")
    elif status == "off":
        lines.append("Статус: 🏖 выходной\n")
    else:
        lines.append("Статус: не отмечен\n")

    if day["job_count"] == 0:
        lines.append("Работ за сегодня нет.")
        if status != "off":
            lines.append("\nЕсли работал — отправь скрины или быстрый ввод: <code>549110 trouble</code>")
    else:
        lines.append(f"Работ: <b>{day['job_count']}</b>")
        lines.append(f"Production: <b>${day['production']:,.2f}</b>")
        if day.get("tips"):
            lines.append(f"Чаевые: <b>${day['tips']:,.2f}</b>")
        if day.get("fuel"):
            lines.append(f"Бензин: <b>${day['fuel']:,.2f}</b>")
        for job in jobs[:5]:
            lines.append(
                f"  • Job# <code>{job['job_number']}</code> — {job.get('work_type', '?')} — ${job['total']:.2f}"
            )
        if len(jobs) > 5:
            lines.append(f"  … и ещё {len(jobs) - 5}")

    week_start, _ = week_bounds(today)
    week = week_totals(tech_id, week_start)
    work_days = count_work_days(week_start, today, tech_id)
    lines.append(f"\n📊 Неделя: {week['job_count']} работ, ${week['production']:,.2f}")
    lines.append(f"Чаевые за неделю: ${week.get('tips', 0):,.2f}")
    lines.append(f"Бензин за неделю: ${week.get('fuel', 0):,.2f}")
    if work_days:
        lines.append(f"Рабочих дней отмечено: {work_days}")
    goal_block = goals_progress_block(tech_id)
    if goal_block:
        lines.append(goal_block)
    lines.append("\n/today — проверить или исправить")

    send_message("\n".join(lines), chat_id=user.chat_id)
    mark_task_ran("evening", today, tech_id=tech_id)
    return True


def run_monday_pdf_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    tech_id = user.tech_id
    if not force and task_already_ran("monday_pdf", today, tech_id=tech_id):
        return False
    if today.weekday() != 0 and not force:
        return False

    from weekly_report import generate_weekly_report, previous_payroll_week

    week_start, week_end = previous_payroll_week(today)
    result = generate_weekly_report(week_start=week_start, week_end=week_end, tech_id=tech_id)
    caption = f"📋 WEEK {week_start} to {week_end}\nITG — расчётный лист ATN (понедельник)"
    send_document(result["pdf"], caption, chat_id=user.chat_id)
    mark_task_ran("monday_pdf", today, tech_id=tech_id)
    return True


def run_weekly_backup_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    tech_id = user.tech_id
    if not force and task_already_ran("weekly_backup", today, tech_id=tech_id):
        return False
    if today.weekday() != 6 and not force:
        return False

    from weekly_report import generate_weekly_report

    week_start, week_end = week_bounds(today)
    result = generate_weekly_report(week_start=week_start, week_end=week_end, tech_id=tech_id)
    caption = (
        f"💾 Бэкап недели {week_start} — {week_end}\n"
        "PDF копия данных (воскресный бэкап)"
    )
    send_document(result["pdf"], caption, chat_id=user.chat_id)
    mark_task_ran("weekly_backup", today, tech_id=tech_id)
    return True


def run_morning_checkin(force: bool = False) -> bool:
    ensure_legacy_migration()
    ran = False
    for user in list_active_users():
        if run_morning_checkin_for(user, force=force):
            ran = True
    return ran


def run_evening_summary(force: bool = False) -> bool:
    ensure_legacy_migration()
    ran = False
    for user in list_active_users():
        if run_evening_summary_for(user, force=force):
            ran = True
    return ran


def run_monday_pdf(force: bool = False) -> bool:
    ensure_legacy_migration()
    ran = False
    for user in list_active_users():
        if run_monday_pdf_for(user, force=force):
            ran = True
    return ran


def run_weekly_backup(force: bool = False) -> bool:
    ensure_legacy_migration()
    ran = False
    for user in list_active_users():
        if run_weekly_backup_for(user, force=force):
            ran = True
    return ran
