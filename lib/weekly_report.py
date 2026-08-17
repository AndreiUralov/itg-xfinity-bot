"""
Load job lines and produce weekly ATN-format payroll invoice (PDF + text) for ITG techs.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from invoice_generator import (
    InvoiceLine,
    build_weekly_invoice,
    default_output_path,
    generate_invoice_pdf,
    invoice_to_text,
    load_json,
    previous_payroll_week,
)

ROOT = Path(__file__).resolve().parent.parent
JOB_LINES_DIR = ROOT / "data" / "job_lines"
OUTPUT_DIR = ROOT / "output" / "invoices"


def parse_date(value: str) -> date:
    return datetime.fromisoformat(value[:10]).date()


def line_in_week(line_date: date, week_start: date, week_end: date) -> bool:
    return week_start <= line_date <= week_end


def load_lines_from_json(path: Path) -> list[InvoiceLine]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        if "rows" in raw:
            return [_dict_to_line(item) for item in raw["rows"]]
        return [_dict_to_line(raw)]
    return [_dict_to_line(item) for item in raw]


def load_lines_from_csv(path: Path) -> list[InvoiceLine]:
    lines: list[InvoiceLine] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("job_number"):
                continue
            lines.append(_dict_to_line(row))
    return lines


def current_payroll_week(reference: date | None = None) -> tuple[date, date]:
    """Current payroll week Sun–Sat (includes today's jobs)."""
    if reference is None:
        reference = datetime.now(ZoneInfo("America/New_York")).date()
    days_since_sunday = (reference.weekday() + 1) % 7
    week_start = reference - timedelta(days=days_since_sunday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def load_all_job_lines(directory: Path = JOB_LINES_DIR) -> list[InvoiceLine]:
    try:
        sys.path.insert(0, str(ROOT))
        from bot.storage import read_all_rows

        rows = read_all_rows()
        if rows:
            return [_dict_to_line(row) for row in rows]
    except Exception:
        pass

    csv_path = directory / "jobs.csv"
    if csv_path.exists():
        return load_lines_from_csv(csv_path)

    lines: list[InvoiceLine] = []
    if not directory.exists():
        return lines
    for path in sorted(directory.glob("*.json")):
        lines.extend(load_lines_from_json(path))
    return lines


def _dict_to_line(data: dict[str, Any]) -> InvoiceLine:
    completion = data.get("completion_date") or data.get("recorded_at", "")
    if isinstance(completion, str) and len(completion) >= 10:
        completion_date = completion
    else:
        completion_date = str(completion)

    return InvoiceLine(
        tech=data.get("tech", "I0KF"),
        job_number=data["job_number"],
        work_area=data.get("work_area", "Broward"),
        completion_date=completion_date,
        address=data["address"],
        job_code=data["job_code"],
        qty=int(data.get("qty", 1)),
        item_total=float(data["item_total"]),
    )


def filter_lines_for_week(lines: list[InvoiceLine], week_start: date, week_end: date) -> list[InvoiceLine]:
    filtered: list[InvoiceLine] = []
    for line in lines:
        cd = line.completion_date
        if isinstance(cd, str):
            line_date = parse_date(cd)
        elif isinstance(cd, datetime):
            line_date = cd.date()
        else:
            line_date = cd
        if line_in_week(line_date, week_start, week_end):
            filtered.append(line)
    return filtered


def generate_weekly_report(
    *,
    week_start: date | None = None,
    week_end: date | None = None,
    lines: list[InvoiceLine] | None = None,
    full_week: bool = True,
    deposit: float | None = None,
    tech_id: str = "I0KF",
) -> dict[str, Path]:
    db = load_json(ROOT / "config" / "pay_database.json")

    if week_start is None or week_end is None:
        week_start, week_end = previous_payroll_week()

    if lines is None:
        all_lines = load_all_job_lines()
        lines = filter_lines_for_week(all_lines, week_start, week_end)

    invoice = build_weekly_invoice(
        lines,
        week_start=week_start,
        week_end=week_end,
        tech_id=tech_id,
        full_week=full_week,
        deposit=deposit,
        db=db,
    )

    pdf_path = default_output_path(invoice)
    txt_path = pdf_path.with_suffix(".txt")

    generate_invoice_pdf(invoice, pdf_path)
    txt_path.write_text(invoice_to_text(invoice), encoding="utf-8")

    summary_path = OUTPUT_DIR / f"summary_{week_start}_{week_end}.json"
    summary_path.write_text(
        json.dumps(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "payment_date": invoice.payment_date.isoformat(),
                "production": invoice.production,
                "truck": invoice.truck,
                "meter": invoice.meter,
                "deposit": invoice.deposit,
                "net": invoice.net,
                "line_count": len(invoice.lines),
                "job_count": len({line.job_number for line in invoice.lines}),
                "pdf": str(pdf_path),
                "txt": str(txt_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {"pdf": pdf_path, "txt": txt_path, "summary": summary_path}


def should_run_monday_report(now: datetime | None = None, tz: str = "America/New_York") -> bool:
    fmt = load_json(ROOT / "config" / "invoice_format.json")
    if now is None:
        now = datetime.now(ZoneInfo(tz))
    else:
        now = now.astimezone(ZoneInfo(tz))

    target_hour = int(fmt["schedule"]["time"].split(":")[0])
    return now.weekday() == 0 and now.hour == target_hour
