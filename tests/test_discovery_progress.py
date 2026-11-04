from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    Completion,
    DiscoveryLimits,
    ResearchState,
    SearchResults,
    SourceDocument,
    SourceRef,
    run_discovery_agent,
)


class CoverageBackend(ReplayBackend):
    async def search(self, city, query):
        self.search_calls.append(city)
        ref = (
            self.documents["seasonal-schedule"].source
            if query.search_focus == "overview"
            else SourceRef(id=query.search_focus, title="Additional public catalog")
        )
        return SearchResults(sources=(ref,))

    async def read(self, source_id, query, *, deadline=None):
        if source_id in {"registration", "schedule"}:
            self.read_calls.append(source_id)
            return SourceDocument(
                source=SourceRef(id=source_id, title="Additional public catalog"),
                text="No additional dated sessions on this page",
            )
        return await super().read(source_id, query, deadline=deadline)


def test_completion_reports_all_missing_detail_and_coverage_obligations_together():
    async def scenario():
        query = request().model_copy(update={"search_mode": "area"})
        state = ResearchState(query, CoverageBackend(), DiscoveryLimits())
        for focus in ("overview", "registration", "schedule"):
            await state.search_courses(query.city, focus)
        await state.read_source("seasonal-schedule")
        with pytest.raises(ModelRetry) as caught:
            state.complete(Completion(outcome="sources_checked"))
        message = str(caught.value)
        assert "dated-registration" in message
        assert '[["registration"], ["schedule"]]' in message
        assert state.read_attempts == 1

    asyncio.run(scenario())


def test_progress_is_state_owned_and_does_not_promote_source_text_to_instructions():
    async def scenario():
        state = ResearchState(request(), ReplayBackend("injection"), DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.search_courses("Santa Clara")
        await state.read_source("seasonal-schedule")
        progress = state.progress_instructions()
        assert "dated-registration" in progress
        assert "collector.invalid" not in progress
        assert "Book and pay" not in progress
        assert "Seasonal adult clinics" not in progress
        await state.read_source("dated-registration")
        progress = json.loads(state.progress_instructions().split("\n", 1)[1])
        assert progress["required_detail_ids"] == []
        assert progress["unattempted_search_groups"] == []
        assert progress["unread_if_no_candidates"] == []

    asyncio.run(scenario())


def test_coverage_groups_preserve_overlap_and_failed_source_semantics():
    state = ResearchState(
        request().model_copy(update={"search_mode": "area"}),
        CoverageBackend(),
        DiscoveryLimits(),
    )
    a, b, c = (SourceRef(id=key, title="Catalog") for key in ("a", "b", "c"))
    state.searches = {
        "overview": SearchResults(sources=(a, b)),
        "registration": SearchResults(sources=(b, c)),
        "schedule": SearchResults(sources=(a,)),
    }
    assert state.unattempted_search_groups() == (("a", "b"), ("c",))
    state.failed_sources.add("a")
    assert state.unattempted_search_groups() == (("c",),)
    state.failed_sources.add("c")
    assert state.unattempted_search_groups() == ()


@pytest.mark.benchmark
def test_real_case_progress_guides_coverage_without_spending_completion_retries():
    query = request().model_copy(update={"search_mode": "area"})
    backend = CoverageBackend()
    finishes = 0

    def respond(messages, info):
        nonlocal finishes
        assert "Current research obligations" in info.instructions
        progress = json.loads(
            info.instructions.split("Current research obligations", 1)[1].split("\n", 1)[1]
        )
        finish_visible = any(tool.name == "finish_discovery" for tool in info.output_tools)
        assert progress["primary_focuses"] == []
        ids = progress["required_detail_ids"]
        groups = progress["unattempted_search_groups"]
        if ids or groups:
            assert not finish_visible
            source_id = ids[0] if ids else groups[0][0]
            return ModelResponse([ToolCallPart("read_source", {"source_id": source_id})])
        assert finish_visible
        finishes += 1
        return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])

    run = asyncio.run(run_discovery_agent(query, backend, FunctionModel(respond)))
    checks = score(run, "baseline")
    assert all(value for key, value in checks.items() if key != "neighbor_search")
    assert finishes == 0 and run.model_requests == 3
    assert run.tool_calls == 7 and backend.search_calls == [query.city] * 3
    assert set(backend.read_calls) == {
        "seasonal-schedule",
        "dated-registration",
        "registration",
        "schedule",
    }
