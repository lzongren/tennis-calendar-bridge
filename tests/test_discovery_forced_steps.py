from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import HOSTILE_TEXT, ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    ResearchState,
    SourceDocument,
    SourceRef,
    SourceUnavailable,
    run_discovery_agent,
)


def area_request():
    return request().model_copy(update={"search_mode": "area"})


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["baseline", "injection", "date_conflict", "transient_read"])
def test_singleton_real_case_preserves_facts_and_audit_without_planner_calls(case):
    backend = ReplayBackend(case)
    model = AsyncMock(side_effect=AssertionError("No model choice is necessary"))
    run = asyncio.run(run_discovery_agent(area_request(), backend, FunctionModel(model)))
    assert all(score(run, case).values())
    assert run.model_requests == 0
    model.assert_not_awaited()
    assert run.tool_calls == 4 + (3 if case == "date_conflict" else 2)
    assert any(
        item.source_id == "dated-registration" and item.outcome == "ok" for item in run.audit
    )


@pytest.mark.parametrize("limit", [DiscoveryLimits(reads=1), DiscoveryLimits(tool_calls=5)])
def test_forced_reads_cannot_escape_read_or_tool_budget(limit):
    model = AsyncMock(side_effect=AssertionError("Model must not run after budget failure"))
    with pytest.raises(AgentDiscoveryError, match="budget"):
        asyncio.run(
            run_discovery_agent(area_request(), ReplayBackend(), FunctionModel(model), limits=limit)
        )
    model.assert_not_awaited()


def test_forced_required_failure_aborts_instead_of_finishing_other_candidates():
    backend = ReplayBackend()
    original = backend.read

    async def read(source_id, query, *, deadline=None):
        if source_id == "dated-registration":
            raise SourceUnavailable()
        return await original(source_id, query, deadline=deadline)

    backend.read = read
    model = AsyncMock(side_effect=AssertionError("A failed required detail must abort"))
    with pytest.raises(AgentDiscoveryError, match="Required registration"):
        asyncio.run(run_discovery_agent(area_request(), backend, FunctionModel(model)))
    model.assert_not_awaited()


def test_forced_steps_stop_at_choices_and_leave_optional_reads_to_planner():
    async def scenario():
        backend = ReplayBackend("navigation_distraction")
        state = ResearchState(area_request(), backend, DiscoveryLimits())
        await state.search_required()
        assert await state.advance_required_reads() == []
        assert not backend.read_calls
        await state.read_source("other-club")
        await state.read_source("operator-overview")
        completed = await state.advance_required_reads()
        assert [item["source"]["id"] for item in completed] == [
            "another-operator",
            "registration-catalog",
            "seasonal-schedule",
            "dated-registration",
        ]
        assert state.available_read_ids() == ("directory-0", "directory-1", "directory-2")
        assert await state.advance_required_reads() == []
        assert state.calls == 10 and state.read_attempts == 6

    asyncio.run(scenario())


def test_forced_observations_are_input_data_not_fabricated_calls_or_instructions():
    backend = ReplayBackend("injection")
    detail = backend.documents["dated-registration"]
    refs = tuple(
        SourceRef(id=f"optional-{index}", title="Untrusted optional title") for index in range(2)
    )
    backend.documents[detail.source.id] = detail.model_copy(update={"links": refs})
    for ref in refs:
        backend.documents[ref.id] = SourceDocument(source=ref, text="No dates.")

    async def respond(messages, info):
        assert not any(
            isinstance(part, ToolCallPart) for message in messages for part in message.parts
        )
        inputs = [
            json.loads(part.content)
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        observed = next(
            item["completed_required_reads"]
            for item in inputs
            if "completed_required_reads" in item
        )
        searches = next(
            item["completed_searches"] for item in inputs if "completed_searches" in item
        )
        assert searches[-1]["result"]["sources"][0]["title"] == HOSTILE_TEXT[:240]
        assert [item["source"]["id"] for item in observed] == [
            "seasonal-schedule",
            "dated-registration",
        ]
        assert "Untrusted optional title" not in info.instructions
        assert "collector.invalid" not in info.instructions
        assert info.output_tools
        hints = json.loads(
            info.instructions.split("Current research obligations", 1)[1].split("\n", 1)[1]
        )
        assert hints["completion"] == {
            "state": "ready",
            "arguments": {"outcome": "sources_checked"},
        }
        assert info.output_tools[0].parameters_json_schema["required"] == ["outcome"]
        return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])

    run = asyncio.run(run_discovery_agent(area_request(), backend, FunctionModel(respond)))
    checks = score(run, "injection")
    assert all(
        checks[key] for key in ("facts", "evidence", "retrieval", "neighbor_search", "safe_tools")
    )
    assert run.model_requests == 1 and run.partial


@pytest.mark.parametrize("cancel", [False, True])
def test_forced_reads_respect_overall_timeout_and_cancellation(cancel):
    async def scenario():
        backend = ReplayBackend()

        async def slow(*args, **kwargs):
            if cancel:
                raise asyncio.CancelledError()
            await asyncio.sleep(1)
            raise AssertionError("Deadline should cancel the read")

        backend.read = slow
        model = AsyncMock(side_effect=AssertionError("Model must not run"))
        with pytest.raises(asyncio.CancelledError if cancel else AgentDiscoveryError):
            await run_discovery_agent(
                area_request(),
                backend,
                FunctionModel(model),
                limits=DiscoveryLimits(total_seconds=10 if cancel else 0.01),
            )
        model.assert_not_awaited()

    asyncio.run(scenario())
