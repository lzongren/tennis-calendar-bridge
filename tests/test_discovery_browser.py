"""Acceptance: public HTML -> discovery storage/API -> visible calendar entries."""

import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview import agent_discovery, discovery
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.models import AppConfig, Config
from tennis_overview.public_web import page_from_html
from tennis_overview.server import TennisServer

NOW = datetime(2026, 10, 25, 23, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


@pytest.fixture(params=["legacy", "agent"])
def populated_server(tmp_path, monkeypatch, request):
    fixtures = Path(__file__).parent / "fixtures"
    urls = ["https://club.example.org/ongoing", "https://academy.example.org/terms"]
    pages = {
        url: page_from_html(url, (fixtures / name).read_bytes())
        for url, name in zip(urls, ["discovery_ongoing.html", "discovery_terms.html"])
    }
    monkeypatch.setattr(discovery, "datetime", FrozenDateTime)
    monkeypatch.setattr(discovery, "fetch_page", lambda url: pages[url])
    monkeypatch.setattr(discovery, "search_courses", lambda *a: [])
    monkeypatch.setattr(
        discovery, "extract", lambda *a: pytest.fail("Structured schedule must not depend on LLM")
    )
    monkeypatch.setattr(discovery.DiscoveryService, "start", lambda *a: None)
    if request.param == "agent":
        details = pages[urls[1]].links
        for url in details:
            pages[url] = page_from_html(
                url,
                b"<p>Public registration information without additional dated sessions for this fixture.</p>",
            )
        calls = iter(
            [
                ToolCallPart("search_courses", {"city": "Example City"}),
                *(
                    ToolCallPart("read_source", {"source_id": identity(url)})
                    for url in (*urls, *details)
                ),
                ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
            ]
        )

        async def respond(messages, info):
            if any(tool.name == "extract_courses" for tool in info.output_tools):
                return ModelResponse([ToolCallPart("extract_courses", {"courses": []})])
            return ModelResponse([next(calls)])

        @asynccontextmanager
        async def model_context(config):
            yield FunctionModel(respond)

        async def read(url):
            return pages[url]

        async def search(*args):
            return []

        monkeypatch.setattr(agent_discovery, "datetime", FrozenDateTime)
        monkeypatch.setattr(agent_discovery, "private_model", model_context)
        monkeypatch.setattr(
            agent_discovery,
            "PublicSourceBackend",
            lambda extractor, sources: PublicSourceBackend(
                extractor, sources, reader=read, searcher=search
            ),
        )
    config = Config(
        AppConfig(
            database_path=str(tmp_path / "test.db"),
            base_path="/tennis",
            timezone="America/Los_Angeles",
            discovery_enabled=True,
            discovery_agent_enabled=request.param == "agent",
            discovery_api_url="https://model.example.ts.net/v1",
            discovery_api_key="test-only-key",
            admin_token="test-admin",
        ),
        [],
        "test",
    )
    server = TennisServer(("127.0.0.1", 0), config)
    server.discovery.add(
        {
            "query": "Example City",
            "level": "3.0",
            "sources": urls,
            "timezone": "America/Los_Angeles",
        }
    )
    assert server.discovery.run_one()
    assert server.discovery.list()[0]["status"] == "ready"
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_realistic_source_html_reaches_calendar_and_week_navigation(
    populated_server, width, height
):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
        page = browser.new_page(viewport={"width": width, "height": height}, locale="en-US")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.install(time=NOW)
        page.goto(f"http://127.0.0.1:{populated_server.server_port}/tennis/discover")
        expect(page.locator("#range")).to_have_text("Oct 26 - Nov 1")
        expect(page.locator("#calendar .course")).to_have_count(4)
        wed = page.locator('[data-start="2026-10-28T19:00:00-07:00"]')
        expect(wed).to_be_visible()
        expect(wed.locator("time")).to_have_text("7:00 PM - 8:00 PM")
        expect(wed).to_contain_text("Weekly timetable")
        sat = page.locator('[data-start="2026-10-31T10:00:00-07:00"]')
        expect(sat).to_contain_text("Year inferred")
        expect(sat.locator("a")).to_have_attribute(
            "href", "https://registration.example.org/Coaching/Book/saturday-term"
        )
        expect(page.locator("#pending article")).to_have_count(2)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="Next week", exact=True).click()
        expect(page.locator("#range")).to_have_text("Nov 2 - Nov 8")
        expect(page.locator('[data-start="2026-11-04T19:00:00-08:00"]')).to_be_visible()
        if width > 760:
            page.get_by_role("button", name="List", exact=True).click()
            expect(page.locator("#calendar")).to_have_class("list")
            expect(page.locator("#calendar .course")).to_have_count(4)
        assert errors == []
        browser.close()
