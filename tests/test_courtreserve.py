from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tennis_overview import db, sync
from tennis_overview.models import AppConfig, ClubConfig, Config, TennisEvent
from tennis_overview.scrapers import ScraperError
from tennis_overview.scrapers.courtreserve import CourtReserveScraper


@pytest.fixture
def scraper(tmp_path):
    return CourtReserveScraper(
        ClubConfig(
            id="example-courtreserve",
            name="Example CourtReserve Club",
            provider="courtreserve",
            base_url="https://app.courtreserve.com/Online/Portal/Index/0000",
            login_url="https://app.courtreserve.com/Online/Account/LogIn/0000",
            username_env="EXAMPLE_USERNAME",
            password_env="EXAMPLE_PASSWORD",
        ),
        tmp_path,
    )


@pytest.mark.parametrize(
    "title,status,headers",
    [
        ("Attention Required! | Cloudflare", 403, {}),
        ("Access Denied | Cloudflare", 403, {}),
        ("Just a moment...", 403, {"cf-mitigated": "challenge"}),
        ("Just a moment...", 200, {"cf-mitigated": "challenge"}),
    ],
)
def test_cloudflare_is_not_reported_as_a_missing_input(scraper, title, status, headers):
    page = MagicMock()
    page.title.return_value = title
    response = SimpleNamespace(status=status, headers=headers)

    with pytest.raises(ScraperError, match="Cloudflare access check") as error:
        scraper._check_login_page(page, response)

    assert f"HTTP {status}" in str(error.value)
    assert "credentials were not submitted" in str(error.value)
    page.wait_for_selector.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_other_http_errors_are_not_misclassified_as_cloudflare(scraper, status):
    page = MagicMock()
    page.title.return_value = "Unavailable"
    response = SimpleNamespace(status=status, headers={"server": "cloudflare"})

    with pytest.raises(ScraperError, match=f"HTTP {status}") as error:
        scraper._check_login_page(page, response)

    assert "Cloudflare access check" not in str(error.value)


@pytest.mark.parametrize("has_response", [True, False])
def test_normal_login_page_can_continue(scraper, has_response):
    page = MagicMock()
    page.title.return_value = "Login | powered by CourtReserve"
    response = SimpleNamespace(status=200, headers={"server": "cloudflare"})

    scraper._check_login_page(page, response if has_response else None)


def test_blocked_fetch_saves_diagnostics_and_preserves_bookings(scraper, tmp_path, monkeypatch):
    import playwright.sync_api as playwright_api

    monkeypatch.setenv("EXAMPLE_USERNAME", "member@example.invalid")
    monkeypatch.setenv("EXAMPLE_PASSWORD", "synthetic-password")
    page = MagicMock()
    page.goto.return_value = SimpleNamespace(status=403, headers={})
    page.title.return_value = "Attention Required! | Cloudflare"
    page.content.return_value = "<title>Attention Required! | Cloudflare</title>"
    browser = MagicMock()
    context = browser.new_context.return_value
    context.new_page.return_value = page
    playwright = MagicMock()
    playwright.chromium.launch.return_value = browser
    monkeypatch.setattr(
        playwright_api,
        "sync_playwright",
        lambda: nullcontext(playwright),
    )
    login = MagicMock()
    monkeypatch.setattr(scraper, "_login", login)
    monkeypatch.setattr(sync, "make_scraper", lambda *args, **kwargs: scraper)

    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(db, "utc_now", lambda: now)
    config = Config(
        app=AppConfig(database_path=str(tmp_path / "test.db")),
        clubs=[scraper.club],
        source_path="test",
    )
    event = TennisEvent(
        club_id=scraper.club.id,
        external_id="existing-booking",
        title="Group Lesson",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1),
        timezone="UTC",
    )
    conn = db.connect(config.app.database_path)
    try:
        db.upsert_events(conn, [event])
        result = sync.sync_all(config)[0]
        saved = db.list_events(conn, now, now + timedelta(days=30))
        run = db.list_recent_sync_runs(conn)[0]
    finally:
        conn.close()

    assert result.status == run.status == "error"
    assert "Cloudflare access check (HTTP 403)" in result.error
    assert result.artifact_path == run.artifact_path
    assert list(tmp_path.glob("debug/*/*/error.html"))
    assert [(e.external_id, e.status) for e in saved] == [("existing-booking", "confirmed")]
    assert "synthetic-password" not in result.error
    login.assert_not_called()
    page.wait_for_selector.assert_not_called()
    page.goto.assert_called_once_with(
        scraper.club.login_url, wait_until="domcontentloaded", timeout=45_000
    )
    context.close.assert_called_once()
    browser.close.assert_called_once()
