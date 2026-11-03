from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import httpx2 as httpx
import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import FIXTURE, request
from benchmarks.source_discovery import BROWSER_URL, CAPTURE_TIME
from tennis_overview.browser_client import BrowserClient
from tennis_overview.browser_network import Resource
from tennis_overview.browser_protocol import BrowserLink, BrowserReply, BrowserRequest, read_url
from tennis_overview.browser_runtime import read_browser
from tennis_overview.discovery_agent import SourceUnavailable, run_discovery_agent
from tennis_overview.discovery_sources import PublicSourceBackend, identity

URL = "https://club.example.org/adults"
CHANNEL = os.getenv("PLAYWRIGHT_CHANNEL") or "chromium"
KEY = SecretStr("synthetic-browser-key-for-tests-only")


async def rendered_reply(body, monkeypatch, tmp_path):
    calls = []

    def fetch(url, headers, *, portal_session):
        calls.append((url, portal_session))
        return Resource(200, {"content-type": "text/html"}, body.encode())

    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    profile = tmp_path / "unused-profile"
    reply = await read_browser(BrowserRequest(operation="read", url=URL), profile, channel=CHANNEL)
    assert not profile.exists()
    assert calls and all(url == URL and not session for url, session in calls)
    return reply


@pytest.mark.benchmark
def test_public_outlink_filter_preserves_original_course_navigation(monkeypatch, tmp_path):
    # Sanitized shape from the live failure: a readable course link beside HTTP chrome.
    body = f"""<h1>Adult tennis courses</h1><p>Published adult group clinic details.</p>
    <a href="http://club.example.org/tennis">Tennis home</a>
    <a href="https://club.example.org/checkout">Checkout</a>
    <a href="{BROWSER_URL}">Adult clinic registration</a>"""

    async def scenario():
        reply = await rendered_reply(body, monkeypatch, tmp_path)
        assert [link.url for link in reply.links] == [BROWSER_URL]
        fixture = (
            Path(__file__).parent
            / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
        ).read_text()
        detail = BrowserReply(
            kind="browser", url=BROWSER_URL, html=fixture, captured_at=CAPTURE_TIME
        )

        def respond_http(req):
            target = json.loads(req.content)["url"]
            assert target in {URL, BROWSER_URL}
            return httpx.Response(
                200, json=(reply if target == URL else detail).model_dump(mode="json")
            )

        client = BrowserClient("http://127.0.0.1:8134", KEY, httpx.MockTransport(respond_http))
        monkeypatch.setattr(
            "tennis_overview.browser_client.read_public_document",
            AsyncMock(side_effect=SourceUnavailable()),
        )
        backend = PublicSourceBackend(
            AsyncMock(extract=AsyncMock(return_value=())),
            (URL,),
            reader=client.read,
            searcher=AsyncMock(return_value=[]),
            clock=lambda: CAPTURE_TIME,
        )
        turns = iter(
            [
                ToolCallPart("search_courses", {"city": "San Jose"}),
                ToolCallPart("read_source", {"source_id": identity(URL)}),
                ToolCallPart("read_source", {"source_id": identity(BROWSER_URL)}),
                ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
            ]
        )

        async def respond(messages, info):
            return ModelResponse([next(turns)])

        run = await run_discovery_agent(request(), backend, FunctionModel(respond))
        expected = json.loads(FIXTURE.read_text())["expected"]
        assert len(run.sessions) == 1
        course = run.sessions[0].model_dump(mode="json")
        for name in ("starts_at", "ends_at", "price_usd", "availability"):
            assert course[name] == expected[name]
        assert course["waitlist_count"] is None  # Anonymous detail does not disclose the queue.
        assert course["user_waitlist_position"] is None
        assert course["evidence_ids"]
        assert run.tool_calls == 3 and run.partial

    asyncio.run(scenario())


def test_unreadable_links_do_not_consume_worker_output_budget(monkeypatch, tmp_path):
    body = "<h1>Adult tennis</h1><p>Published adult group course registration links.</p>"
    body += "".join(f'<a href="http://club.example.org/{i}">Home</a>' for i in range(25))
    body += "".join(f'<a href="https://club.example.org/clinic/{i}">Clinic</a>' for i in range(30))
    reply = asyncio.run(rendered_reply(body, monkeypatch, tmp_path))
    assert [link.url for link in reply.links] == [
        f"https://club.example.org/clinic/{i}" for i in range(24)
    ]
    assert all(read_url(link.url) == link.url for link in reply.links)


@pytest.mark.parametrize(
    "link",
    [
        "http://club.example.org/adults",
        "https://club.example.org/checkout",
        "https://club.example.org/?token=synthetic",
        "https://device.example.ts.net/",
        "https://app.courtreserve.com/Online/Account/LogIn/12345",
    ],
)
def test_client_still_rejects_unreadable_links_from_a_malformed_worker(monkeypatch, link):
    reply = BrowserReply(
        kind="page", url=URL, text="Adult tennis", links=(BrowserLink(url=link, title="Tennis"),)
    )
    client = BrowserClient("http://127.0.0.1:8134", KEY)
    monkeypatch.setattr(client, "call", AsyncMock(return_value=reply))
    monkeypatch.setattr(
        "tennis_overview.browser_client.read_public_document",
        AsyncMock(side_effect=SourceUnavailable()),
    )
    with pytest.raises(ValueError):
        asyncio.run(client.read(URL))
