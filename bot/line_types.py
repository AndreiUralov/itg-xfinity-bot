"""Line classification for job rows (production vs tips vs fuel)."""

from __future__ import annotations

LINE_TYPE_PRODUCTION = "production"
LINE_TYPE_TIP = "tip"
LINE_TYPE_FUEL = "fuel"

TIP_JOB_CODE = "TIP"
TIP_RULE_ID = "tip"
FUEL_JOB_CODE = "FUEL"
FUEL_RULE_ID = "fuel"


def line_type_of(row: dict) -> str:
    value = (row.get("line_type") or "").strip().lower()
    if value in (LINE_TYPE_PRODUCTION, LINE_TYPE_TIP, LINE_TYPE_FUEL):
        return value
    if row.get("job_code") == TIP_JOB_CODE:
        return LINE_TYPE_TIP
    if row.get("job_code") == FUEL_JOB_CODE:
        return LINE_TYPE_FUEL
    return LINE_TYPE_PRODUCTION


def is_production_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_PRODUCTION


def is_tip_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_TIP


def is_fuel_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_FUEL


def sum_production(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_production_line(r)), 2)


def sum_tips(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_tip_line(r)), 2)


def sum_fuel(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_fuel_line(r)), 2)
