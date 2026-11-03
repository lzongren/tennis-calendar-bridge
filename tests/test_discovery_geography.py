from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from geographiclib.geodesic import Geodesic
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.discovery_agent import (
    LIVE_DISCOVERY_LIMITS,
    DiscoveryRequest,
    run_discovery_agent,
)
from tennis_overview.discovery_geography import nearby_cities, resolve_city
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.public_web import Page


@pytest.mark.parametrize(
    "query,country,region",
    [
        ("San Jose, CA", "US", "CA"),
        ("San Jose, California, US", "US", "CA"),
        ("san josé, ca and nearby cities", "US", "CA"),
        ("Paris, TX", "US", "TX"),
        ("Paris, France", "FR", "11"),
        ("London, United Kingdom", "GB", "ENG"),
    ],
)
def test_exact_city_alias_and_qualifiers(query, country, region):
    city = resolve_city(query)
    assert city is not None
    assert (city["countrycode"], city["admin1code"]) == (country, region)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "Cambridge",
        "SJC",
        "San Jose, TX",
        "Paris, TX, France",
        "Example City",
        "https://private/",
        "San\nJose",
        "x" * 121,
    ],
)
def test_unresolved_ambiguous_and_unsafe_locations_are_not_guessed(query):
    assert resolve_city(query) is None
    assert nearby_cities(query) == ()


def test_timezone_can_disambiguate_alias_but_does_not_override_explicit_region():
    assert resolve_city("SJC", "America/Los_Angeles")["countrycode"] == "US"
    assert resolve_city("SJC", "America/Sao_Paulo")["countrycode"] == "BR"
    assert resolve_city("Paris, France", "America/Los_Angeles")["countrycode"] == "FR"
    assert resolve_city("Cambridge", "Europe/London")["countrycode"] == "GB"
    assert resolve_city("Springfield", "America/Chicago") is None


@pytest.mark.benchmark
@pytest.mark.parametrize("query", ["San Jose, CA", "Seattle, WA", "London, United Kingdom"])
def test_nearby_candidates_are_offline_bounded_and_same_country(query, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Geographic expansion must be offline")

    monkeypatch.setattr("socket.getaddrinfo", no_network)
    monkeypatch.setattr("socket.create_connection", no_network)
    nearby_cities.cache_clear()
    center = resolve_city(query)
    names = nearby_cities(query)
    assert 0 < len(names) <= 2
    distances = []
    for name in names:
        city = resolve_city(name)
        assert city["countrycode"] == center["countrycode"]
        distance = Geodesic.WGS84.Inverse(
            center["latitude"], center["longitude"], city["latitude"], city["longitude"]
        )["s12"]
        assert 1000 <= distance <= 25000
        distances.append(distance)
    assert distances == sorted(distances)
    if query == "San Jose, CA":
        assert names[0] == "Santa Clara, CA"


def test_source_check_does_not_expand_geography():
    request = DiscoveryRequest(
        city="San Jose, CA",
        start=date(2026, 11, 1),
        timezone="America/Los_Angeles",
        search_mode="source_check",
    )
    backend = PublicSourceBackend(Mock(), searcher=AsyncMock(return_value=[]))
    result = asyncio.run(backend.search(request.city, request))
    assert result.nearby_cities == () and not result.partial


@pytest.mark.benchmark
def test_area_agent_discovers_real_case_from_nearby_search_without_seed_url():
    city = "San Jose, CA"
    neighbors = nearby_cities(city)
    course_url = (
        "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
    )
    urls = {
        city: "https://club.example.org/adults",
        neighbors[0]: course_url,
        neighbors[1]: "https://other.example.org/adults",
    }
    now = datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc)
    fixture = (
        Path(__file__).parent / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
    ).read_text()

    async def search(city, level, year, focus):
        return [{"url": urls[city], "title": "Adult tennis courses"}]

    async def read(url):
        if url == course_url:
            return BrowserDocument(url, "Course", fixture, now)
        return Page(url, "Courses", "No dated course sessions on this page", ())

    searcher = AsyncMock(side_effect=search)
    backend = PublicSourceBackend(
        Mock(extract=AsyncMock(return_value=())), reader=read, searcher=searcher, clock=lambda: now
    )
    calls = iter(
        [
            *(
                ToolCallPart("search_courses", {"city": city, "focus": focus})
                for focus in ("overview", "registration", "schedule")
            ),
            ToolCallPart("read_source", {"source_id": identity(urls[city])}),
            *(ToolCallPart("search_courses", {"city": nearby}) for nearby in neighbors),
            *(ToolCallPart("read_source", {"source_id": identity(url)}) for url in urls.values()),
            ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
        ]
    )

    def respond(messages, info):
        return ModelResponse([next(calls)])

    request = DiscoveryRequest(
        city=city, level="3.0", start=date(2026, 11, 1), timezone="America/Los_Angeles"
    )
    run = asyncio.run(
        run_discovery_agent(request, backend, FunctionModel(respond), limits=LIVE_DISCOVERY_LIMITS)
    )
    assert backend.initial_urls == () and course_url not in request.model_dump_json()
    assert [call.args[0] for call in searcher.await_args_list] == [city] * 3 + list(neighbors)
    assert run.partial and run.searched_cities == (city, *neighbors)
    assert len(run.sessions) == 1
    session = run.sessions[0]
    assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
    assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
    assert session.price_usd == 54 and session.availability == "full"
    assert session.waitlist_count is None
