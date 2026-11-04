from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import FIXTURE, ReplayBackend, request, score
from benchmarks.source_discovery import BROWSER_URL, CAPTURE_TIME
from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    Completion,
    DiscoveryLimits,
    ResearchState,
    SourceDocument,
    SourceUnavailable,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.public_web import Page, PageLink

URL = "https://club.example.org/adult-tennis"


def backend(*, required=False):
    page = Page(
        URL,
        "Adult tennis registration",
        "Published adult clinics and links to registration details.",
        (BROWSER_URL,),
        link_details=(PageLink(BROWSER_URL, "Adult clinic registration"),),
    )
    detail = BrowserDocument(
        BROWSER_URL,
        "Adult clinic",
        (
            Path(__file__).parent
            / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
        ).read_text(),
        CAPTURE_TIME,
    )

    async def read(url):
        assert url in {URL, BROWSER_URL}
        return page if url == URL else detail

    return PublicSourceBackend(
        AsyncMock(extract=AsyncMock(side_effect=UnexpectedModelBehavior("private model error"))),
        (URL,) if required else (),
        reader=AsyncMock(side_effect=read),
        searcher=AsyncMock(return_value=[{"url": URL, "title": page.title}]),
        clock=lambda: CAPTURE_TIME,
    )


@pytest.mark.benchmark
def test_extraction_deadline_keeps_observed_navigation_without_retrying_model():
    async def scenario():
        sources = backend()
        cancelled = 0

        async def stall(*args):
            nonlocal cancelled
            try:
                await asyncio.Future()
            finally:
                cancelled += 1

        sources.extractor.extract.side_effect = stall
        state = ResearchState(request(), sources, DiscoveryLimits(tool_seconds=0.1))
        await state.search_courses("San Jose")
        first = await state.read_source(identity(URL))
        assert first["reason"] == "evidence_invalid"
        assert [link["id"] for link in first["links"]] == [identity(BROWSER_URL)]
        assert not state.documents[identity(URL)].records
        assert not sources.details
        assert cancelled == 1
        assert state.read_attempts == 1
        assert [event.outcome for event in state.audit] == ["ok", "unavailable"]
        cached = await state.read_source(identity(URL))
        assert cached["cached"] and cached["next_source_ids"] == (identity(BROWSER_URL),)
        sources.extractor.extract.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("timeout", [False, True])
def test_original_anonymous_case_survives_optional_extraction_failure_only(required, timeout):
    async def scenario():
        sources = backend(required=required)
        limits = DiscoveryLimits(tool_seconds=1 if timeout else 20)
        if timeout:

            async def stall(*args):
                await asyncio.Future()

            sources.extractor.extract.side_effect = stall
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

        if required:
            with pytest.raises(AgentDiscoveryError, match="Required registration"):
                await run_discovery_agent(request(), sources, FunctionModel(respond), limits=limits)
            return
        run = await run_discovery_agent(request(), sources, FunctionModel(respond), limits=limits)
        expected = json.loads(FIXTURE.read_text())["expected"]
        assert len(run.sessions) == 1
        course = run.sessions[0].model_dump(mode="json")
        for name in ("starts_at", "ends_at", "price_usd", "availability"):
            assert course[name] == expected[name]
        assert course["waitlist_count"] is None and course["user_waitlist_position"] is None
        assert course["evidence_ids"] == [identity(BROWSER_URL)]
        result = sources.result(run, request())
        failed = next(source for source in result["sources"] if source["id"] == identity(URL))
        assert failed["status"] == "unavailable" and failed["failure_reason"] == "evidence_invalid"
        assert run.partial and result["partial"] and run.tool_calls == 3
        assert len(sources.details) == 1
        sources.extractor.extract.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", ["depth", "registry", "unrelated"])
@pytest.mark.parametrize("timeout", [False, True])
def test_navigation_failure_cannot_bypass_existing_link_limits(limit, timeout):
    async def scenario():
        sources = backend()
        if timeout:

            async def stall(*args):
                await asyncio.Future()

            sources.extractor.extract.side_effect = stall
        ref = sources.register(URL, "Adult tennis", 2 if limit == "depth" else 0)
        if limit == "registry":
            sources.source_limit = 1
        if limit == "unrelated":
            sources.reader = AsyncMock(
                return_value=Page(
                    URL,
                    "Adult tennis",
                    "Published course details and help.",
                    ("https://club.example.org/help",),
                )
            )
        with pytest.raises(SourceUnavailable) as caught:
            await sources.read(ref.id, request(), deadline=asyncio.get_running_loop().time() + 0.01)
        assert caught.value.reason == "evidence_invalid"
        assert identity(BROWSER_URL) not in sources.sources
        assert not sources.details

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/adults",
        "https://device.example.ts.net/adults",
        "https://club.example.org/adults?token=synthetic",
        "https://user:password@club.example.org/adults",
    ],
)
def test_failed_evidence_does_not_relax_navigation_validation(url):
    async def scenario():
        sources = backend()
        sources.reader = AsyncMock(
            return_value=Page(
                URL,
                "Adult tennis",
                "Published adult courses and registration.",
                (url,),
                link_details=(PageLink(url, "Registration"),),
            )
        )
        ref = sources.register(URL, "Adult tennis", 0)
        with pytest.raises(ValueError):
            await sources.read(ref.id, request())
        assert set(sources.sources) == {ref.id}
        assert not sources.details

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [ModelHTTPError(503, "test-model"), UsageLimitExceeded("budget"), asyncio.CancelledError()],
)
def test_linked_page_does_not_hide_model_outage_budget_or_cancellation(failure):
    async def scenario():
        sources = backend()
        sources.extractor.extract.side_effect = failure
        ref = sources.register(URL, "Adult tennis", 0)
        with pytest.raises(type(failure)):
            await sources.read(ref.id, request(), deadline=asyncio.get_running_loop().time() + 1)
        assert set(sources.sources) == {ref.id}
        assert not sources.details and sources.sources[ref.id].status == "not_read"

    asyncio.run(scenario())


def test_external_cancellation_during_extraction_retains_no_navigation_or_facts():
    async def scenario():
        sources = backend()
        started = asyncio.Event()

        async def stall(*args):
            started.set()
            await asyncio.Future()

        sources.extractor.extract.side_effect = stall
        state = ResearchState(request(), sources, DiscoveryLimits(tool_seconds=1))
        await state.search_courses("San Jose")
        task = asyncio.create_task(state.read_source(identity(URL)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert set(state.registry) == set(sources.sources) == {identity(URL)}
        assert not sources.details and not state.documents and not state.failed_sources
        assert [event.outcome for event in state.audit] == ["ok"]

    asyncio.run(scenario())


def test_enclosing_deadline_does_not_become_navigation_only_success():
    async def scenario():
        sources = backend()
        started = asyncio.Event()

        async def stall(*args):
            started.set()
            await asyncio.Future()

        sources.extractor.extract.side_effect = stall
        state = ResearchState(request(), sources, DiscoveryLimits(tool_seconds=1))
        await state.search_courses("San Jose")
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.03):
                await state.read_source(identity(URL))
        assert started.is_set()
        assert not sources.details and not state.documents and not state.failed_sources
        assert set(state.registry) == {identity(URL)}
        assert [event.outcome for event in state.audit] == ["ok"]

    asyncio.run(scenario())


def test_exhausted_extraction_deadline_does_not_start_model():
    async def scenario():
        sources = backend()
        ref = sources.register(URL, "Adult tennis", 0)
        result = await sources.read(
            ref.id, request(), deadline=asyncio.get_running_loop().time() - 1
        )
        assert result.evidence_unavailable and result.partial and not result.records
        assert [link.id for link in result.links] == [identity(BROWSER_URL)]
        sources.extractor.extract.assert_not_awaited()

    asyncio.run(scenario())


def test_unrelated_timeout_is_not_misreported_as_extraction_deadline():
    async def scenario():
        sources = backend()
        sources.extractor.extract.side_effect = TimeoutError("transport detail")
        ref = sources.register(URL, "Adult tennis", 0)
        with pytest.raises(TimeoutError):
            await sources.read(ref.id, request(), deadline=asyncio.get_running_loop().time() + 1)
        assert set(sources.sources) == {ref.id}
        assert not sources.details and sources.sources[ref.id].status == "not_read"

    asyncio.run(scenario())


def test_extractor_suppressing_its_deadline_cannot_publish_late_facts():
    async def scenario():
        sources = backend()

        async def late_result(*args):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                # An invalid late object must be discarded before any fact access.
                return (object(),)

        sources.extractor.extract.side_effect = late_result
        ref = sources.register(URL, "Adult tennis", 0)
        result = await sources.read(
            ref.id, request(), deadline=asyncio.get_running_loop().time() + 0.01
        )
        assert result.evidence_unavailable and result.partial and not result.records
        assert [link.id for link in result.links] == [identity(BROWSER_URL)]
        assert not sources.details

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["records", "coverage", "links"])
def test_navigation_only_document_cannot_claim_valid_facts_or_complete_coverage(invalid):
    original = ReplayBackend().documents["seasonal-schedule"]
    data = original.model_dump()
    data.update(evidence_unavailable=True, partial=True, records=())
    if invalid == "records":
        data["records"] = original.records
    elif invalid == "coverage":
        data["partial"] = False
    else:
        data.update(links=(), required_ids=())
    with pytest.raises(ValidationError, match="Unavailable evidence"):
        SourceDocument.model_validate(data)


@pytest.mark.benchmark
def test_replay_navigation_failure_keeps_original_oracle_and_partial_coverage():
    sources = ReplayBackend("navigation_failure")
    turns = iter(
        [
            ToolCallPart("search_courses", {"city": "San Jose"}),
            ToolCallPart("search_courses", {"city": "Santa Clara"}),
            ToolCallPart("read_source", {"source_id": "operator-overview"}),
            ToolCallPart("read_source", {"source_id": "seasonal-schedule"}),
            ToolCallPart("read_source", {"source_id": "dated-registration"}),
            ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
        ]
    )

    async def respond(messages, info):
        return ModelResponse([next(turns)])

    run = asyncio.run(run_discovery_agent(request(), sources, FunctionModel(respond)))
    assert all(score(run, "navigation_failure").values())
    assert score(run, "baseline")["facts"] and score(run, "baseline")["evidence"]
    assert not score(run.model_copy(update={"partial": False}), "navigation_failure")[
        "partial_coverage"
    ]
    assert not score(run.model_copy(update={"audit": ()}), "navigation_failure")[
        "navigation_failure_retained"
    ]


def test_navigation_without_any_valid_facts_cannot_publish_empty_success():
    async def scenario():
        sources = backend()
        state = ResearchState(request(), sources, DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.read_source(identity(URL))
        sources.reader.side_effect = SourceUnavailable()
        await state.read_source(identity(BROWSER_URL))
        with pytest.raises(AgentDiscoveryError, match="Sources unavailable"):
            state.complete(Completion(outcome="no_results"))
        assert not sources.details

    asyncio.run(scenario())


def test_failed_evidence_retains_only_navigation_and_is_cached_without_retry():
    async def scenario():
        sources = backend()
        state = ResearchState(request(), sources, DiscoveryLimits())
        await state.search_courses("San Jose")
        first = await state.read_source(identity(URL))
        second = await state.read_source(identity(URL))
        assert second["cached"] and second["next_source_ids"] == (identity(BROWSER_URL),)
        assert second["reason"] == first["reason"]
        assert "links" not in second
        assert first["reason"] == "evidence_invalid"
        assert [link["id"] for link in first["links"]] == [identity(BROWSER_URL)]
        assert "candidate_session_count" not in first
        assert "text" not in first and "private model error" not in json.dumps(first)
        assert identity(BROWSER_URL) in state.registry
        assert identity(URL) in state.failed_sources
        assert not state.documents[identity(URL)].records
        assert [event.outcome for event in state.audit[-2:]] == ["unavailable", "cached"]
        assert state.read_attempts == 1
        sources.reader.assert_awaited_once()
        sources.extractor.extract.assert_awaited_once()

    asyncio.run(scenario())
