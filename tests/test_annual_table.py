from datetime import date
from pathlib import Path

import pytest

from tennis_overview.public_schedules import annual_table_courses
from tennis_overview.public_web import Page

TEXT = (Path(__file__).parent / "fixtures" / "discovery_annual_table.txt").read_text()
URL = "https://club.example.org/annual.pdf"


def pdf_page(text=TEXT):
    return Page(URL, "Public annual clinic timetable", text, (), content_type="application/pdf")


@pytest.mark.benchmark
def test_annual_pdf_table_keeps_rows_price_tiers_and_year_scope():
    courses = annual_table_courses(pdf_page(), "America/Los_Angeles", date(2026, 11, 1))
    assert len(courses) == 4
    first, second, third, fourth = courses
    assert first["title"] == "CO ED Adult Tennis Clinic"
    assert first["level"] == "3.0 / 3.5"
    assert first["sessions"][0] == {
        "starts_at": "2026-11-02T10:30:00-08:00",
        "ends_at": "2026-11-02T12:00:00-08:00",
    }
    assert len(first["sessions"]) == 4
    assert first["price"] == "TENNIS MEMBER: $31 FITNESS MEMBER: $36 NON-MEMBER: $42"
    assert "WOMEN'S" not in first["evidence"]
    assert "COACH B" in second["evidence"] and "COACH A" not in second["evidence"]
    assert (
        third["price"]
        == fourth["price"]
        == "TENNIS MEMBER: $33 FITNESS MEMBER: $38 NON-MEMBER: $44"
    )
    assert all(course["availability"] == "Not checked" for course in courses)
    assert all(course["signup_url"] == URL for course in courses)
    assert all(course["schedule_basis"] == "weekly_timetable" for course in courses)


def test_annual_table_never_spills_into_unpublished_year():
    courses = annual_table_courses(pdf_page(), "America/Los_Angeles", date(2026, 12, 20))
    assert courses
    assert all(
        session["starts_at"].startswith("2026-")
        for course in courses
        for session in course["sessions"]
    )
    assert annual_table_courses(pdf_page(), "America/Los_Angeles", date(2027, 1, 1)) == []


@pytest.mark.parametrize(
    "restriction",
    [
        "No class during holidays",
        "Closed November 26",
        "Spring term only",
        "April 1 - May 30",
    ],
)
def test_annual_table_with_exclusions_keeps_evidence_but_does_not_invent_dates(restriction):
    courses = annual_table_courses(
        pdf_page(TEXT + "\n" + restriction), "America/Los_Angeles", date(2026, 11, 1)
    )
    assert courses and all(course["sessions"] == [] for course in courses)
    assert all(restriction in course["evidence"] for course in courses)
    assert all(course["schedule_basis"] == "unresolved" for course in courses)


@pytest.mark.parametrize(
    "text",
    [
        TEXT.replace("2026  Adult  Tennis  ClinicSchedule", "Copyright 2026"),
        TEXT.replace(
            "DAY       TYPE       TIME                   LEVEL       PRO", "Private Lessons"
        ),
        TEXT.replace("10:30AM - 12:00PM", "time to be announced", 1),
    ],
)
def test_unknown_or_changed_table_does_not_return_successful_partial_parse(text):
    assert annual_table_courses(pdf_page(text), "America/Los_Angeles", date(2026, 11, 1)) is None


def test_price_context_does_not_leak_across_pdf_pages():
    text = TEXT.replace("TENNIS MEMBER: $33    FITNESS MEMBER: $38    NON-MEMBER: $44\n", "")
    courses = annual_table_courses(pdf_page(text), "America/Los_Angeles", date(2026, 11, 1))
    assert courses[0]["price"]
    assert courses[2]["price"] == ""


def test_html_lookalike_does_not_select_pdf_adapter():
    page = Page(URL, "HTML", TEXT, ())
    assert annual_table_courses(page, "America/Los_Angeles", date(2026, 11, 1)) is None
