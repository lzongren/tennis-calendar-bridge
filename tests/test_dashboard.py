from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tennis_overview import server
from tennis_overview.models import AppConfig, ClubConfig, Config, TennisEvent


def _config() -> Config:
    return Config(
        app=AppConfig(
            timezone="America/Los_Angeles",
            calendar_name="Tennis Calendar",
            public_base_url="https://example.test",
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
        raw={"instructor": "Wooten"},
    )

    html = server._dashboard_html(config, [event], [])

    assert "<h2>Calendar</h2>" in html
    assert "June 2026" in html
    assert "Custom Group Lesson Series" in html
    assert "Instructor: Wooten" in html
    assert "<td data-label='Location'></td>" in html
    assert "https://example.test/calendar/calendar-token/tennis.ics" in html


def test_dashboard_includes_home_screen_metadata() -> None:
    html = server._dashboard_html(_config(), [], [])

    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in html
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in html
    assert '<meta name="theme-color" content="#0e7c66">' in html
    assert 'navigator.serviceWorker.register("/sw.js")' in html


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


def test_service_worker_does_not_cache_private_schedule_routes() -> None:
    js = server._service_worker_js()

    assert '"/manifest.webmanifest"' in js
    assert '"/icons/icon-192.png"' in js
    assert 'event.request.mode === "navigate"' in js
    assert '"/api/events"' not in js
    assert '"/calendar/' not in js


def test_generated_app_icon_is_png() -> None:
    icon = server._app_icon_png(192)

    assert icon.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in icon
    assert b"IDAT" in icon
