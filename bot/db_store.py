"""PostgreSQL storage for job lines (Neon / any Postgres)."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bot.line_types import LINE_TYPE_PRODUCTION

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

_LINE_COLUMNS = """
    recorded_at, week_start, week_end, tech, job_number, work_area,
    completion_date, address, account_number, work_type, subtype_codes,
    hookup_type, rule_id, job_code, qty, item_total, line_type, confirmed, notes,
    owner_telegram_id
"""

_SELECT_COLUMNS = """
    recorded_at::text, week_start::text, week_end::text, tech, job_number,
    work_area, completion_date, address, account_number, work_type,
    subtype_codes, hookup_type, rule_id, job_code, qty::text, item_total::text,
    line_type, confirmed, notes, owner_telegram_id::text
"""


def db_enabled() -> bool:
    return bool(DATABASE_URL)


def _connect():
    url = DATABASE_URL
    if "sslmode=" not in url and ".render.com" in url:
        url = f"{url}{'&' if '?' in url else '?'}sslmode=require"
    return psycopg.connect(url, row_factory=dict_row)


def _normalize_row(row: dict[str, Any]) -> dict[str, str]:
    normalized = {k: ("" if v is None else str(v)) for k, v in row.items()}
    if not normalized.get("line_type"):
        normalized["line_type"] = LINE_TYPE_PRODUCTION
    normalized.setdefault("owner_telegram_id", "")
    return normalized


def read_all_rows(owner_telegram_id: int | None = None) -> list[dict[str, str]]:
    with _connect() as conn, conn.cursor() as cur:
        if owner_telegram_id is not None:
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM job_lines
                WHERE owner_telegram_id = %s
                ORDER BY recorded_at, id
                """,
                (owner_telegram_id,),
            )
        else:
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM job_lines
                ORDER BY recorded_at, id
                """
            )
        rows = cur.fetchall()
    return [_normalize_row(row) for row in rows]


def append_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with _connect() as conn, conn.cursor() as cur:
        for row in rows:
            payload = dict(row)
            payload.setdefault("line_type", LINE_TYPE_PRODUCTION)
            cur.execute(
                f"""
                INSERT INTO job_lines ({_LINE_COLUMNS})
                VALUES (
                    %(recorded_at)s, %(week_start)s, %(week_end)s, %(tech)s, %(job_number)s,
                    %(work_area)s, %(completion_date)s, %(address)s, %(account_number)s,
                    %(work_type)s, %(subtype_codes)s, %(hookup_type)s, %(rule_id)s,
                    %(job_code)s, %(qty)s, %(item_total)s, %(line_type)s, %(confirmed)s, %(notes)s,
                    %(owner_telegram_id)s
                )
                """,
                payload,
            )
        conn.commit()


def replace_all_rows(rows: list[dict[str, str]]) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM job_lines")
        for row in rows:
            payload = dict(row)
            payload.setdefault("line_type", LINE_TYPE_PRODUCTION)
            cur.execute(
                f"""
                INSERT INTO job_lines ({_LINE_COLUMNS})
                VALUES (
                    %(recorded_at)s, %(week_start)s, %(week_end)s, %(tech)s, %(job_number)s,
                    %(work_area)s, %(completion_date)s, %(address)s, %(account_number)s,
                    %(work_type)s, %(subtype_codes)s, %(hookup_type)s, %(rule_id)s,
                    %(job_code)s, %(qty)s, %(item_total)s, %(line_type)s, %(confirmed)s, %(notes)s,
                    %(owner_telegram_id)s
                )
                """,
                payload,
            )
        conn.commit()


def load_week_lines(
    week_start: date,
    week_end: date,
    owner_telegram_id: int | None = None,
) -> list[dict[str, str]]:
    with _connect() as conn, conn.cursor() as cur:
        if owner_telegram_id is not None:
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM job_lines
                WHERE week_start = %s AND owner_telegram_id = %s
                ORDER BY recorded_at, id
                """,
                (week_start, owner_telegram_id),
            )
        else:
            cur.execute(
                f"""
                SELECT {_SELECT_COLUMNS}
                FROM job_lines
                WHERE week_start = %s
                ORDER BY recorded_at, id
                """,
                (week_start,),
            )
        rows = cur.fetchall()
    return [_normalize_row(row) for row in rows]
