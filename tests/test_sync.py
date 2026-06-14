from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tennis_overview import db, sync
from tennis_overview.models import AppConfig, ClubConfig, Config, TennisEvent
from tennis_overview.scrapers import ScraperError


def _config(database_path: str) -> Config:
    return Config(
        app=AppConfig(
            database_path=database_path,
            lookahead_days=30,
            headless=True,
        ),
        clubs=[
            ClubConfig(
                id="example-accusportview",
                name="Example AccuSportView Club",
                provider="accusportview",
                base_url="https://example.my.accusportview.com/",
                username_env="EXAMPLE_ACCUSPORTVIEW_USERNAME",
                password_env="EXAMPLE_ACCUSPORTVIEW_PASSWORD",
            )
        ],
        source_path="test",
    )


def test_sync_all_records_success(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(db, "utc_now", lambda: now)
    zone = ZoneInfo("America/Los_Angeles")
    event = TennisEvent(
        club_id="example-accusportview",
        external_id="booking-1",
        title="Rent/Private",
        starts_at=datetime(2026, 6, 17, 19, 0, tzinfo=zone),
        ends_at=datetime(2026, 6, 17, 21, 0, tzinfo=zone),
        timezone="America/Los_Angeles",
        location="Tennis/Pickle 6",
    )

    class FakeScraper:
        def fetch(self) -> list[TennisEvent]:
            return [event]

    monkeypatch.setattr(sync, "make_scraper", lambda *args, **kwargs: FakeScraper())

    results = sync.sync_all(_config(str(tmp_path / "tennis.db")))

    assert results == [
        sync.SyncResult(club_id="example-accusportview", status="success", events_seen=1)
    ]
    conn = db.connect(str(tmp_path / "tennis.db"))
    try:
        events = db.list_events(conn, now - timedelta(days=1), now + timedelta(days=30))
        runs = db.list_recent_sync_runs(conn)
    finally:
        conn.close()
    assert [stored.external_id for stored in events] == ["booking-1"]
    assert runs[0].status == "success"
    assert runs[0].events_seen == 1


def test_sync_all_records_scraper_error(tmp_path, monkeypatch) -> None:
    class FailingScraper:
        def fetch(self) -> list[TennisEvent]:
            raise ScraperError("no events found", "/data/debug/no-events.png")

    monkeypatch.setattr(sync, "make_scraper", lambda *args, **kwargs: FailingScraper())

    results = sync.sync_all(_config(str(tmp_path / "tennis.db")))

    assert results[0].status == "error"
    assert results[0].error == "no events found"
    assert results[0].artifact_path == "/data/debug/no-events.png"
    conn = db.connect(str(tmp_path / "tennis.db"))
    try:
        runs = db.list_recent_sync_runs(conn)
    finally:
        conn.close()
    assert runs[0].status == "error"
    assert runs[0].error == "no events found"


def test_sync_all_rejects_unknown_or_disabled_club(tmp_path) -> None:
    with pytest.raises(ValueError, match="Club not found or disabled"):
        sync.sync_all(_config(str(tmp_path / "tennis.db")), only_club_id="missing-club")
