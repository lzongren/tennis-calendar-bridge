from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.discovery_agent import (
    LIVE_DISCOVERY_LIMITS,
    DiscoveryRequest,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import AREA_RESULTS_PER_QUERY, PublicSourceBackend, identity
from tennis_overview.public_web import Page, PageLink, page_from_html

FIXTURES = Path(__file__).parent / "fixtures/discovery_benchmark"
PORTAL = "https://app.courtreserve.com/Online/Portal/Index/12345"
DETAIL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"


@pytest.mark.benchmark
def test_search_frontier_retains_room_for_operator_to_real_course_navigation():
    queries = [
        ("Example City", "overview"),
        ("Example City", "registration"),
        ("Example City", "schedule"),
        ("Nearby City", "overview"),
        ("Other City", "overview"),
        ("Nearby City", "registration"),
    ]
    candidates = {
        query: [
            {"url": f"https://operator-{index}.example.org/{rank}", "title": "Adult tennis"}
            for rank in range(8)
        ]
        for index, query in enumerate(queries)
    }
    operator = candidates[queries[3]][1]["url"]
    checked_at = datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc)
    seen_urls = []

    async def search(city, level, year, focus):
        return candidates[city, focus]

    async def read(url):
        seen_urls.append(url)
        if url == operator:
            return page_from_html(url, (FIXTURES / "operator_navigation.html").read_bytes())
        if url == PORTAL:
            return Page(
                url,
                "Adult course list (partial)",
                "Selected adult clinic detail",
                (DETAIL,),
                link_details=(PageLink(DETAIL, "Adult Coach Clinic"),),
                required_links=(DETAIL,),
                partial=True,
            )
        if url == DETAIL:
            return BrowserDocument(
                url,
                "Course",
                (FIXTURES / "courtreserve_anonymous_detail.html").read_text(),
                checked_at,
            )
        return Page(url, "Courses", "No dated sessions on this public page", ())

    backend = PublicSourceBackend(
        Mock(extract=AsyncMock(return_value=())),
        reader=read,
        searcher=search,
        source_limit=LIVE_DISCOVERY_LIMITS.sources,
        clock=lambda: checked_at,
    )
    request = DiscoveryRequest(
        city="Example City",
        nearby_cities=("Nearby City", "Other City"),
        level="3.0",
        start=date(2026, 11, 1),
        timezone="America/Los_Angeles",
    )
    other_sources = [candidates[query][0]["url"] for query in queries if query != queries[3]]
    calls = iter(
        [
            *(
                ToolCallPart("search_courses", {"city": city, "focus": focus})
                for city, focus in queries
            ),
            *(
                ToolCallPart("read_source", {"source_id": identity(url)})
                for url in (operator, *other_sources, PORTAL, DETAIL)
            ),
            ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
        ]
    )

    def respond(messages, info):
        return ModelResponse([next(calls)])

    run = asyncio.run(
        run_discovery_agent(request, backend, FunctionModel(respond), limits=LIVE_DISCOVERY_LIMITS)
    )
    assert backend.initial_urls == () and DETAIL not in request.model_dump_json()
    assert seen_urls == [operator, *other_sources, PORTAL, DETAIL]
    assert len(backend.sources) < LIVE_DISCOVERY_LIMITS.sources
    assert run.partial and backend.truncated
    assert len(run.sessions) == 1
    session = run.sessions[0]
    assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
    assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
    assert session.price_usd == 54 and session.availability == "full"
    assert session.waitlist_count is None


def test_live_query_cap_reserves_link_capacity_with_user_sources():
    search_capacity = LIVE_DISCOVERY_LIMITS.searches * AREA_RESULTS_PER_QUERY
    assert LIVE_DISCOVERY_LIMITS.sources - search_capacity - 4 >= 12


def test_search_admission_counts_valid_unique_candidates_and_preserves_explicit_sources():
    async def scenario():
        explicit = tuple(f"https://user-source-{i}.example.org/" for i in range(4))
        request = DiscoveryRequest(city="Example City", start=date(2026, 11, 1), timezone="UTC")
        candidates = [
            {"url": "http://127.0.0.1/", "title": "Private"},
            {"title": "Missing URL"},
            *({"url": url, "title": "Already provided"} for url in explicit),
            {"url": "https://club.example.org/adults#one", "title": "Adult classes"},
            {"url": "https://club.example.org/adults#two", "title": "Duplicate"},
            *({"url": f"https://other-{i}.example.org/", "title": "Courses"} for i in range(8)),
        ]
        backend = PublicSourceBackend(Mock(), explicit, searcher=AsyncMock(return_value=candidates))
        result = await backend.search(request.city, request)
        assert len(result.sources) == len(backend.sources) == 4 + AREA_RESULTS_PER_QUERY
        assert result.required_ids == tuple(identity(url) for url in explicit)
        assert result.partial and backend.truncated
        assert result.sources[4].url == "https://club.example.org/adults"

    asyncio.run(scenario())
