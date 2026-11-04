from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    Completion,
    DiscoveryLimits,
    ResearchState,
    SourceDocument,
    SourceRef,
    ToolAudit,
    run_discovery_agent,
)

OPTIONAL = SourceDocument(
    source=SourceRef(id="optional-directory", title="General tennis directory"),
    text="No course dates or registration details.",
)


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["registration_navigation", "membership_navigation"])
def test_post_coverage_reads_preserve_budget_for_observed_registration(case):
    async def scenario():
        backend = ReplayBackend(case)
        backend.documents[OPTIONAL.source.id] = OPTIONAL
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_required()
        state.register((OPTIONAL.source,))
        for source in ("other-club", "operator-overview", "another-operator"):
            await state.read_source(source)
        assert state.unattempted_search_groups() == ()
        assert state.available_read_ids() == ("registration-catalog",)
        before = state.read_attempts
        calls = state.calls
        with pytest.raises(ModelRetry, match="registration navigation"):
            await state.read_source(OPTIONAL.source.id)
        assert state.read_attempts == before and OPTIONAL.source.id not in backend.read_calls
        assert state.calls == calls + 1
        cached = await state.read_source("operator-overview")
        assert cached["next_source_ids"] == ("registration-catalog",)
        await state.read_source("registration-catalog")
        if case != "membership_navigation":
            for source in ("seasonal-schedule", "dated-registration"):
                assert state.available_read_ids() == (source,)
                with pytest.raises(ModelRetry):
                    await state.read_source(OPTIONAL.source.id)
                await state.read_source(source)
        else:
            assert state.failed_source_reasons["registration-catalog"] == "membership_required"
        state.complete(Completion(outcome="sources_checked"))
        assert all(score(state.result(0), case).values())
        assert state.available_read_ids() == (OPTIONAL.source.id,)
        await state.read_source(OPTIONAL.source.id)
        assert not state.available_read_ids()

    asyncio.run(scenario())


def test_known_verification_and_navigation_both_remain_available_after_breadth():
    async def scenario():
        backend = ReplayBackend("registration_navigation")
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_required()
        for source in ("other-club", "operator-overview", "another-operator"):
            await state.read_source(source)
        parent = state.documents["operator-overview"]
        detail = backend.documents["dated-registration"].source
        state.documents[parent.source.id] = parent.model_copy(
            update={"links": (*parent.links, detail), "required_ids": (detail.id,)}
        )
        state.register((detail, OPTIONAL.source))
        assert state.available_read_ids() == ("dated-registration", "registration-catalog")

    asyncio.run(scenario())


def test_source_check_retains_optional_read_policy():
    async def scenario():
        backend = ReplayBackend()
        backend.documents[OPTIONAL.source.id] = OPTIONAL
        state = ResearchState(request(), backend, DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.search_courses("Santa Clara")
        await state.read_source("seasonal-schedule")
        state.register((OPTIONAL.source,))
        assert state.available_read_ids() == ("dated-registration", OPTIONAL.source.id)
        await state.read_source(OPTIONAL.source.id)
        assert backend.read_calls[-1] == OPTIONAL.source.id

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_actual_tool_schema_guides_distraction_case_and_keeps_original_fact_oracle():
    backend = ReplayBackend("navigation_distraction")

    async def respond(messages, info):
        if info.output_tools:
            return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])
        tool = next(tool for tool in info.function_tools if tool.name == "read_source")
        allowed = tool.parameters_json_schema["properties"]["source_id"]["enum"]
        return ModelResponse([ToolCallPart("read_source", {"source_id": allowed[0]})])

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}), backend, FunctionModel(respond)
        )
    )
    assert all(score(run, "navigation_distraction").values())
    assert run.model_requests == 3
    assert not any(source.startswith("directory-") for source in backend.read_calls)
    extra = ToolAudit(tool="read_source", source_id="directory-0", outcome="ok")
    early = run.model_copy(update={"audit": (extra, *run.audit)})
    assert score(early, "navigation_distraction")["facts"]
    assert not score(early, "navigation_distraction")["verification_before_optional_reads"]
    later = run.model_copy(update={"audit": (*run.audit, extra)})
    assert all(score(later, "navigation_distraction").values())
