from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    ResearchState,
    SearchResults,
    SourceDocument,
    SourceRef,
    TransientSourceError,
    run_discovery_agent,
)

SLOW = SourceRef(id="slow-catalog", title="Additional public catalog")


class DeadlineBackend(ReplayBackend):
    def __init__(self, slow_id="slow-catalog"):
        super().__init__()
        self.slow_id = slow_id
        self.cancelled = 0

    async def search(self, city, query):
        result = await super().search(city, query)
        if result.sources:
            return SearchResults(sources=(*result.sources, SLOW))
        return result

    async def read(self, source_id, query):
        if source_id != self.slow_id:
            return await super().read(source_id, query)
        self.read_calls.append(source_id)
        try:
            await asyncio.Future()
        finally:
            self.cancelled += 1


def test_tool_deadline_is_not_a_transient_read_retry():
    async def scenario():
        backend = DeadlineBackend()
        state = ResearchState(request(), backend, DiscoveryLimits(tool_seconds=0.01))
        state.register((SLOW,))
        failure = await state.read_source(SLOW.id)
        assert failure["reason"] == "page_unavailable"
        assert backend.read_calls == [SLOW.id]
        assert backend.cancelled == 1
        assert [event.outcome for event in state.audit] == ["unavailable"]
        assert await state.read_source(SLOW.id) == failure
        assert backend.read_calls == [SLOW.id]

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("required", [False, True])
def test_original_course_survives_only_optional_source_deadline(required):
    async def scenario():
        backend = DeadlineBackend("dated-registration" if required else SLOW.id)
        calls = iter(
            [
                ToolCallPart("search_courses", {"city": "San Jose"}),
                ToolCallPart("search_courses", {"city": "Santa Clara"}),
                ToolCallPart("read_source", {"source_id": "seasonal-schedule"}),
                ToolCallPart("read_source", {"source_id": "dated-registration"}),
                *([] if required else [ToolCallPart("read_source", {"source_id": SLOW.id})]),
                ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
            ]
        )

        async def respond(messages, info):
            return ModelResponse([next(calls)])

        run = run_discovery_agent(
            request(), backend, FunctionModel(respond), limits=DiscoveryLimits(tool_seconds=0.02)
        )
        if required:
            with pytest.raises(AgentDiscoveryError, match="Required registration"):
                await run
        else:
            result = await run
            checks = score(result, "baseline")
            assert all(value for key, value in checks.items() if key != "complete")
            assert not checks["complete"]
            assert result.partial
            assert result.model_requests == 6
        assert backend.read_calls.count(backend.slow_id) == 1
        assert backend.cancelled == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("error", [TimeoutError, TransientSourceError])
def test_adapter_transient_timeout_still_retries_once(error):
    async def scenario():
        document = SourceDocument(source=SLOW, text="No dated sessions")
        backend = AsyncMock()
        backend.read.side_effect = [error("synthetic-private-error"), document]
        state = ResearchState(request(), backend, DiscoveryLimits(tool_seconds=1))
        state.register((SLOW,))
        result = await state.read_source(SLOW.id)
        assert result["source_id"] == SLOW.id
        assert backend.read.await_count == 2
        assert [event.outcome for event in state.audit] == ["retry", "ok"]
        assert "synthetic-private" not in str(result)

    asyncio.run(scenario())


def test_external_cancellation_does_not_retry_or_publish_source_failure():
    async def scenario():
        started = asyncio.Event()
        calls = 0

        async def read(*args):
            nonlocal calls
            calls += 1
            started.set()
            await asyncio.Future()

        backend = AsyncMock()
        backend.read.side_effect = read
        state = ResearchState(request(), backend, DiscoveryLimits(tool_seconds=1))
        state.register((SLOW,))
        task = asyncio.create_task(state.read_source(SLOW.id))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 1
        assert not state.documents and not state.failed_sources and not state.audit

    asyncio.run(scenario())
