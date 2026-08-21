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

from bot.config import JOB_LINES_CSV, TECH_ID
from bot.line_types import LINE_TYPE_PRODUCTION, LINE_TYPE_TIP, TIP_JOB_CODE, TIP_RULE_ID, sum_production, sum_tips
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
]


def _read_all_rows() -> list[dict[str, str]]:
    if _use_db():
        from bot.db_store import read_all_rows as db_read

        return db_read()
    _ensure_csv()
    with JOB_LINES_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row.setdefault("line_type", LINE_TYPE_PRODUCTION)
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
    tip_amount: float = 0.0,
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
                "tech": row.get("tech", TECH_ID),
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

    tip_value = round(float(tip_amount or 0), 2)
    if tip_value > 0:
        rows_to_write.append(
            {
                "recorded_at": now.isoformat(),
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "tech": TECH_ID,
                "job_number": job_number,
                "work_area": work_area,
                "completion_date": completion_str,
                "address": address,
                "account_number": account_number,
                "work_type": work_type,
                "subtype_codes": subtype_str,
                "hookup_type": hookup_type,
                "rule_id": TIP_RULE_ID,
                "job_code": TIP_JOB_CODE,
                "qty": 1,
                "item_total": tip_value,
                "line_type": LINE_TYPE_TIP,
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

        set_work_day(completed.date(), TECH_ID, "working")
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


def load_week_lines(week_start: date, week_end: date) -> list[dict[str, str]]:
    if _use_db():
        from bot.db_store import load_week_lines as db_load_week

        return db_load_week(week_start, week_end)
    if not JOB_LINES_CSV.exists():
        return []
    rows: list[dict[str, str]] = []
    with JOB_LINES_CSV.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ws = date.fromisoformat(row["week_start"][:10])
            if ws == week_start:
                rows.append(row)
    return rows


def read_all_rows() -> list[dict[str, str]]:
    return _read_all_rows()


def write_all_rows(rows: list[dict[str, str]]) -> None:
    _write_all_rows(rows)


def week_totals(week_start: date | None = None) -> dict[str, Any]:
    if week_start is None:
        week_start, week_end = week_bounds(miami_now().date())
    else:
        week_end = week_start + timedelta(days=6)

    lines = load_week_lines(week_start, week_end)
    production = sum_production(lines)
    tips = sum_tips(lines)
    jobs = len({r["job_number"] for r in lines})
    return {
        "week_start": week_start,
        "week_end": week_end,
        "production": production,
        "tips": tips,
        "line_count": len(lines),
        "job_count": jobs,
    }
