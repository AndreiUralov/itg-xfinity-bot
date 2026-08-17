"""Goal progress formatting for handlers and scheduler."""

from __future__ import annotations

from bot.config import TECH_ID
from bot.jobs_manager import today_totals
from bot.settings_store import get_effective_daily_goal, get_goal_work_days, get_weekly_goal
from bot.storage import week_bounds, week_totals
from datetime_miami import miami_now


def daily_goal_progress_line() -> str:
    today = miami_now().date()
    week_start, _ = week_bounds(today)
    goal = get_effective_daily_goal(week_start, TECH_ID)
    if not goal:
        return ""
    day = today_totals(today)
    production = day["production"]
    pct = min(100, round(production / goal * 100)) if goal else 0
    remaining = max(0.0, goal - production)
    if production >= goal:
        return (
            f"📍 <b>День:</b> ${production:,.2f} / ${goal:,.2f} ({pct}%) · "
            f"✅ цель выполнена (+${production - goal:,.2f})"
        )
    return (
        f"📍 <b>День:</b> ${production:,.2f} / ${goal:,.2f} ({pct}%) · "
        f"осталось <b>${remaining:,.2f}</b>"
    )


def weekly_goal_progress_line() -> str:
    today = miami_now().date()
    week_start, _ = week_bounds(today)
    goal = get_weekly_goal(week_start, TECH_ID)
    if not goal:
        return ""
    week = week_totals(week_start)
    pct = min(100, round(week["production"] / goal * 100)) if goal else 0
    remaining = max(0.0, goal - week["production"])
    if week["production"] >= goal:
        return (
            f"🎯 <b>Неделя:</b> ${week['production']:,.2f} / ${goal:,.2f} ({pct}%) · "
            f"✅ цель выполнена"
        )
    return (
        f"🎯 <b>Неделя:</b> ${week['production']:,.2f} / ${goal:,.2f} ({pct}%) · "
        f"осталось <b>${remaining:,.2f}</b>"
    )


def goals_progress_block() -> str:
    lines = [line for line in (daily_goal_progress_line(), weekly_goal_progress_line()) if line]
    return "\n".join(lines)
