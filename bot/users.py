"""Telegram user profiles — identity by telegram_user_id, tech_id is display label only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.config import DEFAULT_WORK_AREA, TECH_ID, TELEGRAM_ALLOWED_USER_IDS

ROOT = Path(__file__).resolve().parent.parent
USERS_JSON = ROOT / "data" / "bot_users.json"

TECH_LABEL_RE = re.compile(r"^[A-Z0-9._-]{2,12}$", re.IGNORECASE)


@dataclass
class UserProfile:
    telegram_user_id: int
    tech_id: str
    chat_id: int
    display_name: str = ""
    work_area: str | None = None
    is_active: bool = True

    @property
    def effective_work_area(self) -> str:
        return (self.work_area or DEFAULT_WORK_AREA).strip()


def user_settings_key(telegram_user_id: int) -> str:
    """Key for goals / work_days — always the Telegram user id."""
    return str(telegram_user_id)


def default_tech_label(telegram_user_id: int) -> str:
    return f"U{abs(telegram_user_id) % 1000000:06d}"


def normalize_tech_label(value: str) -> str:
    return value.strip().upper()


def validate_tech_label(value: str) -> bool:
    return bool(TECH_LABEL_RE.match(normalize_tech_label(value)))


def _db_enabled() -> bool:
    from bot.settings_store import db_enabled

    return db_enabled()


def _connect():
    from bot.settings_store import _connect

    return _connect()


def ensure_users_schema() -> None:
    if not _db_enabled():
        return
    sql = """
    CREATE TABLE IF NOT EXISTS bot_users (
        telegram_user_id BIGINT PRIMARY KEY,
        tech_id TEXT NOT NULL DEFAULT '',
        chat_id BIGINT NOT NULL,
        display_name TEXT DEFAULT '',
        work_area TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ALTER TABLE bot_users DROP CONSTRAINT IF EXISTS bot_users_tech_id_key;
    CREATE INDEX IF NOT EXISTS idx_job_lines_owner ON job_lines(owner_telegram_id);
  """
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with _connect() as conn, conn.cursor() as cur:
        for stmt in statements:
            try:
                cur.execute(stmt)
            except Exception:
                pass
        conn.commit()


def _load_json_users() -> list[dict[str, Any]]:
    if not USERS_JSON.exists():
        return []
    data = json.loads(USERS_JSON.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("users", [])


def _save_json_users(users: list[dict[str, Any]]) -> None:
    USERS_JSON.parent.mkdir(parents=True, exist_ok=True)
    USERS_JSON.write_text(json.dumps({"users": users}, indent=2, ensure_ascii=False), encoding="utf-8")


def _row_to_profile(row: dict[str, Any]) -> UserProfile:
    uid = int(row["telegram_user_id"])
    tech = str(row.get("tech_id") or "").strip() or default_tech_label(uid)
    return UserProfile(
        telegram_user_id=uid,
        tech_id=tech,
        chat_id=int(row["chat_id"]),
        display_name=str(row.get("display_name") or ""),
        work_area=row.get("work_area"),
        is_active=bool(row.get("is_active", True)),
    )


def get_user(telegram_user_id: int) -> UserProfile | None:
    if _db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_user_id, tech_id, chat_id, display_name, work_area, is_active
                FROM bot_users
                WHERE telegram_user_id = %s AND is_active = TRUE
                """,
                (telegram_user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return UserProfile(
            telegram_user_id=int(row[0]),
            tech_id=str(row[1] or "") or default_tech_label(int(row[0])),
            chat_id=int(row[2]),
            display_name=str(row[3] or ""),
            work_area=row[4],
            is_active=bool(row[5]),
        )

    for item in _load_json_users():
        if int(item["telegram_user_id"]) == telegram_user_id and item.get("is_active", True):
            return _row_to_profile(item)
    return None


def list_active_users() -> list[UserProfile]:
    if _db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_user_id, tech_id, chat_id, display_name, work_area, is_active
                FROM bot_users
                WHERE is_active = TRUE
                ORDER BY created_at, telegram_user_id
                """
            )
            rows = cur.fetchall()
        return [
            UserProfile(
                telegram_user_id=int(r[0]),
                tech_id=str(r[1] or "") or default_tech_label(int(r[0])),
                chat_id=int(r[2]),
                display_name=str(r[3] or ""),
                work_area=r[4],
                is_active=bool(r[5]),
            )
            for r in rows
        ]

    return [_row_to_profile(item) for item in _load_json_users() if item.get("is_active", True)]


def _save_profile(profile: UserProfile) -> UserProfile:
    if _db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bot_users (telegram_user_id, tech_id, chat_id, display_name, work_area, is_active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                    tech_id = EXCLUDED.tech_id,
                    chat_id = EXCLUDED.chat_id,
                    display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), bot_users.display_name),
                    work_area = COALESCE(EXCLUDED.work_area, bot_users.work_area),
                    is_active = TRUE,
                    updated_at = NOW()
                """,
                (
                    profile.telegram_user_id,
                    profile.tech_id,
                    profile.chat_id,
                    profile.display_name,
                    profile.work_area,
                ),
            )
            conn.commit()
        return profile

    users = _load_json_users()
    kept = [u for u in users if int(u["telegram_user_id"]) != profile.telegram_user_id]
    kept.append(
        {
            "telegram_user_id": profile.telegram_user_id,
            "tech_id": profile.tech_id,
            "chat_id": profile.chat_id,
            "display_name": profile.display_name,
            "work_area": profile.work_area,
            "is_active": True,
        }
    )
    _save_json_users(kept)
    return profile


def get_or_create_user(
    *,
    telegram_user_id: int,
    chat_id: int,
    display_name: str = "",
    tech_label: str | None = None,
) -> UserProfile:
    existing = get_user(telegram_user_id)
    if existing:
        if display_name and display_name != existing.display_name:
            existing.display_name = display_name
        if chat_id != existing.chat_id:
            existing.chat_id = chat_id
        return _save_profile(existing)

    label = normalize_tech_label(tech_label) if tech_label else default_tech_label(telegram_user_id)
    profile = UserProfile(
        telegram_user_id=telegram_user_id,
        tech_id=label,
        chat_id=chat_id,
        display_name=display_name.strip(),
    )
    return _save_profile(profile)


def set_tech_label(telegram_user_id: int, tech_label: str) -> UserProfile:
    label = normalize_tech_label(tech_label)
    if not validate_tech_label(label):
        raise ValueError("Подпись 2–12 символов: буквы, цифры, точка, дефис (пример: I0KF)")

    profile = get_user(telegram_user_id)
    if not profile:
        raise ValueError("Пользователь не найден")
    profile.tech_id = label
    return _save_profile(profile)


def touch_user(
    telegram_user_id: int,
    chat_id: int,
    *,
    display_name: str | None = None,
) -> UserProfile:
    return get_or_create_user(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        display_name=display_name or "",
    )


def _legacy_owner_telegram_id() -> int | None:
    if TELEGRAM_ALLOWED_USER_IDS:
        return next(iter(TELEGRAM_ALLOWED_USER_IDS))
    users = list_active_users()
    if len(users) == 1:
        return users[0].telegram_user_id
    return None


def ensure_legacy_migration() -> None:
    """Create legacy user profile and attach pre-multi-user data to their Telegram ID."""
    telegram_id = _legacy_owner_telegram_id()
    if telegram_id is None:
        return

    import os

    chat_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    chat_id = int(chat_raw) if chat_raw.isdigit() else telegram_id
    label = TECH_ID or default_tech_label(telegram_id)
    get_or_create_user(
        telegram_user_id=telegram_id,
        chat_id=chat_id,
        display_name="Legacy user",
        tech_label=label,
    )

    from bot.settings_store import migrate_legacy_settings_keys
    from bot.storage import migrate_orphan_job_lines

    migrate_orphan_job_lines(telegram_id)
    migrate_legacy_settings_keys(TECH_ID, user_settings_key(telegram_id))

