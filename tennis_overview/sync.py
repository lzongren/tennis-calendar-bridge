from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import load_config
from .models import ClubConfig, Config
from .scrapers import ScraperError, make_scraper


@dataclass(frozen=True)
class SyncResult:
    club_id: str
    status: str
    events_seen: int
    error: str | None = None
    artifact_path: str | None = None


def sync_all(config: Config | None = None, only_club_id: str | None = None) -> list[SyncResult]:
    config = config or load_config()
    conn = db.connect(config.app.database_path)
    try:
        clubs = [club for club in config.clubs if club.enabled]
        if only_club_id:
            clubs = [club for club in clubs if club.id == only_club_id]
            if not clubs:
                raise ValueError(f"Club not found or disabled: {only_club_id}")
        return [_sync_club(conn, config, club) for club in clubs]
    finally:
        conn.close()


def _sync_club(conn, config: Config, club: ClubConfig) -> SyncResult:
    run_id = db.start_sync_run(conn, club.id)
    data_dir = Path(config.app.database_path).parent
    scraper = make_scraper(club, data_dir, headless=config.app.headless)
    try:
        events = scraper.fetch()
        seen = db.upsert_events(conn, events)
        db.mark_missing_cancelled(conn, club.id, seen, config.app.lookahead_days)
        db.finish_sync_run(conn, run_id, "success", len(events))
        return SyncResult(club_id=club.id, status="success", events_seen=len(events))
    except ScraperError as exc:
        db.finish_sync_run(conn, run_id, "error", 0, str(exc), exc.artifact_path)
        return SyncResult(
            club_id=club.id,
            status="error",
            events_seen=0,
            error=str(exc),
            artifact_path=exc.artifact_path,
        )
    except Exception as exc:
        db.finish_sync_run(conn, run_id, "error", 0, f"{type(exc).__name__}: {exc}")
        return SyncResult(
            club_id=club.id,
            status="error",
            events_seen=0,
            error=f"{type(exc).__name__}: {exc}",
        )
