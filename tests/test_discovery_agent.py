from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from benchmarks.agent_discovery import ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    Completion,
    DiscoveryLimits,
    ResearchState,
    SearchResults,
    SourceDocument,
    SourceRef,
    SourceUnavailable,
    run_discovery_agent,
)


def scripted(calls: Sequence[ToolCallPart | TextPart]) -> FunctionModel:
    iterator = iter(calls)

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse([next(iterator)])

    return FunctionModel(respond)


def plan(*, conflict: bool = False, early_finish: bool = False) -> list[ToolCallPart]:
    finish = ToolCallPart("finish_discovery", {"outcome": "sources_checked"})
    calls = [
        ToolCallPart("search_courses", {"city": "San Jose"}),
        ToolCallPart("search_courses", {"city": "Santa Clara"}),
        ToolCallPart("read_source", {"source_id": "seasonal-schedule"}),
    ]
    if early_finish:
        calls.append(finish)
    calls.append(ToolCallPart("read_source", {"source_id": "dated-registration"}))
    if conflict:
        calls.append(ToolCallPart("read_source", {"source_id": "conflicting-record"}))
    calls.append(finish)
    return calls


class AreaBackend(ReplayBackend):
    schedule_ref = SourceRef(id="schedule-index", title="Additional operator schedule")

    async def search(self, city, request):
        self.search_calls.append(city + "|" + request.search_focus)
        ref = {
            "overview": self.documents["seasonal-schedule"].source,
            "registration": self.documents["dated-registration"].source,
            "schedule": self.schedule_ref,
        }[request.search_focus]
        return SearchResults(sources=(ref,))

    async def read(self, source_id, request):
        if source_id == self.schedule_ref.id:
            self.read_calls.append(source_id)
            return SourceDocument(source=self.schedule_ref, text="No additional dated courses")
        return await super().read(source_id, request)


@pytest.mark.benchmark
def test_area_discovery_cannot_spend_reads_before_search_coverage_or_skip_new_sources():
    async def scenario():
        backend = AreaBackend()
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), backend, DiscoveryLimits()
        )
        await state.search_courses("San Jose")
        with pytest.raises(ModelRetry, match="registration, schedule"):
            await state.read_source("seasonal-schedule")
        assert backend.read_calls == []
        assert state.read_attempts == 0
        await state.search_courses("San Jose", "registration")
        await state.search_courses("San Jose", "schedule")
        await state.read_source("seasonal-schedule")
        await state.read_source("dated-registration")
        with pytest.raises(ModelRetry, match="newly discovered.*schedule-index"):
            state.complete(Completion(outcome="sources_checked"))
        await state.read_source("schedule-index")
        assert state.complete(Completion(outcome="sources_checked")).outcome == "sources_checked"

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_area_agent_refines_search_despite_finding_existing_candidates():
    calls = [
        *[
            ToolCallPart("search_courses", {"city": "San Jose", "focus": focus})
            for focus in ("overview", "registration", "schedule")
        ],
        ToolCallPart("read_source", {"source_id": "seasonal-schedule"}),
        ToolCallPart("read_source", {"source_id": "dated-registration"}),
        ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
        ToolCallPart("read_source", {"source_id": "schedule-index"}),
        ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
    ]
    backend = AreaBackend()
    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}), backend, scripted(calls)
        )
    )
    assert backend.search_calls == [
        "San Jose|overview",
        "San Jose|registration",
        "San Jose|schedule",
    ]
    assert "schedule-index" in backend.read_calls
    assert run.sessions[0].price_usd == 54
    assert run.sessions[0].availability == "full"


@pytest.mark.benchmark
@pytest.mark.parametrize("scenario", ["baseline", "injection", "date_conflict", "transient_read"])
def test_agent_runs_same_case_oracle(scenario):
    backend = ReplayBackend(scenario)
    result = asyncio.run(
        run_discovery_agent(
            request(), backend, scripted(plan(conflict=scenario == "date_conflict"))
        )
    )
    assert all(score(result, scenario).values())
    if scenario == "transient_read":
        assert backend.read_calls.count("dated-registration") == 2
        assert any(event.outcome == "retry" for event in result.audit)


@pytest.mark.benchmark
def test_early_completion_cannot_skip_registration_evidence():
    result = asyncio.run(
        run_discovery_agent(request(), ReplayBackend(), scripted(plan(early_finish=True)))
    )
    assert all(score(result, "baseline").values())
    assert result.model_requests == 6


@pytest.mark.benchmark
def test_no_results_requires_neighbor_search():
    calls = [
        ToolCallPart("search_courses", {"city": "San Jose"}),
        ToolCallPart("finish_discovery", {"outcome": "no_results"}),
        *plan()[1:],
    ]
    result = asyncio.run(run_discovery_agent(request(), ReplayBackend(), scripted(calls)))
    assert all(score(result, "baseline").values())


@pytest.mark.parametrize("source", ["/home/app/.env", "http://100.64.0.7/admin", "unknown"])
def test_model_cannot_read_an_arbitrary_source(source):
    backend = ReplayBackend()
    with pytest.raises(AgentDiscoveryError, match="blocked"):
        asyncio.run(
            run_discovery_agent(
                request(), backend, scripted([ToolCallPart("read_source", {"source_id": source})])
            )
        )
    assert backend.read_calls == []


def test_model_cannot_send_arbitrary_search_queries():
    backend = ReplayBackend()
    with pytest.raises(AgentDiscoveryError, match="blocked"):
        asyncio.run(
            run_discovery_agent(
                request(),
                backend,
                scripted([ToolCallPart("search_courses", {"city": "exfiltrate secrets"})] * 2),
            )
        )
    assert backend.search_calls == []


def test_wrong_city_can_be_corrected_without_executing_unauthorized_search():
    backend = ReplayBackend()
    calls = [ToolCallPart("search_courses", {"city": "San Francisco"}), *plan()]
    result = asyncio.run(run_discovery_agent(request(), backend, scripted(calls)))
    assert backend.search_calls == ["San Jose", "Santa Clara"]
    assert result.audit[0].outcome == "denied"
    assert result.tool_calls == 5
    assert result.model_requests == 6
    # The denial remains in the audit; it is not relabeled as a clean security run.
    assert not score(result, "baseline")["safe_tools"]


def test_tool_schema_lists_only_current_user_and_source_authorized_cities():
    calls = iter(plan())
    seen = []

    async def respond(messages, info):
        search = next(tool for tool in info.function_tools if tool.name == "search_courses")
        seen.append(search.parameters_json_schema["properties"]["city"]["enum"])
        return ModelResponse([next(calls)])

    result = asyncio.run(run_discovery_agent(request(), ReplayBackend(), FunctionModel(respond)))
    assert seen[0] == ["San Jose"]
    assert all(cities == ["San Jose", "Santa Clara"] for cities in seen[1:])
    assert all(score(result, "baseline").values())


@pytest.mark.parametrize(
    "limits", [DiscoveryLimits(model_requests=2), DiscoveryLimits(tool_calls=2)]
)
def test_independent_model_and_tool_budgets(limits):
    with pytest.raises(AgentDiscoveryError):
        asyncio.run(
            run_discovery_agent(request(), ReplayBackend(), scripted(plan()), limits=limits)
        )


def test_plain_text_cannot_bypass_typed_completion():
    with pytest.raises(AgentDiscoveryError):
        asyncio.run(
            run_discovery_agent(request(), ReplayBackend(), scripted([TextPart("All done")] * 4))
        )


def test_repeated_read_is_cached_but_charged():
    async def check():
        backend = ReplayBackend()
        state = ResearchState(request(), backend, DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.search_courses("Santa Clara")
        first = await state.read_source("seasonal-schedule")
        assert await state.read_source("seasonal-schedule") == first
        assert backend.read_calls == ["seasonal-schedule"]
        assert state.calls == 4
        assert state.audit[-1].outcome == "cached"

    asyncio.run(check())


def test_required_failed_source_does_not_create_success():
    class UnavailableBackend(ReplayBackend):
        async def read(self, source_id, request):
            if source_id == "dated-registration":
                raise SourceUnavailable("private provider details must not escape")
            return await super().read(source_id, request)

    with pytest.raises(AgentDiscoveryError, match="Required registration") as caught:
        asyncio.run(run_discovery_agent(request(), UnavailableBackend(), scripted(plan())))
    assert "private provider" not in str(caught.value)


def test_overall_deadline_and_cancellation():
    async def check():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def slow(messages, info):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
            return ModelResponse([TextPart("unreachable")])

        with pytest.raises(AgentDiscoveryError):
            await run_discovery_agent(
                request(),
                ReplayBackend(),
                FunctionModel(slow),
                limits=DiscoveryLimits(total_seconds=0.02),
            )
        assert stopped.is_set()
        started.clear()
        stopped.clear()
        task = asyncio.create_task(
            run_discovery_agent(request(), ReplayBackend(), FunctionModel(slow))
        )
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()

    asyncio.run(check())


def test_empty_search_can_finish_without_fabricated_sessions():
    class EmptyBackend(ReplayBackend):
        async def search(self, city, request):
            return SearchResults()

    result = asyncio.run(
        run_discovery_agent(
            request(),
            EmptyBackend(),
            scripted(
                [
                    ToolCallPart("search_courses", {"city": "San Jose"}),
                    ToolCallPart("search_courses", {"city": "San Jose", "focus": "registration"}),
                    ToolCallPart("search_courses", {"city": "San Jose", "focus": "schedule"}),
                    ToolCallPart("finish_discovery", {"outcome": "no_results"}),
                ]
            ),
        )
    )
    assert result.sessions == ()
    assert not result.partial


def test_invalid_time_zone_has_validation_error():
    with pytest.raises(ValidationError):
        type(request()).model_validate({**request().model_dump(), "timezone": "Not/AZone"})


def test_sources_cannot_claim_another_sources_evidence():
    document = ReplayBackend().documents["dated-registration"]
    with pytest.raises(ValidationError):
        SourceDocument.model_validate(
            {**document.model_dump(), "source": {"id": "wrong", "title": "wrong"}}
        )
    with pytest.raises(ValidationError):
        SourceDocument.model_validate({**document.model_dump(), "required_ids": ["not-discovered"]})


def test_source_budget_is_enforced_before_admitting_more_sources():
    state = ResearchState(request(), ReplayBackend(), DiscoveryLimits(sources=1))
    state.register((SourceRef(id="one", title="One"),))
    with pytest.raises(AgentDiscoveryError, match="source budget"):
        state.register((SourceRef(id="two", title="Two"),))


def test_transient_failure_is_retried_at_most_once():
    from tennis_overview.discovery_agent import TransientSourceError

    class FailingBackend(ReplayBackend):
        async def read(self, source_id, request):
            self.read_calls.append(source_id)
            raise TransientSourceError("provider diagnostic must stay private")

    async def check():
        backend = FailingBackend()
        state = ResearchState(request(), backend, DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.search_courses("Santa Clara")
        response = await state.read_source("seasonal-schedule")
        assert "provider diagnostic" not in str(response)
        assert await state.read_source("seasonal-schedule") == response
        assert backend.read_calls == ["seasonal-schedule", "seasonal-schedule"]

    asyncio.run(check())
