"""Persist job lines for weekly ATN invoice generation."""

from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from bot.config import DEFAULT_WORK_AREA, JOB_LINES_CSV
from bot.line_types import (
    FUEL_JOB_CODE,
    FUEL_RULE_ID,
    LINE_TYPE_FUEL,
    LINE_TYPE_PRODUCTION,
    LINE_TYPE_TIP,
    TIP_JOB_CODE,
    TIP_RULE_ID,
    is_production_line,
    sum_fuel,
    sum_production,
    sum_tips,
)
from datetime_miami import format_atn_datetime, miami_now

CSV_COLUMNS = [
    "recorded_at",
    "week_start",
    "week_end",
    "tech",
    "job_number",
    "work_area",
    "completion_date",
    "address",
    "account_number",
    "work_type",
    "subtype_codes",
    "hookup_type",
    "rule_id",
    "job_code",
    "qty",
    "item_total",
    "line_type",
    "confirmed",
    "notes",
    "owner_telegram_id",
]


def _read_all_rows(owner_telegram_id: int | None = None) -> list[dict[str, str]]:
    if _use_db():
        from bot.db_store import read_all_rows as db_read

        return db_read(owner_telegram_id)
    _ensure_csv()
    with JOB_LINES_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row.setdefault("line_type", LINE_TYPE_PRODUCTION)
        row.setdefault("owner_telegram_id", "")
    if owner_telegram_id is not None:
        owner = str(owner_telegram_id)
        rows = [r for r in rows if r.get("owner_telegram_id") == owner]
    return rows


def _write_all_rows(rows: list[dict[str, str]]) -> None:
    if _use_db():
        from bot.db_store import replace_all_rows

        replace_all_rows(rows)
        return
    _ensure_csv()
    with JOB_LINES_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _use_db() -> bool:
    from bot.db_store import db_enabled

    return db_enabled()


def _ensure_csv() -> None:
    JOB_LINES_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not JOB_LINES_CSV.exists():
        with JOB_LINES_CSV.open("w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()


def week_bounds(d: date) -> tuple[date, date]:
    days_since_sunday = (d.weekday() + 1) % 7
    start = d - timedelta(days=days_since_sunday)
    end = start + timedelta(days=6)
    return start, end


def save_job(
    *,
    owner_telegram_id: int,
    tech_label: str,
    job_number: int | str,
    work_area: str,
    address: str,
    work_type: str,
    subtype_codes: list[str],
    rule_id: str,
    invoice_rows: list[dict[str, Any]],
    account_number: str = "",
    hookup_type: str = "",
    completion_datetime: datetime | None = None,
    notes: str = "",
) -> Path:
    _ensure_csv()
    now = miami_now()
    completed = completion_datetime or now
    completion_str = format_atn_datetime(completed)
    week_start, week_end = week_bounds(completed.date())
    subtype_str = "; ".join(subtype_codes)

    rows_to_write = []
    for row in invoice_rows:
        rows_to_write.append(
            {
                "recorded_at": now.isoformat(),
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "tech": row.get("tech", tech_label),
                "owner_telegram_id": owner_telegram_id,
                "job_number": job_number,
                "work_area": work_area,
                "completion_date": completion_str,
                "address": address,
                "account_number": account_number,
                "work_type": work_type,
                "subtype_codes": subtype_str,
                "hookup_type": hookup_type,
                "rule_id": rule_id,
                "job_code": row["job_code"],
                "qty": row.get("qty", 1),
                "item_total": row["item_total"],
                "line_type": LINE_TYPE_PRODUCTION,
                "confirmed": "TRUE",
                "notes": notes,
            }
        )

    if _use_db():
        from bot.db_store import append_rows

        append_rows(rows_to_write)
    else:
        _ensure_csv()
        with JOB_LINES_CSV.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writerows(rows_to_write)

    try:
        from bot.settings_store import set_work_day
        from bot.users import user_settings_key

        set_work_day(completed.date(), user_settings_key(owner_telegram_id), "working")
    except Exception:
        pass

    backup_dir = ROOT / "data" / "job_lines" / "archive"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{now.strftime('%Y%m%d_%H%M%S')}_{job_number}.json"
    backup_path.write_text(
        json.dumps(
            {
                "saved_at": now.isoformat(),
                "job_number": job_number,
                "rows": rows_to_write,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return backup_path


def save_tip(
    *,
    owner_telegram_id: int,
    tech_label: str,
    amount: float,
    work_area: str | None = None,
    completion_datetime: datetime | None = None,
    notes: str = "",
) -> Path:
    """Save a standalone cash tip (not linked to any job)."""
    tip_value = round(float(amount), 2)
    if tip_value == 0:
        raise ValueError("Tip amount cannot be zero")

    now = miami_now()
    completed = completion_datetime or now
    completion_str = format_atn_datetime(completed)
    week_start, week_end = week_bounds(completed.date())
    area = work_area or DEFAULT_WORK_AREA
    tip_ref = f"T{now.strftime('%H%M%S%f')[:9]}"

    row = {
        "recorded_at": now.isoformat(),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "tech": tech_label,
        "owner_telegram_id": owner_telegram_id,
        "job_number": tip_ref,
        "work_area": area,
        "completion_date": completion_str,
        "address": "CASH TIP",
        "account_number": "",
        "work_type": "Tip",
        "subtype_codes": "",
        "hookup_type": "",
        "rule_id": TIP_RULE_ID,
        "job_code": TIP_JOB_CODE,
        "qty": 1,
        "item_total": tip_value,
        "line_type": LINE_TYPE_TIP,
        "confirmed": "TRUE",
        "notes": notes,
    }

    if _use_db():
        from bot.db_store import append_rows

        append_rows([row])
    else:
        _ensure_csv()
        with JOB_LINES_CSV.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writerow(row)

    backup_dir = ROOT / "data" / "job_lines" / "archive"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{now.strftime('%Y%m%d_%H%M%S')}_tip_{tip_ref}.json"
    backup_path.write_text(
        json.dumps({"saved_at": now.isoformat(), "tip_ref": tip_ref, "rows": [row]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return backup_path


def save_fuel(
    *,
    owner_telegram_id: int,
    tech_label: str,
    amount: float,
    work_area: str | None = None,
    completion_datetime: datetime | None = None,
    notes: str = "",
) -> Path:
    """Save a standalone fuel expense (not linked to any job)."""
    fuel_value = round(float(amount), 2)
    if fuel_value <= 0:
        raise ValueError("Fuel amount must be greater than zero")

    now = miami_now()
    completed = completion_datetime or now
    completion_str = format_atn_datetime(completed)
    week_start, week_end = week_bounds(completed.date())
    area = work_area or DEFAULT_WORK_AREA
    fuel_ref = f"F{now.strftime('%H%M%S%f')[:9]}"

    row = {
        "recorded_at": now.isoformat(),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "tech": tech_label,
        "owner_telegram_id": owner_telegram_id,
        "job_number": fuel_ref,
        "work_area": area,
        "completion_date": completion_str,
        "address": "FUEL",
        "account_number": "",
        "work_type": "Fuel",
        "subtype_codes": "",
        "hookup_type": "",
        "rule_id": FUEL_RULE_ID,
        "job_code": FUEL_JOB_CODE,
        "qty": 1,
        "item_total": fuel_value,
        "line_type": LINE_TYPE_FUEL,
        "confirmed": "TRUE",
        "notes": notes,
    }

    if _use_db():
        from bot.db_store import append_rows

        append_rows([row])
    else:
        _ensure_csv()
        with JOB_LINES_CSV.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writerow(row)

    backup_dir = ROOT / "data" / "job_lines" / "archive"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{now.strftime('%Y%m%d_%H%M%S')}_fuel_{fuel_ref}.json"
    backup_path.write_text(
        json.dumps({"saved_at": now.isoformat(), "fuel_ref": fuel_ref, "rows": [row]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return backup_path


def load_week_lines(
    week_start: date,
    week_end: date,
    owner_telegram_id: int | None = None,
) -> list[dict[str, str]]:
    if _use_db():
        from bot.db_store import load_week_lines as db_load_week

        return db_load_week(week_start, week_end, owner_telegram_id)
    if not JOB_LINES_CSV.exists():
        return []
    rows: list[dict[str, str]] = []
    owner = str(owner_telegram_id) if owner_telegram_id is not None else None
    with JOB_LINES_CSV.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ws = date.fromisoformat(row["week_start"][:10])
            if ws != week_start:
                continue
            if owner is not None and row.get("owner_telegram_id") != owner:
                continue
            rows.append(row)
    return rows


def read_all_rows(owner_telegram_id: int | None = None) -> list[dict[str, str]]:
    return _read_all_rows(owner_telegram_id)


def fix_self_install_row(row: dict[str, str]) -> bool:
    """Normalize legacy Self Install work_type on a stored job line row."""
    if (row.get("work_type") or "").strip() != "Self Install":
        return False
    row["work_type"] = "New Install"
    subtypes = (row.get("subtype_codes") or "").strip()
    if not subtypes:
        row["subtype_codes"] = "Self Install"
    elif "self install" not in subtypes.lower():
        row["subtype_codes"] = f"{subtypes}; Self Install"
    if row.get("rule_id") == "self_install":
        row["rule_id"] = "new_install_self"
    return True


def migrate_self_install_work_types() -> int:
    """Fix legacy rows saved with work_type=Self Install."""
    if _use_db():
        from bot.db_store import migrate_self_install_work_types as db_migrate

        return db_migrate()

    _ensure_csv()
    with JOB_LINES_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    updated = sum(1 for row in rows if fix_self_install_row(row))
    if updated:
        _write_all_rows(rows)
    return updated


def migrate_orphan_job_lines(owner_telegram_id: int) -> int:
    """Assign rows without owner to the legacy user. Returns count updated."""
    if _use_db():
        from bot.db_store import migrate_orphan_job_lines as db_migrate

        return db_migrate(owner_telegram_id)

    _ensure_csv()
    with JOB_LINES_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    owner = str(owner_telegram_id)
    updated = 0
    for row in rows:
        row.setdefault("owner_telegram_id", "")
        if not row["owner_telegram_id"]:
            row["owner_telegram_id"] = owner
            updated += 1
    if updated:
        _write_all_rows(rows)
    return updated


def write_all_rows(rows: list[dict[str, str]]) -> None:
    _write_all_rows(rows)


def week_totals(owner_telegram_id: int, week_start: date | None = None) -> dict[str, Any]:
    if week_start is None:
        week_start, week_end = week_bounds(miami_now().date())
    else:
        week_end = week_start + timedelta(days=6)

    lines = load_week_lines(week_start, week_end, owner_telegram_id)
    production = sum_production(lines)
    tips = sum_tips(lines)
    fuel = sum_fuel(lines)
    jobs = len({r["job_number"] for r in lines if is_production_line(r)})
    return {
        "week_start": week_start,
        "week_end": week_end,
        "production": production,
        "tips": tips,
        "fuel": fuel,
        "line_count": len(lines),
        "job_count": jobs,
    }
