from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import NAVIGATION_SCENARIOS, ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    Completion,
    DiscoveryLimits,
    ResearchState,
    SearchResults,
    run_discovery_agent,
)


def progress(instructions):
    return json.loads(instructions.split("Current research obligations", 1)[1].split("\n", 1)[1])


@pytest.mark.benchmark
@pytest.mark.parametrize("case", NAVIGATION_SCENARIOS)
def test_guidance_matches_live_tool_schema_and_preserves_original_oracle(case):
    backend = ReplayBackend(case)

    async def respond(messages, info):
        state = progress(info.instructions)
        read = next((tool for tool in info.function_tools if tool.name == "read_source"), None)
        assert state["next_read_source_ids"] == (
            read.parameters_json_schema["properties"]["source_id"]["enum"] if read else []
        )
        completion = state["completion"]
        if info.output_tools:
            assert completion == {"state": "ready", "arguments": {"outcome": "sources_checked"}}
            assert info.output_tools[0].parameters_json_schema["required"] == ["outcome"]
            return ModelResponse([ToolCallPart("finish_discovery", completion["arguments"])])
        assert completion == {"state": "pending"}
        return ModelResponse(
            [ToolCallPart("read_source", {"source_id": state["next_read_source_ids"][0]})]
        )

    result = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}), backend, FunctionModel(respond)
        )
    )
    assert all(score(result, case).values())


def test_guidance_does_not_offer_reads_before_search_or_mark_required_failure_ready():
    async def scenario():
        backend = ReplayBackend("registration_navigation")
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        before = progress(state.progress_instructions())
        assert before["next_read_source_ids"] == []
        assert before["completion"] == {"state": "pending"}
        await state.search_required()
        for source in (
            "other-club",
            "operator-overview",
            "another-operator",
            "registration-catalog",
        ):
            await state.read_source(source)
        state.failed_sources.add("seasonal-schedule")
        calls = state.calls
        assert progress(state.progress_instructions())["completion"] == {"state": "failed"}
        assert state.calls == calls

    asyncio.run(scenario())


def test_completion_keeps_required_argument_and_rejects_extra_model_facts():
    for data in ({}, {"outcome": "sources_checked", "price": 1}):
        with pytest.raises(ValidationError):
            Completion.model_validate(data)


def test_empty_search_guidance_does_not_claim_sources_checked_or_skip_coverage():
    async def scenario():
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}),
            AsyncMock(search=AsyncMock(return_value=SearchResults())),
            DiscoveryLimits(),
        )
        assert progress(state.progress_instructions())["completion"] == {"state": "pending"}
        await state.search_required()
        before = state.calls
        assert progress(state.progress_instructions())["completion"] == {
            "state": "ready",
            "arguments": {"outcome": "no_results"},
        }
        assert state.calls == before == 3

    asyncio.run(scenario())


def test_source_check_does_not_receive_area_only_protocol_guidance():
    state = ResearchState(request(), ReplayBackend(), DiscoveryLimits())
    hints = progress(state.progress_instructions())
    assert "completion" not in hints and "next_read_source_ids" not in hints
