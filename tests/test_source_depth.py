from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.discovery_agent import DiscoveryRequest
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.public_web import Page, PageLink

DIRECTORY = "https://directory.example.org/tennis"
OPERATOR = "https://club.example.org/"
ENTRY = "https://club.example.org/register"
PORTAL = "https://app.courtreserve.com/Online/Portal/Index/12345"
DETAIL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
QUERY = DiscoveryRequest(
    city="Example City",
    level="2.5-3.0",
    start=date(2026, 11, 1),
    timezone="America/Los_Angeles",
    search_mode="area",
)


def make_backend():
    detail = BrowserDocument(
        DETAIL,
        "Clinic",
        (
            Path(__file__).parent
            / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
        ).read_text(),
        datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc),
    )
    pages = {
        DIRECTORY: Page(
            DIRECTORY,
            "Facility directory",
            "Public directory listing for adult tennis.",
            (OPERATOR,),
            link_details=(PageLink(OPERATOR, "Facility reference", "same_as"),),
        ),
        OPERATOR: Page(
            OPERATOR,
            "Adult tennis",
            "Register for adult tennis classes.",
            (ENTRY,),
            link_details=(PageLink(ENTRY, "Register now"),),
        ),
        ENTRY: Page(
            ENTRY,
            "Registration",
            "Visit this public registration portal.",
            (PORTAL,),
            link_details=(PageLink(PORTAL, "Registration portal"),),
        ),
        PORTAL: Page(
            PORTAL, "Adult classes", "Observed course details", (DETAIL,), required_links=(DETAIL,)
        ),
        DETAIL: detail,
    }
    backend = PublicSourceBackend(
        Mock(extract=AsyncMock(return_value=())),
        reader=AsyncMock(side_effect=lambda url: pages[url]),
        searcher=AsyncMock(return_value=[{"url": OPERATOR, "title": "Adult tennis"}]),
        refiner=AsyncMock(return_value=[{"url": OPERATOR, "title": "Adult tennis"}]),
        clock=lambda: detail.captured_at,
    )
    return backend


@pytest.mark.benchmark
@pytest.mark.parametrize("rediscovery", ["search", "refine"])
def test_direct_rediscovery_restores_original_course_path_within_existing_depth(rediscovery):
    async def scenario():
        backend = make_backend()
        root = backend.register(DIRECTORY, "Directory", 0)
        await backend.read(root.id, QUERY)
        operator = backend.sources[identity(OPERATOR)]
        original_ref = operator.ref
        assert operator.depth == 1
        if rediscovery == "search":
            await backend.search(QUERY.city, QUERY)
        else:
            await backend.refine(root.id, QUERY)
        assert operator.depth == 0
        assert operator.ref is original_ref
        await backend.read(operator.ref.id, QUERY)
        entry = await backend.read(identity(ENTRY), QUERY)
        assert entry.navigation_ids == (identity(PORTAL),)
        portal = await backend.read(identity(PORTAL), QUERY)
        assert portal.required_ids == (identity(DETAIL),)
        result = await backend.read(identity(DETAIL), QUERY)
        assert len(result.records) == 1
        record = result.records[0]
        assert record.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        assert record.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
        assert record.price_usd == 54 and record.availability == "full"
        assert record.waitlist_count is None
        assert record.source_id == identity(DETAIL)
        assert backend.sources[identity(PORTAL)].depth == 2
        assert backend.reader.await_count == 5

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("portal_available", [True, False])
def test_registration_hop_replay_requires_real_case_facts_and_handles_denied_portal(
    monkeypatch, portal_available
):
    from benchmarks import source_discovery
    from tennis_overview.discovery_agent import SourceUnavailable

    def respond(messages, info):
        tool = next((tool for tool in info.function_tools if tool.name == "read_source"), None)
        if tool is not None:
            source_id = tool.parameters_json_schema["properties"]["source_id"]["enum"][0]
            return ModelResponse([ToolCallPart("read_source", {"source_id": source_id})])
        refinement = next(
            (tool for tool in info.function_tools if tool.name == "refine_source"), None
        )
        if refinement is not None:
            source_id = refinement.parameters_json_schema["properties"]["source_id"]["enum"][0]
            return ModelResponse([ToolCallPart("refine_source", {"source_id": source_id})])
        return ModelResponse(
            [
                ToolCallPart(
                    "finish_discovery",
                    {
                        "outcome": "sources_checked" if portal_available else "no_results",
                    },
                )
            ]
        )

    @asynccontextmanager
    async def model(config):
        yield FunctionModel(respond)

    read = PublicSourceBackend.read

    async def denied_read(self, source_id, request, *, deadline=None):
        if source_id == identity(source_discovery.LOCATION_PORTAL) and not portal_available:
            raise SourceUnavailable("Membership required")
        return await read(self, source_id, request, deadline=deadline)

    monkeypatch.setattr(source_discovery, "private_model", model)
    monkeypatch.setattr(PublicSourceBackend, "read", denied_read)
    rows = asyncio.run(
        source_discovery.evaluate(
            None,
            live=False,
            repeats=1,
            case="registration-hop",
        )
    )
    assert all(rows[0]["checks"].values()) is portal_available
    assert rows[0]["read_sources"] == (5 if portal_available else 3)
    if portal_available:
        assert rows[0]["checks"]["original_registration_facts"]
        assert rows[0]["checks"]["clinic_identity"]
        assert rows[0]["courses"] == 1 and rows[0]["partial"]
        assert rows[0]["denied_tool_attempts"] == 0
    else:
        assert rows[0]["checks"] == {"completed": False}
        assert rows[0]["error"] == "AgentDiscoveryError"
        assert "courses" not in rows[0]


def test_registry_only_shortens_depth_and_preserves_observation_at_capacity():
    backend = make_backend()
    backend.source_limit = 1
    original = backend.register(OPERATOR, "Observed title", 2)
    source = backend.sources[original.id]
    source.status = "checked"
    source.page = Page(OPERATOR, "Observed page", "Public source evidence retained", ())
    observed = source.page
    assert backend.register(OPERATOR, "Replacement title", 0) is original
    assert source.depth == 0
    assert backend.register(OPERATOR, "Longer path", 3) is original
    assert source.depth == 0
    assert source.status == "checked" and source.page is observed
    assert not backend.truncated
    assert backend.register(ENTRY, "New source", 1) is None
    assert backend.truncated and len(backend.sources) == 1


@pytest.mark.benchmark
def test_anonymous_registration_hop_reaches_original_course_without_root_rediscovery():
    async def scenario():
        backend = make_backend()
        root = backend.register(DIRECTORY, "Directory", 0)
        await backend.read(root.id, QUERY)
        await backend.read(identity(OPERATOR), QUERY)
        entry = await backend.read(identity(ENTRY), QUERY)
        assert entry.navigation_ids == (identity(PORTAL),)
        assert backend.sources[identity(PORTAL)].depth == 3
        portal = await backend.read(identity(PORTAL), QUERY)
        assert portal.required_ids == (identity(DETAIL),)
        detail = await backend.read(identity(DETAIL), QUERY)
        assert len(detail.records) == 1
        record = detail.records[0]
        assert record.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        assert record.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
        assert record.price_usd == 54 and record.availability == "full"
        assert record.waitlist_count is None and record.source_id == identity(DETAIL)
        assert backend.reader.await_count == 5
        backend.searcher.assert_not_awaited()
        backend.refiner.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["area", "source_check"])
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_registration_hop_remains_bounded_and_area_only(mode, depth):
    async def scenario():
        backend = make_backend()
        source = backend.register(ENTRY, "Registration entry", depth)
        expected_links = int(depth < 2 or mode == "area" and depth == 2)
        result = await backend.read(source.id, QUERY.model_copy(update={"search_mode": mode}))
        assert len(result.links) == expected_links
        assert result.partial is (expected_links == 0)
        assert (identity(PORTAL) in backend.sources) is bool(expected_links)

    asyncio.run(scenario())


@pytest.mark.parametrize("registration_count", [1, 8])
@pytest.mark.parametrize("source_limit", [1, 24])
def test_extra_hop_preserves_link_registry_and_ordinary_navigation_limits(
    registration_count, source_limit
):
    async def scenario():
        backend = make_backend()
        backend.source_limit = source_limit
        links = tuple(
            PageLink(f"https://portal.example.org/{index}", "Registration portal")
            for index in range(registration_count)
        ) + (PageLink("https://club.example.org/tennis-news", "Adult tennis news"),)
        backend.reader = AsyncMock(
            return_value=Page(
                ENTRY,
                "Registration",
                "Adult courses and news",
                tuple(link.url for link in links),
                link_details=links,
            )
        )
        source = backend.register(ENTRY, "Registration", 2)
        result = await backend.read(source.id, QUERY)
        assert len(result.links) == min(registration_count, 6, source_limit - 1)
        assert result.partial
        assert identity(links[-1].url) not in backend.sources
        assert all(backend.sources[ref.id].depth == 3 for ref in result.links)
        assert result.navigation_ids == tuple(ref.id for ref in result.links)
        assert not result.required_ids

    asyncio.run(scenario())
