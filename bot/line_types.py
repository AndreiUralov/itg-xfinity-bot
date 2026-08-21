"""Line classification for job rows (production vs tips)."""

from __future__ import annotations

LINE_TYPE_PRODUCTION = "production"
LINE_TYPE_TIP = "tip"
TIP_JOB_CODE = "TIP"
TIP_RULE_ID = "tip"


def line_type_of(row: dict) -> str:
    value = (row.get("line_type") or "").strip().lower()
    if value in (LINE_TYPE_PRODUCTION, LINE_TYPE_TIP):
        return value
    if row.get("job_code") == TIP_JOB_CODE:
        return LINE_TYPE_TIP
    return LINE_TYPE_PRODUCTION


def is_production_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_PRODUCTION


def is_tip_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_TIP


def sum_production(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_production_line(r)), 2)


def sum_tips(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_tip_line(r)), 2)
