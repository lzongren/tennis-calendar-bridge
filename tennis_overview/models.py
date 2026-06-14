from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ClubConfig:
    id: str
    name: str
    provider: str
    base_url: str
    username_env: str
    password_env: str
    login_url: str | None = None
    enabled: bool = True
    timezone: str = "America/Los_Angeles"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    timezone: str = "America/Los_Angeles"
    database_path: str = "data/tennis.db"
    sync_interval_minutes: int = 60
    lookahead_days: int = 90
    calendar_name: str = "Tennis Calendar"
    public_base_url: str | None = None
    admin_token: str | None = None
    calendar_token: str | None = None
    headless: bool = True


@dataclass(frozen=True)
class Config:
    app: AppConfig
    clubs: list[ClubConfig]
    source_path: str


@dataclass(frozen=True)
class TennisEvent:
    club_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    external_id: str | None = None
    location: str | None = None
    category: str | None = None
    source_url: str | None = None
    status: str = "confirmed"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncRun:
    id: int
    club_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    events_seen: int
    error: str | None
    artifact_path: str | None
