from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from benchmarks.agent_discovery import NAVIGATION_SCENARIOS, ReplayBackend, request, score
from benchmarks.source_discovery import BROWSER_URL, CAPTURE_TIME
from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.courtreserve_discovery import courtreserve_observation
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    Completion,
    DiscoveryLimits,
    ResearchState,
    SearchResults,
    SourceDocument,
    SourceRef,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.public_web import Page, PageLink, page_from_html

URL = "https://club.example.org/adult-tennis"
PORTAL = "https://app.courtreserve.com/Online/Portal/Index/12345"


@pytest.mark.benchmark
@pytest.mark.parametrize("case", NAVIGATION_SCENARIOS)
def test_registration_navigation_cannot_be_skipped_after_other_courses_found(case):
    async def scenario():
        sources = ReplayBackend(case)
        query = request().model_copy(update={"search_mode": "area"})
        state = ResearchState(query, sources, DiscoveryLimits())
        await state.search_required()
        await state.read_source("other-club")
        observation = await state.read_source("operator-overview")
        assert observation["navigation_ids"] == ("registration-catalog",)
        assert state.unattempted_navigation_ids() == ("registration-catalog",)
        await state.read_source("another-operator")
        with pytest.raises(ModelRetry, match="registration navigation"):
            state.complete(Completion(outcome="sources_checked"))
        await state.read_source("registration-catalog")
        assert not state.unattempted_navigation_ids()
        if case != "membership_navigation":
            with pytest.raises(ModelRetry, match="required detail"):
                state.complete(Completion(outcome="sources_checked"))
            await state.read_source("seasonal-schedule")
            await state.read_source("dated-registration")
        else:
            assert state.failed_source_reasons["registration-catalog"] == "membership_required"
            await state.read_source("registration-catalog")
            assert sources.read_calls.count("registration-catalog") == 1
        state.complete(Completion(outcome="sources_checked"))
        run = state.result(0)
        assert all(score(run, case).values())
        assert not score(run.model_copy(update={"audit": ()}), case)["navigation_attempted"]

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("case", NAVIGATION_SCENARIOS)
def test_model_obligations_hide_finish_until_registration_navigation_attempted(case):
    sources = ReplayBackend(case)
    reads = ["other-club", "operator-overview", "another-operator", "registration-catalog"]
    if case != "membership_navigation":
        reads.extend(["seasonal-schedule", "dated-registration"])
    remaining = iter(reads)

    async def respond(messages, info):
        progress = json.loads(
            info.instructions.split("Current research obligations", 1)[1].split("\n", 1)[1]
        )
        if sources.read_calls == ["other-club", "operator-overview"]:
            assert progress["registration_navigation_ids"] == ["registration-catalog"]
            assert not info.output_tools
            read_tool = next(tool for tool in info.function_tools if tool.name == "read_source")
            assert read_tool.parameters_json_schema["properties"]["source_id"]["enum"] == [
                "another-operator"
            ]
        if source := next(remaining, None):
            return ModelResponse([ToolCallPart("read_source", {"source_id": source})])
        assert info.output_tools
        return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])

    run = asyncio.run(
        run_discovery_agent(
            request().model_copy(update={"search_mode": "area"}),
            sources,
            FunctionModel(respond),
        )
    )
    assert all(score(run, case).values())
    assert run.model_requests == 2 and run.tool_calls == len(reads) + 4


@pytest.mark.parametrize("failed", [False, True])
def test_real_operator_link_shape_creates_bounded_navigation_obligation(failed):
    async def scenario():
        page = page_from_html(
            URL,
            (
                Path(__file__).parent / "fixtures/discovery_benchmark/operator_navigation.html"
            ).read_bytes(),
        )
        extract = AsyncMock(return_value=())
        if failed:
            extract.side_effect = UnexpectedModelBehavior("private model error")
        backend = PublicSourceBackend(
            AsyncMock(extract=extract), reader=AsyncMock(return_value=page)
        )
        ref = backend.register(URL, "Adult tennis", 0)
        doc = await backend.read(ref.id, request())
        assert doc.navigation_ids == (identity(PORTAL),)
        assert not doc.required_ids and not doc.records
        assert len(doc.links) <= 6
        assert doc.evidence_unavailable is failed
        assert ResearchState.model_document(doc)["navigation_ids"] == doc.navigation_ids

    asyncio.run(scenario())


@pytest.mark.parametrize("depth,limit", [(2, 24), (0, 1)])
def test_navigation_does_not_bypass_link_depth_or_registry_limits(depth, limit):
    async def scenario():
        page = Page(
            URL,
            "Adult tennis",
            "Published adult courses with registration links.",
            (PORTAL,),
            link_details=(PageLink(PORTAL, "Register now"),),
        )
        backend = PublicSourceBackend(
            AsyncMock(extract=AsyncMock(return_value=())),
            reader=AsyncMock(return_value=page),
            source_limit=limit,
        )
        ref = backend.register(URL, "Adult tennis", depth)
        doc = await backend.read(ref.id, request())
        assert not doc.navigation_ids and not doc.links

    asyncio.run(scenario())


def test_navigation_ids_must_be_discovered_and_are_not_course_identity():
    with pytest.raises(ValidationError, match="Navigation sources"):
        SourceDocument(
            source=SourceRef(id="operator", title="Operator"), text="", navigation_ids=("invented",)
        )


def test_source_check_does_not_expand_area_navigation_and_required_failure_stays_fatal():
    async def scenario():
        sources = ReplayBackend("membership_navigation")
        state = ResearchState(request(), sources, DiscoveryLimits())
        await state.search_courses("San Jose")
        await state.read_source("other-club")
        state.register((sources.documents["operator-overview"].source,))
        await state.read_source("operator-overview")
        assert not state.unattempted_navigation_ids()
        state.complete(Completion(outcome="sources_checked"))
        source = state.documents["operator-overview"]
        state.documents["operator-overview"] = source.model_copy(
            update={"required_ids": source.navigation_ids}
        )
        await state.read_source("registration-catalog")
        with pytest.raises(AgentDiscoveryError, match="Required registration"):
            state.complete(Completion(outcome="sources_checked"))

    asyncio.run(scenario())


def test_read_budget_exhaustion_does_not_silently_clear_navigation():
    async def scenario():
        sources = ReplayBackend("registration_navigation")
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), sources, DiscoveryLimits(reads=2)
        )
        await state.search_required()
        await state.read_source("other-club")
        await state.read_source("operator-overview")
        with pytest.raises(AgentDiscoveryError, match="read budget"):
            await state.read_source("another-operator")
        with pytest.raises(ModelRetry, match="search groups"):
            await state.read_source("registration-catalog")
        assert state.unattempted_navigation_ids() == ("registration-catalog",)
        with pytest.raises(ModelRetry):
            state.complete(Completion(outcome="sources_checked"))

    asyncio.run(scenario())


@pytest.mark.parametrize("case", NAVIGATION_SCENARIOS)
def test_navigation_oracle_rejects_premature_results_even_with_other_courses(case):
    async def scenario():
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}),
            ReplayBackend(case),
            DiscoveryLimits(),
        )
        await state.search_required()
        await state.read_source("other-club")
        await state.read_source("operator-overview")
        premature = state.result(0)
        checks = score(premature, case)
        assert checks["other_courses_retained"] and checks["partial_coverage"]
        assert not checks["navigation_attempted"] and not all(checks.values())

    asyncio.run(scenario())


def test_verified_registration_detail_does_not_reopen_general_catalog(monkeypatch):
    def with_back_link(*args, **kwargs):
        observation = courtreserve_observation(*args, **kwargs)
        return replace(
            observation,
            page=replace(
                observation.page,
                links=(PORTAL,),
                link_details=(PageLink(PORTAL, "Back to registration portal"),),
            ),
        )

    monkeypatch.setattr(
        "tennis_overview.discovery_sources.courtreserve_observation", with_back_link
    )

    async def scenario():
        html = (
            Path(__file__).parent
            / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
        ).read_text()
        backend = PublicSourceBackend(
            AsyncMock(),
            reader=AsyncMock(
                return_value=BrowserDocument(BROWSER_URL, "Clinic", html, CAPTURE_TIME)
            ),
            clock=lambda: CAPTURE_TIME,
        )
        ref = backend.register(BROWSER_URL, "Clinic", 0)
        document = await backend.read(ref.id, request())
        assert document.records and document.links
        assert not document.navigation_ids

    asyncio.run(scenario())


def test_new_search_groups_precede_optional_navigation_without_skipping_it():
    async def scenario():
        sources = ReplayBackend("registration_navigation")
        extra = SourceDocument(
            source=SourceRef(id="another-operator", title="Another operator"),
            text="No course facts.",
        )
        sources.documents[extra.source.id] = extra
        original_search = sources.search

        async def search(city, query):
            return (
                SearchResults(sources=(extra.source,))
                if city == "Santa Clara"
                else await original_search(city, query)
            )

        sources.search = search
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}), sources, DiscoveryLimits()
        )
        await state.search_required()
        await state.read_source("other-club")
        await state.read_source("operator-overview")
        with pytest.raises(ModelRetry, match="search groups"):
            await state.read_source("registration-catalog")
        assert state.read_attempts == 2
        assert "registration-catalog" not in sources.read_calls
        await state.read_source("another-operator")
        with pytest.raises(ModelRetry, match="registration navigation"):
            state.complete(Completion(outcome="sources_checked"))
        await state.read_source("registration-catalog")
        assert not state.unattempted_navigation_ids()

    asyncio.run(scenario())


def test_required_course_details_can_precede_remaining_search_groups():
    async def scenario():
        sources = ReplayBackend("registration_navigation")
        detail = sources.documents["dated-registration"].source
        sources.documents["other-club"] = sources.documents["other-club"].model_copy(
            update={"links": (detail,), "required_ids": (detail.id,)},
        )
        state = ResearchState(
            request().model_copy(update={"search_mode": "area"}),
            sources,
            DiscoveryLimits(),
        )
        await state.search_required()
        await state.read_source("other-club")
        assert state.unattempted_search_groups()
        assert detail.id in state.coverage_read_ids()
        await state.read_source(detail.id)
        assert state.documents[detail.id].records
        assert state.unattempted_search_groups()

    asyncio.run(scenario())
