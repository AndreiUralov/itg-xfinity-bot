"""Line classification for job rows (production vs tips vs fuel vs per diem).

Tips and per diem are tracked during the week but excluded from payroll invoice
Production/Net totals (ATN comparison). Tips remain separate personal income.
"""

from __future__ import annotations

LINE_TYPE_PRODUCTION = "production"
LINE_TYPE_TIP = "tip"
LINE_TYPE_FUEL = "fuel"
LINE_TYPE_PER_DIEM = "per_diem"

TIP_JOB_CODE = "TIP"
TIP_RULE_ID = "tip"
FUEL_JOB_CODE = "FUEL"
FUEL_RULE_ID = "fuel"
PER_DIEM_JOB_CODE = "PD."
PER_DIEM_RULE_ID = "per_diem"

INCOME_LINE_TYPES = frozenset({LINE_TYPE_TIP, LINE_TYPE_PER_DIEM})


def line_type_of(row: dict) -> str:
    value = (row.get("line_type") or "").strip().lower()
    if value in (LINE_TYPE_PRODUCTION, LINE_TYPE_TIP, LINE_TYPE_FUEL, LINE_TYPE_PER_DIEM):
        return value
    if row.get("job_code") == TIP_JOB_CODE:
        return LINE_TYPE_TIP
    if row.get("job_code") == FUEL_JOB_CODE:
        return LINE_TYPE_FUEL
    if row.get("job_code") == PER_DIEM_JOB_CODE:
        return LINE_TYPE_PER_DIEM
    return LINE_TYPE_PRODUCTION


def is_production_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_PRODUCTION


def is_tip_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_TIP


def is_fuel_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_FUEL


def is_per_diem_line(row: dict) -> bool:
    return line_type_of(row) == LINE_TYPE_PER_DIEM


def is_income_line(row: dict) -> bool:
    return line_type_of(row) in INCOME_LINE_TYPES


def sum_production(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_production_line(r)), 2)


def sum_tips(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_tip_line(r)), 2)


def sum_fuel(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_fuel_line(r)), 2)


def sum_per_diem(rows: list[dict]) -> float:
    return round(sum(float(r["item_total"]) for r in rows if is_per_diem_line(r)), 2)
