from __future__ import annotations

from datetime import datetime
import json
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
    assert all(event.raw.get("instructor") == "Example Coach" for event in clubautomation_events)

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
    fixture = (
        Path(__file__).resolve().parents[1]
        / "tests/fixtures/accusportview/member-reservations.json"
    )
    payload = json.loads(fixture.read_text())["response"]
    zone = ZoneInfo("America/Los_Angeles")
    events = accusportview._events_from_schedule(
        payload,
        "example-member",
        "https://example.my.accusportview.com/my",
        datetime(2026, 9, 6, tzinfo=zone),
        datetime(2026, 12, 6, tzinfo=zone),
    )
    assert len(events) == 7
    assert events[0].title == "Tennis Adult Tactics"
    assert events[0].starts_at == datetime(2026, 9, 7, 19, 30, tzinfo=zone)
    assert events[0].ends_at == datetime(2026, 9, 7, 20, 30, tzinfo=zone)
    assert events[0].location == "Court D"
    assert events[0].raw["instructor"] == "Example Coach Beta"
    assert events[0].raw["access_code"] == "1111#"
    print("parser smoke test passed")


if __name__ == "__main__":
    main()
