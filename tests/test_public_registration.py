from datetime import date

from tennis_overview.public_registration import registration_courses
from tennis_overview.public_web import Page


def test_public_course_sessions_are_explicit_and_deduplicated():
    page = Page(
        "https://playtennis.usta.com/example-club/Coaching/Course/example-id",
        "Example Club - Programming / Course",
        """Adult Clinic
Level:
Intermediate
Price:
$35
Sessions
Mon, Oct 26, 2026
7:00 pm - 8:30 pm
Court 1
Mon, Nov 02, 2026
7:00 pm - 8:30 pm
Court 1
Mon, Nov 02, 2026
7:00 pm - 8:30 pm
Court 1
Where to find us
Example Park
""",
        (),
        "Adult Clinic NTRP 3.0",
    )
    courses = registration_courses(page, "America/Los_Angeles", date(2026, 10, 25))
    assert len(courses) == 1
    assert courses[0]["price"] == "$35"
    assert courses[0]["venue"] == "Example Park"
    assert [s["starts_at"] for s in courses[0]["sessions"]] == [
        "2026-10-26T19:00:00-07:00",
        "2026-11-02T19:00:00-08:00",
    ]
    assert registration_courses(page, "UTC", date(2027, 1, 1)) == []


def test_ordinary_pages_use_generic_extraction():
    page = Page("https://club.example.org/", "Club", "Tuesday 7 PM", (), "Adult Clinic")
    assert registration_courses(page, "UTC", date.today()) is None
