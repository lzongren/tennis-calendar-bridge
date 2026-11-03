from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx2 as httpx
import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import Error as PlaywrightError
from pydantic import SecretStr

from tennis_overview.browser_client import BrowserClient
from tennis_overview.browser_network import Resource
from tennis_overview.browser_protocol import SOURCE_ERRORS, BrowserReply, BrowserRequest
from tennis_overview.browser_runtime import BrowserReadError, read_browser
from tennis_overview.browser_worker import child_reply, execute
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    Completion,
    DiscoveryLimits,
    DiscoveryRequest,
    ResearchState,
    SourceUnavailable,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity

KEY = "synthetic-browser-key-" + "x" * 32
URL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
BAD = "https://app.courtreserve.com/Online/Events/List/12345"
NOW = datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc)
REQUEST = DiscoveryRequest(
    city="Example City",
    level="3.0",
    timezone="America/Los_Angeles",
    start=date(2026, 11, 1),
    search_mode="source_check",
)


def client_with_error(code, status=503):
    return BrowserClient(
        "http://127.0.0.1:8134",
        SecretStr(KEY),
        httpx.MockTransport(lambda request: httpx.Response(status, json={"error": code})),
    )


@pytest.mark.parametrize("code", sorted(SOURCE_ERRORS))
def test_only_read_failures_are_source_scoped(code):
    client = client_with_error(code)
    with pytest.raises(SourceUnavailable, match=code):
        asyncio.run(client.read(URL))
    with pytest.raises(AgentDiscoveryError):
        asyncio.run(client.search("Example City", "3.0", 2026, "overview"))


@pytest.mark.parametrize("status", [200, 302, 401, 403, 500])
def test_page_error_label_cannot_hide_protocol_or_auth_failure(status):
    with pytest.raises(AgentDiscoveryError):
        asyncio.run(client_with_error("page_unavailable", status).read(URL))


@pytest.mark.parametrize(
    "error,expected",
    [
        (BrowserReadError("page_unavailable"), "page_unavailable"),
        (BrowserReadError("membership_required"), "membership_required"),
        (BrowserReadError("private diagnostic"), "worker_unavailable"),
        (RuntimeError("private diagnostic"), "worker_unavailable"),
        (OSError("private diagnostic"), "worker_unavailable"),
    ],
)
def test_child_only_exports_fixed_error_codes(tmp_path, monkeypatch, error, expected):
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.read_browser", AsyncMock(side_effect=error)
    )
    assert child_reply(BrowserRequest(operation="read", url=URL), tmp_path, None) == {
        "error": expected
    }


@pytest.mark.parametrize(
    "payload,exit_code,expected",
    [
        (b'{"error":"page_unavailable"}', 0, "page_unavailable"),
        (b'{"error":"worker_unavailable"}', 0, "worker_unavailable"),
        (b'{"error":"page_unavailable"}', 1, "worker_unavailable"),
        (b'{"error":"private diagnostic"}', 0, "worker_unavailable"),
        (b'{"error":[]}', 0, "worker_unavailable"),
        (b"not json", 0, "worker_unavailable"),
        (b"x" * 192001, 0, "worker_unavailable"),
    ],
)
def test_process_failures_never_look_like_page_failures(
    tmp_path, monkeypatch, payload, exit_code, expected
):
    process = Mock(returncode=exit_code)
    process.poll.return_value = exit_code

    def spawn(*args, **kwargs):
        kwargs["stdout"].write(payload)
        return process

    monkeypatch.setattr("tennis_overview.browser_worker.subprocess.Popen", spawn)
    assert execute(BrowserRequest(operation="read", url=URL), tmp_path) == {"error": expected}


@pytest.mark.parametrize(
    "error,expected",
    [
        (BrowserReadError("page_unavailable"), "page_unavailable"),
        (PlaywrightTimeoutError("private page text"), "page_unavailable"),
        (TimeoutError("private page text"), "page_unavailable"),
        (RuntimeError("private internal fault"), "worker_unavailable"),
    ],
)
def test_read_timeout_is_scoped_but_internal_fault_is_not(tmp_path, monkeypatch, error, expected):
    async def fail(context, request, network):
        if isinstance(error, RuntimeError):
            # Earlier page-level observations must not mask a later internal fault.
            network.denied = network.login_required = network.membership_required = True
        raise error

    monkeypatch.setattr("tennis_overview.browser_runtime._read", fail)
    with pytest.raises(BrowserReadError, match="^" + expected + "$"):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL),
                tmp_path / "unused",
                channel=os.getenv("PLAYWRIGHT_CHANNEL") or "chromium",
            )
        )


def test_disconnected_browser_is_fatal_even_after_page_denial(tmp_path, monkeypatch):
    async def fail(context, request, network):
        network.denied = network.login_required = True
        await context.browser.close()
        raise PlaywrightError("private browser failure")

    monkeypatch.setattr("tennis_overview.browser_runtime._read", fail)
    with pytest.raises(BrowserReadError, match="^worker_unavailable$"):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL),
                tmp_path / "unused",
                channel=os.getenv("PLAYWRIGHT_CHANNEL") or "chromium",
            )
        )


@pytest.mark.parametrize("status", [404, 410, 429, 500, 503])
def test_public_http_failure_is_not_a_successful_empty_page(tmp_path, monkeypatch, status):
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(
            status,
            {"content-type": "text/html"},
            b"<h1>This page is unavailable, please try again later</h1>",
        ),
    )
    with pytest.raises(BrowserReadError, match="^page_unavailable$"):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url="https://club.example.org/classes"),
                tmp_path / "unused",
                channel=os.getenv("PLAYWRIGHT_CHANNEL") or "chromium",
            )
        )


@pytest.mark.benchmark
@pytest.mark.parametrize("failure", ["page_unavailable", "membership_required"])
@pytest.mark.parametrize("scope", ["optional", "required", "all"])
def test_anonymous_real_case_survives_only_optional_page_failure(failure, scope):
    html = (
        Path(__file__).parent / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
    ).read_text()

    def respond(request):
        url = json.loads(request.content)["url"]
        if url == BAD or scope == "all":
            return httpx.Response(503, json={"error": failure})
        reply = BrowserReply(kind="browser", url=URL, html=html, captured_at=NOW)
        return httpx.Response(200, json=reply.model_dump(mode="json"))

    client = BrowserClient("http://127.0.0.1:8134", SecretStr(KEY), httpx.MockTransport(respond))
    extractor = Mock()
    backend = PublicSourceBackend(
        extractor,
        initial_urls=(BAD,) if scope == "required" else (),
        reader=client.read,
        searcher=AsyncMock(return_value=[{"url": url, "title": "Course"} for url in [BAD, URL]]),
        clock=lambda: NOW,
    )

    async def scenario():
        state = ResearchState(REQUEST, backend, DiscoveryLimits())
        await state.search_courses(REQUEST.city)
        await state.read_source(identity(BAD))
        await state.read_source(identity(URL))
        assert identity(BAD) in state.failed_sources
        if scope != "optional":
            with pytest.raises(AgentDiscoveryError):
                state.complete(Completion(outcome="sources_checked"))
            return
        state.complete(Completion(outcome="sources_checked"))
        run = state.result(0)
        assert run.partial and len(run.sessions) == 1
        session = run.sessions[0]
        assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
        assert session.price_usd == 54 and session.availability == "full"
        assert session.waitlist_count is None
        assert backend.sources[identity(BAD)].status == "unavailable"
        extractor.extract.assert_not_called()

    asyncio.run(scenario())
