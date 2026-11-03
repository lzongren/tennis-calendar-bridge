from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest

from tennis_overview.discovery_agent import DiscoveryRequest
from tennis_overview.discovery_sources import PublicSourceBackend, identity, related_links
from tennis_overview.public_web import Page, PageLink

OPERATOR = "https://club.example.org/adult-tennis"
PORTAL = PageLink("https://registration.example.org/portal", "Register Now")
RATING = PageLink("https://club.example.org/tennis-rating-program.pdf", "Tennis rating guide")
PROGRAM = PageLink("https://club.example.org/tennis-program/", "Adult tennis program")
SCHEDULE = PageLink("https://club.example.org/adults-fall.pdf", "Adult Tennis | Fall View Schedule")
CLINICS = PageLink(
    "https://club.example.org/clinics-fall.pdf", "Weekly Coach Clinics View Schedule"
)


def operator_page():
    # Preserve the observed operator-page ordering, with synthetic URLs and labels.
    links = (PORTAL, RATING, PROGRAM, SCHEDULE, CLINICS)
    return Page(
        OPERATOR,
        "Adult tennis",
        "Published programs and linked schedules",
        tuple(link.url for link in links),
        link_details=links,
    )


def test_schedules_precede_general_tennis_information_without_losing_signup():
    assert related_links(operator_page()) == (PORTAL, SCHEDULE, CLINICS, RATING, PROGRAM)


@pytest.mark.parametrize(
    "schedule",
    [
        PageLink("https://club.example.org/files/calendar.pdf", ""),
        PageLink("https://club.example.org/files/annual.pdf", "Adult clinic timetable"),
        PageLink("https://club.example.org/files/adult-tennis.pdf", "Adult tennis"),
        PageLink("https://club.example.org/tennis/schedule", "Weekly sessions"),
    ],
)
def test_schedule_priority_uses_observed_labels_and_paths(schedule):
    page = Page(OPERATOR, "Club", "", (RATING.url, schedule.url), link_details=(RATING, schedule))
    assert related_links(page) == (schedule, RATING)


@pytest.mark.parametrize("topic", ["rating", "rules", "policy", "policies"])
def test_adult_document_label_does_not_promote_general_guidance(topic):
    guide = PageLink(f"https://club.example.org/adult-tennis-{topic}.pdf", "Adult tennis guide")
    page = Page(OPERATOR, "Club", "", (guide.url, SCHEDULE.url), link_details=(guide, SCHEDULE))
    assert related_links(page) == (SCHEDULE, guide)


@pytest.mark.benchmark
@pytest.mark.parametrize("slots", [2, 3])
def test_tight_registry_preserves_registration_and_schedules_before_rating_guide(slots):
    async def scenario():
        reader = AsyncMock(return_value=operator_page())
        extractor = Mock(extract=AsyncMock(return_value=()))
        backend = PublicSourceBackend(extractor, reader=reader, source_limit=slots + 1)
        source = backend.register(OPERATOR, "Adult tennis", 0)
        request = DiscoveryRequest(city="Example City", start=date(2026, 11, 1), timezone="UTC")
        document = await backend.read(source.id, request)
        expected = (PORTAL, SCHEDULE, CLINICS)[:slots]
        assert [link.url for link in document.links] == [link.url for link in expected]
        assert document.navigation_ids == (identity(PORTAL.url),)
        assert document.records == () and document.required_ids == ()
        assert document.partial and backend.truncated
        assert len(backend.sources) == slots + 1
        assert identity(RATING.url) not in backend.sources
        reader.assert_awaited_once_with(OPERATOR)
        extractor.extract.assert_awaited_once()

    asyncio.run(scenario())


def test_ranking_does_not_promote_junior_schedule_or_make_a_link_course_evidence():
    junior = PageLink("https://club.example.org/junior-schedule.pdf", "Junior tennis timetable")
    page = Page(OPERATOR, "Club", "", (junior.url, RATING.url), link_details=(junior, RATING))
    assert related_links(page) == (RATING,)
