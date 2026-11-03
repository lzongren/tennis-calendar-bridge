from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    DiscoveryLimits,
    ResearchState,
    SourceDocument,
    SourceRef,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.public_web import Page


def test_planner_projection_is_independent_of_page_body_and_retains_navigation():
    original = ReplayBackend().documents["seasonal-schedule"]
    large = original.model_copy(update={"text": "PAGE_BODY_SENTINEL " * 1200})
    before = large.model_dump()
    compact = ResearchState.model_document(large)
    assert compact == ResearchState.model_document(original)
    assert "PAGE_BODY_SENTINEL" not in json.dumps(compact)
    assert "text" not in compact
    assert compact["source"] == original.source.model_dump(mode="json")
    assert compact["links"] == [ref.model_dump() for ref in original.links]
    assert compact["required_ids"] == original.required_ids
    assert compact["candidate_session_count"] == 1
    record = original.records[0]
    assert compact["candidate_sessions"] == [
        {
            "title": record.title,
            "starts_at": record.starts_at.isoformat(),
            "ends_at": record.ends_at.isoformat(),
        }
    ]
    assert compact["candidate_summary_truncated"] is False
    assert large.model_dump() == before


def test_candidate_title_summary_is_bounded_without_truncating_evidence():
    original = ReplayBackend().documents["dated-registration"]
    record = original.records[0]
    records = tuple(
        record.model_copy(update={"title": str(i) + "X" * 499, "session_key": f"session-{i}"})
        for i in range(10)
    )
    document = original.model_copy(update={"records": (*records, records[0])})
    compact = ResearchState.model_document(document)
    assert compact["candidate_session_count"] == 10
    assert [item["title"] for item in compact["candidate_sessions"]] == [
        r.title[:240] for r in records[:8]
    ]
    assert compact["candidate_summary_truncated"] is True
    assert len(document.records) == 11 and document.records[9].title == "9" + "X" * 499


def test_empty_observation_does_not_invent_candidates():
    document = SourceDocument(source=SourceRef(id="empty", title="No courses"), text="No courses")
    compact = ResearchState.model_document(document)
    assert compact["candidate_session_count"] == 0
    assert compact["candidate_sessions"] == []
    assert compact["candidate_summary_truncated"] is False


def test_cached_reads_keep_full_document_without_resending_body():
    async def scenario():
        backend = ReplayBackend()
        state = ResearchState(request(), backend, DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.search_courses("Santa Clara")
        first = await state.read_source("seasonal-schedule")
        second = await state.read_source("seasonal-schedule")
        assert first["candidate_session_count"] == 1
        assert second["cached"] and second["next_source_ids"] == ("dated-registration",)
        assert "text" not in second and "candidate_sessions" not in second
        assert state.documents["seasonal-schedule"] == backend.documents["seasonal-schedule"]
        assert backend.read_calls == ["seasonal-schedule"]

    asyncio.run(scenario())


def test_extractor_receives_full_page_before_planner_projection():
    async def scenario():
        page = Page(
            "https://club.example.org/adults",
            "Adult courses",
            ("Public clinic details " * 1000).strip(),
            (),
        )
        extractor = AsyncMock(extract=AsyncMock(return_value=()))
        backend = PublicSourceBackend(
            extractor,
            (page.url,),
            reader=AsyncMock(return_value=page),
            searcher=AsyncMock(return_value=[]),
        )
        state = ResearchState(request(), backend, DiscoveryLimits())
        await state.search_courses("San Jose")
        observation = await state.read_source(identity(page.url))
        extractor.extract.assert_awaited_once_with(page, state.request)
        assert state.documents[identity(page.url)].text == page.text
        assert "Public clinic details" not in json.dumps(observation)

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["baseline", "injection", "date_conflict", "transient_read"])
def test_large_page_observations_preserve_real_case_oracles(case):
    backend = ReplayBackend(case)
    for key, document in backend.documents.items():
        backend.documents[key] = document.model_copy(
            update={"text": document.text + "\n" + "PAGE_BODY_SENTINEL " * 1000}
        )
    calls = [
        ToolCallPart("search_courses", {"city": "San Jose"}),
        ToolCallPart("search_courses", {"city": "Santa Clara"}),
        ToolCallPart("read_source", {"source_id": "seasonal-schedule"}),
        ToolCallPart("read_source", {"source_id": "dated-registration"}),
    ]
    if case == "date_conflict":
        calls.append(ToolCallPart("read_source", {"source_id": "conflicting-record"}))
    calls.append(ToolCallPart("finish_discovery", {"outcome": "sources_checked"}))
    turns = iter(calls)
    observed = []

    async def respond(messages, info):
        for message in messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_source":
                    payload = part.model_response_str()
                    assert "PAGE_BODY_SENTINEL" not in payload
                    assert len(payload) < 2000
                    if payload not in observed:
                        observed.append(payload)
        return ModelResponse([next(turns)])

    run = asyncio.run(run_discovery_agent(request(), backend, FunctionModel(respond)))
    assert all(score(run, case).values())
    assert len(observed) == (3 if case == "date_conflict" else 2)
    assert all("PAGE_BODY_SENTINEL" in doc.text for doc in backend.documents.values())


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["baseline", "injection", "date_conflict", "transient_read"])
def test_eager_finisher_cannot_skip_real_case_retrieval(case):
    backend = ReplayBackend(case)
    calls = [
        ToolCallPart("search_courses", {"city": "San Jose"}),
        ToolCallPart("search_courses", {"city": "Santa Clara"}),
        ToolCallPart("read_source", {"source_id": "seasonal-schedule"}),
        ToolCallPart("read_source", {"source_id": "dated-registration"}),
    ]
    if case == "date_conflict":
        calls.append(ToolCallPart("read_source", {"source_id": "conflicting-record"}))
    turns = iter(calls)
    finishes = 0

    async def respond(messages, info):
        nonlocal finishes
        if any(tool.name == "finish_discovery" for tool in info.output_tools):
            finishes += 1
            return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])
        return ModelResponse([next(turns)])

    run = asyncio.run(run_discovery_agent(request(), backend, FunctionModel(respond)))
    assert all(score(run, case).values())
    assert finishes == 1 and run.model_requests == len(calls) + 1
