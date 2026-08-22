"""Inline keyboards for ITG bot."""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

WORK_TYPES = [
    "Trouble Call",
    "Service Change",
    "Special Request",
    "New Install",
    "Self Install",
]


def workday_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 На работе", callback_data="work:on"),
                InlineKeyboardButton("🏖 Выходной", callback_data="work:off"),
            ],
        ]
    )


def photo_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Обработать", callback_data="act:process"),
                InlineKeyboardButton("📸 Жду 2-й скрин", callback_data="act:wait"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="act:cancel")],
        ]
    )


def duplicate_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Всё равно сохранить", callback_data="dup:save"),
                InlineKeyboardButton("❌ Отмена", callback_data="dup:cancel"),
            ],
        ]
    )


def tips_keyboard(*, show_cancel: bool = True) -> InlineKeyboardMarkup:
    amounts = [5, 10, 15, 20, 25, 30, 50]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for amount in amounts:
        row.append(InlineKeyboardButton(f"${amount}", callback_data=f"tips:{amount}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другая сумма", callback_data="tips:custom")])
    if show_cancel:
        rows.append([InlineKeyboardButton("❌ Отмена", callback_data="tips:cancel")])
    return InlineKeyboardMarkup(rows)


def fuel_keyboard(*, show_cancel: bool = True) -> InlineKeyboardMarkup:
    amounts = [20, 30, 40, 50, 60, 70, 80, 100]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for amount in amounts:
        row.append(InlineKeyboardButton(f"${amount}", callback_data=f"fuel:{amount}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Другая сумма", callback_data="fuel:custom")])
    if show_cancel:
        rows.append([InlineKeyboardButton("❌ Отмена", callback_data="fuel:cancel")])
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💵 Чаевые", callback_data="tips:menu"),
                InlineKeyboardButton("⛽ Бензин", callback_data="fuel:menu"),
            ],
            [InlineKeyboardButton("📋 Сегодня", callback_data="today:refresh")],
        ]
    )


def confirm_keyboard(work_area: str = "Broward") -> InlineKeyboardMarkup:
    broward_mark = "✓ " if work_area == "Broward" else ""
    miami_mark = "✓ " if work_area == "Miami" else ""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"{broward_mark}Broward", callback_data="area:Broward"),
                InlineKeyboardButton(f"{miami_mark}Miami", callback_data="area:Miami"),
            ],
            [
                InlineKeyboardButton("✅ Верно", callback_data="act:confirm"),
                InlineKeyboardButton("✏️ Тип работы", callback_data="act:worktype"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="act:cancel")],
        ]
    )


def work_type_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for wt in WORK_TYPES:
        rows.append([InlineKeyboardButton(wt, callback_data=f"wt:{wt}")])
    rows.append([InlineKeyboardButton("« Назад", callback_data="act:back_preview")])
    return InlineKeyboardMarkup(rows)


def product_keyboard(options: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for opt in options:
        rows.append([InlineKeyboardButton(opt["label"], callback_data=f"prod:{opt['code']}")])
    rows.append([InlineKeyboardButton("« Назад", callback_data="act:back_preview")])
    return InlineKeyboardMarkup(rows)


def equipment_keyboard(
    buttons: list[dict[str, Any]],
    selected: list[str],
    manual_addons: list[dict[str, Any]],
    selected_addons: list[str],
) -> InlineKeyboardMarkup:
    rows = []
    for btn in buttons:
        code = btn["code"]
        count = selected.count(code)
        if btn.get("allow_repeat"):
            mark = f"×{count} " if count else ""
            label = f"{mark}{btn['label_short']}"
        else:
            mark = "✓ " if count else ""
            label = f"{mark}{btn['label_short']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"eq:{btn['id']}")])

    addon_row = []
    for addon in manual_addons:
        code = addon["code"]
        mark = "✓ " if code in selected_addons else ""
        short = addon.get("label_short") or addon.get("label", code)
        addon_row.append(
            InlineKeyboardButton(f"{mark}{short}", callback_data=f"addon:{code}")
        )
    if addon_row:
        for btn in addon_row:
            rows.append([btn])

    rows.append([InlineKeyboardButton("⏭ Ничего не менял", callback_data="act:equip_none")])
    rows.append([InlineKeyboardButton("✅ Готово", callback_data="act:equip_done")])
    rows.append([InlineKeyboardButton("« Назад", callback_data="act:back_preview")])
    return InlineKeyboardMarkup(rows)


def format_equipment_summary(equipment: list[str], db: dict) -> str:
    """Running total text for equipment selection step."""
    from collections import Counter

    if not equipment:
        return "Пока только база R.A.1. $17.85 (UP). Добавь оборудование или «Ничего не менял»."

    lines = ["<b>Выбрано:</b>"]
    counts = Counter(equipment)
    total = 17.85  # R.A.1. base for UP
    lines.append("  R.A.1. (база UP) → $17.85")

    code_labels = {b["code"]: b for b in db.get("equipment_prompt_buttons", [])}
    for code, qty in counts.items():
        btn = code_labels.get(code, {})
        amount = _lookup_code_amount(code, db)
        sub = amount * qty
        total += sub
        label = btn.get("label_short", code).split("+")[0].strip()
        if qty > 1:
            lines.append(f"  {label} ×{qty} → ${sub:.2f}")
        else:
            lines.append(f"  {label} → ${sub:.2f}")
    lines.append(f"\n<b>Итого: ${total:.2f}</b>")
    return "\n".join(lines)


def _lookup_code_amount(code: str, db: dict) -> float:
    if code in db.get("job_codes", {}):
        return db["job_codes"][code]["amount"]
    if code in db.get("equipment_codes", {}):
        return db["equipment_codes"][code]["amount"]
    for item in db.get("manual_addon_codes", []):
        if item["code"] == code:
            return item["amount"]
    return 0.0


def subtype_keyboard(subtypes: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for st in subtypes:
        rows.append([InlineKeyboardButton(st, callback_data=f"st:{st}")])
    rows.append([InlineKeyboardButton("« Назад", callback_data="act:worktype")])
    return InlineKeyboardMarkup(rows)


SERVICE_CHANGE_SUBTYPES = ["TECH RECOVERY", "HSD UP", "VID UP", "Другое"]
SPECIAL_REQUEST_SUBTYPES = ["GENESIS SRO-CF", "PROACTIVE XIT-CF", "Другое"]
NEW_INSTALL_SUBTYPES = ["HSD NC", "HSD RC", "Другое"]


def today_list_keyboard(jobs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs:
        label = f"#{job['job_number']} — ${job['total']:.2f}"
        rows.append([InlineKeyboardButton(label, callback_data=f"today:view:{job['job_number']}")])
    rows.append([InlineKeyboardButton("🔄 Обновить", callback_data="today:refresh")])
    return InlineKeyboardMarkup(rows)


def today_job_keyboard(job_number: str | int) -> InlineKeyboardMarkup:
    jn = str(job_number)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Пересчитать", callback_data=f"today:edit:{jn}"),
                InlineKeyboardButton("🗑 Удалить", callback_data=f"today:delete:{jn}"),
            ],
            [InlineKeyboardButton("« К списку", callback_data="today:back")],
        ]
    )


def today_delete_confirm_keyboard(job_number: str | int) -> InlineKeyboardMarkup:
    jn = str(job_number)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"today:delok:{jn}"),
                InlineKeyboardButton("« Отмена", callback_data=f"today:view:{jn}"),
            ]
        ]
    )
