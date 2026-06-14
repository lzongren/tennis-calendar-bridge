from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tennis_overview.scrapers.clubautomation import ClubAutomationScraper


def test_sidebar_parses_events_and_instructor(
    clubautomation_scraper: ClubAutomationScraper,
) -> None:
    text = """
    MY EVENTS
    JUN 14 Custom Group Lesson Series
    2:30pm - 3:45pm
    Adult 3.0-3.5 | June | Example Coach
    MY REGISTRATIONS
    Program: Custom Group Lesson Series
    Sun | 02:30pm - 03:45pm
    Adult 3.0-3.5 | June | Example Coach (06/07/2026 - 06/28/2026)
    """

    events = clubautomation_scraper._parse_my_events(text, "local")
    registrations = clubautomation_scraper._parse_my_registrations(text, "local")

    assert any(
        event.starts_at
        == datetime(2026, 6, 14, 14, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        for event in events
    )
    assert [event.starts_at.day for event in registrations] == [7, 14, 21, 28]
    assert all(event.location is None for event in events + registrations)
    assert all(
        event.raw.get("instructor") == "Example Coach"
        for event in events + registrations
    )


def test_detail_keeps_court_like_values_as_location(
    clubautomation_scraper: ClubAutomationScraper,
) -> None:
    text = """
    MY EVENTS
    JUN 14 Ball Machine Rental
    2:30pm - 3:45pm
    Adult 3.0-3.5 | June | Court 4
    """

    events = clubautomation_scraper._parse_my_events(text, "local")

    assert len(events) == 1
    assert events[0].location == "Court 4"
    assert "instructor" not in events[0].raw
