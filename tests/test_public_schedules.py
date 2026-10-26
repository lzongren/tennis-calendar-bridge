from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from tennis_overview.discovery import matches_level
from tennis_overview.public_web import Page, ScheduleBlock, page_from_html
from tennis_overview.public_schedules import timetable_courses

FIXTURES = Path(__file__).parent / "fixtures"
START = date(2026, 10, 25)
ZONE = "America/Los_Angeles"


def fixture_page(name):
    return page_from_html("https://club.example.org/" + name, (FIXTURES / name).read_bytes())


def test_ongoing_weekly_timetable_produces_calendar_candidates_not_bookings():
    courses = [
        c
        for c in timetable_courses(fixture_page("discovery_ongoing.html"), ZONE, START)
        if matches_level(c, "3.0")
    ]
    assert len(courses) == 2
    assert courses[0]["sessions"][0] == {
        "starts_at": "2026-10-28T19:00:00-07:00",
        "ends_at": "2026-10-28T20:00:00-07:00",
    }
    assert courses[1]["sessions"][0]["starts_at"] == "2026-10-29T18:00:00-07:00"
    assert all(c["schedule_basis"] == "weekly_timetable" for c in courses)
    assert all(c["availability"] == "Not checked" for c in courses)
    assert courses[0]["sessions"][1]["starts_at"] == "2026-11-04T19:00:00-08:00"


def test_term_card_preserves_weekday_time_dates_and_its_own_link():
    page = fixture_page("discovery_terms.html")
    courses = timetable_courses(page, ZONE, START)
    assert len(courses) == 4
    assert courses[0]["sessions"][0]["starts_at"] == "2026-10-27T19:00:00-07:00"
    assert courses[0]["signup_url"].endswith("/tuesday-term")
    assert courses[2]["sessions"][0]["starts_at"] == "2026-10-31T10:00:00-07:00"
    assert courses[2]["signup_url"].endswith("/saturday-term")
    assert courses[2]["schedule_basis"] == "year_inferred"
    assert "2026" in courses[2]["schedule_note"]
    assert courses[0]["price"] == ""  # No inherited price from a different term.
    assert courses[1]["sessions"] == []  # Four possible dates, only three classes.
    assert courses[3]["sessions"] == []  # Unknown holiday exclusion.
    assert courses[3]["price"] == "$120"
    assert "Saturdays" in courses[3]["evidence"]


@pytest.mark.parametrize(
    "schedule",
    [
        "Saturdays | 10:00 AM - 11:30 AM Oct 11 - Nov 14",  # Conflicting weekday/date.
        "Saturdays | 10:00 AM - 11:30 AM Jan 3 - Feb 7",  # Outside current window.
        "Saturdays | 10:00 - 11:30 Oct 10 - Nov 14",  # No AM/PM.
        "Saturdays | 10:00 AM - 11:30 AM Final Session (2 Classes)",
        "Saturdays | 10:00 AM - 11:30 AM Oct 10 - Nov 14 except Oct 31",
    ],
)
def test_uncertain_terms_are_not_guessed(schedule):
    page = Page(
        "https://club.example.org",
        "Club",
        schedule,
        (),
        schedules=(ScheduleBlock("Clinic", "3.0", schedule, "https://club.example.org"),),
    )
    assert timetable_courses(page, ZONE, START)[0]["sessions"] == []


def test_explicit_dates_and_year_boundary():
    page = fixture_page("discovery_terms.html")
    block = replace(
        page.schedules[0], text="Tuesdays | 7:00 PM - 8:30 PM Dec 22, 2026 - Jan 12, 2027"
    )
    page = replace(page, schedules=(block,))
    result = timetable_courses(page, ZONE, date(2026, 12, 20))[0]
    assert result["schedule_basis"] == "published_dates"
    assert result["sessions"][-1]["starts_at"] == "2027-01-12T19:00:00-08:00"


def test_ordinary_undated_or_historical_text_is_not_an_ongoing_timetable():
    page = fixture_page("discovery_ongoing.html")
    assert (
        timetable_courses(
            replace(page, text=page.text.replace("ARE ONGOING", "ENDED")), ZONE, START
        )
        is None
    )
