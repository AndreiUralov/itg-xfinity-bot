"""Telegram bot handlers for ITG job logging."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from bot.config import (  # noqa: E402
    DEFAULT_WORK_AREA,
    PAY_DB_PATH,
    PHOTO_WAIT_SECONDS,
    TELEGRAM_ALLOWED_USER_IDS,
)
from bot.keyboards import (  # noqa: E402
    NEW_INSTALL_SUBTYPES,
    SERVICE_CHANGE_SUBTYPES,
    SPECIAL_REQUEST_SUBTYPES,
    confirm_keyboard,
    duplicate_confirm_keyboard,
    equipment_keyboard,
    format_equipment_summary,
    fuel_keyboard,
    invoice_week_keyboard,
    main_menu_keyboard,
    per_diem_keyboard,
    photo_actions_keyboard,
    product_keyboard,
    subtype_keyboard,
    tips_keyboard,
    today_delete_confirm_keyboard,
    today_job_keyboard,
    today_list_keyboard,
    up_install_mode_keyboard,
    work_type_keyboard,
    workday_keyboard,
)
from bot.jobs_manager import (  # noqa: E402
    delete_job,
    find_existing_job,
    get_job,
    get_today_fuel,
    get_today_jobs,
    get_today_per_diem,
    get_today_tips,
    job_to_session_data,
    today_totals,
)
from bot.job_hints import format_job_hint, lookup_job_hint  # noqa: E402
from bot.quick_input import parse_quick_input  # noqa: E402
from bot.goals import daily_goal_progress_line, goals_progress_block, weekly_goal_progress_line  # noqa: E402
from bot.settings_store import (  # noqa: E402
    clear_daily_goal,
    clear_weekly_goal,
    count_work_days,
    get_daily_goal,
    get_effective_daily_goal,
    get_goal_work_days,
    get_weekly_goal,
    get_work_day,
    set_daily_goal,
    set_weekly_goal_with_daily,
    set_work_day,
)
from bot.storage import save_fuel, save_job, save_per_diem, save_tip, week_bounds, week_totals  # noqa: E402
from bot.users import (  # noqa: E402
    UserProfile,
    ensure_legacy_migration,
    get_or_create_user,
    normalize_tech_label,
    set_tech_label,
    touch_user,
    user_settings_key,
    validate_tech_label,
)
from bot.work_types import normalize_extracted  # noqa: E402
from bot.vision import NO_API_KEY_MSG, RATE_LIMIT_MSG, empty_extraction, extract_from_images  # noqa: E402
from datetime_miami import miami_now  # noqa: E402
from work_area import is_confident, resolve_work_area  # noqa: E402
from calculator import calculate_job, find_matching_rule, load_database  # noqa: E402

TZ = ZoneInfo("America/New_York")
TIPS_BUTTON_TEXT = "💵 Чаевые"
FUEL_BUTTON_TEXT = "⛽ Бензин"
PER_DIEM_BUTTON_TEXT = "🧳 Командировочные"

_media_group_buffers: dict[str, dict[str, Any]] = {}
_photo_wait_tasks: dict[int, asyncio.Task] = {}


def _authorized(user_id: int) -> bool:
    if not TELEGRAM_ALLOWED_USER_IDS:
        return True
    return user_id in TELEGRAM_ALLOWED_USER_IDS


async def _deny(update: Update) -> None:
    if update.message:
        await update.message.reply_text("⛔ Доступ запрещён.")
    elif update.callback_query:
        await update.callback_query.answer("Доступ запрещён", show_alert=True)


def _display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return ""
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(part for part in parts if part).strip()
    return name or (user.username or "")


def _telegram_user_id(update: Update) -> int:
    user = update.effective_user
    if not user:
        return 0
    return user.id


def _chat_id(update: Update) -> int:
    chat = update.effective_chat
    if not chat:
        return 0
    return chat.id


def _remember_user(context: ContextTypes.DEFAULT_TYPE, profile: UserProfile) -> None:
    context.user_data["_owner_id"] = profile.telegram_user_id
    context.user_data["_tech_label"] = profile.tech_id


def _owner_id(context: ContextTypes.DEFAULT_TYPE) -> int:
    owner = context.user_data.get("_owner_id")
    if owner is None:
        raise RuntimeError("owner_id missing from session")
    return int(owner)


def _tech_label(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(context.user_data.get("_tech_label") or "")


async def _ensure_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> UserProfile | None:
    if not _authorized(_telegram_user_id(update)):
        await _deny(update)
        return None
    ensure_legacy_migration()
    profile = get_or_create_user(
        telegram_user_id=_telegram_user_id(update),
        chat_id=_chat_id(update),
        display_name=_display_name(update),
    )
    touch_user(profile.telegram_user_id, _chat_id(update), display_name=_display_name(update))
    _remember_user(context, profile)
    return profile


def _reset_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in list(context.user_data.keys()):
        del context.user_data[key]


def _get_db() -> dict:
    return load_database(PAY_DB_PATH)


def _resolve_work_area(data: dict[str, Any]) -> None:
    address = data.get("address") or ""
    if not address:
        data["work_area"] = DEFAULT_WORK_AREA
        data["work_area_source"] = "default"
        return
    area, source = resolve_work_area(address, DEFAULT_WORK_AREA)
    data["work_area"] = area
    data["work_area_source"] = source


def _work_area_label(data: dict[str, Any]) -> str:
    area = data.get("work_area") or DEFAULT_WORK_AREA
    source = data.get("work_area_source", "default")
    hints = {"zip": "по ZIP", "city": "по городу", "default": "по умолчанию"}
    return f"{area} ({hints.get(source, source)})"


def _duplicate_notice(existing: dict[str, Any] | None) -> str:
    if not existing:
        return ""
    jn = existing["job_number"]
    total = float(existing["total"])
    if existing.get("scope") == "today":
        return (
            f"⚠️ <b>Job# {jn} уже сохранён сегодня</b> (${total:.2f}).\n"
            "Похоже на повторную загрузку того же скрина.\n\n"
        )
    day = existing.get("day", "")
    return (
        f"⚠️ <b>Job# {jn} уже есть на этой неделе</b> ({day}, ${total:.2f}).\n\n"
    )


def _format_preview(
    data: dict[str, Any],
    pay_result=None,
    *,
    existing: dict[str, Any] | None = None,
    owner_telegram_id: int | None = None,
) -> str:
    notice = _duplicate_notice(existing)
    hint_line = ""
    jn = data.get("job_number")
    if jn:
        hint = lookup_job_hint(jn, owner_telegram_id)
        if hint:
            hint_line = format_job_hint(hint) + "\n\n"
    subtypes = ", ".join(data.get("subtype_codes") or []) or "—"
    lines = [
        f"{notice}{hint_line}📋 <b>Распознано</b>",
        f"Job#: <code>{data.get('job_number') or '?'}</code>",
        f"Адрес: {data.get('address') or '—'}",
        f"Work Area: <b>{_work_area_label(data)}</b>",
        f"Account: <code>{data.get('account_number') or '—'}</code>",
        f"Тип: <b>{data.get('work_type') or '?'}</b>",
        f"Коды: {subtypes}",
    ]
    if data.get("hookup_type"):
        lines.append(f"Hookup: {data['hookup_type']}")
    if pay_result and pay_result.lines:
        lines.append("")
        lines.append("💰 <b>Расчёт ATN:</b>")
        for line in pay_result.lines:
            lines.append(f"  {line.code} → ${line.total:.2f}")
        lines.append(f"<b>Итого: ${pay_result.total:.2f}</b>")
    return "\n".join(lines)


def _existing_for_job(data: dict[str, Any], owner_telegram_id: int) -> dict[str, Any] | None:
    if data.get("from_edit"):
        return None
    job_number = data.get("job_number")
    if not job_number:
        return None
    return find_existing_job(owner_telegram_id, job_number)


def _preview_pay(
    data: dict,
    equipment: list[str] | None = None,
    product_code: str | None = None,
    addons: list[str] | None = None,
    up_install_mode: str | None = None,
):
    normalize_extracted(data)
    db = _get_db()
    return calculate_job(
        db,
        job_number=data.get("job_number") or "?",
        work_type=data.get("work_type") or "",
        subtypes=data.get("subtype_codes"),
        equipment=equipment,
        product_code=product_code,
        optional_addons=addons,
        up_install_mode=up_install_mode,
    )


def _pay_kwargs(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return {
        "equipment": context.user_data.get("equipment"),
        "product_code": context.user_data.get("product_code"),
        "addons": context.user_data.get("optional_addons"),
        "up_install_mode": context.user_data.get("up_install_mode"),
    }


def _up_equipment_intro(summary: str) -> str:
    return (
        "🔧 <b>Service Change UP</b>\n"
        "Что реально ставил / менял?\n"
        "• Gateway — один раз\n"
        "• Wired TV — один раз\n"
        "• Wireless TV — жми несколько раз (3 коробки = 3 раза)\n\n"
        f"{summary}"
    )


async def _prompt_up_base(message_target, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["step"] = "up_base"
    await message_target.edit_message_text(
        "🔧 <b>Service Change UP</b>\n\n"
        "Что делал с оборудованием?\n\n"
        "🔄 <b>Swap</b> — заменил существующее (R.A.1. $17.85)\n"
        "➕ <b>Добавил</b> — поставил новое к тому что уже было (R.M.1. $23.46)\n\n"
        "После выбора укажешь конкретное оборудование.",
        parse_mode="HTML",
        reply_markup=up_install_mode_keyboard(),
    )


async def _prompt_up_equipment(message_target, context: ContextTypes.DEFAULT_TYPE, db: dict) -> None:
    context.user_data["step"] = "equipment"
    kb = equipment_keyboard(
        db["equipment_prompt_buttons"],
        context.user_data.get("equipment", []),
        db.get("manual_addon_codes", []),
        context.user_data.get("optional_addons", []),
    )
    summary = format_equipment_summary(
        context.user_data.get("equipment", []),
        db,
        context.user_data.get("up_install_mode"),
        for_up=True,
    )
    await message_target.edit_message_text(
        _up_equipment_intro(summary),
        parse_mode="HTML",
        reply_markup=kb,
    )


async def _prompt_generic_equipment(message_target, context: ContextTypes.DEFAULT_TYPE, db: dict) -> None:
    context.user_data["step"] = "equipment"
    kb = equipment_keyboard(
        db["equipment_prompt_buttons"],
        context.user_data.get("equipment", []),
        db.get("manual_addon_codes", []),
        context.user_data.get("optional_addons", []),
    )
    summary = format_equipment_summary(context.user_data.get("equipment", []), db)
    await message_target.edit_message_text(
        f"🔧 Что ставил / менял?\n\n{summary}",
        parse_mode="HTML",
        reply_markup=kb,
    )


def _equipment_step_is_up(context: ContextTypes.DEFAULT_TYPE, db: dict, extracted: dict) -> bool:
    rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
    return bool(rule and rule.get("up_base_prompt"))


def _jobs_word(n: int) -> str:
    n = abs(n) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return "работ"
    if n1 == 1:
        return "работа"
    if 2 <= n1 <= 4:
        return "работы"
    return "работ"


def _short_month(day: date) -> str:
    months = (
        "янв", "фев", "мар", "апр", "май", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    )
    return months[day.month - 1]


def _format_week_range(week_start: date, week_end: date) -> str:
    if week_start.month == week_end.month:
        return f"{week_start.day}–{week_end.day} {_short_month(week_start)}"
    return (
        f"{week_start.day} {_short_month(week_start)} – "
        f"{week_end.day} {_short_month(week_end)}"
    )


INVOICE_WEEK_LABELS = ("Текущая", "Прошлая", "Позапрошлая", "Позапозапрошлая")


def _invoice_week_options(count: int = 4) -> list[tuple[str, date, date]]:
    from weekly_report import current_payroll_week

    today = miami_now().date()
    options: list[tuple[str, date, date]] = []
    for offset in range(count):
        ref = today - timedelta(days=7 * offset)
        week_start, week_end = current_payroll_week(ref)
        options.append((INVOICE_WEEK_LABELS[offset], week_start, week_end))
    return options


def _workday_status_line(owner_telegram_id: int) -> str:
    today = miami_now().date()
    status = get_work_day(today, user_settings_key(owner_telegram_id))
    date_label = today.strftime("%d.%m")
    if status == "working":
        return f"🟢 <b>На работе</b>  ·  {date_label}"
    if status == "off":
        return f"🏖 <b>Выходной</b>  ·  {date_label}"
    return f"⚪ Статус не отмечен  ·  {date_label}"


def _goal_progress_line(owner_telegram_id: int) -> str:
    return goals_progress_block(owner_telegram_id)


def _format_stats_block(owner_telegram_id: int) -> str:
    day = today_totals(owner_telegram_id)
    week = week_totals(owner_telegram_id)
    today = miami_now().date()
    work_days = count_work_days(week['week_start'], today, user_settings_key(owner_telegram_id))
    week_range = _format_week_range(week["week_start"], week["week_end"])

    lines = [_workday_status_line(owner_telegram_id), ""]

    lines.append(
        f"📈 <b>Сегодня</b>  ·  {day['job_count']} {_jobs_word(day['job_count'])}  ·  "
        f"<b>${day['production']:,.2f}</b>"
    )
    today_extras: list[str] = []
    if day.get("tips"):
        today_extras.append(f"💵 ${day['tips']:,.2f}")
    if day.get("fuel"):
        today_extras.append(f"⛽ ${day['fuel']:,.2f}")
    if day.get("per_diem"):
        today_extras.append(f"🧳 ${day['per_diem']:,.2f}")
    if today_extras:
        lines.append("   " + "  ·  ".join(today_extras))

    lines.append("")
    week_header = f"📊 <b>Неделя</b>  ·  <i>{week_range}</i>"
    if work_days:
        week_header += f"  ·  {work_days} раб. дн."
    lines.append(week_header)
    lines.append(
        f"   {week['job_count']} {_jobs_word(week['job_count'])}  ·  "
        f"<b>${week['production']:,.2f}</b>"
    )
    lines.append(
        f"   💵 ${week.get('tips', 0):,.2f}  ·  ⛽ ${week.get('fuel', 0):,.2f}  ·  "
        f"🧳 ${week.get('per_diem', 0):,.2f}"
    )

    goal_block = _goal_progress_line(owner_telegram_id)
    if goal_block:
        lines.append("")
        lines.append(goal_block)
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    _reset_session(context)
    _remember_user(context, profile)
    await update.message.reply_text(
        f"👋 <b>ITG Job Tracker</b>\n"
        f"<i>Подпись в инвойсе: {profile.tech_id}</i>\n\n"
        f"{_format_stats_block(profile.telegram_user_id)}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    args = (update.message.text or "").split()[1:]
    if not args:
        await update.message.reply_text(
            f"👤 <b>Профиль</b>\n"
            f"Подпись в инвойсе: <code>{profile.tech_id}</code>\n"
            f"Имя: {profile.display_name or '—'}\n\n"
            f"Сменить подпись: <code>/setup I0KF</code>",
            parse_mode="HTML",
        )
        return
    try:
        profile = set_tech_label(profile.telegram_user_id, normalize_tech_label(args[0]))
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    _remember_user(context, profile)
    await update.message.reply_text(
        f"✅ Подпись в инвойсе: <b>{profile.tech_id}</b>",
        parse_mode="HTML",
    )


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    today = miami_now().date()
    set_work_day(today, user_settings_key(profile.telegram_user_id), "working")
    await update.message.reply_text(
        f"🟢 Отмечено: <b>на работе</b> ({today.strftime('%d.%m.%Y')})\n\n"
        f"{_format_stats_block(profile.telegram_user_id)}",
        parse_mode="HTML",
    )


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    today = miami_now().date()
    set_work_day(today, user_settings_key(profile.telegram_user_id), "off")
    await update.message.reply_text(
        f"🏖 Отмечено: <b>выходной</b> ({today.strftime('%d.%m.%Y')})",
        parse_mode="HTML",
    )


async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    owner_telegram_id = profile.telegram_user_id
    settings_key = user_settings_key(owner_telegram_id)
    today = miami_now().date()
    week_start, week_end = week_bounds(today)
    parts = (update.message.text or "").split()
    args = parts[1:]

    if not args:
        week_goal = get_weekly_goal(week_start, user_settings_key(owner_telegram_id))
        day_goal = get_effective_daily_goal(week_start, user_settings_key(owner_telegram_id))
        if not week_goal and not day_goal:
            await update.message.reply_text(
                f"🎯 Цели не заданы.\n\n"
                f"Неделя: <code>/goal 1755</code> (авто-день ≈ $351 при 5 днях)\n"
                f"День: <code>/goal day 351</code>",
                parse_mode="HTML",
            )
            return
        lines = ["🎯 <b>Цели</b>\n"]
        if day_goal:
            lines.append(daily_goal_progress_line(owner_telegram_id))
        if week_goal:
            lines.append(weekly_goal_progress_line(owner_telegram_id))
        lines.append("\nСбросить всё: <code>/goal off</code>")
        lines.append("Только день: <code>/goal day off</code>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    if args[0].lower() in ("off", "clear", "сброс", "0"):
        clear_weekly_goal(week_start, user_settings_key(owner_telegram_id))
        clear_daily_goal(user_settings_key(owner_telegram_id))
        await update.message.reply_text("🎯 Недельная и дневная цели сброшены.")
        return

    if args[0].lower() in ("day", "день", "d"):
        if len(args) < 2:
            day_goal = get_effective_daily_goal(week_start, user_settings_key(owner_telegram_id))
            if not day_goal:
                await update.message.reply_text(
                    "Дневная цель не задана.\nПример: <code>/goal day 351</code>",
                    parse_mode="HTML",
                )
                return
            await update.message.reply_text(
                f"📍 Дневная цель: <b>${day_goal:,.2f}</b>\n{daily_goal_progress_line(owner_telegram_id)}",
                parse_mode="HTML",
            )
            return
        if args[1].lower() in ("off", "clear", "сброс", "0"):
            clear_daily_goal(user_settings_key(owner_telegram_id))
            await update.message.reply_text("📍 Дневная цель сброшена.")
            return
        try:
            amount = float(args[1].replace("$", "").replace(",", ""))
        except ValueError:
            await update.message.reply_text("⚠️ Пример: <code>/goal day 351</code>", parse_mode="HTML")
            return
        if amount <= 0:
            await update.message.reply_text("⚠️ Цель должна быть больше 0.")
            return
        set_daily_goal(user_settings_key(owner_telegram_id), amount)
        await update.message.reply_text(
            f"📍 Дневная цель: <b>${amount:,.2f}</b>\n{daily_goal_progress_line(owner_telegram_id)}",
            parse_mode="HTML",
        )
        return

    try:
        amount = float(args[0].replace("$", "").replace(",", ""))
    except ValueError:
        await update.message.reply_text(
            "⚠️ Примеры:\n<code>/goal 1755</code>\n<code>/goal day 351</code>",
            parse_mode="HTML",
        )
        return

    if amount <= 0:
        await update.message.reply_text("⚠️ Цель должна быть больше 0.")
        return

    work_days = None
    if len(args) >= 2:
        try:
            work_days = int(args[1])
        except ValueError:
            pass

    daily = set_weekly_goal_with_daily(week_start, user_settings_key(owner_telegram_id), amount, work_days=work_days)
    days = get_goal_work_days(user_settings_key(owner_telegram_id))
    week = week_totals(owner_telegram_id, week_start)
    pct = min(100, round(week["production"] / amount * 100))
    await update.message.reply_text(
        f"🎯 Цель на неделю: <b>${amount:,.2f}</b>\n"
        f"📍 Дневная цель: <b>${daily:,.2f}</b> ({days} раб. дн.)\n"
        f"Уже за неделю: ${week['production']:,.2f} ({pct}%)\n"
        f"{daily_goal_progress_line(owner_telegram_id)}",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    await update.message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        "1. Бот узнаёт тебя по Telegram — ничего привязывать не нужно\n"
        "2. Подпись в инвойсе (необязательно): <code>/setup I0KF</code>\n"
        "3. Сделай скрин(ы) работы в Tech360\n"
        "4. Отправь в бот (альбомом или по одному)\n"
        "5. Проверь данные → подтверди\n"
        "6. Выбери оборудование (если нужно)\n"
        "7. Работа сохранится для недельного инвойса ATN\n\n"
        "<b>Чаевые:</b> /tips или «💵 Чаевые» — не идут в план.\n"
        "<b>Бензин:</b> /fuel или «⛽ Бензин» — учёт расходов, не в план.\n"
        "<b>Командировочные:</b> /perdiem или «🧳 Командировочные» — per diem, "
        "попадает в инвойс ATN. Можно за вчера: <code>/perdiem 75 вчера</code>\n\n"
        "<b>Быстрый ввод без скрина:</b>\n"
        "<code>549110 trouble</code> · <code>508836 service</code>\n\n"
        "/on — на работе · /off — выходной\n"
        "/goal 1755 — цель на неделю (дневная авто)\n"
        "/today — посмотреть сегодняшние работы, удалить или пересчитать при ошибке.\n"
        "/invoice — PDF за текущую или прошлые 3 недели.\n\n"
        "🌅 Утром (7:00) — напоминание отметить рабочий день.\n"
        "🌙 Вечером (21:00) — итог дня.\n"
        "📋 По понедельникам (7:00) — PDF за прошлую неделю.\n"
        "💾 По воскресеньям (21:00) — бэкап PDF недели.",
        parse_mode="HTML",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    uid = update.effective_user.id
    if uid in _photo_wait_tasks:
        _photo_wait_tasks[uid].cancel()
        del _photo_wait_tasks[uid]
    _reset_session(context)
    await update.message.reply_text("❌ Отменено. Жду новый скрин.")


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    owner_telegram_id = profile.telegram_user_id
    settings_key = user_settings_key(owner_telegram_id)
    db = _get_db()
    totals = week_totals(owner_telegram_id)
    truck = db["deductions"]["truck"]["full_week"]
    meter = db["deductions"]["meter"]["per_week"]
    tips = totals.get("tips", 0.0)
    fuel = totals.get("fuel", 0.0)
    per_diem = totals.get("per_diem", 0.0)
    net = round(totals["production"] - truck - meter + tips + per_diem - fuel, 2)
    work_days = count_work_days(totals['week_start'], totals['week_end'], user_settings_key(owner_telegram_id))
    goal_line = goals_progress_block(owner_telegram_id)
    extra = f"\n{goal_line}" if goal_line else ""
    if work_days:
        extra += f"\nРабочих дней: {work_days}"
    await update.message.reply_text(
        f"📊 <b>Неделя {totals['week_start']} — {totals['week_end']}</b>\n\n"
        f"Работ: {totals['job_count']}\n"
        f"Строк: {totals['line_count']}\n"
        f"Production: <b>${totals['production']:,.2f}</b>\n"
        f"Чаевые: <b>${tips:,.2f}</b>\n"
        f"Командировочные: <b>${per_diem:,.2f}</b>\n"
        f"Бензин: <b>${fuel:,.2f}</b>\n"
        f"Truck: (${truck:,.2f})\n"
        f"Meter: (${meter:,.2f})\n"
        f"≈ Net: <b>${net:,.2f}</b>{extra}",
        parse_mode="HTML",
    )


def _format_today_list(jobs: list[dict], owner_telegram_id: int) -> str:
    today = miami_now().date()
    months = (
        "янв", "фев", "мар", "апр", "май", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    )
    date_label = f"{today.day} {months[today.month - 1]} {today.year}"
    if not jobs:
        return f"📋 <b>Сегодня ({date_label})</b>\n\nНет сохранённых работ."

    total = round(sum(j["total"] for j in jobs), 2)
    tips_total = round(sum(float(r["item_total"]) for r in get_today_tips(owner_telegram_id)), 2)
    fuel_total = round(sum(float(r["item_total"]) for r in get_today_fuel(owner_telegram_id)), 2)
    count = len(jobs)
    word = "работа" if count == 1 else ("работы" if 2 <= count <= 4 else "работ")
    header = f"📋 <b>Сегодня ({date_label})</b> — {count} {word}, <b>${total:.2f}</b>"
    if tips_total:
        header += f"\n💵 Чаевые сегодня: <b>${tips_total:.2f}</b> (не в плане)"
    if fuel_total:
        header += f"\n⛽ Бензин сегодня: <b>${fuel_total:.2f}</b>"
    per_diem_total = round(sum(float(r["item_total"]) for r in get_today_per_diem(owner_telegram_id)), 2)
    if per_diem_total:
        header += f"\n🧳 Per diem сегодня: <b>${per_diem_total:.2f}</b>"
    lines = [header + "\n"]
    for i, job in enumerate(jobs, 1):
        addr = job.get("address") or "—"
        if len(addr) > 45:
            addr = addr[:42] + "..."
        lines.append(
            f"{i}. Job# <code>{job['job_number']}</code> — {job.get('work_type', '?')} — "
            f"<b>${job['total']:.2f}</b>\n   {addr}"
        )
    lines.append("\nНажми на работу, чтобы изменить или удалить.")
    return "\n".join(lines)


def _format_today_job(job: dict) -> str:
    lines = [
        f"📄 <b>Job# {job['job_number']}</b>",
        f"Тип: <b>{job.get('work_type', '—')}</b>",
        f"Work Area: {job.get('work_area', '—')}",
        f"Адрес: {job.get('address', '—')}",
        f"Account: <code>{job.get('account_number') or '—'}</code>",
        f"Коды: {job.get('subtype_codes') or '—'}",
        "",
        "💰 <b>ATN строки:</b>",
        job.get("codes", "—"),
        "",
        f"<b>Итого: ${job['total']:.2f}</b>",
    ]
    return "\n".join(lines)


def _tips_menu_text(owner_telegram_id: int) -> str:
    tips_today = get_today_tips(owner_telegram_id)
    total = round(sum(float(r["item_total"]) for r in tips_today), 2)
    count = len(tips_today)
    lines = [
        "💵 <b>Чаевые</b>",
        "",
        f"Сегодня: <b>${total:.2f}</b> ({count})",
        "<i>Не привязаны к работам и не идут в план.</i>",
        "",
        "Выбери сумму или введи свою:",
    ]
    return "\n".join(lines)


async def _send_tips_menu(target, owner_telegram_id: int, *, edit: bool = False) -> None:
    text = _tips_menu_text(owner_telegram_id)
    markup = tips_keyboard()
    if edit and hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cmd_tips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    context.user_data["step"] = "pick_tip"
    await _send_tips_menu(update.message, profile.telegram_user_id)


def _fuel_menu_text(owner_telegram_id: int) -> str:
    fuel_today = get_today_fuel(owner_telegram_id)
    total = round(sum(float(r["item_total"]) for r in fuel_today), 2)
    count = len(fuel_today)
    lines = [
        "⛽ <b>Бензин</b>",
        "",
        f"Сегодня: <b>${total:.2f}</b> ({count})",
        "<i>Не привязано к работам и не идёт в план.</i>",
        "",
        "Выбери сумму или введи свою:",
    ]
    return "\n".join(lines)


async def _send_fuel_menu(target, owner_telegram_id: int, *, edit: bool = False) -> None:
    text = _fuel_menu_text(owner_telegram_id)
    markup = fuel_keyboard()
    if edit and hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cmd_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    context.user_data["step"] = "pick_fuel"
    await _send_fuel_menu(update.message, profile.telegram_user_id)


def _parse_per_diem_date(text: str) -> date | None:
    """Parse optional date token: вчера/yesterday, DD.MM, DD.MM.YYYY."""
    cleaned = text.strip().lower()
    if cleaned in ("вчера", "yesterday", "вч"):
        return miami_now().date() - timedelta(days=1)
    match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", cleaned)
    if not match:
        return None
    day_num = int(match.group(1))
    month_num = int(match.group(2))
    year_raw = match.group(3)
    if year_raw:
        year_num = int(year_raw)
        if year_num < 100:
            year_num += 2000
    else:
        year_num = miami_now().year
    try:
        return date(year_num, month_num, day_num)
    except ValueError:
        return None


def _per_diem_completion_datetime(target_day: date) -> datetime:
    now = miami_now()
    if target_day == now.date():
        return now
    return datetime.combine(target_day, now.time(), tzinfo=TZ)


def _per_diem_date_label(target_day: date) -> str:
    today = miami_now().date()
    if target_day == today:
        return "сегодня"
    if target_day == today - timedelta(days=1):
        return "вчера"
    return target_day.strftime("%d.%m.%Y")


def _per_diem_menu_text(owner_telegram_id: int, *, for_yesterday: bool = False) -> str:
    target = miami_now().date() - timedelta(days=1) if for_yesterday else miami_now().date()
    entries = get_today_per_diem(owner_telegram_id, target)
    total = round(sum(float(r["item_total"]) for r in entries), 2)
    count = len(entries)
    when = _per_diem_date_label(target)
    lines = [
        "🧳 <b>Командировочные (per diem)</b>",
        "",
        f"За {when}: <b>${total:.2f}</b> ({count})",
        "<i>Отображается в инвойсе ATN.</i>",
        "",
        "Выбери сумму или введи свою:",
    ]
    return "\n".join(lines)


async def _send_per_diem_menu(
    target,
    owner_telegram_id: int,
    *,
    for_yesterday: bool = False,
    edit: bool = False,
) -> None:
    text = _per_diem_menu_text(owner_telegram_id, for_yesterday=for_yesterday)
    markup = per_diem_keyboard(for_yesterday=for_yesterday)
    if edit and hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cmd_perdiem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return

    parts = (update.message.text or "").split()[1:]
    if not parts:
        context.user_data["step"] = "pick_per_diem"
        context.user_data.pop("per_diem_for_yesterday", None)
        await _send_per_diem_menu(update.message, profile.telegram_user_id)
        return

    amount = _parse_tip_amount(parts[0])
    if amount is None:
        await update.message.reply_text(
            "⚠️ Пример: <code>/perdiem 75</code> или <code>/perdiem 75 вчера</code>",
            parse_mode="HTML",
        )
        return

    target_day = miami_now().date()
    if len(parts) >= 2:
        parsed = _parse_per_diem_date(parts[1])
        if parsed is None:
            await update.message.reply_text(
                "⚠️ Дата: <code>вчера</code> или <code>28.08</code>",
                parse_mode="HTML",
            )
            return
        target_day = parsed

    await _save_standalone_per_diem(
        update.message,
        context,
        amount,
        completion_datetime=_per_diem_completion_datetime(target_day),
        date_label=_per_diem_date_label(target_day),
    )


async def _send_today_list(target, owner_telegram_id: int, *, edit: bool = False) -> None:
    jobs = get_today_jobs(owner_telegram_id)
    text = _format_today_list(jobs, owner_telegram_id)
    markup = today_list_keyboard(jobs) if jobs else None
    if edit:
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return
    await _send_today_list(update.message, profile.telegram_user_id)


async def _send_invoice_pdf(
    target,
    *,
    profile: UserProfile,
    week_start: date,
    week_end: date,
    week_label: str,
) -> None:
    from weekly_report import generate_weekly_report

    try:
        result = generate_weekly_report(
            week_start=week_start,
            week_end=week_end,
            tech_id=profile.tech_id,
            owner_telegram_id=profile.telegram_user_id,
        )
    except Exception as exc:
        await target.reply_text(f"⚠️ Не удалось создать PDF: {exc}")
        return

    if not result["pdf"].exists():
        await target.reply_text(
            f"📋 За <b>{week_label.lower()}</b> неделю ({_format_week_range(week_start, week_end)}) работ нет.",
            parse_mode="HTML",
        )
        return

    with result["pdf"].open("rb") as doc:
        await target.reply_document(
            document=doc,
            filename=result["pdf"].name,
            caption=(
                f"📋 ITG — расчётный лист ATN\n"
                f"{week_label} неделя · {week_start} — {week_end}"
            ),
        )


async def cmd_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await _ensure_user(update, context)
    if not profile:
        return

    weeks = _invoice_week_options()
    lines = ["📋 <b>Инвойс ATN</b> — выбери неделю:", ""]
    for label, week_start, week_end in weeks:
        lines.append(f"• <b>{label}</b> — {_format_week_range(week_start, week_end)}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=invoice_week_keyboard(weeks),
    )


async def _download_photos(context: ContextTypes.DEFAULT_TYPE, file_ids: list[str]) -> list[Path]:
    paths: list[Path] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="itg_"))
    for i, file_id in enumerate(file_ids):
        tg_file = await context.bot.get_file(file_id)
        dest = tmp_dir / f"photo_{i}.jpg"
        await tg_file.download_to_drive(custom_path=dest)
        paths.append(dest)
    return paths


def _apply_hint_to_extracted(extracted: dict[str, Any], owner_telegram_id: int) -> dict[str, Any]:
    hint = lookup_job_hint(extracted.get("job_number", ""), owner_telegram_id)
    if not hint:
        return extracted
    if not extracted.get("work_type") and hint.get("work_type"):
        extracted["work_type"] = hint["work_type"]
    if not extracted.get("subtype_codes") and hint.get("subtype_codes"):
        extracted["subtype_codes"] = list(hint["subtype_codes"])
    if not extracted.get("hookup_type") and hint.get("hookup_type"):
        extracted["hookup_type"] = hint["hookup_type"]
    if not extracted.get("address") and hint.get("address"):
        extracted["address"] = hint["address"]
    return extracted


async def _start_quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE, extracted: dict) -> None:
    owner_telegram_id = _owner_id(context)
    extracted = normalize_extracted(_apply_hint_to_extracted(extracted, owner_telegram_id))
    context.user_data["extracted"] = extracted
    context.user_data["equipment"] = []
    context.user_data["optional_addons"] = []
    context.user_data["product_code"] = None
    context.user_data.pop("up_install_mode", None)

    intro = f"⚡ Быстрый ввод Job# <code>{extracted['job_number']}</code>"
    hint = lookup_job_hint(extracted["job_number"], owner_telegram_id)
    if hint:
        intro += f"\n{format_job_hint(hint)}"

    if not extracted.get("work_type"):
        context.user_data["step"] = "manual_work_type"
        await update.message.reply_text(
            f"{intro}\n\nВыбери тип работы:",
            parse_mode="HTML",
            reply_markup=work_type_keyboard(),
        )
        return

    if not extracted.get("address"):
        context.user_data["step"] = "await_address"
        await update.message.reply_text(f"{intro}\n\n📍 Введи адрес одной строкой:", parse_mode="HTML")
        return

    _resolve_work_area(extracted)
    context.user_data["step"] = "confirm"
    pay = _preview_pay(extracted)
    await update.message.reply_text(
        f"{intro}\n\n"
        + _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
        parse_mode="HTML",
        reply_markup=_confirm_markup(extracted),
    )


def _confirm_markup(data: dict[str, Any]):
    return confirm_keyboard(data.get("work_area") or DEFAULT_WORK_AREA)


async def _show_preview_or_ask_details(message_target, context, extracted: dict) -> None:
    """After work type is set — ask Job#/address if missing, else show preview."""
    if not extracted.get("job_number"):
        context.user_data["step"] = "await_job_number"
        await message_target.edit_message_text(
            "🔢 Введи <b>Job#</b> (только цифры, напр. <code>497745</code>):",
            parse_mode="HTML",
        )
        return
    if not extracted.get("address"):
        context.user_data["step"] = "await_address"
        await message_target.edit_message_text(
            "📍 Введи <b>адрес</b> клиента одной строкой:",
            parse_mode="HTML",
        )
        return

    _resolve_work_area(extracted)
    context.user_data["extracted"] = extracted
    context.user_data["step"] = "confirm"
    pay = _preview_pay(
        extracted,
        context.user_data.get("equipment"),
        context.user_data.get("product_code"),
        context.user_data.get("optional_addons"),
    )
    await message_target.edit_message_text(
        _format_preview(
            extracted,
            pay if not pay.needs_user_input else None,
            existing=_existing_for_job(extracted, _owner_id(context)),
            owner_telegram_id=_owner_id(context),
        ),
        parse_mode="HTML",
        reply_markup=_confirm_markup(extracted),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(_telegram_user_id(update)):
        return await _deny(update)

    profile = await _ensure_user(update, context)
    if not profile:
        return

    step = context.user_data.get("step")
    text = (update.message.text or "").strip()

    extracted: dict = context.user_data.get("extracted", empty_extraction())

    if step == "await_job_number":
        digits = re.sub(r"\D", "", text)
        if len(digits) < 5:
            await update.message.reply_text("⚠️ Нужен номер работы, напр. 497745")
            return
        extracted["job_number"] = digits
        context.user_data["extracted"] = extracted
        if not extracted.get("address"):
            context.user_data["step"] = "await_address"
            await update.message.reply_text("📍 Теперь введи адрес одной строкой:")
            return
        _resolve_work_area(extracted)
        context.user_data["step"] = "confirm"
        pay = _preview_pay(extracted)
        await update.message.reply_text(
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if step == "await_address":
        if len(text) < 8:
            await update.message.reply_text("⚠️ Адрес слишком короткий. Введи полный адрес.")
            return
        extracted["address"] = text
        context.user_data["extracted"] = extracted
        _resolve_work_area(extracted)
        context.user_data["step"] = "confirm"
        pay = _preview_pay(extracted)
        await update.message.reply_text(
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if step == "await_standalone_tip" or step == "pick_tip":
        tip_amount = _parse_tip_amount(text, allow_negative=True)
        if tip_amount is None:
            await update.message.reply_text(
                "⚠️ Введи сумму числом, напр. <code>10</code>, <code>12.50</code> или <code>-20</code> для исправления",
                parse_mode="HTML",
            )
            return
        await _save_standalone_tip(update.message, context, tip_amount)
        return

    if step == "await_standalone_fuel" or step == "pick_fuel":
        fuel_amount = _parse_tip_amount(text)
        if fuel_amount is None:
            await update.message.reply_text("⚠️ Введи сумму числом, напр. <code>40</code> или <code>52.30</code>", parse_mode="HTML")
            return
        await _save_standalone_fuel(update.message, context, fuel_amount)
        return

    if step == "await_standalone_per_diem" or step == "pick_per_diem":
        per_diem_amount = _parse_tip_amount(text)
        if per_diem_amount is None:
            await update.message.reply_text(
                "⚠️ Введи сумму числом, напр. <code>75</code> или <code>125.50</code>",
                parse_mode="HTML",
            )
            return
        for_yesterday = bool(context.user_data.get("per_diem_for_yesterday"))
        target_day = miami_now().date() - timedelta(days=1) if for_yesterday else miami_now().date()
        await _save_standalone_per_diem(
            update.message,
            context,
            per_diem_amount,
            completion_datetime=_per_diem_completion_datetime(target_day),
            date_label=_per_diem_date_label(target_day),
        )
        return

    if not step:
        if text == TIPS_BUTTON_TEXT:
            await cmd_tips(update, context)
            return
        if text == FUEL_BUTTON_TEXT:
            await cmd_fuel(update, context)
            return
        if text == PER_DIEM_BUTTON_TEXT:
            await cmd_perdiem(update, context)
            return
        if _should_accept_standalone_tip_text(text):
            tip_amount = _parse_tip_amount(text, allow_negative=True)
            if tip_amount is not None:
                await _save_standalone_tip(update.message, context, tip_amount)
                return
        quick = parse_quick_input(text)
        if quick:
            await _start_quick_input(update, context, normalize_extracted({**empty_extraction(), **quick}))
            return


async def _process_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file_ids = context.user_data.get("photo_file_ids", [])
    if not file_ids:
        return

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id, "🔍 Обрабатываю скриншот(ы)...")

    try:
        paths = await _download_photos(context, file_ids)
        extracted = normalize_extracted(await extract_from_images(paths))
    except Exception as exc:
        extracted = empty_extraction()
        context.user_data["extracted"] = extracted
        context.user_data["equipment"] = []
        context.user_data["optional_addons"] = []
        context.user_data["product_code"] = None
        context.user_data.pop("up_install_mode", None)
        context.user_data["step"] = "manual_work_type"
        if str(exc) == NO_API_KEY_MSG:
            msg = (
                "📸 <b>Скрин получен</b>\n\n"
                "Авто-распознавание выключено (нет OPENAI_API_KEY в .env).\n"
                "Продолжаем вручную — выбери тип работы:"
            )
        elif str(exc) == RATE_LIMIT_MSG:
            msg = (
                "📸 <b>Скрин получен</b>\n\n"
                "⚠️ OpenAI временно недоступен (лимит запросов / нет баланса).\n"
                "Выбери тип работы вручную — на скрине обычно видно внизу\n"
                "(New Install, Service Change, Trouble Call…):"
            )
        else:
            msg = (
                "📸 <b>Скрин получен</b>\n\n"
                f"⚠️ Авто-распознавание не удалось.\n"
                "Выбери тип работы вручную:"
            )
        await status_msg.edit_text(msg, parse_mode="HTML", reply_markup=work_type_keyboard())
        return

    context.user_data["extracted"] = extracted
    context.user_data["equipment"] = []
    context.user_data["optional_addons"] = []
    context.user_data["product_code"] = None
    context.user_data.pop("up_install_mode", None)
    _resolve_work_area(extracted)
    context.user_data["step"] = "confirm"

    pay = _preview_pay(extracted)
    await status_msg.edit_text(
        _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
        parse_mode="HTML",
        reply_markup=_confirm_markup(extracted),
    )


async def _schedule_photo_wait(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id

    async def waiter():
        await asyncio.sleep(PHOTO_WAIT_SECONDS)
        if context.user_data.get("step") == "collecting_photos":
            await _process_photos(update, context)

    if uid in _photo_wait_tasks:
        _photo_wait_tasks[uid].cancel()
    _photo_wait_tasks[uid] = asyncio.create_task(waiter())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(_telegram_user_id(update)):
        return await _deny(update)

    profile = await _ensure_user(update, context)
    if not profile:
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id
    media_group_id = update.message.media_group_id

    if media_group_id:
        key = str(media_group_id)
        if key not in _media_group_buffers:
            _media_group_buffers[key] = {
                "user_id": update.effective_user.id,
                "chat_id": update.effective_chat.id,
                "file_ids": [],
                "task": None,
            }

        buf = _media_group_buffers[key]
        buf["file_ids"].append(file_id)
        context.user_data["photo_file_ids"] = buf["file_ids"]
        context.user_data["step"] = "collecting_photos"

        if buf["task"]:
            buf["task"].cancel()

        async def process_album():
            await asyncio.sleep(1.5)
            context.user_data["photo_file_ids"] = buf["file_ids"]
            fake_update = update
            await _process_photos(fake_update, context)
            _media_group_buffers.pop(key, None)

        buf["task"] = asyncio.create_task(process_album())
        return

    if context.user_data.get("step") not in (None, "collecting_photos", "waiting_second"):
        _reset_session(context)

    ids = context.user_data.get("photo_file_ids", [])
    ids.append(file_id)
    context.user_data["photo_file_ids"] = ids
    context.user_data["step"] = "collecting_photos"

    count = len(ids)
    await update.message.reply_text(
        f"📸 Получил {count} скрин(ов).\n"
        f"Отправь второй или нажми «Обработать».\n"
        f"Авто-обработка через {PHOTO_WAIT_SECONDS} сек.",
        reply_markup=photo_actions_keyboard(),
    )
    await _schedule_photo_wait(update, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(_telegram_user_id(update)):
        return await _deny(update)

    query = update.callback_query
    await query.answer()
    profile = await _ensure_user(update, context)
    if not profile:
        return
    owner_telegram_id = profile.telegram_user_id
    settings_key = user_settings_key(owner_telegram_id)

    data = query.data or ""
    extracted: dict = context.user_data.get("extracted", empty_extraction())
    db = _get_db()

    if data.startswith("invoice:"):
        action = data[8:]
        if action == "cancel":
            await query.edit_message_text("❌ Отменено.")
            return
        try:
            offset = int(action)
        except ValueError:
            await query.answer("Неверный выбор", show_alert=True)
            return
        weeks = _invoice_week_options()
        if offset < 0 or offset >= len(weeks):
            await query.answer("Неверный выбор", show_alert=True)
            return
        week_label, week_start, week_end = weeks[offset]
        await query.edit_message_text(
            f"⏳ Генерирую PDF за <b>{week_label.lower()}</b> неделю "
            f"({_format_week_range(week_start, week_end)})…",
            parse_mode="HTML",
        )
        await _send_invoice_pdf(
            query.message,
            profile=profile,
            week_start=week_start,
            week_end=week_end,
            week_label=week_label,
        )
        return

    if data == "act:cancel":
        _reset_session(context)
        await query.edit_message_text("❌ Отменено.")
        return

    if data == "dup:cancel":
        context.user_data.pop("allow_duplicate", None)
        context.user_data.pop("pending_rule_id", None)
        extracted = context.user_data.get("extracted", empty_extraction())
        pay = _preview_pay(
            extracted,
            context.user_data.get("equipment"),
            context.user_data.get("product_code"),
            context.user_data.get("optional_addons"),
        )
        context.user_data["step"] = "confirm"
        await query.edit_message_text(
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if data == "dup:save":
        context.user_data["allow_duplicate"] = True
        extracted = context.user_data.get("extracted", empty_extraction())
        pay = _preview_pay(
            extracted,
            context.user_data.get("equipment"),
            context.user_data.get("product_code"),
            context.user_data.get("optional_addons"),
        )
        rule_id = context.user_data.pop("pending_rule_id", None)
        if not rule_id:
            rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
            rule_id = rule["id"] if rule else "manual"
        pay = _preview_pay(
            extracted,
            context.user_data.get("equipment"),
            context.user_data.get("product_code"),
            context.user_data.get("optional_addons"),
        )
        await _save_and_finish(query, context, extracted, pay, rule_id)
        return

    if data == "work:on":
        today = miami_now().date()
        set_work_day(today, user_settings_key(owner_telegram_id), "working")
        await query.edit_message_text(
            f"🟢 <b>На работе</b> — {today.strftime('%d.%m.%Y')}\n\n{_format_stats_block(owner_telegram_id)}",
            parse_mode="HTML",
        )
        return

    if data == "work:off":
        today = miami_now().date()
        set_work_day(today, user_settings_key(owner_telegram_id), "off")
        await query.edit_message_text(
            f"🏖 <b>Выходной</b> — {today.strftime('%d.%m.%Y')}",
            parse_mode="HTML",
        )
        return

    if data == "act:wait":
        context.user_data["step"] = "waiting_second"
        await query.edit_message_text(
            f"📸 Жду второй скрин ({PHOTO_WAIT_SECONDS} сек)...",
            reply_markup=photo_actions_keyboard(),
        )
        return

    if data == "act:process":
        await query.edit_message_text("🔍 Обрабатываю...")
        await _process_photos(update, context)
        return

    if data == "act:worktype":
        context.user_data["step"] = "manual_work_type"
        await query.edit_message_text("Выбери тип работы:", reply_markup=work_type_keyboard())
        return

    if data == "act:back_preview":
        context.user_data["step"] = "confirm"
        context.user_data.pop("up_install_mode", None)
        pay = _preview_pay(extracted, **_pay_kwargs(context))
        await query.edit_message_text(
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if data.startswith("area:"):
        extracted["work_area"] = data[5:]
        extracted["work_area_source"] = "manual"
        context.user_data["extracted"] = extracted
        pay = _preview_pay(
            extracted,
            context.user_data.get("equipment"),
            context.user_data.get("product_code"),
            context.user_data.get("optional_addons"),
        )
        await query.edit_message_text(
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if data.startswith("wt:"):
        work_type = data[3:]
        if work_type == "Self Install":
            work_type = "New Install"
            codes = extracted.get("subtype_codes") or []
            if "Self Install" not in codes:
                codes.append("Self Install")
            extracted["subtype_codes"] = codes
        extracted["work_type"] = work_type
        extracted["subtype_codes"] = extracted.get("subtype_codes") or []
        context.user_data["extracted"] = extracted
        extracted = normalize_extracted(extracted)
        context.user_data["extracted"] = extracted

        if work_type == "Service Change" and not extracted["subtype_codes"]:
            context.user_data["step"] = "pick_subtype"
            await query.edit_message_text("Подтип Service Change:", reply_markup=subtype_keyboard(SERVICE_CHANGE_SUBTYPES))
            return
        if work_type == "Special Request" and not extracted["subtype_codes"]:
            context.user_data["step"] = "pick_subtype"
            await query.edit_message_text("Подтип Special Request:", reply_markup=subtype_keyboard(SPECIAL_REQUEST_SUBTYPES))
            return
        if work_type == "New Install" and not extracted["subtype_codes"]:
            context.user_data["step"] = "pick_subtype"
            await query.edit_message_text("Подтип New Install:", reply_markup=subtype_keyboard(NEW_INSTALL_SUBTYPES))
            return

        await _show_preview_or_ask_details(query, context, extracted)
        return

    if data.startswith("st:"):
        subtype = data[3:]
        if subtype != "Другое":
            codes = extracted.get("subtype_codes") or []
            if subtype not in codes:
                codes.append(subtype)
            extracted["subtype_codes"] = codes
        context.user_data["extracted"] = extracted
        await _show_preview_or_ask_details(query, context, extracted)
        return

    if data == "act:confirm":
        extracted = normalize_extracted(extracted)
        context.user_data["extracted"] = extracted
        rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
        if not rule:
            await query.edit_message_text("⚠️ Не удалось определить правило оплаты. Выбери тип работы.", reply_markup=work_type_keyboard())
            return

        if rule.get("product_prompt") and not context.user_data.get("product_code"):
            context.user_data["step"] = "product"
            options = rule["product_prompt"]["options"]
            await query.edit_message_text("Выбери product code:", reply_markup=product_keyboard(options))
            return

        if rule.get("up_base_prompt") and not context.user_data.get("up_install_mode"):
            await _prompt_up_base(query, context)
            return

        pay = _preview_pay(extracted, **_pay_kwargs(context))

        if pay.needs_user_input == "up_base_prompt":
            await _prompt_up_base(query, context)
            return

        if pay.needs_user_input == "equipment_prompt":
            await _prompt_up_equipment(query, context, db)
            return

        existing = _existing_for_job(extracted, _owner_id(context))
        if existing and existing.get("scope") == "today" and not context.user_data.get("allow_duplicate"):
            context.user_data["pending_rule_id"] = rule["id"]
            await query.edit_message_text(
                _duplicate_notice(existing)
                + f"Сохранить Job# <code>{extracted['job_number']}</code> ещё раз?",
                parse_mode="HTML",
                reply_markup=duplicate_confirm_keyboard(),
            )
            return

        await _save_and_finish(query, context, extracted, pay, rule["id"])
        return

    if data.startswith("tips:"):
        action = data[5:]
        if action == "menu":
            context.user_data["step"] = "pick_tip"
            await _send_tips_menu(query, owner_telegram_id, edit=True)
            return
        if action == "cancel":
            context.user_data.pop("step", None)
            await query.edit_message_text("❌ Отменено.")
            return
        if action == "custom":
            context.user_data["step"] = "await_standalone_tip"
            await query.answer()
            await query.message.reply_text(
                "💵 Введи сумму чаевых ($), напр. <code>10</code>, <code>12.50</code> или <code>-20</code> чтобы вычесть",
                parse_mode="HTML",
            )
            return
        tip_amount = _parse_tip_amount(action, allow_negative=True)
        if tip_amount is None:
            await query.answer("Неверная сумма", show_alert=True)
            return
        await _save_standalone_tip(query, context, tip_amount)
        return

    if data.startswith("fuel:"):
        action = data[5:]
        if action == "menu":
            context.user_data["step"] = "pick_fuel"
            await _send_fuel_menu(query, owner_telegram_id, edit=True)
            return
        if action == "cancel":
            context.user_data.pop("step", None)
            await query.edit_message_text("❌ Отменено.")
            return
        if action == "custom":
            context.user_data["step"] = "await_standalone_fuel"
            await query.answer()
            await query.message.reply_text(
                "⛽ Введи сумму на бензин ($), напр. <code>40</code> или <code>52.30</code>",
                parse_mode="HTML",
            )
            return
        fuel_amount = _parse_tip_amount(action)
        if fuel_amount is None:
            await query.answer("Неверная сумма", show_alert=True)
            return
        await _save_standalone_fuel(query, context, fuel_amount)
        return

    if data.startswith("perdiem:"):
        action = data[8:]
        if action == "menu" or action == "menu:today":
            context.user_data["step"] = "pick_per_diem"
            context.user_data.pop("per_diem_for_yesterday", None)
            await _send_per_diem_menu(query, owner_telegram_id, edit=True)
            return
        if action == "menu:yesterday":
            context.user_data["step"] = "pick_per_diem"
            context.user_data["per_diem_for_yesterday"] = True
            await _send_per_diem_menu(query, owner_telegram_id, for_yesterday=True, edit=True)
            return
        if action == "cancel":
            context.user_data.pop("step", None)
            context.user_data.pop("per_diem_for_yesterday", None)
            await query.edit_message_text("❌ Отменено.")
            return
        for_yesterday = action.endswith(":yesterday")
        base_action = action[:-10] if for_yesterday else action
        if base_action == "custom":
            context.user_data["step"] = "await_standalone_per_diem"
            context.user_data["per_diem_for_yesterday"] = for_yesterday
            await query.answer()
            when = "вчера" if for_yesterday else "сегодня"
            await query.message.reply_text(
                f"🧳 Введи сумму per diem ($) за <b>{when}</b>, напр. <code>75</code> или <code>125.50</code>",
                parse_mode="HTML",
            )
            return
        per_diem_amount = _parse_tip_amount(base_action)
        if per_diem_amount is None:
            await query.answer("Неверная сумма", show_alert=True)
            return
        target_day = miami_now().date() - timedelta(days=1) if for_yesterday else miami_now().date()
        await _save_standalone_per_diem(
            query,
            context,
            per_diem_amount,
            completion_datetime=_per_diem_completion_datetime(target_day),
            date_label=_per_diem_date_label(target_day),
        )
        return

    if data.startswith("up:"):
        mode = data[3:]
        if mode not in ("swap", "add"):
            await query.answer("Неизвестный режим", show_alert=True)
            return
        context.user_data["up_install_mode"] = mode
        context.user_data["equipment"] = []
        context.user_data["optional_addons"] = []
        await _prompt_up_equipment(query, context, db)
        return

    if data.startswith("prod:"):
        context.user_data["product_code"] = data[5:]
        pay = _preview_pay(extracted, **_pay_kwargs(context))
        if pay.needs_user_input == "equipment_prompt":
            await _prompt_generic_equipment(query, context, db)
            return
        context.user_data["step"] = "confirm"
        await query.edit_message_text(
            _format_preview(extracted, pay, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if data.startswith("eq:"):
        btn_id = data[3:]
        btn = next(b for b in db["equipment_prompt_buttons"] if b["id"] == btn_id)
        code = btn["code"]
        equipment: list[str] = context.user_data.get("equipment", [])
        if btn.get("allow_repeat"):
            equipment.append(code)
        elif code in equipment:
            equipment.remove(code)
        else:
            equipment.append(code)
        context.user_data["equipment"] = equipment
        kb = equipment_keyboard(
            db["equipment_prompt_buttons"],
            equipment,
            db.get("manual_addon_codes", []),
            context.user_data.get("optional_addons", []),
        )
        is_up = _equipment_step_is_up(context, db, extracted)
        summary = format_equipment_summary(
            equipment,
            db,
            context.user_data.get("up_install_mode"),
            for_up=is_up,
        )
        intro = _up_equipment_intro(summary) if is_up else f"🔧 Что ставил / менял?\n\n{summary}"
        await query.edit_message_text(
            intro,
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if data == "act:equip_none":
        context.user_data["equipment"] = []
        context.user_data["optional_addons"] = []
        context.user_data["up_install_mode"] = "swap"
        pay = _preview_pay(extracted, [], context.user_data.get("product_code"), [], up_install_mode="swap")
        rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
        rule_id = rule["id"] if rule else "manual"
        existing = _existing_for_job(extracted, _owner_id(context))
        if existing and existing.get("scope") == "today" and not context.user_data.get("allow_duplicate"):
            context.user_data["pending_rule_id"] = rule_id
            await query.edit_message_text(
                _duplicate_notice(existing)
                + f"Сохранить Job# <code>{extracted['job_number']}</code> ещё раз?",
                parse_mode="HTML",
                reply_markup=duplicate_confirm_keyboard(),
            )
            return
        await _save_and_finish(query, context, extracted, pay, rule_id)
        return

    if data.startswith("addon:"):
        code = data[6:]
        addons: list[str] = context.user_data.get("optional_addons", [])
        if code in addons:
            addons.remove(code)
        else:
            addons.append(code)
        context.user_data["optional_addons"] = addons
        kb = equipment_keyboard(
            db["equipment_prompt_buttons"],
            context.user_data.get("equipment", []),
            db.get("manual_addon_codes", []),
            addons,
        )
        await query.edit_message_reply_markup(reply_markup=kb)
        return

    if data == "act:equip_done":
        pay = _preview_pay(extracted, **_pay_kwargs(context))
        rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
        rule_id = rule["id"] if rule else "manual"
        existing = _existing_for_job(extracted, _owner_id(context))
        if existing and existing.get("scope") == "today" and not context.user_data.get("allow_duplicate"):
            context.user_data["pending_rule_id"] = rule_id
            await query.edit_message_text(
                _duplicate_notice(existing)
                + f"Сохранить Job# <code>{extracted['job_number']}</code> ещё раз?",
                parse_mode="HTML",
                reply_markup=duplicate_confirm_keyboard(),
            )
            return
        await _save_and_finish(query, context, extracted, pay, rule_id)
        return

    if data == "today:refresh" or data == "today:back":
        await _send_today_list(query, owner_telegram_id, edit=True)
        return

    if data.startswith("today:view:"):
        job_number = data.split(":", 2)[2]
        job = get_job(owner_telegram_id, job_number)
        if not job:
            await query.edit_message_text("⚠️ Работа не найдена (возможно уже удалена).")
            return
        await query.edit_message_text(
            _format_today_job(job),
            parse_mode="HTML",
            reply_markup=today_job_keyboard(job_number),
        )
        return

    if data.startswith("today:delete:"):
        job_number = data.split(":", 2)[2]
        job = get_job(owner_telegram_id, job_number)
        if not job:
            await query.edit_message_text("⚠️ Работа не найдена.")
            return
        await query.edit_message_text(
            f"🗑 Удалить Job# <code>{job_number}</code>?\n\n"
            f"Тип: {job.get('work_type', '—')}\n"
            f"Сумма: <b>${job['total']:.2f}</b>\n\n"
            "Строки исчезнут из недельного инвойса.",
            parse_mode="HTML",
            reply_markup=today_delete_confirm_keyboard(job_number),
        )
        return

    if data.startswith("today:delok:"):
        job_number = data.split(":", 2)[2]
        ok, removed = delete_job(owner_telegram_id, job_number)
        if not ok:
            await query.edit_message_text("⚠️ Не удалось удалить — работа не найдена.")
            return
        await query.edit_message_text(
            f"✅ Удалено Job# <code>{job_number}</code> ({removed} строк).\n\n"
            "Используй /today чтобы увидеть список.",
            parse_mode="HTML",
        )
        return

    if data.startswith("today:edit:"):
        job_number = data.split(":", 2)[2]
        job = get_job(owner_telegram_id, job_number)
        if not job:
            await query.edit_message_text("⚠️ Работа не найдена.")
            return
        delete_job(owner_telegram_id, job_number)
        _reset_session(context)
        extracted = job_to_session_data(job)
        context.user_data["extracted"] = extracted
        context.user_data["equipment"] = []
        context.user_data["optional_addons"] = []
        context.user_data["product_code"] = None
        context.user_data.pop("up_install_mode", None)
        context.user_data["step"] = "confirm"
        pay = _preview_pay(extracted)
        await query.edit_message_text(
            "✏️ <b>Пересчёт</b> — старая запись удалена.\n"
            "Проверь данные и подтверди заново:\n\n"
            + _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted, _owner_id(context)), owner_telegram_id=_owner_id(context)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return


def _parse_tip_amount(text: str, *, allow_negative: bool = False) -> float | None:
    cleaned = text.strip().replace("$", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        amount = round(float(cleaned), 2)
    except ValueError:
        return None
    if amount == 0:
        return None
    if not allow_negative and amount < 0:
        return None
    if abs(amount) > 9999:
        return None
    return amount


def _should_accept_standalone_tip_text(text: str) -> bool:
    """Accept plain tip amounts even if session step was lost (/start, bot restart)."""
    cleaned = text.strip().replace("$", "").replace(",", ".")
    if not cleaned or not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", cleaned):
        return False
    # Leave 5–7 digit numbers for quick job entry (549110 trouble).
    if re.fullmatch(r"\d{5,7}", cleaned):
        return False
    return _parse_tip_amount(text, allow_negative=True) is not None


async def _respond(target, text: str, **kwargs) -> None:
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, **kwargs)
    else:
        await target.reply_text(text, **kwargs)


async def _save_standalone_tip(target, context, tip_amount: float) -> None:
    owner_telegram_id = _owner_id(context)
    try:
        save_tip(owner_telegram_id=owner_telegram_id, tech_label=_tech_label(context), amount=tip_amount)
    except Exception as exc:
        await _respond(target, f"⚠️ Не удалось сохранить чаевые: {exc}")
        return
    context.user_data.pop("step", None)
    if tip_amount < 0:
        saved_line = f"✅ <b>Корректировка чаевых ${tip_amount:.2f}</b>"
    else:
        saved_line = f"✅ <b>Чаевые ${tip_amount:.2f}</b> добавлены"
    await _respond(
        target,
        f"{saved_line}\n\n{_format_stats_block(owner_telegram_id)}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def _save_standalone_fuel(target, context, fuel_amount: float) -> None:
    owner_telegram_id = _owner_id(context)
    try:
        save_fuel(owner_telegram_id=owner_telegram_id, tech_label=_tech_label(context), amount=fuel_amount)
    except Exception as exc:
        await _respond(target, f"⚠️ Не удалось сохранить бензин: {exc}")
        return
    context.user_data.pop("step", None)
    await _respond(
        target,
        f"✅ <b>Бензин ${fuel_amount:.2f}</b> добавлен\n\n{_format_stats_block(owner_telegram_id)}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def _save_standalone_per_diem(
    target,
    context,
    per_diem_amount: float,
    *,
    completion_datetime: datetime | None = None,
    date_label: str = "сегодня",
) -> None:
    owner_telegram_id = _owner_id(context)
    try:
        save_per_diem(
            owner_telegram_id=owner_telegram_id,
            tech_label=_tech_label(context),
            amount=per_diem_amount,
            completion_datetime=completion_datetime,
        )
    except Exception as exc:
        await _respond(target, f"⚠️ Не удалось сохранить per diem: {exc}")
        return
    context.user_data.pop("step", None)
    context.user_data.pop("per_diem_for_yesterday", None)
    await _respond(
        target,
        f"✅ <b>Per diem ${per_diem_amount:.2f}</b> за {date_label} — попадёт в инвойс\n\n"
        f"{_format_stats_block(owner_telegram_id)}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def _save_and_finish(target, context, extracted: dict, pay, rule_id: str) -> None:
    if not extracted.get("job_number"):
        await _respond(target, "⚠️ Нет Job#. Отправь скрин с номером работы или /cancel.")
        return
    if not extracted.get("address"):
        await _respond(target, "⚠️ Нет адреса. Отправь скрин с адресом или /cancel.")
        return

    owner_telegram_id = _owner_id(context)
    tech_label = _tech_label(context)
    work_area = extracted.get("work_area") or DEFAULT_WORK_AREA
    today = miami_now()
    invoice_rows = pay.to_invoice_rows(
        tech_label,
        work_area,
        extracted["address"].upper(),
        today.date(),
    )

    save_job(
        owner_telegram_id=owner_telegram_id,
        tech_label=tech_label,
        job_number=extracted["job_number"],
        work_area=work_area,
        address=extracted["address"].upper(),
        work_type=extracted.get("work_type") or "",
        subtype_codes=extracted.get("subtype_codes") or [],
        rule_id=rule_id,
        invoice_rows=invoice_rows,
        account_number=str(extracted.get("account_number") or ""),
        hookup_type=str(extracted.get("hookup_type") or ""),
        completion_datetime=today,
    )

    lines_text = "\n".join(f"  {line.code} ${line.total:.2f}" for line in pay.lines)
    await _respond(
        target,
        f"✅ <b>Сохранено — Job# {extracted['job_number']}</b>\n\n"
        f"{lines_text}\n"
        f"<b>За работу: ${pay.total:.2f}</b>\n\n"
        f"{_format_stats_block(owner_telegram_id)}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    context.user_data.pop("allow_duplicate", None)
    context.user_data.pop("pending_rule_id", None)
    _reset_session(context)
