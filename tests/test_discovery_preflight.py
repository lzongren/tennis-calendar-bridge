from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import SCENARIOS, ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    ResearchState,
    SourceUnavailable,
    TransientSourceError,
    run_discovery_agent,
)


class PreflightBackend(ReplayBackend):
    def __init__(self, case="baseline"):
        super().__init__(case)
        self.queries = []

    async def search(self, city, query):
        self.queries.append((city, query.search_focus))
        return await super().search(city, query)


EXPECTED_QUERIES = [
    ("San Jose", "overview"),
    ("San Jose", "registration"),
    ("San Jose", "schedule"),
    ("Santa Clara", "overview"),
]


@pytest.mark.benchmark
@pytest.mark.parametrize("case", SCENARIOS)
def test_area_preflight_preserves_real_case_without_model_search_turns(case):
    sources = PreflightBackend(case)
    reads = []
    if case == "navigation_failure":
        reads.append("operator-overview")
    reads.extend(["seasonal-schedule", "dated-registration"])
    if case == "date_conflict":
        reads.append("conflicting-record")

    async def respond(messages, info):
        assert sources.queries == EXPECTED_QUERIES
        prompt = next(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        payload = json.loads(prompt)
        completed = payload["completed_searches"]
        assert [(item["city"], item["focus"]) for item in completed] == EXPECTED_QUERIES
        assert completed[-1]["result"]["sources"][0]["id"] == reads[0]
        assert "Seasonal adult clinics" not in info.instructions
        tool = next(tool for tool in info.function_tools if tool.name == "read_source")
        source = tool.parameters_json_schema["properties"]["source_id"]["enum"][0]
        return ModelResponse([ToolCallPart("read_source", {"source_id": source})])

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}),
            sources,
            FunctionModel(respond),
        )
    )
    assert all(score(run, case).values())
    assert sources.queries == EXPECTED_QUERIES
    assert run.model_requests == (1 if case == "navigation_failure" else 0)
    assert run.tool_calls == len(EXPECTED_QUERIES) + len(reads)
    assert sum(event.tool == "search_courses" for event in run.audit) == 4


@pytest.mark.parametrize(
    "limits,searches", [(DiscoveryLimits(searches=2), 2), (DiscoveryLimits(tool_calls=3), 3)]
)
def test_preflight_charges_existing_limits_before_model_invocation(limits, searches):
    async def scenario():
        sources = PreflightBackend()
        respond = AsyncMock(
            side_effect=AssertionError("Model must not run after a failed preflight")
        )
        with pytest.raises(AgentDiscoveryError):
            await run_discovery_agent(
                request().model_copy(update={"search_mode": "area"}),
                sources,
                FunctionModel(respond),
                limits=limits,
            )
        respond.assert_not_awaited()
        assert len(sources.queries) == searches

    asyncio.run(scenario())


def test_preflight_search_failure_does_not_invoke_model_or_publish_empty_success():
    async def scenario():
        sources = PreflightBackend()
        sources.search = AsyncMock(side_effect=SourceUnavailable("private response"))
        respond = AsyncMock(side_effect=AssertionError("Model must not run"))
        with pytest.raises(AgentDiscoveryError) as caught:
            await run_discovery_agent(
                request().model_copy(update={"search_mode": "area"}),
                sources,
                FunctionModel(respond),
            )
        assert "private response" not in str(caught.value)
        respond.assert_not_awaited()
        sources.search.assert_awaited_once()

    asyncio.run(scenario())


def test_preflight_retains_one_transient_retry_without_recharging_the_query(monkeypatch):
    async def scenario():
        sources = PreflightBackend()
        search = sources.search
        attempts = 0

        async def flaky(city, query):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TransientSourceError()
            return await search(city, query)

        sources.search = flaky
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), sources, DiscoveryLimits()
        )
        completed = await state.search_required()
        assert attempts == 5 and len(completed) == state.calls == state.search_attempts == 4
        assert sources.queries == EXPECTED_QUERIES
        assert [event.outcome for event in state.audit] == ["retry", "ok", "ok", "ok", "ok"]

    monkeypatch.setattr("tennis_overview.discovery_agent.asyncio.sleep", AsyncMock())
    asyncio.run(scenario())


def test_preflight_stays_inside_overall_deadline_and_propagates_cancellation():
    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def slow(*args):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()

        sources = PreflightBackend()
        sources.search = slow
        respond = AsyncMock(side_effect=AssertionError("Model must not run"))
        query = request().model_copy(update={"search_mode": "area"})
        with pytest.raises(AgentDiscoveryError):
            await run_discovery_agent(
                query, sources, FunctionModel(respond), limits=DiscoveryLimits(total_seconds=0.02)
            )
        assert stopped.is_set()
        started.clear()
        stopped.clear()
        task = asyncio.create_task(run_discovery_agent(query, sources, FunctionModel(respond)))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        respond.assert_not_awaited()

    asyncio.run(scenario())
