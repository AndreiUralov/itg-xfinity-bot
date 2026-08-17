"""
ITG pay calculator — rates from ATN payroll, work logged from Tech360.
Uses config/pay_database.json — same line-item logic as payroll invoices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "config" / "pay_database.json"


@dataclass
class PayLine:
    code: str
    qty: int
    amount: float
    label: str = ""

    @property
    def total(self) -> float:
        return round(self.qty * self.amount, 2)


@dataclass
class JobPayResult:
    job_number: int | str
    rule_id: str | None
    lines: list[PayLine] = field(default_factory=list)
    confirmed: bool = True
    needs_user_input: str | None = None

    @property
    def total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    def to_invoice_rows(self, tech_id: str, work_area: str, address: str, completion_date: date) -> list[dict]:
        rows = []
        for line in self.lines:
            rows.append(
                {
                    "tech": tech_id,
                    "job_number": self.job_number,
                    "work_area": work_area,
                    "completion_date": completion_date.isoformat(),
                    "address": address,
                    "job_code": line.code,
                    "qty": line.qty,
                    "item_total": line.total,
                }
            )
        return rows


@dataclass
class WeeklySummary:
    week_start: date
    week_end: date
    production: float
    truck: float
    meter: float
    deposit: float
    net: float
    job_count: int
    line_count: int


def load_database(path: Path = DB_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _code_amount(db: dict, code: str) -> tuple[float, str]:
    if code in db["job_codes"]:
        entry = db["job_codes"][code]
        return entry["amount"], entry.get("label", code)
    if code in db["equipment_codes"]:
        entry = db["equipment_codes"][code]
        return entry["amount"], entry.get("label", code)
    for item in db.get("manual_addon_codes", []):
        if item["code"] == code:
            return item["amount"], item.get("label", code)
    raise KeyError(f"Unknown job code: {code}")


def _normalize_subtype_list(subtypes: list[str] | str | None) -> list[str]:
    if subtypes is None:
        return []
    if isinstance(subtypes, str):
        subtypes = [subtypes]
    return [s.upper().strip() for s in subtypes]


def _subtype_matches(rule: dict, subtypes: list[str]) -> bool:
    upper = [s.upper() for s in subtypes]
    joined = " ".join(upper)

    if "match_all_subtype" in rule:
        for needle in rule["match_all_subtype"]:
            if needle.upper() not in joined:
                return False
        return True

    if "match_any_subtype" in rule:
        for needle in rule["match_any_subtype"]:
            if needle.upper() in joined:
                return True
        return False

    return True


def _build_lines(db: dict, line_defs: list[dict]) -> list[PayLine]:
    lines: list[PayLine] = []
    for item in line_defs:
        amount, label = _code_amount(db, item["code"])
        lines.append(PayLine(code=item["code"], qty=item.get("qty", 1), amount=amount, label=label))
    return lines


def find_matching_rule(db: dict, work_type: str, subtypes: list[str] | str | None) -> dict | None:
    subtype_list = _normalize_subtype_list(subtypes)
    work_type_norm = work_type.strip()

    sorted_rules = sorted(db["calculation_rules"], key=lambda r: r["priority"])
    for rule in sorted_rules:
        if rule["match"].get("work_type") != work_type_norm:
            continue
        if "match_all_subtype" in rule or "match_any_subtype" in rule:
            if not _subtype_matches(rule, subtype_list):
                continue
        return rule
    return None


def calculate_job(
    db: dict,
    *,
    job_number: int | str,
    work_type: str,
    subtypes: list[str] | str | None = None,
    equipment: list[str] | None = None,
    product_code: str | None = None,
    optional_addons: list[str] | None = None,
) -> JobPayResult:
    rule = find_matching_rule(db, work_type, subtypes)
    if rule is None:
        return JobPayResult(
            job_number=job_number,
            rule_id=None,
            confirmed=False,
            needs_user_input="manual_code_entry",
        )

    rule_id = rule["id"]
    lines: list[PayLine] = []

    if rule.get("user_prompt") == "manual_code_entry":
        return JobPayResult(
            job_number=job_number,
            rule_id=rule_id,
            confirmed=False,
            needs_user_input="manual_code_entry",
        )

    if "lines" in rule:
        lines.extend(_build_lines(db, rule["lines"]))

    if "base_lines" in rule:
        lines.extend(_build_lines(db, rule["base_lines"]))

    if rule.get("product_prompt"):
        code = product_code or rule["product_prompt"]["options"][0]["code"]
        amount, label = _code_amount(db, code)
        lines.append(PayLine(code=code, qty=1, amount=amount, label=label))

    if rule.get("equipment_prompt"):
        if not equipment:
            return JobPayResult(
                job_number=job_number,
                rule_id=rule_id,
                lines=lines,
                confirmed=bool(lines),
                needs_user_input="equipment_prompt",
            )
        for code in equipment:
            amount, label = _code_amount(db, code)
            lines.append(PayLine(code=code, qty=1, amount=amount, label=label))

    if optional_addons:
        for code in optional_addons:
            amount, label = _code_amount(db, code)
            lines.append(PayLine(code=code, qty=1, amount=amount, label=label))

    return JobPayResult(
        job_number=job_number,
        rule_id=rule_id,
        lines=lines,
        confirmed=rule.get("confirmed", True) is True,
        needs_user_input=None,
    )


def week_bounds(d: date) -> tuple[date, date]:
    """Payroll week: Sunday → Saturday."""
    days_since_sunday = (d.weekday() + 1) % 7
    start = d - timedelta(days=days_since_sunday)
    end = start + timedelta(days=6)
    return start, end


def calculate_weekly(
    db: dict,
    job_results: list[JobPayResult],
    *,
    week_start: date | None = None,
    full_week: bool = True,
    deposit: float = 0.0,
) -> WeeklySummary:
    if week_start is None:
        week_start, week_end = week_bounds(date.today())
    else:
        week_end = week_start + timedelta(days=6)

    production = round(sum(job.total for job in job_results), 2)
    truck = db["deductions"]["truck"]["full_week"] if full_week else db["deductions"]["truck"]["partial_week_example"]
    meter = db["deductions"]["meter"]["per_week"]
    net = round(production - truck - meter - deposit, 2)
    line_count = sum(len(job.lines) for job in job_results)

    return WeeklySummary(
        week_start=week_start,
        week_end=week_end,
        production=production,
        truck=truck,
        meter=meter,
        deposit=deposit,
        net=net,
        job_count=len(job_results),
        line_count=line_count,
    )


def validate_examples(db: dict | None = None) -> list[str]:
    db = db or load_database()
    errors: list[str] = []

    for example in db["confirmed_examples"]:
        result = calculate_job(
            db,
            job_number=example["job_number"],
            work_type=example["work_type"],
            subtypes=example.get("subtype"),
            equipment=example.get("equipment"),
            product_code=example.get("product_code"),
            optional_addons=example.get("optional_addons"),
        )

        if result.needs_user_input:
            errors.append(f"Job {example['job_number']}: unexpected user input required ({result.needs_user_input})")
            continue

        if abs(result.total - example["expected_total"]) > 0.01:
            errors.append(
                f"Job {example['job_number']}: expected ${example['expected_total']}, got ${result.total}"
            )

        expected_codes = [line["code"] for line in example["expected_lines"]]
        actual_codes = [line.code for line in result.lines]
        if expected_codes != actual_codes:
            errors.append(
                f"Job {example['job_number']}: expected codes {expected_codes}, got {actual_codes}"
            )

    return errors


if __name__ == "__main__":
    database = load_database()
    validation_errors = validate_examples(database)

    print("=== Pay Database Validation ===")
    if validation_errors:
        for err in validation_errors:
            print(f"FAIL: {err}")
    else:
        print(f"OK: All {len(database['confirmed_examples'])} confirmed examples passed.")

    print("\n=== Sample Calculations ===")
    samples = [
        dict(job_number=497745, work_type="Service Change", subtypes=["TECH RECOVERY", "DF:CHNL-CARE"]),
        dict(job_number=509413, work_type="Trouble Call", subtypes=["HSD OUT", "H2:INT OUT"]),
        dict(
            job_number=498945,
            work_type="Service Change",
            subtypes=["VID UP"],
            equipment=["E.B.5."],
        ),
    ]

    for kwargs in samples:
        result = calculate_job(database, **kwargs)
        print(f"\nJob #{result.job_number} [{result.rule_id}]")
        for line in result.lines:
            print(f"  {line.code} x{line.qty} = ${line.total:.2f}  ({line.label})")
        print(f"  TOTAL: ${result.total:.2f}")
