"""Telegram user profiles linked to ATN tech IDs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.config import (
    DEFAULT_WORK_AREA,
    TECH_BINDINGS,
    TECH_ID,
    TELEGRAM_ADMIN_USER_IDS,
    TELEGRAM_ALLOWED_USER_IDS,
)

ROOT = Path(__file__).resolve().parent.parent
USERS_JSON = ROOT / "data" / "bot_users.json"

TECH_ID_RE = re.compile(r"^[A-Z0-9]{2,10}$", re.IGNORECASE)


class UserNotLinkedError(Exception):
    """Raised when a Telegram user has no linked tech profile."""


class TechIdTakenError(Exception):
    """Raised when tech_id is already linked to another Telegram account."""


class LinkNotAllowedError(Exception):
    """Raised when user is not allowed to link this tech_id."""


def is_admin(telegram_user_id: int) -> bool:
    if telegram_user_id in TELEGRAM_ADMIN_USER_IDS:
        return True
    if TELEGRAM_ADMIN_USER_IDS:
        return False
    if len(TELEGRAM_ALLOWED_USER_IDS) == 1:
        return telegram_user_id in TELEGRAM_ALLOWED_USER_IDS
    if TELEGRAM_ALLOWED_USER_IDS:
        return telegram_user_id == min(TELEGRAM_ALLOWED_USER_IDS)
    return False


def can_self_link(telegram_user_id: int, tech_id: str) -> bool:
    tech = normalize_tech_id(tech_id)
    allowed_uid = TECH_BINDINGS.get(tech)
    return allowed_uid is not None and allowed_uid == telegram_user_id


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


def normalize_tech_id(value: str) -> str:
    return value.strip().upper()


def validate_tech_id(value: str) -> bool:
    return bool(TECH_ID_RE.match(normalize_tech_id(value)))


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
        tech_id TEXT NOT NULL UNIQUE,
        chat_id BIGINT NOT NULL,
        display_name TEXT DEFAULT '',
        work_area TEXT,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_bot_users_tech ON bot_users(tech_id);
    CREATE INDEX IF NOT EXISTS idx_job_lines_tech ON job_lines(tech);
    """
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with _connect() as conn, conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
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
    return UserProfile(
        telegram_user_id=int(row["telegram_user_id"]),
        tech_id=str(row["tech_id"]),
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
            tech_id=str(row[1]),
            chat_id=int(row[2]),
            display_name=str(row[3] or ""),
            work_area=row[4],
            is_active=bool(row[5]),
        )

    for item in _load_json_users():
        if int(item["telegram_user_id"]) == telegram_user_id and item.get("is_active", True):
            return _row_to_profile(item)
    return None


def get_user_by_tech(tech_id: str) -> UserProfile | None:
    tech = normalize_tech_id(tech_id)
    if _db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_user_id, tech_id, chat_id, display_name, work_area, is_active
                FROM bot_users
                WHERE tech_id = %s AND is_active = TRUE
                """,
                (tech,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return UserProfile(
            telegram_user_id=int(row[0]),
            tech_id=str(row[1]),
            chat_id=int(row[2]),
            display_name=str(row[3] or ""),
            work_area=row[4],
            is_active=bool(row[5]),
        )

    for item in _load_json_users():
        if normalize_tech_id(str(item.get("tech_id", ""))) == tech and item.get("is_active", True):
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
                ORDER BY created_at, tech_id
                """
            )
            rows = cur.fetchall()
        return [
            UserProfile(
                telegram_user_id=int(r[0]),
                tech_id=str(r[1]),
                chat_id=int(r[2]),
                display_name=str(r[3] or ""),
                work_area=r[4],
                is_active=bool(r[5]),
            )
            for r in rows
        ]

    return [_row_to_profile(item) for item in _load_json_users() if item.get("is_active", True)]


def link_user(
    *,
    telegram_user_id: int,
    tech_id: str,
    chat_id: int,
    display_name: str = "",
    work_area: str | None = None,
    by_admin: bool = False,
) -> UserProfile:
    tech = normalize_tech_id(tech_id)
    if not validate_tech_id(tech):
        raise ValueError("Неверный формат Tech ID (пример: I0KF)")

    existing_user = get_user(telegram_user_id)
    if existing_user and existing_user.tech_id != tech and not by_admin:
        raise LinkNotAllowedError("Сменить Tech ID может только админ.")

    if not by_admin and not can_self_link(telegram_user_id, tech):
        raise LinkNotAllowedError(
            "Этот Tech ID нельзя привязать самостоятельно. Попроси админа привязать аккаунт."
        )

    existing_tech = get_user_by_tech(tech)
    if existing_tech and existing_tech.telegram_user_id != telegram_user_id:
        raise TechIdTakenError(f"Tech ID {tech} уже привязан к другому аккаунту")

    profile = UserProfile(
        telegram_user_id=telegram_user_id,
        tech_id=tech,
        chat_id=chat_id,
        display_name=display_name.strip(),
        work_area=work_area,
        is_active=True,
    )

    if _db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bot_users (telegram_user_id, tech_id, chat_id, display_name, work_area, is_active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                    tech_id = EXCLUDED.tech_id,
                    chat_id = EXCLUDED.chat_id,
                    display_name = EXCLUDED.display_name,
                    work_area = COALESCE(EXCLUDED.work_area, bot_users.work_area),
                    is_active = TRUE,
                    updated_at = NOW()
                """,
                (telegram_user_id, tech, chat_id, profile.display_name, work_area),
            )
            conn.commit()
        return profile

    users = _load_json_users()
    kept = [u for u in users if int(u["telegram_user_id"]) != telegram_user_id]
    kept = [u for u in kept if normalize_tech_id(str(u.get("tech_id", ""))) != tech]
    kept.append(
        {
            "telegram_user_id": telegram_user_id,
            "tech_id": tech,
            "chat_id": chat_id,
            "display_name": profile.display_name,
            "work_area": work_area,
            "is_active": True,
        }
    )
    _save_json_users(kept)
    return profile


def touch_chat(telegram_user_id: int, chat_id: int, *, display_name: str | None = None) -> None:
    if _db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            if display_name:
                cur.execute(
                    """
                    UPDATE bot_users
                    SET chat_id = %s, display_name = %s, updated_at = NOW()
                    WHERE telegram_user_id = %s
                    """,
                    (chat_id, display_name.strip(), telegram_user_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE bot_users
                    SET chat_id = %s, updated_at = NOW()
                    WHERE telegram_user_id = %s
                    """,
                    (chat_id, telegram_user_id),
                )
            conn.commit()
        return

    users = _load_json_users()
    changed = False
    for item in users:
        if int(item["telegram_user_id"]) == telegram_user_id:
            item["chat_id"] = chat_id
            if display_name:
                item["display_name"] = display_name.strip()
            changed = True
            break
    if changed:
        _save_json_users(users)


def unlink_user(telegram_user_id: int) -> bool:
    if _db_enabled():
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE bot_users SET is_active = FALSE, updated_at = NOW() WHERE telegram_user_id = %s",
                (telegram_user_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    users = _load_json_users()
    changed = False
    for item in users:
        if int(item["telegram_user_id"]) == telegram_user_id:
            item["is_active"] = False
            changed = True
    if changed:
        _save_json_users(users)
    return changed


def ensure_legacy_migration() -> None:
    """Auto-link the first allowed Telegram user to TECH_ID from env when no profiles exist."""
    if list_active_users():
        return
    if not TECH_ID:
        return

    import os

    telegram_id: int | None = None
    if TELEGRAM_ALLOWED_USER_IDS:
        telegram_id = next(iter(TELEGRAM_ALLOWED_USER_IDS))
    chat_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    chat_id = int(chat_raw) if chat_raw.isdigit() else 0
    if telegram_id is None:
        return

    link_user(
        telegram_user_id=telegram_id,
        tech_id=TECH_ID,
        chat_id=chat_id or telegram_id,
        display_name="Legacy user",
        by_admin=True,
    )
