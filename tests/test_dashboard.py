from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tennis_overview import server
from tennis_overview.models import AppConfig, ClubConfig, Config, TennisEvent


def test_dashboard_renders_calendar_and_keeps_instructor_out_of_location(monkeypatch) -> None:
    monkeypatch.setattr(
        server,
        "utc_now",
        lambda: datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc),
    )
    config = Config(
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
