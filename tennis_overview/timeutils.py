from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def ensure_aware(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(timezone_name))
    return value


def to_utc(value: datetime, timezone_name: str = "UTC") -> datetime:
    return ensure_aware(value, timezone_name).astimezone(timezone.utc)


def from_db(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def to_db(value: datetime, timezone_name: str = "UTC") -> str:
    return to_utc(value, timezone_name).isoformat()
