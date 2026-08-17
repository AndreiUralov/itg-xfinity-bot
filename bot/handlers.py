"""Telegram bot handlers for ITG job logging."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from datetime import date, datetime
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
    TECH_ID,
)
from bot.keyboards import (  # noqa: E402
    NEW_INSTALL_SUBTYPES,
    SERVICE_CHANGE_SUBTYPES,
    SPECIAL_REQUEST_SUBTYPES,
    confirm_keyboard,
    duplicate_confirm_keyboard,
    equipment_keyboard,
    format_equipment_summary,
    photo_actions_keyboard,
    product_keyboard,
    subtype_keyboard,
    today_delete_confirm_keyboard,
    today_job_keyboard,
    today_list_keyboard,
    work_type_keyboard,
    workday_keyboard,
)
from bot.jobs_manager import delete_job, find_existing_job, get_job, get_today_jobs, job_to_session_data, today_totals  # noqa: E402
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
    save_chat_id,
    set_daily_goal,
    set_weekly_goal_with_daily,
    set_work_day,
)
from bot.storage import save_job, week_bounds, week_totals  # noqa: E402
from bot.vision import NO_API_KEY_MSG, RATE_LIMIT_MSG, empty_extraction, extract_from_images  # noqa: E402
from datetime_miami import miami_now  # noqa: E402
from work_area import is_confident, resolve_work_area  # noqa: E402
from calculator import calculate_job, find_matching_rule, load_database  # noqa: E402

TZ = ZoneInfo("America/New_York")

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


def _format_preview(data: dict[str, Any], pay_result=None, *, existing: dict[str, Any] | None = None) -> str:
    notice = _duplicate_notice(existing)
    hint_line = ""
    jn = data.get("job_number")
    if jn:
        hint = lookup_job_hint(jn)
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


def _existing_for_job(data: dict[str, Any]) -> dict[str, Any] | None:
    if data.get("from_edit"):
        return None
    job_number = data.get("job_number")
    if not job_number:
        return None
    return find_existing_job(job_number)


def _preview_pay(data: dict, equipment: list[str] | None = None, product_code: str | None = None, addons: list[str] | None = None):
    db = _get_db()
    return calculate_job(
        db,
        job_number=data.get("job_number") or "?",
        work_type=data.get("work_type") or "",
        subtypes=data.get("subtype_codes"),
        equipment=equipment,
        product_code=product_code,
        optional_addons=addons,
    )


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


def _workday_status_line() -> str:
    today = miami_now().date()
    status = get_work_day(today, TECH_ID)
    if status == "working":
        return "🟢 Сегодня: <b>на работе</b>"
    if status == "off":
        return "🏖 Сегодня: <b>выходной</b>"
    return "⚪ Сегодня: статус не отмечен (/on или /off)"


def _goal_progress_line() -> str:
    return goals_progress_block()


def _format_stats_block() -> str:
    day = today_totals()
    week = week_totals()
    work_days = count_work_days(week["week_start"], miami_now().date(), TECH_ID)
    block = (
        f"{_workday_status_line()}\n"
        f"📈 <b>Сегодня:</b> {day['job_count']} {_jobs_word(day['job_count'])}, "
        f"<b>${day['production']:,.2f}</b>\n"
        f"📊 <b>Неделя</b> ({week['week_start']}–{week['week_end']}): "
        f"{week['job_count']} {_jobs_word(week['job_count'])}, "
        f"<b>${week['production']:,.2f}</b>"
    )
    if work_days:
        block += f" · {work_days} раб. дн."
    goal_block = _goal_progress_line()
    if goal_block:
        block += f"\n{goal_block}"
    return block


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    save_chat_id(update.effective_chat.id)
    _reset_session(context)
    await update.message.reply_text(
        "👋 <b>ITG Job Tracker</b>\n\n"
        f"{_format_stats_block()}\n\n"
        "Отправь скриншот(ы) из Tech360 — 1 или 2 фото одним сообщением.\n"
        "Или быстрый ввод: <code>549110 trouble</code>\n\n"
        "<b>Команды:</b>\n"
        "/on — на работе сегодня · /off — выходной\n"
        "/goal 1755 — цель на неделю (дневная авто)\n"
        "/week — итог текущей недели (Вс–Сб)\n"
        "/today — работы за сегодня (изменить / удалить)\n"
        "/invoice — PDF в формате ATN\n"
        "/cancel — отменить текущую работу\n"
        "/help — справка",
        parse_mode="HTML",
    )


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    save_chat_id(update.effective_chat.id)
    today = miami_now().date()
    set_work_day(today, TECH_ID, "working")
    await update.message.reply_text(
        f"🟢 Отмечено: <b>на работе</b> ({today.strftime('%d.%m.%Y')})\n\n"
        f"{_format_stats_block()}",
        parse_mode="HTML",
    )


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    save_chat_id(update.effective_chat.id)
    today = miami_now().date()
    set_work_day(today, TECH_ID, "off")
    await update.message.reply_text(
        f"🏖 Отмечено: <b>выходной</b> ({today.strftime('%d.%m.%Y')})",
        parse_mode="HTML",
    )


async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    save_chat_id(update.effective_chat.id)
    today = miami_now().date()
    week_start, week_end = week_bounds(today)
    parts = (update.message.text or "").split()
    args = parts[1:]

    if not args:
        week_goal = get_weekly_goal(week_start, TECH_ID)
        day_goal = get_effective_daily_goal(week_start, TECH_ID)
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
            lines.append(daily_goal_progress_line())
        if week_goal:
            lines.append(weekly_goal_progress_line())
        lines.append("\nСбросить всё: <code>/goal off</code>")
        lines.append("Только день: <code>/goal day off</code>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    if args[0].lower() in ("off", "clear", "сброс", "0"):
        clear_weekly_goal(week_start, TECH_ID)
        clear_daily_goal(TECH_ID)
        await update.message.reply_text("🎯 Недельная и дневная цели сброшены.")
        return

    if args[0].lower() in ("day", "день", "d"):
        if len(args) < 2:
            day_goal = get_effective_daily_goal(week_start, TECH_ID)
            if not day_goal:
                await update.message.reply_text(
                    "Дневная цель не задана.\nПример: <code>/goal day 351</code>",
                    parse_mode="HTML",
                )
                return
            await update.message.reply_text(
                f"📍 Дневная цель: <b>${day_goal:,.2f}</b>\n{daily_goal_progress_line()}",
                parse_mode="HTML",
            )
            return
        if args[1].lower() in ("off", "clear", "сброс", "0"):
            clear_daily_goal(TECH_ID)
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
        set_daily_goal(TECH_ID, amount)
        await update.message.reply_text(
            f"📍 Дневная цель: <b>${amount:,.2f}</b>\n{daily_goal_progress_line()}",
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

    daily = set_weekly_goal_with_daily(week_start, TECH_ID, amount, work_days=work_days)
    days = get_goal_work_days(TECH_ID)
    week = week_totals(week_start)
    pct = min(100, round(week["production"] / amount * 100))
    await update.message.reply_text(
        f"🎯 Цель на неделю: <b>${amount:,.2f}</b>\n"
        f"📍 Дневная цель: <b>${daily:,.2f}</b> ({days} раб. дн.)\n"
        f"Уже за неделю: ${week['production']:,.2f} ({pct}%)\n"
        f"{daily_goal_progress_line()}",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    await update.message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        "1. Сделай скрин(ы) работы в Tech360\n"
        "2. Отправь в бот (альбомом или по одному)\n"
        "3. Проверь данные → подтверди\n"
        "4. Выбери оборудование (если нужно)\n"
        "5. Работа сохранится для недельного инвойса ATN\n\n"
        "<b>Быстрый ввод без скрина:</b>\n"
        "<code>549110 trouble</code> · <code>508836 service</code>\n\n"
        "/on — на работе · /off — выходной\n"
        "/goal 1755 — цель на неделю (дневная авто)\n"
        "/today — посмотреть сегодняшние работы, удалить или пересчитать при ошибке.\n\n"
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
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    db = _get_db()
    totals = week_totals()
    truck = db["deductions"]["truck"]["full_week"]
    meter = db["deductions"]["meter"]["per_week"]
    net = round(totals["production"] - truck - meter, 2)
    work_days = count_work_days(totals["week_start"], totals["week_end"], TECH_ID)
    goal_line = goals_progress_block()
    extra = f"\n{goal_line}" if goal_line else ""
    if work_days:
        extra += f"\nРабочих дней: {work_days}"
    await update.message.reply_text(
        f"📊 <b>Неделя {totals['week_start']} — {totals['week_end']}</b>\n\n"
        f"Работ: {totals['job_count']}\n"
        f"Строк: {totals['line_count']}\n"
        f"Production: <b>${totals['production']:,.2f}</b>\n"
        f"Truck: (${truck:,.2f})\n"
        f"Meter: (${meter:,.2f})\n"
        f"≈ Net: <b>${net:,.2f}</b>{extra}",
        parse_mode="HTML",
    )


def _format_today_list(jobs: list[dict]) -> str:
    today = miami_now().date()
    months = (
        "янв", "фев", "мар", "апр", "май", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    )
    date_label = f"{today.day} {months[today.month - 1]} {today.year}"
    if not jobs:
        return f"📋 <b>Сегодня ({date_label})</b>\n\nНет сохранённых работ."

    total = round(sum(j["total"] for j in jobs), 2)
    count = len(jobs)
    word = "работа" if count == 1 else ("работы" if 2 <= count <= 4 else "работ")
    lines = [f"📋 <b>Сегодня ({date_label})</b> — {count} {word}, <b>${total:.2f}</b>\n"]
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


async def _send_today_list(target, *, edit: bool = False) -> None:
    jobs = get_today_jobs()
    text = _format_today_list(jobs)
    markup = today_list_keyboard(jobs) if jobs else None
    if edit:
        await target.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    await _send_today_list(update.message)


async def cmd_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)
    from weekly_report import current_payroll_week, generate_weekly_report

    week_start, week_end = current_payroll_week(datetime.now(TZ).date())
    try:
        result = generate_weekly_report(week_start=week_start, week_end=week_end)
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Не удалось создать PDF: {exc}")
        return

    if not result["pdf"].exists():
        await update.message.reply_text("Нет работ за эту неделю.")
        return

    with result["pdf"].open("rb") as doc:
        await update.message.reply_document(
            document=doc,
            filename=result["pdf"].name,
            caption=f"📋 ITG — расчётный лист ATN\nWEEK {week_start} to {week_end}",
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


def _apply_hint_to_extracted(extracted: dict[str, Any]) -> dict[str, Any]:
    hint = lookup_job_hint(extracted.get("job_number", ""))
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
    extracted = _apply_hint_to_extracted(extracted)
    context.user_data["extracted"] = extracted
    context.user_data["equipment"] = []
    context.user_data["optional_addons"] = []
    context.user_data["product_code"] = None

    intro = f"⚡ Быстрый ввод Job# <code>{extracted['job_number']}</code>"
    hint = lookup_job_hint(extracted["job_number"])
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
        + _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
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
            existing=_existing_for_job(extracted),
        ),
        parse_mode="HTML",
        reply_markup=_confirm_markup(extracted),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update.effective_user.id):
        return await _deny(update)

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
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
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
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if not step:
        quick = parse_quick_input(text)
        if quick:
            await _start_quick_input(update, context, {**empty_extraction(), **quick})
            return


async def _process_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file_ids = context.user_data.get("photo_file_ids", [])
    if not file_ids:
        return

    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id, "🔍 Обрабатываю скриншот(ы)...")

    try:
        paths = await _download_photos(context, file_ids)
        extracted = await extract_from_images(paths)
    except Exception as exc:
        extracted = empty_extraction()
        context.user_data["extracted"] = extracted
        context.user_data["equipment"] = []
        context.user_data["optional_addons"] = []
        context.user_data["product_code"] = None
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
    _resolve_work_area(extracted)
    context.user_data["step"] = "confirm"

    pay = _preview_pay(extracted)
    await status_msg.edit_text(
        _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
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
    if not _authorized(update.effective_user.id):
        return await _deny(update)

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
    if not _authorized(update.effective_user.id):
        return await _deny(update)

    query = update.callback_query
    await query.answer()
    data = query.data or ""
    extracted: dict = context.user_data.get("extracted", empty_extraction())
    db = _get_db()

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
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
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
        await _save_and_finish(query, context, extracted, pay, rule_id)
        return

    if data == "work:on":
        save_chat_id(query.message.chat_id)
        today = miami_now().date()
        set_work_day(today, TECH_ID, "working")
        await query.edit_message_text(
            f"🟢 <b>На работе</b> — {today.strftime('%d.%m.%Y')}\n\n{_format_stats_block()}",
            parse_mode="HTML",
        )
        return

    if data == "work:off":
        save_chat_id(query.message.chat_id)
        today = miami_now().date()
        set_work_day(today, TECH_ID, "off")
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
        pay = _preview_pay(extracted, context.user_data.get("equipment"), context.user_data.get("product_code"), context.user_data.get("optional_addons"))
        await query.edit_message_text(
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
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
            _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return

    if data.startswith("wt:"):
        work_type = data[3:]
        extracted["work_type"] = work_type
        extracted["subtype_codes"] = extracted.get("subtype_codes") or []
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
        rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
        if not rule:
            await query.edit_message_text("⚠️ Не удалось определить правило оплаты. Выбери тип работы.", reply_markup=work_type_keyboard())
            return

        if rule.get("product_prompt") and not context.user_data.get("product_code"):
            context.user_data["step"] = "product"
            options = rule["product_prompt"]["options"]
            await query.edit_message_text("Выбери product code:", reply_markup=product_keyboard(options))
            return

        pay = _preview_pay(
            extracted,
            context.user_data.get("equipment"),
            context.user_data.get("product_code"),
            context.user_data.get("optional_addons"),
        )

        if pay.needs_user_input == "equipment_prompt":
            context.user_data["step"] = "equipment"
            kb = equipment_keyboard(
                db["equipment_prompt_buttons"],
                context.user_data.get("equipment", []),
                db.get("manual_addon_codes", []),
                context.user_data.get("optional_addons", []),
            )
            summary = format_equipment_summary(context.user_data.get("equipment", []), db)
            await query.edit_message_text(
                "🔧 <b>Service Change UP</b>\n"
                "Что реально ставил / менял?\n"
                "• Gateway — один раз\n"
                "• Wired TV — один раз\n"
                "• Wireless TV — жми несколько раз (3 коробки = 3 раза)\n\n"
                f"{summary}",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        existing = _existing_for_job(extracted)
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

    if data.startswith("prod:"):
        context.user_data["product_code"] = data[5:]
        pay = _preview_pay(extracted, context.user_data.get("equipment"), context.user_data["product_code"], context.user_data.get("optional_addons"))
        if pay.needs_user_input == "equipment_prompt":
            context.user_data["step"] = "equipment"
            kb = equipment_keyboard(
                db["equipment_prompt_buttons"],
                context.user_data.get("equipment", []),
                db.get("manual_addon_codes", []),
                context.user_data.get("optional_addons", []),
            )
            summary = format_equipment_summary(context.user_data.get("equipment", []), db)
            await query.edit_message_text(
                f"🔧 Что ставил / менял?\n\n{summary}",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return
        context.user_data["step"] = "confirm"
        await query.edit_message_text(
            _format_preview(extracted, pay, existing=_existing_for_job(extracted)),
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
        summary = format_equipment_summary(equipment, db)
        await query.edit_message_text(
            "🔧 <b>Service Change UP</b>\n"
            "Что реально ставил / менял?\n\n"
            f"{summary}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if data == "act:equip_none":
        context.user_data["equipment"] = []
        context.user_data["optional_addons"] = []
        pay = _preview_pay(extracted, [], context.user_data.get("product_code"), [])
        rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
        rule_id = rule["id"] if rule else "manual"
        existing = _existing_for_job(extracted)
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
        pay = _preview_pay(
            extracted,
            context.user_data.get("equipment"),
            context.user_data.get("product_code"),
            context.user_data.get("optional_addons"),
        )
        rule = find_matching_rule(db, extracted.get("work_type") or "", extracted.get("subtype_codes"))
        rule_id = rule["id"] if rule else "manual"
        existing = _existing_for_job(extracted)
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
        await _send_today_list(query, edit=True)
        return

    if data.startswith("today:view:"):
        job_number = data.split(":", 2)[2]
        job = get_job(job_number)
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
        job = get_job(job_number)
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
        ok, removed = delete_job(job_number)
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
        job = get_job(job_number)
        if not job:
            await query.edit_message_text("⚠️ Работа не найдена.")
            return
        delete_job(job_number)
        _reset_session(context)
        extracted = job_to_session_data(job)
        context.user_data["extracted"] = extracted
        context.user_data["equipment"] = []
        context.user_data["optional_addons"] = []
        context.user_data["product_code"] = None
        context.user_data["step"] = "confirm"
        pay = _preview_pay(extracted)
        await query.edit_message_text(
            "✏️ <b>Пересчёт</b> — старая запись удалена.\n"
            "Проверь данные и подтверди заново:\n\n"
            + _format_preview(extracted, pay if not pay.needs_user_input else None, existing=_existing_for_job(extracted)),
            parse_mode="HTML",
            reply_markup=_confirm_markup(extracted),
        )
        return


async def _save_and_finish(query, context, extracted: dict, pay, rule_id: str) -> None:
    if not extracted.get("job_number"):
        await query.edit_message_text("⚠️ Нет Job#. Отправь скрин с номером работы или /cancel.")
        return
    if not extracted.get("address"):
        await query.edit_message_text("⚠️ Нет адреса. Отправь скрин с адресом или /cancel.")
        return

    work_area = extracted.get("work_area") or DEFAULT_WORK_AREA
    today = miami_now()
    invoice_rows = pay.to_invoice_rows(
        TECH_ID,
        work_area,
        extracted["address"].upper(),
        today.date(),
    )

    save_job(
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

    lines_text = "\n".join(f"  {l.code} ${l.total:.2f}" for l in pay.lines)
    await query.edit_message_text(
        f"✅ <b>Сохранено — Job# {extracted['job_number']}</b>\n\n"
        f"{lines_text}\n"
        f"<b>За работу: ${pay.total:.2f}</b>\n\n"
        f"{_format_stats_block()}",
        parse_mode="HTML",
    )
    context.user_data.pop("allow_duplicate", None)
    context.user_data.pop("pending_rule_id", None)
    _reset_session(context)
