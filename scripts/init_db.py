#!/usr/bin/env python3
"""Create job_lines table on Render Postgres if missing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.db_store import DATABASE_URL, db_enabled

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS job_lines (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    tech TEXT NOT NULL,
    job_number TEXT NOT NULL,
    work_area TEXT NOT NULL,
    completion_date TEXT NOT NULL,
    address TEXT NOT NULL,
    account_number TEXT DEFAULT '',
    work_type TEXT NOT NULL,
    subtype_codes TEXT DEFAULT '',
    hookup_type TEXT DEFAULT '',
    rule_id TEXT NOT NULL,
    job_code TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    item_total NUMERIC(10,2) NOT NULL,
    confirmed TEXT DEFAULT 'TRUE',
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_job_lines_week_start ON job_lines(week_start);
CREATE INDEX IF NOT EXISTS idx_job_lines_job_number ON job_lines(job_number);
"""


def _seed_if_empty() -> None:
    import csv

    from bot.db_store import append_rows, read_all_rows

    seed_path = ROOT / "data" / "seed_jobs.csv"
    if not seed_path.exists():
        return
    if read_all_rows():
        return
    rows = list(csv.DictReader(seed_path.open(encoding="utf-8")))
    if not rows:
        return
    append_rows(rows)
    print(f"Seeded {len(rows)} job lines from {seed_path.name}.")


def main() -> None:
    if not db_enabled():
        print("DATABASE_URL not set — skip DB init (using local CSV).")
        return

    import psycopg

    statements = [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
    print("Database schema ready.")
    _seed_if_empty()


if __name__ == "__main__":
    main()
