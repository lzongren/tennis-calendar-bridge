from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tennis_overview.calendar import make_ics, parse_ics_events
from tennis_overview.models import TennisEvent


def test_ics_includes_instructor_in_description_not_location() -> None:
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
        category="registration",
        raw={"instructor": "Example Coach"},
    )

    ics = make_ics([event], "Tennis Calendar")

    assert "Instructor: Example Coach" in ics
    assert "LOCATION:Example Coach" not in ics
    parsed = parse_ics_events(ics, "example-clubautomation", "America/Los_Angeles", "local")
    assert len(parsed) == 1
    assert parsed[0].title == "Custom Group Lesson Series"


def test_ics_keeps_real_location() -> None:
    zone = ZoneInfo("America/Los_Angeles")
    start = datetime(2026, 6, 17, 19, 0, tzinfo=zone)
    event = TennisEvent(
        club_id="example-accusportview",
        external_id="accusportview-2026-06-17-1900-tennis-pickle-6",
        title="Rent/Private",
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        timezone="America/Los_Angeles",
        location="Tennis/Pickle 6",
        category="reservation",
        raw={"access_code": "1234#"},
    )

    ics = make_ics([event], "Tennis Calendar")

    assert "LOCATION:Tennis/Pickle 6" in ics
    assert "SUMMARY:Rent/Private" in ics
    assert "Access code: 1234#" in ics
    assert "SUMMARY:1234#" not in ics
