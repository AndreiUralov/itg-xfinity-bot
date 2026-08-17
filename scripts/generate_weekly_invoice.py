#!/usr/bin/env python3
"""
Generate weekly ATN-format payroll invoice PDF (ITG project).

Usage:
  python scripts/generate_weekly_invoice.py
  python scripts/generate_weekly_invoice.py --week 2026-07-26 2026-08-01
  python scripts/generate_weekly_invoice.py --sample
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from invoice_generator import InvoiceLine, build_weekly_invoice, generate_invoice_pdf, invoice_to_text, default_output_path
from weekly_report import generate_weekly_report, load_lines_from_json


def build_sample_invoice() -> None:
    """Reproduce week 2026-07-26 to 2026-08-01 from real ATN payroll for format validation."""
    sample_path = ROOT / "data" / "sample" / "week_2026-07-26_lines.json"
    lines = load_lines_from_json(sample_path)

    invoice = build_weekly_invoice(
        lines,
        week_start=date(2026, 7, 26),
        week_end=date(2026, 8, 1),
        full_week=True,
    )

    pdf_path = ROOT / "output" / "invoices" / "M_I0KF_tech_SAMPLE_week_2026-07-26_to_2026-08-01.pdf"
    txt_path = pdf_path.with_suffix(".txt")

    generate_invoice_pdf(invoice, pdf_path)
    txt_path.write_text(invoice_to_text(invoice), encoding="utf-8")

    print(f"Sample invoice generated:")
    print(f"  PDF: {pdf_path}")
    print(f"  TXT: {txt_path}")
    print(f"  Production: ${invoice.production:,.2f}")
    print(f"  Net:        ${invoice.net:,.2f}")
    print(f"  Lines:      {len(invoice.lines)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ATN-format weekly payroll invoice (ITG)")
    parser.add_argument("--week", nargs=2, metavar=("START", "END"), help="Week range YYYY-MM-DD")
    parser.add_argument("--sample", action="store_true", help="Generate sample from real payroll week")
    parser.add_argument("--partial-week", action="store_true", help="Use partial truck deduction")
    args = parser.parse_args()

    if args.sample:
        build_sample_invoice()
        return

    week_start = week_end = None
    if args.week:
        week_start = date.fromisoformat(args.week[0])
        week_end = date.fromisoformat(args.week[1])

    result = generate_weekly_report(
        week_start=week_start,
        week_end=week_end,
        full_week=not args.partial_week,
    )

    print("Weekly invoice generated:")
    for key, path in result.items():
        print(f"  {key}: {path}")


if __name__ == "__main__":
    main()
