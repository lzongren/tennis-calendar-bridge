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
    run_discovery_agent,
)


def with_optional_sources(case="baseline"):
    backend = ReplayBackend(case)
    refs = tuple(SourceRef(id=f"optional-{i}", title="Other courses") for i in range(3))
    detail = backend.documents["dated-registration"]
    backend.documents[detail.source.id] = detail.model_copy(
        update={"links": (*detail.links, *refs), "refinement_available": True}
    )
    for ref in refs:
        backend.documents[ref.id] = SourceDocument(source=ref, text="No dates published")
    return backend


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["baseline", "injection", "date_conflict", "transient_read"])
def test_budget_finish_preserves_original_facts_and_partial_coverage(case):
    backend = with_optional_sources(case)
    model = AsyncMock(side_effect=AssertionError("No inference after read budget exhaustion"))
    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}),
            backend,
            FunctionModel(model),
            limits=DiscoveryLimits(reads=3 if case == "date_conflict" else 2),
        )
    )
    checks = score(run, case)
    assert all(
        checks[key] for key in ("facts", "evidence", "retrieval", "neighbor_search", "safe_tools")
    )
    assert run.partial
    assert run.model_requests == 0
    assert not any(sid.startswith("optional-") for sid in backend.read_calls)
    model.assert_not_awaited()


@pytest.mark.benchmark
def test_optional_planner_read_at_limit_returns_evidence_without_an_extra_turn():
    backend = with_optional_sources()
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        assert calls == 1
        return ModelResponse([ToolCallPart("read_source", {"source_id": "optional-0"})])

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}),
            backend,
            FunctionModel(respond),
            limits=DiscoveryLimits(reads=3),
        )
    )
    assert backend.read_calls == ["seasonal-schedule", "dated-registration", "optional-0"]
    assert run.model_requests == calls == 1
    assert run.partial
    checks = score(run, "baseline")
    assert all(checks[key] for key in ("facts", "evidence", "retrieval", "safe_tools"))


@pytest.mark.benchmark
@pytest.mark.parametrize("obligation", ["required_ids", "navigation_ids"])
def test_budget_cannot_hide_unread_mandatory_choices(obligation):
    backend = with_optional_sources()
    detail = backend.documents["dated-registration"]
    backend.documents[detail.source.id] = detail.model_copy(
        update={obligation: ("optional-0", "optional-1")}
    )
    model = AsyncMock(side_effect=AssertionError("Unfulfillable coverage must fail closed"))
    with pytest.raises(AgentDiscoveryError, match="budget"):
        asyncio.run(
            run_discovery_agent(
                request().model_copy(update={"search_mode": "area"}),
                backend,
                FunctionModel(model),
                limits=DiscoveryLimits(reads=2),
            )
        )
    assert backend.read_calls == ["seasonal-schedule", "dated-registration"]
    model.assert_not_awaited()


def test_exhausted_read_choices_are_not_advertised_but_evidence_is_retained():
    async def scenario():
        backend = with_optional_sources()
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}),
            backend,
            DiscoveryLimits(reads=2),
        )
        await state.search_required()
        await state.advance_required_reads()
        assert state.available_read_ids() == ()
        assert state.planner_snapshot()["read_sources"] == []
        assert "optional-0" in state.registry
        assert "dated-registration" in state.documents
        with pytest.raises(AgentDiscoveryError, match="budget"):
            await state.read_source("optional-0")
        assert len(backend.read_calls) == 2

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_budget_cannot_report_no_results_while_unread_sources_remain():
    backend = with_optional_sources()
    for sid in ("seasonal-schedule", "dated-registration"):
        backend.documents[sid] = backend.documents[sid].model_copy(update={"records": ()})
    model = AsyncMock(side_effect=AssertionError("No-results coverage remains mandatory"))
    with pytest.raises(AgentDiscoveryError, match="budget"):
        asyncio.run(
            run_discovery_agent(
                request().model_copy(update={"search_mode": "area"}),
                backend,
                FunctionModel(model),
                limits=DiscoveryLimits(reads=2),
            )
        )
    assert backend.read_calls == ["seasonal-schedule", "dated-registration"]
    model.assert_not_awaited()


@pytest.mark.benchmark
def test_budget_cannot_skip_unattempted_search_groups():
    backend = ReplayBackend()
    detail = backend.documents["dated-registration"]

    async def search(city, query):
        backend.search_calls.append(city)
        ref = SourceRef(id=f"group-{len(backend.search_calls)}", title="Adult courses")
        backend.documents[ref.id] = detail.model_copy(
            update={
                "source": ref,
                "records": (detail.records[0].model_copy(update={"source_id": ref.id}),),
            }
        )
        return SearchResults(sources=(ref,), nearby_cities=("Santa Clara",))

    backend.search = search
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        assert calls == 1
        return ModelResponse([ToolCallPart("read_source", {"source_id": "group-1"})])

    with pytest.raises(AgentDiscoveryError, match="budget"):
        asyncio.run(
            run_discovery_agent(
                request().model_copy(update={"search_mode": "area"}),
                backend,
                FunctionModel(respond),
                limits=DiscoveryLimits(reads=1),
            )
        )
    assert len(backend.search_calls) == 4
    assert backend.read_calls == ["group-1"]
