"""Miami / Florida Eastern time helpers (America/New_York)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")


def miami_now() -> datetime:
    return datetime.now(TZ)


def format_atn_datetime(dt: datetime) -> str:
    """Format like ATN invoice: 2026-08-17 9:44:41"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    else:
        dt = dt.astimezone(TZ)
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour}:{dt.minute:02d}:{dt.second:02d}"


def parse_completion_datetime(value: date | datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(TZ) if value.tzinfo else value.replace(tzinfo=TZ)
    if isinstance(value, str):
        text = value.strip()
        if " " in text:
            date_part, time_part = text.split(" ", 1)
            y, m, d = map(int, date_part.split("-"))
            parts = time_part.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            second = int(parts[2]) if len(parts) > 2 else 0
            return datetime(y, m, d, hour, minute, second, tzinfo=TZ)
        return datetime.fromisoformat(text[:10]).replace(tzinfo=TZ)
    return datetime.combine(value, miami_now().time(), tzinfo=TZ)
