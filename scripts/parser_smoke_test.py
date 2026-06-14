from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennis_overview.models import ClubConfig
from tennis_overview.scrapers.accusportview import AccuSportViewScraper
from tennis_overview.scrapers.clubautomation import ClubAutomationScraper


def main() -> None:
    clubautomation = ClubAutomationScraper(
        ClubConfig(
            id="example-clubautomation",
            name="Example Club Automation Club",
            provider="clubautomation",
            base_url="https://clubautomation.example.com/",
            username_env="EXAMPLE_CLUBAUTOMATION_USERNAME",
            password_env="EXAMPLE_CLUBAUTOMATION_PASSWORD",
        ),
        Path("/tmp"),
    )
    clubautomation_text = """
    MY EVENTS
    JUN 14 Custom Group Lesson Series
    2:30pm - 3:45pm
    Adult 3.0-3.5 | June | Example Coach
    MY REGISTRATIONS
    Program: Custom Group Lesson Series
    Sun | 02:30pm - 03:45pm
    Adult 3.0-3.5 | June | Example Coach (06/07/2026 - 06/28/2026)
    """
    clubautomation_events = clubautomation._parse_my_events(clubautomation_text, "local")
    clubautomation_events += clubautomation._parse_my_registrations(clubautomation_text, "local")
    assert any(
        event.starts_at == datetime(2026, 6, 14, 14, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        for event in clubautomation_events
    )
    assert any(
        event.starts_at == datetime(2026, 6, 21, 14, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
        for event in clubautomation_events
    )
    assert all(event.location is None for event in clubautomation_events)
    assert all(
        event.raw.get("instructor") == "Example Coach"
        for event in clubautomation_events
    )

    accusportview = AccuSportViewScraper(
        ClubConfig(
            id="example-accusportview",
            name="Example AccuSportView Club",
            provider="accusportview",
            base_url="https://example.my.accusportview.com/",
            username_env="EXAMPLE_ACCUSPORTVIEW_USERNAME",
            password_env="EXAMPLE_ACCUSPORTVIEW_PASSWORD",
        ),
        Path("/tmp"),
    )
    card = """
    Rent/Private
    Member Name
    Wed, 06/17/2026, 07:00 PM - 09:00 PM, Tennis/Pickle 6
    Instructor Name
    """
    event = accusportview._event_from_card_text(card, "local")
    assert event is not None
    assert event.starts_at == datetime(2026, 6, 17, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert event.ends_at == datetime(2026, 6, 17, 21, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert event.location == "Tennis/Pickle 6"

    body_text = """
    06/01/2026 - 06/30/2026
    Rent/Private
    Member Name
    Wed, 06/17/2026, 07:00 PM - 09:00 PM, Tennis/Pickle 6
    Instructor Name
    2 h
    Rent/Private
    Member Name
    Fri, 06/19/2026, 07:00 PM - 10:00 PM, Tennis Court 1
    Instructor Name
    3 h
    PAST RESERVATIONS
    Rent/Private
    Member Name
    Sat, 06/06/2026, 01:00 PM - 05:00 PM, Tennis Court 3
    """
    events = accusportview._events_from_body_text(body_text, "local")
    assert len(events) == 2
    assert events[0].starts_at == datetime(2026, 6, 17, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert events[1].starts_at == datetime(2026, 6, 19, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert events[1].ends_at == datetime(2026, 6, 19, 22, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert events[1].location == "Tennis Court 1"
    print("parser smoke test passed")


if __name__ == "__main__":
    main()
