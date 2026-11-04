from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    Completion,
    AgentDiscoveryError,
    DiscoveryLimits,
    ResearchState,
    SourceDocument,
    SourceRef,
    SourceUnavailable,
    ToolAudit,
    run_discovery_agent,
)

REFERENCE = SourceRef(id="facility-homepage", title="Facility reference")
OTHER = SourceRef(id="unrelated-directory", title="General tennis directory")


def backend_for(case="baseline"):
    backend = ReplayBackend(case)
    parent = backend.documents["seasonal-schedule"]
    backend.documents[parent.source.id] = parent.model_copy(
        update={"links": (*parent.links, REFERENCE, OTHER), "reference_ids": (REFERENCE.id,)}
    )
    for ref in (REFERENCE, OTHER):
        backend.documents[ref.id] = SourceDocument(source=ref, text="No additional course facts")
    return backend


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["baseline", "injection", "date_conflict", "transient_read"])
def test_reference_priority_preserves_original_facts_and_required_reads(case):
    async def scenario():
        backend = backend_for(case)
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_required()
        while required := state.coverage_read_ids():
            await state.read_source(required[0])
        assert "dated-registration" in backend.read_calls
        assert REFERENCE.id not in backend.read_calls
        assert state.available_read_ids() == (REFERENCE.id,)
        with pytest.raises(ModelRetry, match="facility references"):
            state.complete(Completion(outcome="sources_checked"))
        before = state.read_attempts
        with pytest.raises(ModelRetry, match="facility references"):
            await state.read_source(OTHER.id)
        assert state.read_attempts == before and OTHER.id not in backend.read_calls
        await state.read_source(REFERENCE.id)
        assert state.available_read_ids() == (OTHER.id,)
        state.complete(Completion(outcome="sources_checked"))
        checks = score(state.result(0), case)
        assert all(checks[key] for key in ("facts", "evidence", "retrieval", "safe_tools"))

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_exhausted_budget_can_finish_without_optional_reference_reads():
    backend = backend_for()

    def respond(messages, info):
        raise AssertionError("No model call needed after budget exhaustion")

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}),
            backend,
            FunctionModel(respond),
            limits=DiscoveryLimits(reads=2),
        )
    )
    assert run.partial and run.model_requests == 0
    assert backend.read_calls == ["seasonal-schedule", "dated-registration"]
    checks = score(run, "baseline")
    assert all(checks[key] for key in ("facts", "evidence", "retrieval", "safe_tools"))


def test_failed_reference_is_not_retried_and_does_not_invalidate_other_facts():
    async def scenario():
        backend = backend_for()
        original = backend.read

        async def read(source_id, query, *, deadline=None):
            if source_id == REFERENCE.id:
                raise SourceUnavailable()
            return await original(source_id, query, deadline=deadline)

        backend.read = read
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_required()
        await state.advance_required_reads()
        assert REFERENCE.id in state.failed_sources
        attempts = state.read_attempts
        assert state.available_read_ids() == (OTHER.id,)
        assert (await state.read_source(REFERENCE.id))["cached"]
        assert state.read_attempts == attempts
        state.complete(Completion(outcome="sources_checked"))
        assert state.result(0).partial

    asyncio.run(scenario())


def test_source_check_does_not_inherit_area_reference_priority():
    async def scenario():
        backend = backend_for()
        state = ResearchState(request(), backend, DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.search_courses("Santa Clara")
        await state.read_source("seasonal-schedule")
        assert state.unattempted_reference_ids() == ()
        await state.read_source(OTHER.id)
        assert backend.read_calls[-1] == OTHER.id

    asyncio.run(scenario())


def test_references_require_adapter_owned_discovered_links():
    with pytest.raises(ValidationError, match="discovered links"):
        SourceDocument(source=REFERENCE, text="", reference_ids=(OTHER.id,))
    document = SourceDocument(source=REFERENCE, text="", links=(OTHER,), reference_ids=(OTHER.id,))
    assert ResearchState.model_document(document)["reference_ids"] == (OTHER.id,)


@pytest.mark.benchmark
def test_reference_cannot_displace_pending_registration_navigation():
    async def scenario():
        backend = ReplayBackend("registration_navigation")
        parent = backend.documents["operator-overview"]
        backend.documents[parent.source.id] = parent.model_copy(
            update={"links": (*parent.links, REFERENCE), "reference_ids": (REFERENCE.id,)}
        )
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_required()
        for sid in ("other-club", "operator-overview", "another-operator"):
            await state.read_source(sid)
        assert state.available_read_ids() == ("registration-catalog",)
        with pytest.raises(ModelRetry, match="registration navigation"):
            await state.read_source(REFERENCE.id)
        assert REFERENCE.id not in backend.read_calls

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["facility_reference", "multiple_facility_references"])
def test_reference_replay_oracle_rejects_distractors_without_changing_facts(case):
    backend = ReplayBackend(case)

    def respond(messages, info):
        tool = next(tool for tool in info.function_tools if tool.name == "read_source")
        allowed = tool.parameters_json_schema["properties"]["source_id"]["enum"]
        if "dated-registration" in backend.read_calls:
            return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])
        return ModelResponse([ToolCallPart("read_source", {"source_id": allowed[0]})])

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}), backend, FunctionModel(respond)
        )
    )
    assert all(score(run, case).values())
    assert not any(sid.startswith("directory-") for sid in backend.read_calls)
    extra = ToolAudit(tool="read_source", source_id="directory-0", outcome="ok")
    premature = run.model_copy(update={"audit": (extra, *run.audit)})
    checks = score(premature, case)
    assert checks["facts"] and not checks["verification_before_optional_reads"]


@pytest.mark.parametrize("remaining", [1, 2])
def test_automatic_references_require_enough_budget_for_the_known_set(remaining):
    async def scenario():
        backend = backend_for()
        parent = backend.documents["seasonal-schedule"]
        backend.documents[parent.source.id] = parent.model_copy(
            update={"reference_ids": (REFERENCE.id, OTHER.id)}
        )
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}),
            backend,
            DiscoveryLimits(reads=2 + remaining),
        )
        await state.search_required()
        await state.advance_required_reads()
        if remaining == 2:
            assert backend.read_calls == [
                "seasonal-schedule",
                "dated-registration",
                REFERENCE.id,
                OTHER.id,
            ]
            assert state.read_attempts == 4
        else:
            assert backend.read_calls == ["seasonal-schedule", "dated-registration"]
            assert set(state.available_read_ids()) == {REFERENCE.id, OTHER.id}
            assert state.read_attempts == 2

    asyncio.run(scenario())


def test_automatic_references_still_charge_the_shared_tool_budget():
    async def scenario():
        backend = backend_for()
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}),
            backend,
            DiscoveryLimits(tool_calls=6),
        )
        await state.search_required()
        with pytest.raises(AgentDiscoveryError, match="tool budget"):
            await state.advance_required_reads()
        assert backend.read_calls == ["seasonal-schedule", "dated-registration"]
        assert state.read_attempts == 2

    asyncio.run(scenario())
