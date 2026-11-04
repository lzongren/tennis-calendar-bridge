from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

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


@pytest.mark.parametrize("depth,expected_links", [(1, 1), (2, 0), (3, 0)])
def test_depth_truncation_is_partial_without_expanding_ordinary_links(depth, expected_links):
    async def scenario():
        backend = make_backend()
        source = backend.register(ENTRY, "Registration entry", depth)
        result = await backend.read(source.id, QUERY)
        assert len(result.links) == expected_links
        assert result.partial is (expected_links == 0)
        assert (identity(PORTAL) in backend.sources) is bool(expected_links)

    asyncio.run(scenario())
