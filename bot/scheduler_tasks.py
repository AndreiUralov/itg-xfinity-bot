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
from bot.users import UserProfile, ensure_legacy_migration, list_active_users, user_settings_key
from datetime_miami import miami_now


def _scheduler_key(user: UserProfile) -> str:
    return user_settings_key(user.telegram_user_id)


def run_morning_checkin_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    key = _scheduler_key(user)
    if not force and task_already_ran("morning", today, tech_id=key):
        return False
    if get_work_day(today, key) == "working":
        mark_task_ran("morning", today, tech_id=key)
        return False

    send_message(
        "🌅 <b>Доброе утро!</b>\n\n"
        "Если сегодня на смене — кидай скрины в бот.",
        chat_id=user.chat_id,
    )
    mark_task_ran("morning", today, tech_id=key)
    return True


def run_evening_summary_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    uid = user.telegram_user_id
    key = _scheduler_key(user)
    if not force and task_already_ran("evening", today, tech_id=key):
        return False

    day = today_totals(uid, today)
    status = get_work_day(today, key)
    if day["job_count"] == 0 and status != "working":
        mark_task_ran("evening", today, tech_id=key)
        return False

    jobs = get_today_jobs(uid, today)
    lines = [f"🌙 <b>Итог дня — {today.strftime('%d.%m.%Y')}</b>\n"]

    if day["job_count"] == 0:
        lines.append("Работ за сегодня нет.")
    else:
        lines.append(f"Работ: <b>{day['job_count']}</b>")
        lines.append(f"Production: <b>${day['production']:,.2f}</b>")
        if day.get("tips"):
            lines.append(f"Чаевые: <b>${day['tips']:,.2f}</b> <i>(не в инвойсе)</i>")
        if day.get("fuel"):
            lines.append(f"Бензин: <b>${day['fuel']:,.2f}</b>")
        if day.get("per_diem"):
            lines.append(f"Per diem: <b>${day['per_diem']:,.2f}</b>")
        for job in jobs[:5]:
            lines.append(
                f"  • Job# <code>{job['job_number']}</code> — {job.get('work_type', '?')} — ${job['total']:.2f}"
            )
        if len(jobs) > 5:
            lines.append(f"  … и ещё {len(jobs) - 5}")

    week_start, _ = week_bounds(today)
    week = week_totals(uid, week_start)
    work_days = count_work_days(week_start, today, key)
    lines.append(f"\n📊 Неделя: {week['job_count']} работ, ${week['production']:,.2f}")
    lines.append(f"Чаевые за неделю: ${week.get('tips', 0):,.2f} (не в инвойсе ATN)")
    lines.append(f"Per diem за неделю: ${week.get('per_diem', 0):,.2f}")
    lines.append(f"Бензин за неделю: ${week.get('fuel', 0):,.2f}")
    if work_days:
        lines.append(f"Рабочих дней: {work_days}")
    goal_block = goals_progress_block(uid)
    if goal_block:
        lines.append(goal_block)
    lines.append("\n/today — проверить или исправить")

    send_message("\n".join(lines), chat_id=user.chat_id)
    mark_task_ran("evening", today, tech_id=key)
    return True


def run_monday_pdf_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    key = _scheduler_key(user)
    if not force and task_already_ran("monday_pdf", today, tech_id=key):
        return False
    if today.weekday() != 0 and not force:
        return False

    from weekly_report import generate_weekly_report, previous_payroll_week

    week_start, week_end = previous_payroll_week(today)
    result = generate_weekly_report(
        week_start=week_start,
        week_end=week_end,
        tech_id=user.tech_id,
        owner_telegram_id=user.telegram_user_id,
    )
    caption = f"📋 WEEK {week_start} to {week_end}\nITG — расчётный лист ATN (понедельник)"
    send_document(result["pdf"], caption, chat_id=user.chat_id)
    mark_task_ran("monday_pdf", today, tech_id=key)
    return True


def run_weekly_backup_for(user: UserProfile, *, force: bool = False) -> bool:
    today = miami_now().date()
    key = _scheduler_key(user)
    if not force and task_already_ran("weekly_backup", today, tech_id=key):
        return False
    if today.weekday() != 6 and not force:
        return False

    from weekly_report import generate_weekly_report

    week_start, week_end = week_bounds(today)
    result = generate_weekly_report(
        week_start=week_start,
        week_end=week_end,
        tech_id=user.tech_id,
        owner_telegram_id=user.telegram_user_id,
    )
    caption = (
        f"💾 Бэкап недели {week_start} — {week_end}\n"
        "PDF копия данных (воскресный бэкап)"
    )
    send_document(result["pdf"], caption, chat_id=user.chat_id)
    mark_task_ran("weekly_backup", today, tech_id=key)
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
