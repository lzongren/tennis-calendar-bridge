from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tennis_overview.scrapers.accusportview import AccuSportViewScraper


def test_body_parser_keeps_active_reservations_only(
    accusportview_scraper: AccuSportViewScraper,
) -> None:
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

    events = accusportview_scraper._events_from_body_text(body_text, "local")

    assert len(events) == 2
    assert events[0].title == "Rent/Private"
    assert events[0].starts_at == datetime(
        2026, 6, 17, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles")
    )
    assert events[0].ends_at == datetime(
        2026, 6, 17, 21, 0, tzinfo=ZoneInfo("America/Los_Angeles")
    )
    assert events[0].location == "Tennis/Pickle 6"
    assert events[1].starts_at == datetime(
        2026, 6, 19, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles")
    )
    assert events[1].ends_at == datetime(
        2026, 6, 19, 22, 0, tzinfo=ZoneInfo("America/Los_Angeles")
    )
    assert events[1].location == "Tennis Court 1"


def test_parser_ignores_generic_json_payloads(
    accusportview_scraper: AccuSportViewScraper,
) -> None:
    payloads = [
        (
            "https://example.my.accusportview.com/api/profile",
            {"title": "Tennis", "start": "2026-06-14T20:38:52-07:00"},
        )
    ]

    assert accusportview_scraper._collect_from_json(payloads) == []


def test_title_heuristic_keeps_two_word_event_titles(
    accusportview_scraper: AccuSportViewScraper,
) -> None:
    body_text = """
    Private Lesson
    Wed, 06/17/2026, 07:00 PM - 09:00 PM, Tennis/Pickle 6
    """

    events = accusportview_scraper._events_from_body_text(body_text, "local")

    assert len(events) == 1
    assert events[0].title == "Private Lesson"
