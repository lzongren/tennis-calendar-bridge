from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx2 as httpx
import pytest
from pydantic import SecretStr, ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.browser_client import BrowserClient
from tennis_overview.browser_protocol import BrowserRequest
from tennis_overview.browser_worker import child_reply
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    DiscoveryRequest,
    ResearchState,
    SourceUnavailable,
    TransientSourceError,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.public_web import Page, PageLink, search_courses, search_subject

DIRECTORY = "https://directory.example.org/tennis/operator"
OPERATOR = "https://operator.example.org/adult-tennis"
DETAIL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
FIXTURES = Path(__file__).parent / "fixtures/discovery_benchmark"
NOW = datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc)


def request():
    return DiscoveryRequest(
        city="Example City",
        level="2.5-3.0",
        start=date(2026, 11, 1),
        timezone="America/Los_Angeles",
        search_mode="area",
    )


def backend_for(page=None, **kwargs):
    return PublicSourceBackend(
        Mock(extract=AsyncMock(return_value=())),
        reader=AsyncMock(
            return_value=page
            or Page(
                DIRECTORY,
                "Untrusted search title",
                "Public directory with no course details.",
                (),
                heading="Example Activities: Example City",
            )
        ),
        searcher=AsyncMock(return_value=[{"url": DIRECTORY, "title": "Spoofed search title"}]),
        refiner=AsyncMock(return_value=[{"url": OPERATOR, "title": "Adult tennis lessons"}]),
        **kwargs,
    )


async def checked_state(backend):
    state = ResearchState(request(), backend, DiscoveryLimits())
    await state.search_required()
    await state.read_source(identity(DIRECTORY))
    return state


@pytest.mark.benchmark
def test_anonymous_directory_refinement_reaches_original_course_facts_without_seed():
    async def scenario():
        backend = backend_for(clock=lambda: NOW)
        directory = backend.reader.return_value

        async def read(url):
            if url == DIRECTORY:
                return directory
            if url == OPERATOR:
                return Page(
                    url,
                    "Adult lessons",
                    "Dated adult clinic registration.",
                    (DETAIL,),
                    link_details=(PageLink(DETAIL, "Adult clinic"),),
                    required_links=(DETAIL,),
                )
            assert url == DETAIL
            return BrowserDocument(
                url, "Course", (FIXTURES / "courtreserve_anonymous_detail.html").read_text(), NOW
            )

        backend.reader = AsyncMock(side_effect=read)
        steps = iter(
            [
                ("refine_source", {"source_id": identity(DIRECTORY)}),
                ("finish_discovery", {"outcome": "sources_checked"}),
            ]
        )

        def respond(messages, info):
            name, args = next(steps)
            if name == "refine_source":
                tool = next(tool for tool in info.function_tools if tool.name == name)
                schema = tool.parameters_json_schema
                assert set(schema["properties"]) == {"source_id"}
                assert schema["properties"]["source_id"]["enum"] == [identity(DIRECTORY)]
            return ModelResponse([ToolCallPart(name, args)])

        run = await run_discovery_agent(request(), backend, FunctionModel(respond))
        assert backend.initial_urls == () and DETAIL not in request().model_dump_json()
        assert [call.args[0] for call in backend.reader.call_args_list] == [
            DIRECTORY,
            OPERATOR,
            DETAIL,
        ]
        backend.refiner.assert_awaited_once_with(
            "Example Activities Example City", "Example City", 2026
        )
        assert len(run.sessions) == 1
        session = run.sessions[0]
        assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
        assert session.price_usd == 54 and session.availability == "full"
        assert session.waitlist_count is None
        assert run.partial

    asyncio.run(scenario())


def test_refinement_uses_checked_metadata_shared_budget_and_cache():
    async def scenario():
        backend = backend_for()
        state = await checked_state(backend)
        sid = identity(DIRECTORY)
        assert state.refinement_ids() == (sid,)
        assert "Example Activities" not in state.progress_instructions()
        result = await state.refine_source(sid)
        assert await state.refine_source(sid) == result
        assert state.search_attempts == 4 and not state.refinement_ids()
        backend.refiner.assert_awaited_once_with(
            "Example Activities Example City", "Example City", 2026
        )
        assert state.audit[-1].outcome == "cached"
        assert identity(OPERATOR) in state.registry
        state.allow_cities(("Nearby City",))
        with pytest.raises(AgentDiscoveryError, match="search budget"):
            await state.search_courses("Nearby City")

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["unknown", "unread", "failed", "source_check", "budget"])
def test_ineligible_refinement_never_reaches_network(mode):
    async def scenario():
        backend = backend_for()
        state = await checked_state(backend)
        sid = identity(DIRECTORY)
        if mode == "unknown":
            sid = "unknown"
        elif mode == "unread":
            state.documents.clear()
        elif mode == "failed":
            state.failed_sources.add(sid)
        elif mode == "source_check":
            state.request = state.request.model_copy(update={"search_mode": "source_check"})
        else:
            state.search_attempts = state.limits.searches
        with pytest.raises(ModelRetry):
            await state.refine_source(sid)
        backend.refiner.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [TransientSourceError, TimeoutError])
@pytest.mark.parametrize("recovers", [True, False])
def test_refinement_preserves_bounded_retry(monkeypatch, failure, recovers):
    monkeypatch.setattr("tennis_overview.discovery_agent.asyncio.sleep", AsyncMock())

    async def scenario():
        backend = backend_for()
        backend.refiner.side_effect = [
            failure("private diagnostics"),
            [] if recovers else failure(),
        ]
        state = await checked_state(backend)
        if recovers:
            assert not (await state.refine_source(identity(DIRECTORY))).sources
        else:
            with pytest.raises(AgentDiscoveryError, match="Public search unavailable"):
                await state.refine_source(identity(DIRECTORY))
        assert backend.refiner.await_count == 2
        assert state.search_attempts == 4

    asyncio.run(scenario())


def test_refinement_cancellation_is_not_retried():
    async def scenario():
        backend = backend_for()
        backend.refiner.side_effect = asyncio.CancelledError()
        state = await checked_state(backend)
        with pytest.raises(asyncio.CancelledError):
            await state.refine_source(identity(DIRECTORY))
        assert backend.refiner.await_count == 1

    asyncio.run(scenario())


def test_refinement_timeout_cancels_and_reaps_each_attempt(monkeypatch):
    monkeypatch.setattr("tennis_overview.discovery_agent.asyncio.sleep", AsyncMock())

    async def scenario():
        backend = backend_for()
        state = await checked_state(backend)
        state.limits = state.limits.model_copy(update={"tool_seconds": 0.01})
        cancelled = 0

        async def stalled(*args):
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            finally:
                cancelled += 1

        backend.refiner.side_effect = stalled
        with pytest.raises(AgentDiscoveryError, match="Public search unavailable"):
            await state.refine_source(identity(DIRECTORY))
        assert cancelled == 2 and backend.refiner.await_count == 2
        assert state.search_attempts == 4

    asyncio.run(scenario())


def test_backend_rejects_unread_source_and_caps_public_results():
    async def scenario():
        backend = backend_for()
        await backend.search("Example City", request())
        with pytest.raises(SourceUnavailable):
            await backend.refine(identity(DIRECTORY), request())
        backend.refiner.assert_not_awaited()
        await backend.read(identity(DIRECTORY), request())
        backend.refiner.return_value = [
            {"url": "http://127.0.0.1/", "title": "Private"},
            {"url": "https://club.example.org/?token=secret", "title": "Secret"},
            {"url": DIRECTORY, "title": "Self"},
            *[
                {"url": f"https://operator.example.org/{i}", "title": "Adult tennis"}
                for i in range(7)
            ],
        ]
        result = await backend.refine(identity(DIRECTORY), request())
        assert len(result.sources) == 4 and result.partial and backend.truncated
        assert all(
            source.url.startswith("https://operator.example.org/") for source in result.sources
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("heading", ["", "x", "word " * 17, "x" * 121])
def test_unusable_metadata_does_not_offer_refinement(heading):
    async def scenario():
        page = Page(
            DIRECTORY, heading, "No adult class schedule on this public page", (), heading=heading
        )
        state = await checked_state(backend_for(page))
        assert not state.refinement_ids()

    asyncio.run(scenario())


def test_registration_navigation_is_not_replaced_by_refinement():
    async def scenario():
        page = Page(
            DIRECTORY,
            "Operator directory",
            "Adult registration is linked here",
            (DETAIL,),
            required_links=(DETAIL,),
        )
        state = await checked_state(backend_for(page))
        assert not state.refinement_ids()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "subject", ["site:private.example", "https://example.org/", "word\nword", "x" * 121]
)
def test_protocol_rejects_arbitrary_search_syntax(subject):
    with pytest.raises(ValidationError):
        BrowserRequest(
            operation="search",
            city="Example City",
            year=2026,
            focus="registration",
            subject=subject,
        )


def test_search_subject_is_plain_bounded_words_and_fixed_query(monkeypatch):
    subject = search_subject("Example Activities: Example City | Adult Tennis")
    assert subject == "Example Activities Example City Adult Tennis"
    search = Mock(return_value=[])
    monkeypatch.setattr("tennis_overview.search_layouts.search_index", search)
    assert search_courses("Example City", "3.0", 2026, "registration", subject=subject) == []
    search.assert_called_once_with(subject + " Example City adult tennis clinics 2026")


def test_worker_refinement_is_search_only(tmp_path, monkeypatch):
    search = Mock(return_value=[])
    read = AsyncMock()
    monkeypatch.setattr("tennis_overview.public_web.search_courses", search)
    monkeypatch.setattr("tennis_overview.browser_runtime.read_browser", read)
    subject = "Example Activities"
    query = BrowserRequest(
        operation="search", city="Example City", year=2026, focus="registration", subject=subject
    )
    assert child_reply(query, tmp_path, None)["kind"] == "search"
    search.assert_called_once_with("Example City", "", 2026, "registration", subject=subject)
    read.assert_not_called()
    with pytest.raises(ValidationError):
        BrowserRequest(operation="read", url=DETAIL, subject=subject)
    with pytest.raises(ValidationError):
        BrowserRequest(operation="search", city="Example City", year=2026, subject=subject)


def test_client_refines_at_fixed_private_endpoint_and_preserves_legacy_search_payload():
    key = "synthetic-test-key-" + "x" * 32
    requests = []

    def respond(request):
        assert str(request.url) == "https://worker.example.ts.net:8444/v1/read"
        assert request.headers["authorization"] == "Bearer " + key
        assert key not in request.content.decode()
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"kind": "search", "links": []})

    client = BrowserClient(
        "https://worker.example.ts.net:8444", SecretStr(key), httpx.MockTransport(respond)
    )
    asyncio.run(client.refine("Example Activities", "Example City", 2026))
    asyncio.run(client.search("Example City", "3.0", 2026, "overview"))
    assert requests[0]["subject"] == "Example Activities"
    assert requests[0]["operation"] == "search" and requests[0]["focus"] == "registration"
    assert "subject" not in requests[1]


@pytest.mark.benchmark
def test_reproducible_refinement_benchmark_checks_original_oracle(monkeypatch):
    from benchmarks import source_discovery

    def respond(messages, info):
        tool = next(tool for tool in info.function_tools if tool.name == "refine_source")
        source_id = tool.parameters_json_schema["properties"]["source_id"]["enum"][0]
        return ModelResponse([ToolCallPart("refine_source", {"source_id": source_id})])

    @asynccontextmanager
    async def model(config):
        yield FunctionModel(respond)

    monkeypatch.setattr(source_discovery, "private_model", model)
    rows = asyncio.run(
        source_discovery.evaluate(None, live=False, repeats=1, case="source-refinement")
    )
    assert all(rows[0]["checks"].values())
    assert rows[0]["read_sources"] == 3
