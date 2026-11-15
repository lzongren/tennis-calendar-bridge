import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

from tennis_overview.course_evidence import reconcile_session
from tennis_overview.discovery_agent import DiscoveryRequest, DiscoveryRun
from tennis_overview.discovery_sources import PublicSourceBackend
from tennis_overview.public_web import page_from_html


URL = "https://player.courtreserve.com/players/events/e-123"
HTML = b"""
<header><h1>Adult drills</h1></header><p>Level: NTRP 3.0</p><dl>
<dt>Sport</dt><dd>Tennis</dd><dt>Type</dt><dd>Adult tennis</dd>
<dt>Starts</dt><dd><time datetime="2030-04-03T18:00:00-07:00"></time></dd>
<dt>Ends</dt><dd><time datetime="2030-04-03T19:30:00-07:00"></time></dd>
<dt>Registration fee</dt><dd>$42 USD</dd>
<dt>Capacity</dt><dd>8</dd><dt>Spots left</dt><dd>3</dd></dl>
<p>Other dates: 7 spots open</p>
"""
REQUEST = DiscoveryRequest(
    city="Example City", level="3.0", start=date(2030, 4, 3), timezone="America/Los_Angeles"
)


@pytest.mark.parametrize("spots", [0, 3])
def test_public_occurrence_seats_flow_to_calendar_result(spots: int) -> None:
    page = page_from_html(URL, HTML.replace(b"<dd>3</dd>", f"<dd>{spots}</dd>".encode()))
    backend = PublicSourceBackend(AsyncMock(), reader=AsyncMock(return_value=page))
    source = backend.register(URL, "", 0)
    assert source
    doc = asyncio.run(backend.read(source.id, REQUEST))
    session = reconcile_session(doc.records)
    run = DiscoveryRun(
        sessions=(session,),
        sources=(),
        searched_cities=(),
        audit=(),
        partial=False,
        model_requests=0,
        tool_calls=1,
    )
    (row,) = backend.result(run, REQUEST)["courses"]
    assert (row["spots_left"], row["price"]) == (spots, "$42.00")
    assert row["availability"] == ("available" if spots else "full")
    assert row["sessions"] == [
        {"starts_at": "2030-04-03T18:00:00-07:00", "ends_at": "2030-04-03T19:30:00-07:00"}
    ]


@pytest.mark.parametrize(
    "body",
    [
        HTML.replace(b"<dd>3</dd>", b"<dd>9</dd>"),
        HTML.replace(b"Adult tennis", b"Junior tennis"),
        HTML.replace(b"-07:00", b""),
        HTML + HTML,
        HTML.replace(b"Spots left", b"Waitlisted"),
    ],
)
def test_invalid_or_ambiguous_counts_are_not_registration_evidence(body: bytes) -> None:
    assert page_from_html(URL, body).player_event is None


def test_copied_content_does_not_gain_registration_authority() -> None:
    assert page_from_html("https://example.org/players/events/e-123", HTML).player_event is None


def test_trace_distinguishes_search_hits_admission_and_extraction() -> None:
    trace: list[tuple[str, str]] = []
    irrelevant = "https://example.org/tournaments"
    backend = PublicSourceBackend(
        AsyncMock(),
        searcher=AsyncMock(
            return_value=[
                {"url": irrelevant, "title": "Championships"},
                {"url": URL, "title": "Adult drills"},
            ]
        ),
        reader=AsyncMock(return_value=page_from_html(URL, HTML)),
        trace=lambda stage, url: trace.append((stage, url)),
    )
    request = REQUEST.model_copy(update={"search_focus": "registration"})
    results = asyncio.run(backend.search(request.city, request))
    assert [r.url for r in results.sources] == [URL]
    asyncio.run(backend.read(results.sources[0].id, request))
    assert [stage for stage, url in trace if url == irrelevant] == ["search_hit"]
    assert [stage for stage, url in trace if url == URL] == [
        "search_hit",
        "admitted",
        "read_attempt",
        "read_ok",
        "extracted",
    ]
