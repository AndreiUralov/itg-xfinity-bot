"""Load, list, delete and reload today's saved jobs."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from bot.line_types import is_production_line, is_tip_line, sum_tips
from bot.storage import read_all_rows, write_all_rows
from datetime_miami import miami_now


def _read_all_rows() -> list[dict[str, str]]:
    return read_all_rows()


def _write_all_rows(rows: list[dict[str, str]]) -> None:
    write_all_rows(rows)


def _row_day(row: dict[str, str]) -> date:
    """Date the row belongs to (completion date preferred)."""
    completion = row.get("completion_date", "")
    if completion:
        return date.fromisoformat(completion[:10])
    recorded = row.get("recorded_at", "")
    if recorded:
        return date.fromisoformat(recorded[:10])
    return miami_now().date()


def _group_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = str(row["job_number"])
        grouped.setdefault(key, []).append(row)
    return grouped


def get_today_tips(day: date | None = None) -> list[dict[str, str]]:
    target = day or miami_now().date()
    return [r for r in _read_all_rows() if _row_day(r) == target and is_tip_line(r)]


def get_today_jobs(day: date | None = None) -> list[dict[str, Any]]:
    """Return today's jobs grouped by job_number with totals (production lines only)."""
    target = day or miami_now().date()
    all_rows = _read_all_rows()
    today_rows = [r for r in all_rows if _row_day(r) == target and is_production_line(r)]
    grouped = _group_rows(today_rows)

    jobs: list[dict[str, Any]] = []
    for job_number, lines in grouped.items():
        total = round(sum(float(r["item_total"]) for r in lines), 2)
        first = lines[0]
        codes = ", ".join(f"{r['job_code']} ${float(r['item_total']):.2f}" for r in lines)
        jobs.append(
            {
                "job_number": job_number,
                "work_type": first.get("work_type", ""),
                "work_area": first.get("work_area", ""),
                "address": first.get("address", ""),
                "account_number": first.get("account_number", ""),
                "hookup_type": first.get("hookup_type", ""),
                "subtype_codes": first.get("subtype_codes", ""),
                "rule_id": first.get("rule_id", ""),
                "completion_date": first.get("completion_date", ""),
                "total": total,
                "codes": codes,
                "line_count": len(lines),
                "rows": lines,
            }
        )

    jobs.sort(key=lambda j: j.get("completion_date", ""), reverse=True)
    return jobs


def today_totals(day: date | None = None) -> dict[str, Any]:
    jobs = get_today_jobs(day)
    tips = sum_tips(get_today_tips(day))
    return {
        "job_count": len(jobs),
        "production": round(sum(j["total"] for j in jobs), 2),
        "tips": tips,
    }


def get_job(job_number: str | int, day: date | None = None) -> dict[str, Any] | None:
    target = day or miami_now().date()
    for job in get_today_jobs(target):
        if str(job["job_number"]) == str(job_number):
            return job
    return None


def find_existing_job(job_number: str | int) -> dict[str, Any] | None:
    """Return saved job summary if job_number already exists today or this week."""
    if not job_number:
        return None

    today = miami_now().date()
    today_job = get_job(job_number, today)
    if today_job:
        return {**today_job, "scope": "today", "day": today.isoformat()}

    from bot.storage import week_bounds

    week_start, week_end = week_bounds(today)
    matching_rows = [
        r
        for r in _read_all_rows()
        if str(r["job_number"]) == str(job_number)
        and is_production_line(r)
        and week_start <= _row_day(r) <= week_end
    ]
    if not matching_rows:
        return None

    grouped = _group_rows(matching_rows)
    lines = grouped[str(job_number)]
    total = round(sum(float(r["item_total"]) for r in lines), 2)
    first = lines[0]
    row_day = _row_day(first)
    codes = ", ".join(f"{r['job_code']} ${float(r['item_total']):.2f}" for r in lines)
    return {
        "job_number": str(job_number),
        "work_type": first.get("work_type", ""),
        "work_area": first.get("work_area", ""),
        "address": first.get("address", ""),
        "account_number": first.get("account_number", ""),
        "hookup_type": first.get("hookup_type", ""),
        "subtype_codes": first.get("subtype_codes", ""),
        "rule_id": first.get("rule_id", ""),
        "completion_date": first.get("completion_date", ""),
        "total": total,
        "codes": codes,
        "line_count": len(lines),
        "rows": lines,
        "scope": "week",
        "day": row_day.isoformat(),
    }


def delete_job(job_number: str | int, day: date | None = None) -> tuple[bool, int]:
    """Remove all CSV lines for job_number on the given day. Returns (ok, removed_count)."""
    target = day or miami_now().date()
    all_rows = _read_all_rows()
    kept: list[dict[str, str]] = []
    removed = 0

    for row in all_rows:
        if str(row["job_number"]) == str(job_number) and _row_day(row) == target:
            removed += 1
        else:
            kept.append(row)

    if removed == 0:
        return False, 0

    _write_all_rows(kept)
    return True, removed


def job_to_session_data(job: dict[str, Any]) -> dict[str, Any]:
    """Build session payload so user can re-confirm / edit a saved job."""
    subtype_raw = job.get("subtype_codes") or ""
    subtype_codes = [s.strip() for s in subtype_raw.split(";") if s.strip()]
    return {
        "job_number": job["job_number"],
        "work_area": job.get("work_area", "Broward"),
        "address": job.get("address", ""),
        "account_number": job.get("account_number", ""),
        "work_type": job.get("work_type", ""),
        "subtype_codes": subtype_codes,
        "hookup_type": job.get("hookup_type", ""),
        "rule_id": job.get("rule_id", ""),
        "completion_date": job.get("completion_date", ""),
        "from_edit": True,
    }
