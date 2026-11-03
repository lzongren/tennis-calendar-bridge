from __future__ import annotations

import asyncio
import os
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.sync_api import expect, sync_playwright

from tennis_overview.discovery_agent import (
    DiscoveryLimits,
    DiscoveryRequest,
    ResearchState,
    SourceRef,
    SourceUnavailable,
)
from tennis_overview.discovery_sources import PublicSourceBackend
from tennis_overview.discovery_ui import discovery_html
from tennis_overview.models import AppConfig, Config
from tennis_overview.public_web import Page

QUERY = DiscoveryRequest(
    city="Example City", start=date(2026, 11, 2), timezone="UTC", search_mode="source_check"
)
URL = "https://club.example.org/courses"


def test_unknown_source_diagnostics_are_not_exposed_to_the_model():
    async def scenario():
        error = SourceUnavailable("private diagnostic must not escape", reason="private-code")
        backend = Mock(read=AsyncMock(side_effect=error))
        state = ResearchState(QUERY, backend, DiscoveryLimits())
        state.register((SourceRef(id="source", title="Courses"),))
        result = await state.read_source("source")
        assert result["reason"] == "page_unavailable"
        assert "private" not in str(result)
        assert await state.read_source("source") == result
        backend.read.assert_awaited_once()

    asyncio.run(scenario())


def test_source_success_clears_failure_diagnostic():
    async def scenario():
        reader = AsyncMock(
            side_effect=[
                SourceUnavailable("private diagnostic"),
                Page(URL, "Courses", "No additional dated classes listed on this page", ()),
            ]
        )
        backend = PublicSourceBackend(Mock(extract=AsyncMock(return_value=())), reader=reader)
        source = backend.register(URL, "Courses", 0)
        with pytest.raises(SourceUnavailable):
            await backend.read(source.id, QUERY)
        assert backend.sources[source.id].failure_reason == "page_unavailable"
        await backend.read(source.id, QUERY)
        assert backend.sources[source.id].status == "checked"
        assert backend.sources[source.id].failure_reason is None

    asyncio.run(scenario())


def test_exhausted_tool_timeout_is_not_reported_as_uninspected():
    async def scenario():
        async def slow_read(url):
            await asyncio.sleep(60)
            raise AssertionError("Cancelled read must not continue")

        backend = PublicSourceBackend(Mock(), reader=slow_read)
        source = backend.register(URL, "Courses", 0)
        state = ResearchState(QUERY, backend, DiscoveryLimits(tool_seconds=0.01))
        state.register((source,))
        failure = await state.read_source(source.id)
        assert failure["reason"] == "page_unavailable"
        reported = backend.result(state.result(0), QUERY)["sources"][0]
        assert reported["status"] == "unavailable"
        assert reported["failure_reason"] == "page_unavailable"

    asyncio.run(scenario())


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_source_diagnostics_are_visible_without_confusing_unread_and_unavailable(width, height):
    config = Config(AppConfig(base_path="/tennis", timezone="UTC"), [], "test")
    sources = [
        ("Unvisited", "not_read", None, "Not inspected in this refresh"),
        ("Checked", "checked", None, "Page checked"),
        (
            "Member catalog",
            "unavailable",
            "membership_required",
            "Provider restricts this catalog to members",
        ),
        ("Login catalog", "unavailable", "login_required", "Sign-in required to read this source"),
        (
            "Verification",
            "unavailable",
            "browser_attention_required",
            "Website verification required",
        ),
        ("Evidence", "unavailable", "evidence_invalid", "Course details could not be validated"),
        ("Legacy", "unavailable", None, "Could not read this page"),
        ("Unknown", "unavailable", "private diagnostic", "Could not read this page"),
    ]
    payload = {
        "enabled": True,
        "locations": [
            {
                "id": 1,
                "query": "Example City",
                "level": "3.0",
                "timezone": "UTC",
                "status": "ready",
                "result": {
                    "courses": [],
                    "partial": True,
                    "checked_at": "2026-11-03T08:00:00Z",
                    "sources": [
                        {
                            "id": str(index),
                            "url": URL,
                            "title": title,
                            "status": status,
                            "failure_reason": reason,
                        }
                        for index, (title, status, reason, _) in enumerate(sources)
                    ],
                },
            }
        ],
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.abort())
        page.route(
            "https://bridge.example/tennis/discover",
            lambda route: route.fulfill(content_type="text/html", body=discovery_html(config)),
        )
        page.route(
            "https://bridge.example/tennis/api/discovery", lambda route: route.fulfill(json=payload)
        )
        page.goto("https://bridge.example/tennis/discover")
        expect(page.locator("#sources .source")).to_have_count(len(sources))
        for index, (_, _, _, label) in enumerate(sources):
            expect(page.locator("#sources .source").nth(index)).to_contain_text(label)
        expect(page.locator("#sources")).not_to_contain_text("private diagnostic")
        expect(page.locator("#sources .notice")).to_have_text(
            "Partial results: not all sources or sessions were verified."
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()
