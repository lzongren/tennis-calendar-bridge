from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError
from urllib.request import Request
from zoneinfo import ZoneInfo

import pytest

from tennis_overview import db, sync
from tennis_overview.server import _agenda_item, _calendar_event, _event_row
from tennis_overview.calendar import make_ics
from tennis_overview.models import AppConfig, ClubConfig, Config
from tennis_overview.scrapers import ScraperError
from tennis_overview.scrapers import calendar_feed as feed
from tennis_overview.scrapers.browser import BrowserPortalScraper
from tennis_overview.scrapers.courtreserve import CourtReserveScraper


ZONE = ZoneInfo("America/Los_Angeles")
LOWER = datetime(2026, 9, 6, tzinfo=ZONE)
UPPER = datetime(2026, 10, 1, tzinfo=ZONE)
SECRET_URL = "https://example.invalid/calendar/private-test-token?subscription=test"
FIXTURE = Path(__file__).parent / "fixtures" / "courtreserve" / "calendar.ics"


@pytest.fixture
def club():
    return ClubConfig(
        id="example-feed",
        name="Example Feed Club",
        provider="courtreserve",
        base_url="https://example.invalid/club",
        username_env="",
        password_env="",
        options={"calendar_feed_env": "EXAMPLE_FEED_URL"},
    )


def calendar(*events):
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\n"
        + "\n".join("BEGIN:VEVENT\n" + event + "\nEND:VEVENT" for event in events)
        + "\nEND:VCALENDAR\n"
    ).encode()


def booking(extra="", uid="booking"):
    return (
        f"UID:{uid}\nSUMMARY:Private Lesson\n"
        "DTSTART:20260907T190000\nDTEND:20260907T200000\n" + extra
    )


def test_fixture_dates_recurrence_exclusions_moves_and_cancellations(club):
    events = feed.parse_feed(FIXTURE.read_bytes(), club, LOWER, UPPER)
    assert [
        (e.starts_at.strftime("%m-%d %H:%M"), e.ends_at.strftime("%H:%M"), e.status) for e in events
    ] == [
        ("09-07 19:00", "20:00", "confirmed"),
        ("09-08 19:30", "21:00", "confirmed"),
        ("09-23 18:00", "19:30", "confirmed"),
        ("09-29 19:30", "21:00", "cancelled"),
    ]
    assert events[0].location == "Court 2, Indoor"
    assert events[2].location == "Court 3"
    assert len({e.external_id for e in events}) == len(events)
    assert all(e.raw == {} and e.source_url == club.base_url for e in events)
    assert "secret-feed-token" not in repr(events)
    assert "secret-feed-token" not in make_ics(events, "Test Calendar")


def test_single_event_id_survives_time_and_title_changes(club):
    original = calendar(booking())
    moved = (
        original.replace(b"190000", b"170000")
        .replace(b"200000", b"180000")
        .replace(b"Private Lesson", b"Updated Lesson")
    )
    assert (
        feed.parse_feed(original, club, LOWER, UPPER)[0].external_id
        == feed.parse_feed(moved, club, LOWER, UPPER)[0].external_id
    )


def test_moved_occurrence_keeps_original_recurrence_identity(club):
    original = FIXTURE.read_bytes()
    moved = original.replace(b"20260923T180000", b"20260924T170000").replace(
        b"20260923T193000", b"20260924T183000"
    )
    before = feed.parse_feed(original, club, LOWER, UPPER)[2]
    after = feed.parse_feed(moved, club, LOWER, UPPER)[2]
    assert before.external_id == after.external_id
    assert before.starts_at != after.starts_at


def test_dst_changes_preserve_recurring_local_start_time(club):
    body = calendar(
        "UID:dst\nSUMMARY:Weekly Lesson\nDTSTART;TZID=America/Los_Angeles:20261025T190000\nDTEND;TZID=America/Los_Angeles:20261025T200000\nRRULE:FREQ=WEEKLY;COUNT=3"
    )
    events = feed.parse_feed(
        body,
        club,
        datetime(2026, 10, 24, tzinfo=ZONE),
        datetime(2026, 11, 10, tzinfo=ZONE),
    )
    assert [e.starts_at.hour for e in events] == [19, 19, 19]
    assert [e.starts_at.utcoffset() for e in events] == [
        timedelta(hours=-7),
        timedelta(hours=-8),
        timedelta(hours=-8),
    ]


def test_calendar_timezone_applies_to_floating_times(club):
    body = calendar(booking()).replace(
        b"VERSION:2.0", b"VERSION:2.0\nX-WR-TIMEZONE:America/New_York"
    )
    assert feed.parse_feed(body, club, LOWER, UPPER)[0].starts_at.hour == 16


def test_duration_all_day_and_rdate_are_supported(club):
    body = calendar(
        "UID:duration\nDTSTART:20260907T180000\nDURATION:PT90M\nRDATE:20260909T180000",
        "UID:all-day\nDTSTART;VALUE=DATE:20260908",
    )
    events = feed.parse_feed(body, club, LOWER, UPPER)
    assert [e.ends_at - e.starts_at for e in events] == [
        timedelta(minutes=90),
        timedelta(days=1),
        timedelta(minutes=90),
    ]
    assert events[1].starts_at == datetime(2026, 9, 8, tzinfo=ZONE)
    assert events[1].raw == {"all_day": True}
    exported = make_ics([events[1]], "Test")
    assert "DTSTART;VALUE=DATE:20260908" in exported
    assert "DTEND;VALUE=DATE:20260909" in exported
    assert all(
        "All day" in rendered
        for rendered in (
            _agenda_item(events[1]),
            _calendar_event(events[1], ZONE),
            _event_row(events[1]),
        )
    )


def test_empty_calendar_is_a_successful_snapshot(club):
    assert feed.parse_feed(calendar(), club, LOWER, UPPER) == []


def test_expansion_is_bounded_to_requested_window(club):
    body = calendar(booking("RRULE:FREQ=DAILY"))
    assert len(feed.parse_feed(body, club, LOWER, LOWER + timedelta(days=3))) == 2


def test_excessive_event_count_fails_instead_of_truncating(club, monkeypatch):
    monkeypatch.setattr(feed, "MAX_EVENTS", 1)
    with pytest.raises(ScraperError):
        feed.parse_feed(calendar(booking("RRULE:FREQ=DAILY;COUNT=3")), club, LOWER, UPPER)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>Sign in</html>",
        calendar(booking())[:-20],
        calendar(booking()).replace(b"VERSION:2.0", b"VERSION:2.0\nMETHOD:CANCEL"),
        calendar(booking(), "UID:broken\nDTSTART:not-a-date"),
        calendar(booking().replace("DTEND:20260907T200000", "DTEND:20260907T180000")),
        calendar(booking().replace("DTEND:20260907T200000", "")),
        calendar(booking().replace("UID:booking", "")),
        calendar(booking().replace("DTSTART:", "DTSTART;TZID=Unknown/Zone:")),
        calendar(booking("RRULE:FREQ=BOGUS")),
        calendar(booking("RRULE:FREQ=SECONDLY")),
        calendar(booking("STATUS:BOGUS")),
        calendar(booking("DURATION:PT1H")),
        calendar(booking("DESCRIPTION:private-test-token\nRRULE:FREQ=invalid")),
    ],
)
def test_bad_or_partial_snapshot_fails_without_exposing_content(club, body):
    with pytest.raises(ScraperError, match="invalid, incomplete") as error:
        feed.parse_feed(body, club, LOWER, UPPER)
    assert "private-test-token" not in str(error.value)
    assert error.value.__suppress_context__


def test_configured_feed_does_not_use_browser_or_credentials(club, tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_FEED_URL", SECRET_URL)
    monkeypatch.setattr(feed, "utc_now", lambda: LOWER)
    download = MagicMock(return_value=calendar(booking()))
    browser = MagicMock(side_effect=AssertionError("must not launch browser"))
    monkeypatch.setattr(feed, "download_feed", download)
    monkeypatch.setattr(BrowserPortalScraper, "fetch", browser)
    events = CourtReserveScraper(club, tmp_path).fetch()
    assert len(events) == 1
    download.assert_called_once_with(SECRET_URL)
    browser.assert_not_called()


@pytest.mark.parametrize(
    "env_name,value",
    [
        ("EXAMPLE_FEED_URL", ""),
        ("", ""),
        ("bad\nname", ""),
        ("EXAMPLE_FEED_URL", SECRET_URL),
    ],
)
def test_missing_or_failed_feed_never_falls_back_to_browser(
    club, tmp_path, monkeypatch, env_name, value
):
    monkeypatch.setenv("EXAMPLE_FEED_URL", value)
    monkeypatch.setattr(feed, "download_feed", MagicMock(side_effect=ScraperError("HTTP 403")))
    browser = MagicMock(side_effect=AssertionError("must not launch browser"))
    monkeypatch.setattr(BrowserPortalScraper, "fetch", browser)
    club = replace(club, options={"calendar_feed_env": env_name})
    with pytest.raises(ScraperError):
        CourtReserveScraper(club, tmp_path).fetch()
    browser.assert_not_called()


def test_existing_browser_mode_unchanged(club, tmp_path, monkeypatch):
    browser = MagicMock(return_value=[])
    monkeypatch.setattr(BrowserPortalScraper, "fetch", browser)
    assert CourtReserveScraper(replace(club, options={}), tmp_path).fetch() == []
    browser.assert_called_once()


def fake_download(monkeypatch, body, status=200, headers=None):
    response = MagicMock()
    response.status = status
    response.headers = headers or {}
    response.read.return_value = body
    response.__enter__.return_value = response
    opener = MagicMock()
    opener.open.return_value = response
    monkeypatch.setattr(feed, "build_opener", lambda *args: opener)
    return opener, response


def test_download_uses_https_and_identifies_the_application(monkeypatch):
    opener, response = fake_download(monkeypatch, calendar())
    assert feed.download_feed(SECRET_URL.replace("https:", "webcal:")) == calendar()
    request = opener.open.call_args.args[0]
    assert request.full_url == SECRET_URL
    assert request.get_header("User-agent") == "TennisCalendarBridge/0.1"
    assert request.get_header("Accept") == "text/calendar"
    assert opener.open.call_args.kwargs["timeout"] == 30
    response.read.assert_called_once_with(feed.MAX_FEED_BYTES + 1)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/private-test-token",
        "file:///etc/passwd",
        "https://user:private-test-token@example.invalid/",
        "https://example.invalid:bad/",
        SECRET_URL + "\nInjected: data",
        SECRET_URL + "#fragment",
    ],
)
def test_invalid_feed_url_never_reaches_network(monkeypatch, url):
    opener, _ = fake_download(monkeypatch, b"")
    with pytest.raises(ScraperError) as error:
        feed.download_feed(url)
    opener.open.assert_not_called()
    assert "private-test-token" not in str(error.value)


@pytest.mark.parametrize(
    "failure",
    [
        HTTPError(SECRET_URL, 403, "Forbidden", {}, None),
        HTTPError(SECRET_URL, 500, "Internal error", {}, None),
        URLError(SECRET_URL),
        TimeoutError(SECRET_URL),
    ],
)
def test_download_errors_are_redacted(monkeypatch, failure):
    opener, _ = fake_download(monkeypatch, b"")
    opener.open.side_effect = failure
    with pytest.raises(ScraperError) as error:
        feed.download_feed(SECRET_URL)
    assert "private-test-token" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "status,body,headers",
    [
        (204, b"", {}),
        (206, b"partial", {}),
        (200, b"a" * 11, {}),
        (200, b"small", {"Content-Length": "20"}),
        (200, b"small", {"Content-Length": "9"}),
    ],
)
def test_noncomplete_or_oversized_downloads_fail(monkeypatch, status, body, headers):
    monkeypatch.setattr(feed, "MAX_FEED_BYTES", 10)
    fake_download(monkeypatch, body, status, headers)
    with pytest.raises(ScraperError):
        feed.download_feed(SECRET_URL)


@pytest.mark.parametrize(
    "target",
    [
        "http://example.invalid/feed",
        "https://other.invalid/feed",
        "https://example.invalid:444/feed",
        "https://user:pass@example.invalid/feed",
    ],
)
def test_feed_redirect_does_not_send_secrets_to_other_origins(target):
    with pytest.raises(ScraperError, match="redirected"):
        feed._FeedRedirectHandler().redirect_request(Request(SECRET_URL), None, 302, "", {}, target)


def test_same_origin_https_redirect_is_allowed():
    request = feed._FeedRedirectHandler().redirect_request(
        Request(SECRET_URL), None, 302, "", {}, "/new-feed"
    )
    assert request.full_url == "https://example.invalid/new-feed"


def test_feed_sync_preserves_on_failure_and_reconciles_verified_empty(club, tmp_path, monkeypatch):
    monkeypatch.setenv("EXAMPLE_FEED_URL", SECRET_URL)
    monkeypatch.setattr(feed, "utc_now", lambda: LOWER)
    monkeypatch.setattr(db, "utc_now", lambda: LOWER)
    config = Config(AppConfig(database_path=str(tmp_path / "events.db")), [club], "test")
    monkeypatch.setattr(feed, "download_feed", lambda url: calendar(booking("STATUS:TENTATIVE")))
    assert sync.sync_all(config)[0].status == "success"
    monkeypatch.setattr(feed, "download_feed", lambda url: b"<html>Access denied</html>")
    assert sync.sync_all(config)[0].status == "error"
    conn = db.connect(config.app.database_path)
    try:
        stored = db.list_events(conn, LOWER, UPPER)
        assert len(stored) == 1
        assert stored[0].status == "tentative"
        assert "STATUS:TENTATIVE" in make_ics(stored, "Test")
        assert "private-test-token" not in json.dumps(
            dict(conn.execute("SELECT * FROM events").fetchone())
        )
        monkeypatch.setattr(feed, "download_feed", lambda url: calendar())
        assert sync.sync_all(config)[0].status == "success"
        assert db.list_events(conn, LOWER, UPPER) == []
        assert db.list_events(conn, LOWER, UPPER, include_cancelled=True)[0].status == "cancelled"
    finally:
        conn.close()
