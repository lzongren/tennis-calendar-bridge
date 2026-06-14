from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path
from typing import Any

from .models import AppConfig, ClubConfig, Config


DEFAULT_CONFIG_PATH = Path("config/clubs.toml")
EXAMPLE_CONFIG_PATH = Path("config/clubs.example.toml")


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def resolve_config_path(path: str | None = None) -> Path:
    if path:
        return Path(path)
    if os.getenv("TENNIS_CONFIG"):
        return Path(os.environ["TENNIS_CONFIG"])
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return EXAMPLE_CONFIG_PATH


def load_config(path: str | None = None) -> Config:
    config_path = resolve_config_path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. Copy config/clubs.example.toml first."
        )

    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    app_raw: dict[str, Any] = raw.get("app", {})
    data_dir = os.getenv("TENNIS_DATA_DIR")
    configured_database_path = app_raw.get("database_path", "data/tennis.db")
    database_path = os.getenv("TENNIS_DATABASE_PATH")
    if not database_path and data_dir:
        database_path = str(Path(data_dir) / "tennis.db")
    if not database_path:
        database_path = configured_database_path

    app = AppConfig(
        timezone=app_raw.get("timezone", "America/Los_Angeles"),
        database_path=database_path,
        sync_interval_minutes=_int_from_env(
            "TENNIS_SYNC_INTERVAL_MINUTES",
            int(app_raw.get("sync_interval_minutes", 60)),
        ),
        lookahead_days=_int_from_env(
            "TENNIS_LOOKAHEAD_DAYS", int(app_raw.get("lookahead_days", 90))
        ),
        calendar_name=app_raw.get("calendar_name", "Tennis Calendar"),
        public_base_url=os.getenv("TENNIS_PUBLIC_BASE_URL")
        or app_raw.get("public_base_url"),
        admin_token=os.getenv("TENNIS_ADMIN_TOKEN"),
        calendar_token=os.getenv("TENNIS_CALENDAR_TOKEN"),
        headless=_bool_from_env("TENNIS_HEADLESS", True),
    )

    club_rows = raw.get("clubs", [])
    if isinstance(club_rows, dict):
        club_rows = []

    clubs: list[ClubConfig] = []
    for item in club_rows:
        club_id = item["id"]
        item_options = item.get("options", {})
        if isinstance(item_options, dict) and club_id in item_options:
            item_options = item_options[club_id]
        if not isinstance(item_options, dict):
            item_options = {}
        clubs.append(
            ClubConfig(
                id=club_id,
                name=item["name"],
                provider=item["provider"],
                base_url=item["base_url"],
                login_url=item.get("login_url"),
                username_env=item["username_env"],
                password_env=item["password_env"],
                enabled=bool(item.get("enabled", True)),
                timezone=item.get("timezone", app.timezone),
                options=item_options,
            )
        )

    return Config(app=app, clubs=clubs, source_path=str(config_path))


def init_config(destination: str | None = None) -> Path:
    target = Path(destination) if destination else DEFAULT_CONFIG_PATH
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_CONFIG_PATH, target)
    return target


def append_club(
    *,
    config_path: str | None,
    club_id: str,
    name: str,
    provider: str,
    base_url: str,
    login_url: str | None,
    username_env: str,
    password_env: str,
    timezone: str,
) -> Path:
    target = resolve_config_path(config_path)
    if not target.exists():
        init_config(str(target))

    login_line = f'login_url = "{login_url}"\n' if login_url else ""
    block = f"""

[[clubs]]
id = "{club_id}"
name = "{name}"
provider = "{provider}"
base_url = "{base_url}"
{login_line}username_env = "{username_env}"
password_env = "{password_env}"
enabled = true
timezone = "{timezone}"
"""
    with target.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return target
