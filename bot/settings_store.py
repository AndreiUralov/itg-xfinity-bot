"""Work days, weekly goals, scheduler dedup, and bot settings."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_JSON = ROOT / "data" / "bot_settings.json"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_days (
    work_date DATE NOT NULL,
    tech TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('working', 'off')),
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (work_date, tech)
);
CREATE TABLE IF NOT EXISTS weekly_goals (
    week_start DATE NOT NULL,
    tech TEXT NOT NULL,
    goal_amount NUMERIC(10,2) NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (week_start, tech)
);
CREATE TABLE IF NOT EXISTS scheduler_runs (
    task_name TEXT NOT NULL,
    run_date DATE NOT NULL,
    ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (task_name, run_date)
);
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def db_enabled() -> bool:
    return bool(DATABASE_URL)


def _connect():
    import psycopg

    url = DATABASE_URL
    if "sslmode=" not in url and ".render.com" in url:
        url = f"{url}{'&' if '?' in url else '?'}sslmode=require"
    return psycopg.connect(url)


def ensure_schema() -> None:
    if not db_enabled():
        return
    statements = [s.strip() for s in EXTRA_SCHEMA.split(";") if s.strip()]
    with _connect() as conn, conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()


def _load_json() -> dict[str, Any]:
    if not SETTINGS_JSON.exists():
        return {"work_days": {}, "weekly_goals": {}, "scheduler_runs": {}, "bot_settings": {}}
    return json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))


def _save_json(data: dict[str, Any]) -> None:
    SETTINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_chat_id(chat_id: int | str) -> None:
    set_setting("telegram_chat_id", str(chat_id))


def get_chat_id() -> str | None:
    return get_setting("telegram_chat_id")


def set_setting(key: str, value: str) -> None:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """,
                (key, value),
            )
            conn.commit()
        return
    data = _load_json()
    data.setdefault("bot_settings", {})[key] = value
    _save_json(data)


def get_setting(key: str) -> str | None:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
            row = cur.fetchone()
        return row[0] if row else None
    data = _load_json()
    return data.get("bot_settings", {}).get(key)


def set_work_day(work_date: date, tech: str, status: str) -> None:
    if status not in ("working", "off"):
        raise ValueError(status)
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO work_days (work_date, tech, status, checked_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (work_date, tech) DO UPDATE
                SET status = EXCLUDED.status, checked_at = NOW()
                """,
                (work_date, tech, status),
            )
            conn.commit()
        return
    data = _load_json()
    data.setdefault("work_days", {})[f"{work_date.isoformat()}:{tech}"] = status
    _save_json(data)


def get_work_day(work_date: date, tech: str) -> str | None:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM work_days WHERE work_date = %s AND tech = %s",
                (work_date, tech),
            )
            row = cur.fetchone()
        return row[0] if row else None
    data = _load_json()
    return data.get("work_days", {}).get(f"{work_date.isoformat()}:{tech}")


def count_work_days(week_start: date, week_end: date, tech: str) -> int:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM work_days
                WHERE tech = %s AND work_date BETWEEN %s AND %s AND status = 'working'
                """,
                (tech, week_start, week_end),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0
    data = _load_json()
    count = 0
    for key, status in data.get("work_days", {}).items():
        if status != "working":
            continue
        day_str, key_tech = key.rsplit(":", 1)
        if key_tech != tech:
            continue
        day = date.fromisoformat(day_str)
        if week_start <= day <= week_end:
            count += 1
    return count


def set_weekly_goal(week_start: date, tech: str, amount: float) -> None:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO weekly_goals (week_start, tech, goal_amount, set_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (week_start, tech) DO UPDATE
                SET goal_amount = EXCLUDED.goal_amount, set_at = NOW()
                """,
                (week_start, tech, amount),
            )
            conn.commit()
        return
    data = _load_json()
    data.setdefault("weekly_goals", {})[f"{week_start.isoformat()}:{tech}"] = amount
    _save_json(data)


def clear_weekly_goal(week_start: date, tech: str) -> None:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM weekly_goals WHERE week_start = %s AND tech = %s",
                (week_start, tech),
            )
            conn.commit()
        return
    data = _load_json()
    data.get("weekly_goals", {}).pop(f"{week_start.isoformat()}:{tech}", None)
    _save_json(data)


def get_weekly_goal(week_start: date, tech: str) -> float | None:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT goal_amount FROM weekly_goals WHERE week_start = %s AND tech = %s",
                (week_start, tech),
            )
            row = cur.fetchone()
        return float(row[0]) if row else None
    data = _load_json()
    value = data.get("weekly_goals", {}).get(f"{week_start.isoformat()}:{tech}")
    return float(value) if value is not None else None


def task_already_ran(task_name: str, run_date: date) -> bool:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM scheduler_runs WHERE task_name = %s AND run_date = %s",
                (task_name, run_date),
            )
            return cur.fetchone() is not None
    data = _load_json()
    return data.get("scheduler_runs", {}).get(f"{task_name}:{run_date.isoformat()}") is True


def mark_task_ran(task_name: str, run_date: date) -> None:
    if db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scheduler_runs (task_name, run_date, ran_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT DO NOTHING
                """,
                (task_name, run_date),
            )
            conn.commit()
        return
    data = _load_json()
    data.setdefault("scheduler_runs", {})[f"{task_name}:{run_date.isoformat()}"] = True
    _save_json(data)
