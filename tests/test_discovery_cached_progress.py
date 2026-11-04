from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import NAVIGATION_SCENARIOS, ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    ResearchState,
    run_discovery_agent,
)


def test_cached_failed_page_returns_pending_ids_without_repeating_hostile_title():
    async def scenario():
        backend = ReplayBackend("navigation_injection")
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_required()
        first = await state.read_source("operator-overview")
        assert "collector.invalid" in json.dumps(first)
        await state.read_source("another-operator")
        cached = await state.read_source("operator-overview")
        assert cached["cached"] is True
        assert cached["next_source_ids"] == ("other-club",)
        assert cached["reason"] == "evidence_invalid"
        assert "collector.invalid" not in json.dumps(cached)
        assert "links" not in cached and "candidate_sessions" not in cached
        assert backend.read_calls == ["operator-overview", "another-operator"]
        assert state.read_attempts == 2 and state.calls == 7
        assert state.audit[-1].outcome == "cached"
        assert state.documents["operator-overview"] == backend.documents["operator-overview"]

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("case", NAVIGATION_SCENARIOS)
def test_cached_progress_preserves_original_facts_and_navigation_gates(case):
    backend = ReplayBackend(case)
    turns = iter(
        [
            "operator-overview",
            "operator-overview",
            "another-operator",
            "other-club",
            "registration-catalog",
            *(
                []
                if case == "membership_navigation"
                else ["seasonal-schedule", "dated-registration"]
            ),
        ]
    )
    seen_cache = False

    async def respond(messages, info):
        nonlocal seen_cache
        tools = {tool.name: tool for tool in info.function_tools}
        if len(backend.read_calls) < (4 if case == "membership_navigation" else 6):
            allowed = tools["read_source"].parameters_json_schema["properties"]["source_id"]["enum"]
            assert not set(allowed) & set(backend.read_calls)
        for message in messages:
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    payload = json.loads(part.content)["research_snapshot"]
                    if payload["recent_actions"][-1]["outcome"] == "cached":
                        assert sorted(ref["id"] for ref in payload["read_sources"]) == [
                            "another-operator",
                            "other-club",
                        ]
                        assert "collector.invalid" not in part.content
                        seen_cache = True
        if source := next(turns, None):
            # A scripted stale call exercises the execution path even though it
            # has already disappeared from the advertised schema.
            return ModelResponse([ToolCallPart("read_source", {"source_id": source})])
        assert "read_source" not in tools
        return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}),
            backend,
            FunctionModel(respond),
        )
    )
    assert seen_cache and all(score(run, case).values())
    assert backend.read_calls.count("operator-overview") == 1
    assert any(
        event.source_id == "operator-overview" and event.outcome == "cached" for event in run.audit
    )


def test_cached_membership_denial_is_not_a_retry_or_successful_read():
    async def scenario():
        backend = ReplayBackend("membership_navigation")
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_required()
        for source in (
            "other-club",
            "operator-overview",
            "another-operator",
            "registration-catalog",
        ):
            await state.read_source(source)
        cached = await state.read_source("registration-catalog")
        assert cached["cached"] and cached["reason"] == "membership_required"
        assert cached["next_source_ids"] == ()
        assert "registration-catalog" not in state.documents
        assert backend.read_calls.count("registration-catalog") == 1
        assert state.result(0).partial
        assert state.audit[-1].outcome == "cached"
        with pytest.raises(AgentDiscoveryError, match="undiscovered"):
            await state.read_source("invented-source")

    asyncio.run(scenario())


def test_read_tool_is_unavailable_before_any_source_is_discovered():
    backend = ReplayBackend()

    async def respond(messages, info):
        assert all(tool.name != "read_source" for tool in info.function_tools)
        return ModelResponse(
            [ToolCallPart("read_source", {"source_id": "http://100.64.0.7/admin"})]
        )

    with pytest.raises(AgentDiscoveryError):
        asyncio.run(run_discovery_agent(request(), backend, FunctionModel(respond)))
    assert not backend.read_calls and not backend.search_calls
