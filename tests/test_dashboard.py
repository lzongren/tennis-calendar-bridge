from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tennis_overview import server
from tennis_overview.models import AppConfig, ClubConfig, Config, TennisEvent


def _config(
    *,
    base_path: str = "",
    database_path: str = "data/test-dashboard.db",
    public_base_url: str = "https://example.test",
) -> Config:
    return Config(
        app=AppConfig(
            timezone="America/Los_Angeles",
            database_path=database_path,
            calendar_name="Tennis Calendar",
            base_path=base_path,
            public_base_url=public_base_url,
            calendar_token="calendar-token",
        ),
        clubs=[
            ClubConfig(
                id="example-clubautomation",
                name="Example Club Automation Club",
                provider="clubautomation",
                base_url="https://clubautomation.example.com/",
                username_env="EXAMPLE_CLUBAUTOMATION_USERNAME",
                password_env="EXAMPLE_CLUBAUTOMATION_PASSWORD",
            )
        ],
        source_path="test",
    )


def test_dashboard_renders_calendar_and_keeps_instructor_out_of_location(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "utc_now",
        lambda: datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc),
    )
    config = _config()
    zone = ZoneInfo("America/Los_Angeles")
    start = datetime(2026, 6, 14, 14, 30, tzinfo=zone)
    event = TennisEvent(
        club_id="example-clubautomation",
        external_id="clubautomation-registration-2026-06-14-custom-group-lesson-series",
        title="Custom Group Lesson Series",
        starts_at=start,
        ends_at=start + timedelta(minutes=75),
        timezone="America/Los_Angeles",
        location=None,
        status="confirmed",
        raw={"instructor": "Example Coach", "access_code": "1234#"},
    )

    html = server._dashboard_html(config, [event], [])

    assert 'data-view-tab="agenda"' in html
    assert 'data-view-panel="calendar"' in html
    assert "Next Session" in html
    assert "June 2026" in html
    assert "has-events" in html
    assert "Custom Group Lesson Series" in html
    assert "Instructor: Example Coach" in html
    assert "Access code: 1234#" in html
    assert "Copy Link" in html
    assert "webcal://example.test/calendar/calendar-token/tennis.ics" in html
    assert ">https://example.test/calendar/calendar-token/tennis.ics<" not in html


def test_dashboard_events_drop_sessions_after_grace_period() -> None:
    zone = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 14, 19, 14, tzinfo=zone)

    def event_at(
        external_id: str,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
    ) -> TennisEvent:
        return TennisEvent(
            club_id="example-clubautomation",
            external_id=external_id,
            title=external_id,
            starts_at=datetime(2026, 6, 14, start_hour, start_minute, tzinfo=zone),
            ends_at=datetime(2026, 6, 14, end_hour, end_minute, tzinfo=zone),
            timezone="America/Los_Angeles",
        )

    visible = server._visible_dashboard_events(
        [
            event_at("ended-hours-ago", 14, 30, 15, 45),
            event_at("ended-in-grace", 18, 0, 19, 5),
            event_at("active-now", 18, 30, 19, 30),
            event_at("future", 20, 0, 21, 0),
        ],
        now,
    )

    assert [event.external_id for event in visible] == [
        "ended-in-grace",
        "active-now",
        "future",
    ]


def test_dashboard_includes_home_screen_metadata() -> None:
    html = server._dashboard_html(_config(), [], [])

    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
    assert '<meta name="theme-color" content="#0e7c66">' in html
    assert 'navigator.serviceWorker.register("/sw.js")' in html


def test_dashboard_uses_configured_base_path_for_home_screen_metadata() -> None:
    html = server._dashboard_html(_config(base_path="/tennis"), [], [])

    assert '<link rel="manifest" href="/tennis/manifest.webmanifest">' in html
    assert '<link rel="apple-touch-icon" href="/tennis/apple-touch-icon.png">' in html
    assert 'navigator.serviceWorker.register("/tennis/sw.js")' in html
    assert "webcal://example.test/tennis/calendar/calendar-token/tennis.ics" in html


def test_dashboard_does_not_double_base_path_in_calendar_url() -> None:
    html = server._dashboard_html(
        _config(base_path="/tennis", public_base_url="https://example.test/tennis"),
        [],
        [],
    )

    assert "webcal://example.test/tennis/calendar/calendar-token/tennis.ics" in html
    assert "/tennis/tennis/calendar" not in html


def test_manifest_payload_is_installable_and_uses_configured_name() -> None:
    manifest = json.loads(server._manifest_json(_config()))

    assert manifest["name"] == "Tennis Calendar"
    assert manifest["short_name"] == "Tennis"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["theme_color"] == "#0e7c66"
    assert manifest["icons"][0]["src"] == "/icons/icon-192.png"
    assert manifest["icons"][1]["src"] == "/icons/icon-512.png"


def test_manifest_payload_uses_configured_base_path() -> None:
    manifest = json.loads(server._manifest_json(_config(base_path="/tennis")))

    assert manifest["start_url"] == "/tennis/"
    assert manifest["scope"] == "/tennis/"
    assert manifest["icons"][0]["src"] == "/tennis/icons/icon-192.png"
    assert manifest["icons"][1]["src"] == "/tennis/icons/icon-512.png"


def test_service_worker_does_not_cache_private_schedule_routes() -> None:
    js = server._service_worker_js(_config())

    assert '"/manifest.webmanifest"' in js
    assert '"/icons/icon-192.png"' in js
    assert 'event.request.mode === "navigate"' in js
    assert '"/api/events"' not in js
    assert '"/calendar/' not in js


def test_service_worker_uses_configured_base_path() -> None:
    js = server._service_worker_js(_config(base_path="/tennis"))

    assert '"/tennis/manifest.webmanifest"' in js
    assert '"/tennis/icons/icon-192.png"' in js
    assert '"/api/events"' not in js
    assert '"/calendar/' not in js


def test_generated_app_icon_is_png() -> None:
    icon = server._app_icon_png(192)

    assert icon.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in icon
    assert b"IDAT" in icon


def test_calendar_subscribe_url_prefers_webcal_scheme() -> None:
    assert (
        server._webcal_url("https://example.test/calendar/token/tennis.ics")
        == "webcal://example.test/calendar/token/tennis.ics"
    )
    assert (
        server._webcal_url("http://127.0.0.1:8080/calendar/token/tennis.ics")
        == "webcal://127.0.0.1:8080/calendar/token/tennis.ics"
    )


def test_request_paths_are_normalized_under_configured_base_path() -> None:
    config = _config(base_path="/tennis")
    handler = object.__new__(server.TennisRequestHandler)
    handler.server = SimpleNamespace(config=config)
    redirects: list[tuple[str, bool]] = []
    handler._redirect = lambda location, send_body=True: redirects.append(
        (location, send_body)
    )

    assert handler._route_path("/tennis/") == "/"
    assert handler._route_path("/tennis/api/events") == "/api/events"
    assert handler._route_path("/tennis/calendar/token/tennis.ics") == (
        "/calendar/token/tennis.ics"
    )
    assert handler._route_path("/healthz") == "/healthz"

    assert handler._route_path("/tennis", "view=agenda", send_body=False) is None
    assert redirects == [("/tennis/?view=agenda", False)]


def test_initial_sync_runs_in_background(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def fake_sync_all(config: Config, only_club_id: str | None = None) -> list[object]:
        started.set()
        release.wait(timeout=1)
        return []

    monkeypatch.setattr(server, "sync_all", fake_sync_all)

    thread = server._start_initial_sync(_config())
    try:
        assert started.wait(timeout=1)
        assert thread.is_alive()
    finally:
        release.set()
        thread.join(timeout=1)
