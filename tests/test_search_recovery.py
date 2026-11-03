from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx2 as httpx
import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview.browser_client import BrowserClient
from tennis_overview.browser_protocol import BrowserLink, BrowserReply, BrowserRequest
from tennis_overview.browser_worker import child_reply, execute
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryRequest,
    TransientSourceError,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity

KEY = "synthetic-search-key-" + "x" * 32
URL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
NOW = datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc)
SEARCH = BrowserRequest(operation="search", city="Example City", level="3.0", year=2026)


@pytest.mark.parametrize("error_type", [DDGSException, RatelimitException, TimeoutException])
def test_worker_classifies_search_provider_failures_without_exporting_diagnostics(
    tmp_path, monkeypatch, error_type
):
    monkeypatch.setattr(
        "tennis_overview.public_web.search_courses",
        Mock(side_effect=error_type("private diagnostic")),
    )
    assert child_reply(SEARCH, tmp_path, None) == {"error": "search_unavailable"}


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, OSError])
def test_unknown_search_fault_is_not_retryable(tmp_path, monkeypatch, error_type):
    monkeypatch.setattr(
        "tennis_overview.public_web.search_courses",
        Mock(side_effect=error_type("private diagnostic")),
    )
    assert child_reply(SEARCH, tmp_path, None) == {"error": "worker_unavailable"}


@pytest.mark.parametrize(
    "operation,exit_code,expected",
    [
        ("search", 0, "search_unavailable"),
        ("read", 0, "worker_unavailable"),
        ("search", 1, "worker_unavailable"),
    ],
)
def test_process_search_error_is_bound_to_operation_and_successful_child_exit(
    tmp_path, monkeypatch, operation, exit_code, expected
):
    process = Mock(returncode=exit_code)
    process.poll.return_value = exit_code

    def spawn(*args, **kwargs):
        kwargs["stdout"].write(b'{"error":"search_unavailable"}')
        return process

    monkeypatch.setattr("tennis_overview.browser_worker.subprocess.Popen", spawn)
    request = SEARCH if operation == "search" else BrowserRequest(operation="read", url=URL)
    assert execute(request, tmp_path) == {"error": expected}


@pytest.mark.parametrize(
    "status,operation",
    [
        (503, "search"),
        (503, "read"),
        (200, "search"),
        (302, "search"),
        (401, "search"),
        (403, "search"),
        (500, "search"),
    ],
)
def test_client_only_retries_explicit_search_failure(status, operation):
    client = BrowserClient(
        "http://127.0.0.1:8134",
        SecretStr(KEY),
        httpx.MockTransport(
            lambda request: httpx.Response(status, json={"error": "search_unavailable"})
        ),
    )
    request = SEARCH if operation == "search" else BrowserRequest(operation="read", url=URL)
    expected = (
        TransientSourceError if (status, operation) == (503, "search") else AgentDiscoveryError
    )
    with pytest.raises(expected):
        asyncio.run(client.call(request))


@pytest.mark.parametrize(
    "code",
    [
        "worker_unavailable",
        "source_unavailable",
        "busy",
        "timeout",
        "login_required",
        "membership_required",
        "unknown",
    ],
)
def test_other_worker_failures_do_not_become_search_retries(code):
    client = BrowserClient(
        "http://127.0.0.1:8134",
        SecretStr(KEY),
        httpx.MockTransport(lambda request: httpx.Response(503, json={"error": code})),
    )
    with pytest.raises(AgentDiscoveryError):
        asyncio.run(client.call(SEARCH))


@pytest.mark.benchmark
@pytest.mark.parametrize("recovers", [True, False])
def test_real_anonymous_case_survives_only_bounded_search_recovery(monkeypatch, recovers):
    sleep = AsyncMock()
    monkeypatch.setattr("tennis_overview.discovery_agent.asyncio.sleep", sleep)
    html = (
        Path(__file__).parent / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
    ).read_text()
    queries = []
    reads = []

    def respond(request):
        payload = json.loads(request.content)
        if payload["operation"] == "search":
            queries.append(payload)
            if len(queries) == 1 or not recovers:
                return httpx.Response(503, json={"error": "search_unavailable"})
            reply = BrowserReply(kind="search", links=(BrowserLink(url=URL, title="Adult clinic"),))
        else:
            reads.append(payload["url"])
            reply = BrowserReply(kind="browser", url=URL, html=html, captured_at=NOW)
        return httpx.Response(200, json=reply.model_dump(mode="json"))

    client = BrowserClient("http://127.0.0.1:8134", SecretStr(KEY), httpx.MockTransport(respond))
    extractor = Mock()
    backend = PublicSourceBackend(
        extractor, reader=client.read, searcher=client.search, clock=lambda: NOW
    )
    query = DiscoveryRequest(
        city="Example City",
        level="3.0",
        start=date(2026, 11, 1),
        timezone="America/Los_Angeles",
        search_mode="source_check",
    )
    calls = iter(
        [
            ToolCallPart("search_courses", {"city": query.city}),
            ToolCallPart("read_source", {"source_id": identity(URL)}),
            ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
        ]
    )
    model = FunctionModel(lambda messages, info: ModelResponse([next(calls)]))
    if recovers:
        run = asyncio.run(run_discovery_agent(query, backend, model))
        assert reads == [URL] and len(run.sessions) == 1
        assert [event.outcome for event in run.audit if event.tool == "search_courses"] == [
            "retry",
            "ok",
        ]
        session = run.sessions[0]
        assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
        assert session.price_usd == 54 and session.availability == "full"
        assert session.waitlist_count is None
    else:
        with pytest.raises(AgentDiscoveryError, match="unavailable after retry"):
            asyncio.run(run_discovery_agent(query, backend, model))
        assert reads == [] and backend.sources == {}
    assert len(queries) == 2 and queries[0] == queries[1]
    sleep.assert_awaited_once_with(1)
    extractor.extract.assert_not_called()
