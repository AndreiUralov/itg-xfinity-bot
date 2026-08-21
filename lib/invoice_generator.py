"""
Generate weekly payroll PDF in ATN invoice format.
Layout mirrors ATN payroll PDF (M_I0KF_tech.pdf) for side-by-side comparison.
Employer: ITG | Payroll: ATN
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.line_types import LINE_TYPE_PRODUCTION, LINE_TYPE_TIP
from datetime_miami import format_atn_datetime, miami_now, parse_completion_datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
FORMAT_PATH = ROOT / "config" / "invoice_format.json"
DB_PATH = ROOT / "config" / "pay_database.json"


@dataclass
class InvoiceLine:
    tech: str
    job_number: int | str
    work_area: str
    completion_date: date | datetime | str
    address: str
    job_code: str
    qty: int
    item_total: float
    line_type: str = LINE_TYPE_PRODUCTION


@dataclass
class WeeklyInvoice:
    week_start: date
    week_end: date
    payment_date: date
    lines: list[InvoiceLine]
    production: float
    tips: float
    truck: float
    meter: float
    deposit: float | None
    net: float
    tech_id: str = "I0KF"

    @property
    def week_label(self) -> str:
        return f"WEEK {self.week_start.isoformat()} to {self.week_end.isoformat()} / PAYMENT DATE {self.payment_date.isoformat()}"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def format_completion_date(value: date | datetime | str) -> str:
    if isinstance(value, str) and " " in value and ":" in value.split(" ", 1)[-1]:
        # Already has time — normalize via parser
        return format_atn_datetime(parse_completion_datetime(value))
    if isinstance(value, str) and len(value) >= 10:
        parsed = parse_completion_datetime(value[:10])
        return format_atn_datetime(parsed)
    if isinstance(value, datetime):
        return format_atn_datetime(value)
    if isinstance(value, date):
        return format_atn_datetime(datetime.combine(value, miami_now().time(), tzinfo=ZoneInfo("America/New_York")))
    return format_atn_datetime(miami_now())


def format_currency(amount: float, negative_paren: bool = False) -> str:
    if negative_paren and amount > 0:
        return f"({amount:,.2f})"
    return f"{amount:,.2f}"


def format_deposit(amount: float | None) -> str:
    if amount is None or amount == 0:
        return "-"
    return f"{amount:,.2f}"


def sort_lines(lines: list[InvoiceLine]) -> list[InvoiceLine]:
    def sort_key(line: InvoiceLine) -> tuple:
        cd = line.completion_date
        if isinstance(cd, str):
            cd_key = cd[:10]
        elif isinstance(cd, datetime):
            cd_key = cd.date().isoformat()
        else:
            cd_key = cd.isoformat()
        return (cd_key, str(line.job_number), line.job_code)

    return sorted(lines, key=sort_key)


def previous_payroll_week(reference: date | None = None, tz: str = "America/New_York") -> tuple[date, date]:
    """Last completed payroll week (Sun–Sat). On Monday morning → week that ended last Saturday."""
    if reference is None:
        reference = datetime.now(ZoneInfo(tz)).date()

    if reference.weekday() == 0:
        week_end = reference - timedelta(days=2)
    else:
        days_since_saturday = (reference.weekday() - 5) % 7
        if days_since_saturday == 0:
            days_since_saturday = 7
        week_end = reference - timedelta(days=days_since_saturday)

    week_start = week_end - timedelta(days=6)
    return week_start, week_end


def payment_date_for_week(week_end: date, lag_days: int = 13) -> date:
    return week_end + timedelta(days=lag_days)


def build_weekly_invoice(
    lines: list[InvoiceLine],
    *,
    week_start: date,
    week_end: date,
    tech_id: str = "I0KF",
    full_week: bool = True,
    deposit: float | None = None,
    db: dict | None = None,
) -> WeeklyInvoice:
    db = db or load_json(DB_PATH)
    sorted_lines = sort_lines(lines)
    production = round(sum(line.item_total for line in sorted_lines if line.line_type != LINE_TYPE_TIP), 2)
    tips = round(sum(line.item_total for line in sorted_lines if line.line_type == LINE_TYPE_TIP), 2)
    truck = db["deductions"]["truck"]["full_week"] if full_week else db["deductions"]["truck"]["partial_week_example"]
    meter = db["deductions"]["meter"]["per_week"]
    deposit_val = deposit or 0.0
    net = round(production - truck - meter - deposit_val + tips, 2)
    lag = db["meta"].get("payment_lag_days", 13)

    return WeeklyInvoice(
        week_start=week_start,
        week_end=week_end,
        payment_date=payment_date_for_week(week_end, lag),
        lines=sorted_lines,
        production=production,
        tips=tips,
        truck=truck,
        meter=meter,
        deposit=deposit if deposit else None,
        net=net,
        tech_id=tech_id,
    )


def invoice_to_text(invoice: WeeklyInvoice) -> str:
    """Plain-text export matching PDF text extraction for diff/compare."""
    rows: list[str] = []
    rows.append("\t".join(["Production", "Truck", "Meter_Charge", "DEPOSIT", ""]))
    rows.append(
        "\t".join(
            [
                format_currency(invoice.production),
                format_currency(invoice.truck, negative_paren=True),
                format_currency(invoice.meter, negative_paren=True),
                format_deposit(invoice.deposit),
                format_currency(invoice.net),
            ]
        )
        + "\t$"
    )
    rows.append("Tech Job Number Work Area Completion Date Address Job Code QTY Item Total")

    for line in invoice.lines:
        address = line.address.upper()
        rows.append(
            f"{line.tech} {line.job_number} {line.work_area} "
            f"{format_completion_date(line.completion_date)} {address} "
            f"{line.job_code} {line.qty} {line.item_total:.2f}\t$"
        )

    rows.append(invoice.week_label)
    rows.append("DEPOSIT")
    return "\n".join(rows) + "\n"


def generate_invoice_pdf(invoice: WeeklyInvoice, output_path: Path) -> Path:
    fmt = load_json(FORMAT_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_size = landscape(letter)
    margins = fmt["page"]["margins_inch"]
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=page_size,
        leftMargin=margins["left"] * inch,
        rightMargin=margins["right"] * inch,
        topMargin=margins["top"] * inch,
        bottomMargin=margins["bottom"] * inch,
    )

    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "HeaderRight",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        alignment=TA_RIGHT,
    )
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        textColor=colors.HexColor("#333333"),
    )
    week_style = ParagraphStyle(
        "WeekLabel",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        spaceBefore=8,
        spaceAfter=4,
    )

    col_w = 1.2 * inch
    usable_width = page_size[0] - (margins["left"] + margins["right"]) * inch
    summary_inner = Table(
        [
            ["Production", "Truck", "Meter_Charge", "DEPOSIT", ""],
            [
                format_currency(invoice.production) + " $",
                format_currency(invoice.truck, negative_paren=True) + " $",
                format_currency(invoice.meter, negative_paren=True) + " $",
                format_deposit(invoice.deposit) + (" $" if invoice.deposit else ""),
                format_currency(invoice.net) + " $",
            ],
        ],
        colWidths=[col_w] * 5,
    )
    summary_inner.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 1), (-1, 1), 10),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                ("TOPPADDING", (0, 1), (-1, 1), 2),
            ]
        )
    )
    summary_width = col_w * 5
    summary_table = Table(
        [["", summary_inner]],
        colWidths=[usable_width - summary_width, summary_width],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    col_headers = [c["header"] for c in fmt["table_columns"]]
    table_data = [col_headers]

    for line in invoice.lines:
        address = line.address.upper() if fmt["formatting"]["address_uppercase"] else line.address
        table_data.append(
            [
                line.tech,
                str(line.job_number),
                line.work_area,
                format_completion_date(line.completion_date),
                address,
                line.job_code,
                str(line.qty),
                f"{line.item_total:.2f} $",
            ]
        )

    col_widths = [page_size[0] * c["width"] for c in fmt["table_columns"]]
    detail_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    detail_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (6, 1), (7, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ]
        )
    )

    story = [
        summary_table,
        Spacer(1, 0.12 * inch),
        detail_table,
        Spacer(1, 0.15 * inch),
        Paragraph(invoice.week_label, week_style),
        Paragraph("DEPOSIT", footer_style),
    ]

    doc.build(story)
    return output_path


def default_output_path(invoice: WeeklyInvoice) -> Path:
    fmt = load_json(FORMAT_PATH)
    pattern = fmt["formatting"]["filename_pattern"]
    filename = pattern.format(
        tech_id=invoice.tech_id,
        week_start=invoice.week_start.isoformat(),
        week_end=invoice.week_end.isoformat(),
    )
    return ROOT / "output" / "invoices" / filename
