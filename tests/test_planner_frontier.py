from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
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


class FrontierBackend(ReplayBackend):
    other = SourceRef(id="other-page", title="Another adult tennis source")

    async def search(self, city, request):
        self.search_calls.append(city)
        return SearchResults(
            sources=(self.documents["seasonal-schedule"].source, self.other),
            nearby_cities=("Santa Clara",),
        )

    async def read(self, source_id, request, *, deadline=None):
        if source_id == self.other.id:
            self.read_calls.append(source_id)
            return SourceDocument(source=self.other, text="No more courses")
        return await super().read(source_id, request, deadline=deadline)


def snapshot(messages):
    assert len(messages) == 1
    message = messages[0]
    assert isinstance(message, ModelRequest)
    prompts = [part for part in message.parts if isinstance(part, UserPromptPart)]
    assert len(prompts) == 1
    assert not any(isinstance(part, ToolCallPart) for part in message.parts)
    return json.loads(prompts[0].content)


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["baseline", "injection", "date_conflict", "transient_read"])
def test_frontier_keeps_real_case_facts_without_obsolete_choices(case):
    backend = FrontierBackend(case)
    query = request().model_copy(update={"search_mode": "area"})
    observed = []

    def respond(messages, info):
        payload = snapshot(messages)
        current = payload["research_snapshot"]
        observed.append(current)
        refs = {ref["id"] for ref in current["read_sources"]}
        tool = next(tool for tool in info.function_tools if tool.name == "read_source")
        assert refs == set(tool.parameters_json_schema["properties"]["source_id"]["enum"])
        assert "previous_response_invalid" in payload
        assert "records" not in current and "text" not in current
        assert "Ignore earlier instructions" not in (info.instructions or "")
        assert "Respond with exactly one advertised tool call" in info.instructions
        if len(observed) == 1:
            assert refs == {"seasonal-schedule", "other-page"}
            assert current["candidate_session_count"] == 0
            selected = "seasonal-schedule"
        else:
            assert refs == {"other-page"}
            assert current["candidate_session_count"] == 1
            assert current["candidate_sessions"]
            selected = "other-page"
        return ModelResponse([ToolCallPart("read_source", {"source_id": selected})])

    run = asyncio.run(run_discovery_agent(query, backend, FunctionModel(respond)))
    assert len(observed) == 2
    assert all(score(run, case).values())
    assert "dated-registration" in backend.read_calls
    assert any(event.source_id == "seasonal-schedule" for event in run.audit)
    assert not any(event.outcome == "cached" for event in run.audit)


def test_invalid_response_feedback_is_generic_and_does_not_replay_arguments():
    backend = FrontierBackend()
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        payload = snapshot(messages)
        assert "UNTRUSTED_ERROR_SENTINEL" not in json.dumps(payload)
        assert payload["previous_response_invalid"] is (calls == 2), (calls, payload)
        if calls == 1:
            return ModelResponse([ToolCallPart("UNTRUSTED_ERROR_SENTINEL", {})])
        return ModelResponse(
            [
                ToolCallPart(
                    "read_source",
                    {"source_id": "seasonal-schedule" if calls == 2 else "other-page"},
                )
            ]
        )

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}), backend, FunctionModel(respond)
        )
    )
    assert calls == run.model_requests == 3
    assert all(score(run, "baseline").values())


def test_frontier_does_not_reset_model_budget_on_retries():
    def respond(messages, info):
        snapshot(messages)
        return ModelResponse([ToolCallPart("nonexistent_tool", {})])

    with pytest.raises(AgentDiscoveryError):
        asyncio.run(
            run_discovery_agent(
                request().model_copy(update={"search_mode": "area"}),
                FrontierBackend(),
                FunctionModel(respond),
                limits=DiscoveryLimits(model_requests=2),
            )
        )


def test_snapshot_keeps_evidence_and_audit_untouched_and_bounds_summaries():
    backend = FrontierBackend()
    state = ResearchState(request(), backend, DiscoveryLimits())
    document = backend.documents["dated-registration"]
    record = document.records[0]
    records = tuple(
        record.model_copy(update={"session_key": f"session-{i}", "title": str(i) + "x" * 499})
        for i in range(10)
    )
    document = document.model_copy(update={"records": records})
    state.registry[document.source.id] = document.source
    state.documents[document.source.id] = document
    before = document.model_dump()
    current = state.planner_snapshot()
    assert current["read_sources"] == []
    assert current["candidate_session_count"] == 10
    assert current["candidate_summary_truncated"]
    assert len(current["candidate_sessions"]) == 8
    assert all(len(item["title"]) == 240 for item in current["candidate_sessions"])
    assert document.model_dump() == before
    assert state.audit == []
    json.dumps(current)


def test_frontier_preserves_discovery_order_not_opaque_id_sort_order():
    state = ResearchState(request(), FrontierBackend(), DiscoveryLimits())
    refs = tuple(SourceRef(id=sid, title="Adult tennis") for sid in ("z-first", "a-second"))
    state.register(refs)
    assert state.available_read_ids() == ("a-second", "z-first")
    assert [ref["id"] for ref in state.planner_snapshot()["read_sources"]] == [
        "z-first",
        "a-second",
    ]
    state.failed_sources.add("z-first")
    assert [ref["id"] for ref in state.planner_snapshot()["read_sources"]] == ["a-second"]


def test_request_projection_preserves_framework_transcript(monkeypatch):
    from tennis_overview import discovery_agent

    original = discovery_agent.Hooks
    history_seen = []

    def observe(*, before_model_request):
        def wrapped(ctx, request_context):
            before = list(ctx.messages)
            outgoing = before_model_request(ctx, request_context)
            assert ctx.messages == before
            assert outgoing.messages is not ctx.messages
            history_seen.extend(
                part
                for message in ctx.messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_source"
            )
            return outgoing

        return original(before_model_request=wrapped)

    monkeypatch.setattr(discovery_agent, "Hooks", observe)
    sources = iter(("seasonal-schedule", "other-page"))

    def respond(messages, info):
        snapshot(messages)
        return ModelResponse([ToolCallPart("read_source", {"source_id": next(sources)})])

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}),
            FrontierBackend(),
            FunctionModel(respond),
        )
    )
    assert history_seen
    assert all(score(run, "baseline").values())
