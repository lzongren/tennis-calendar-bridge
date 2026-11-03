from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.course_extraction import CourseExtractor
from tennis_overview.courtreserve_discovery import courtreserve_observation
from tennis_overview.discovery_agent import DiscoveryRequest, SourceUnavailable, run_discovery_agent
from tennis_overview.discovery_sources import PublicSourceBackend, identity

FIXTURES = Path(__file__).parent / "fixtures" / "discovery_benchmark"
HTML = (FIXTURES / "courtreserve_detail.html").read_text()
URL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
NOW = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)
REQUEST = DiscoveryRequest(
    city="Example City", level="3.0", timezone="America/Los_Angeles", start=date(2026, 11, 1)
)


def observation(html=HTML, **kwargs):
    return courtreserve_observation(
        BrowserDocument(URL, "Account title must not be used", html, NOW),
        REQUEST,
        now=NOW,
        **kwargs,
    )


@pytest.mark.benchmark
def test_observed_dom_matches_original_screenshot_oracle():
    result = observation()
    expected = json.loads((FIXTURES / "nov2_registration.json").read_text())["expected"]
    assert result.course is not None
    session = result.course.sessions[0]
    assert session.starts_at.isoformat() == expected["starts_at"]
    assert session.ends_at.isoformat() == expected["ends_at"]
    assert result.course.price == "$" + expected["price_usd"]
    assert result.availability == expected["availability"]
    assert result.waitlist_count == expected["waitlist_count"]
    assert result.partial  # Additional dates need their own evidence.
    assert len(result.course.sessions) == 1


@pytest.mark.parametrize(
    "status,expected,count",
    [
        ("Full", "full", None),
        ("Full - 0 Registrant(s) Waitlisted", "full", 0),
        ("Full - 2 Registrant(s) Waitlisted", "full", 2),
        ("Waitlisted", "unknown", None),
        ("Register", "unknown", None),
        ("Join Waitlist", "unknown", None),
        ("Cancelled", "cancelled", None),
        ("Full - -2 Registrant(s) Waitlisted", "unknown", None),
    ],
)
def test_status_never_infers_seats_or_personal_position(status, expected, count):
    result = observation(HTML.replace("Full - 2 Registrant(s) Waitlisted", status))
    assert (result.availability, result.waitlist_count) == (expected, count)


@pytest.mark.parametrize(
    "old,new",
    [
        ("Mon, Nov 2nd", "Tue, Nov 2nd"),
        ("Mon, Nov 2nd", "Mon, Nov 32nd"),
        ("6:30p - 7:50p", "6:30 - 7:50"),
        ("6:30p - 7:50p", "13:30p - 7:50p"),
        ("6:30p - 7:50p", "7:50p - 6:30p"),
        ('data-testid="events-details-date"', 'data-testid="changed-date"'),
        ('data-testid="events-details-page"', 'data-testid="login-page"'),
    ],
)
def test_bad_or_changed_dom_fails_closed(old, new):
    with pytest.raises(SourceUnavailable):
        observation(HTML.replace(old, new))


def test_duplicate_fields_and_hidden_date_are_not_evidence():
    for html in [
        HTML.replace("</main>", '<div data-testid="events-details-date">Mon, Nov 9th</div></main>'),
        HTML.replace(
            'data-testid="events-details-date"', 'hidden data-testid="events-details-date"'
        ),
        HTML + HTML,
    ]:
        with pytest.raises(SourceUnavailable):
            observation(html)


def test_unexpanded_series_is_still_partial():
    html = HTML.replace('data-testid="events-details-dates-list"', 'data-testid="unmounted-dates"')
    html = html.replace(
        "</main>", '<div data-testid="events-details-tab-dates">Dates (6)</div></main>'
    )
    assert observation(html).partial
    assert observation(html.replace("Dates (6)", "Dates (1)")).partial
    assert not observation(html.replace("Dates (6)", "Dates (0)")).partial
    with pytest.raises(SourceUnavailable):
        observation(html.replace("Dates (6)", "Layout changed"))


@pytest.mark.parametrize("delta", [-6, 901])
def test_stale_or_future_observation_rejected(delta):
    document = BrowserDocument(URL, "Course", HTML, NOW - timedelta(seconds=delta))
    with pytest.raises(SourceUnavailable):
        courtreserve_observation(document, REQUEST, now=NOW)


def test_timestamp_must_be_aware_and_payload_bounded():
    document = BrowserDocument(URL, "Course", HTML, NOW)
    for invalid in [
        replace(document, captured_at=NOW.replace(tzinfo=None)),
        replace(document, html="x" * 128001),
    ]:
        with pytest.raises(SourceUnavailable):
            courtreserve_observation(invalid, REQUEST, now=NOW)


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/Online/Events/Details/12345/EXAMPLE",
        "https://app.courtreserve.com/Online/Events/List/12345",
        "https://app.courtreserve.com/Online/Portal/Index/12345",
        URL + "&email=synthetic@example.test",
        URL + "&resId=456",
        URL.replace("https://", "http://"),
        URL.replace("courtreserve.com/", "courtreserve.com:8443/"),
    ],
)
def test_only_observed_detail_shape_has_registration_authority(url):
    with pytest.raises(SourceUnavailable):
        courtreserve_observation(BrowserDocument(url, "Course", HTML, NOW), REQUEST, now=NOW)


def test_filters_and_year_context_are_not_search_date_guesses():
    document = BrowserDocument(URL, "Course", HTML, NOW)
    for request in [
        REQUEST.model_copy(update={"level": "4.5"}),
        REQUEST.model_copy(update={"start": date(2027, 11, 1)}),
    ]:
        assert courtreserve_observation(document, request, now=NOW).course is None
    assert (
        observation(HTML.replace("Adult Tennis - Camps &amp; Clinics", "Junior Tennis")).course
        is None
    )
    assert observation(HTML.replace("$54.00", "$54 - $324")).course.price == ""


def test_explicit_year_and_dst_ambiguity():
    document = BrowserDocument(
        URL, "Course", HTML.replace("Mon, Nov 2nd", "Mon, Jan 4th, 2027"), NOW
    )
    request = REQUEST.model_copy(update={"start": date(2027, 1, 1)})
    assert (
        courtreserve_observation(document, request, now=NOW).course.sessions[0].starts_at.year
        == 2027
    )
    html = HTML.replace("Mon, Nov 2nd", "Sun, Nov 1st").replace("6:30p - 7:50p", "1:10a - 1:50a")
    with pytest.raises(SourceUnavailable):
        observation(html)


@pytest.mark.benchmark
def test_browser_reader_to_agent_preserves_facts_without_account_data_or_model_extraction():
    async def scenario():
        calls = iter(
            [
                ToolCallPart("search_courses", {"city": REQUEST.city}),
                ToolCallPart("read_source", {"source_id": identity(URL)}),
                ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
            ]
        )

        async def respond(messages, info):
            assert not any(t.name == "extract_courses" for t in info.output_tools)
            return ModelResponse([next(calls)])

        model, usage = FunctionModel(respond), RunUsage()
        private = "<nav>synthetic-private-email@example.test</nav><button>Unsubscribe From Waitlist synthetic-private-action</button><script>synthetic-private-token</script>"
        reader = AsyncMock(
            return_value=BrowserDocument(
                URL, "synthetic-private-title", HTML.replace("</main>", private + "</main>"), NOW
            )
        )
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 12),
            (URL,),
            reader=reader,
            searcher=AsyncMock(return_value=[]),
            clock=lambda: NOW,
        )
        with capture_run_messages() as messages:
            run = await run_discovery_agent(
                REQUEST.model_copy(update={"search_mode": "source_check"}),
                backend,
                model,
                usage=usage,
            )
        result = backend.result(run, REQUEST)
        assert "synthetic-private" not in str(messages) + str(result)
        assert "Unsubscribe" not in str(messages) + str(result)
        course = next(
            course
            for course in result["courses"]
            if course["sessions"][0]["starts_at"] == "2026-11-02T18:30:00-08:00"
        )
        assert course["availability"] == "full"
        assert course["waitlist_count"] == 2
        assert course["user_waitlist_position"] is None
        assert course["price"] == "$54.00"
        assert course["sessions"][0]["starts_at"] == "2026-11-02T18:30:00-08:00"
        assert result["partial"]
        assert result["agent"]["requests"] == 3
        assert backend.sources[identity(URL)].status == "checked"
        reader.assert_awaited_once_with(URL)

    asyncio.run(scenario())


def test_browser_result_for_another_source_is_rejected():
    async def scenario():
        backend = PublicSourceBackend(
            AsyncMock(), reader=AsyncMock(return_value=BrowserDocument(URL, "Course", HTML, NOW))
        )
        other = URL.replace("resId=123", "resId=456")
        ref = backend.register(other, "Course", 0)
        with pytest.raises(SourceUnavailable, match="does not match"):
            await backend.read(ref.id, REQUEST)
        assert backend.sources[ref.id].status == "unavailable"

    asyncio.run(scenario())
