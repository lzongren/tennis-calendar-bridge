from __future__ import annotations

import asyncio
import json
import threading
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart, RetryPromptPart, ToolReturnPart
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from benchmarks.source_discovery import error_types

from tennis_overview.agent_discovery import run_until_stopped
from tennis_overview.course_extraction import (
    CourseExtractor,
    ExtractedCourse,
    ExtractionReport,
    grounded_course,
)
from tennis_overview.discovery import DiscoveryService, discover
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    DiscoveryRequest,
    ResearchState,
    SourceUnavailable,
    TransientSourceError,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity, related_links
from tennis_overview.models import AppConfig
from tennis_overview.public_web import Page, PageLink, page_from_html

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://club.example.org/adults"
REQUEST = DiscoveryRequest(
    city="Example City",
    timezone="America/Los_Angeles",
    level="3.0",
    start=date(2026, 10, 25),
    search_mode="source_check",
)


def test_benchmark_error_diagnostics_traverse_groups_without_messages_or_cycles():
    error = ValueError("private error content")
    group = ExceptionGroup("private group content", [error])
    error.__context__ = group
    assert error_types(group) == ["ExceptionGroup", "ValueError"]


def test_public_source_metadata_reaches_search_and_read_results():
    async def scenario():
        backend = PublicSourceBackend(CourseExtractor(planning_model(), RunUsage(), 4))
        backend.searcher = AsyncMock(return_value=[{"url": URL, "title": "Adult classes"}])
        backend.reader = AsyncMock(return_value=generic_page())
        result = await backend.search(REQUEST.city, REQUEST)
        ref = result.sources[0]
        assert ref.url == URL
        assert result.model_dump(mode="json")["sources"][0]["url"] == URL
        document = await backend.read(ref.id, REQUEST)
        assert ResearchState.model_document(document)["source"] == ref.model_dump(mode="json")
        assert backend.register(URL, "Changed title", 1) == ref

    asyncio.run(scenario())


def source_fixture(name):
    return page_from_html(URL, (FIXTURES / name).read_bytes())


def generic_page():
    return Page(
        URL,
        "Example Club",
        "\n".join(
            [
                "Adult Coach Clinic",
                "NTRP 2.5 - 3.0",
                "$54",
                "Monday November 2, 2026 6:30 PM - 7:50 PM",
            ]
        ),
        (),
    )


def quoted_course():
    return dict(
        title_line=0,
        level="NTRP 2.5 - 3.0",
        price="$54",
        evidence_start_line=0,
        evidence_end_line=3,
        year_text="2026",
        date_start_text="November 2, 2026",
        date_end_text="",
        start_time_text="6:30 PM",
        end_time_text="7:50 PM",
        weekdays=["Monday"],
    )


def planning_model():
    calls = iter(
        [
            ToolCallPart("search_courses", {"city": REQUEST.city}),
            ToolCallPart("read_source", {"source_id": identity(URL)}),
            ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
        ]
    )

    async def respond(messages, info):
        if any(tool.name == "extract_courses" for tool in info.output_tools):
            return ModelResponse([ToolCallPart("extract_courses", {"courses": [quoted_course()]})])
        return ModelResponse([next(calls)])

    return FunctionModel(respond)


async def no_search(*args):
    return []


@pytest.mark.parametrize("recovers", [True, False])
def test_public_search_failure_retries_once_without_empty_success(monkeypatch, recovers):
    sleep = AsyncMock()
    monkeypatch.setattr("tennis_overview.discovery_agent.asyncio.sleep", sleep)

    async def scenario():
        backend = PublicSourceBackend(CourseExtractor(planning_model(), RunUsage(), 4))
        backend.searcher = AsyncMock(
            side_effect=[
                TransientSourceError("Public search temporarily unavailable"),
                [{"url": URL, "title": "Adult courses"}]
                if recovers
                else TransientSourceError("Public search temporarily unavailable"),
            ]
        )
        state = ResearchState(REQUEST, backend, DiscoveryLimits())
        if recovers:
            result = await state.search_courses(REQUEST.city)
            assert result.sources[0].id == identity(URL)
            assert not backend.search_failed
            assert [event.outcome for event in state.audit] == ["retry", "ok"]
        else:
            with pytest.raises(AgentDiscoveryError, match="unavailable after retry"):
                await state.search_courses(REQUEST.city)
            assert state.searches == {}
            assert backend.sources == {}
        assert backend.searcher.await_count == 2
        sleep.assert_awaited_once_with(1)

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_ongoing_case_survives_actual_agent_backend():
    async def scenario():
        model, usage = planning_model(), RunUsage()
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 12),
            (URL,),
            reader=AsyncMock(return_value=source_fixture("discovery_ongoing.html")),
            searcher=no_search,
        )
        run = await run_discovery_agent(REQUEST, backend, model, usage=usage)
        result = backend.result(run, REQUEST)
        assert (
            result["agent"]["requests"] == 3
        )  # Deterministic adapter needed no extra model request.
        assert len(result["courses"]) == 2
        first = result["courses"][0]
        starts = sorted(s["starts_at"] for c in result["courses"] for s in c["sessions"])
        assert "2026-10-28T19:00:00-07:00" in starts
        assert "2026-11-04T19:00:00-08:00" in starts
        assert first["availability"] == "Not checked"
        assert first["schedule_basis"] == "weekly_timetable"
        assert first["needs_confirmation"]
        assert first["evidence_ids"] == [identity(URL)]
        assert not result["partial"]

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_term_case_preserves_owned_links_and_uncertainty():
    async def scenario():
        model = planning_model()
        backend = PublicSourceBackend(
            CourseExtractor(model, RunUsage(), 12),
            reader=AsyncMock(return_value=source_fixture("discovery_terms.html")),
        )
        ref = backend.register(URL, "Terms", 0)
        doc = await backend.read(ref.id, REQUEST)
        assert len(doc.required_ids) == 4
        assert all(
            backend.sources[sid].url.startswith("https://registration.example.org/")
            for sid in doc.required_ids
        )
        model_links = ResearchState.model_document(doc)["links"]
        assert model_links == [link.model_dump(mode="json") for link in doc.links]
        assert all(link.url == backend.sources[link.id].url for link in doc.links)
        dated = [record for record in doc.records if record.starts_at]
        assert any(record.starts_at.isoformat() == "2026-10-31T10:00:00-07:00" for record in dated)
        assert sum(record.starts_at is None for record in doc.records) == 2
        assert all(record.availability == "unknown" for record in doc.records)

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_generic_dates_and_price_grounded_but_no_registration_authority():
    async def scenario():
        model, usage = planning_model(), RunUsage()
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 12),
            (URL,),
            reader=AsyncMock(return_value=generic_page()),
            searcher=no_search,
        )
        run = await run_discovery_agent(REQUEST, backend, model, usage=usage)
        assert run.model_requests == usage.requests == 4
        session = run.sessions[0]
        assert session.title == "Adult Coach Clinic"
        assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
        assert str(session.price_usd) == "54"
        assert session.availability == "unknown" and session.waitlist_count is None
        assert session.user_waitlist_position is None

    asyncio.run(scenario())


def test_nested_extraction_cannot_bypass_model_request_budget():
    async def scenario():
        model, usage = planning_model(), RunUsage()
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 3),
            (URL,),
            reader=AsyncMock(return_value=generic_page()),
            searcher=no_search,
        )
        with pytest.raises(AgentDiscoveryError):
            await run_discovery_agent(
                REQUEST, backend, model, usage=usage, limits=DiscoveryLimits(model_requests=3)
            )
        assert usage.requests == 3

    asyncio.run(scenario())


def test_missing_evidence_from_real_model_failure_is_retried_not_silently_dropped():
    responses = iter(
        [
            {
                "title": "Adult Coach Clinic",
                "price": "$54",
                "date_start_text": "Monday November 2, 2026",
            },
            quoted_course(),
        ]
    )

    async def respond(messages, info):
        return ModelResponse([ToolCallPart("extract_courses", {"courses": [next(responses)]})])

    async def scenario():
        usage = RunUsage()
        extractor = CourseExtractor(FunctionModel(respond), usage, 4)
        courses = await extractor.extract(generic_page(), REQUEST)
        assert len(courses) == 1
        assert courses[0].sessions[0].starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        assert usage.requests == 2

    asyncio.run(scenario())


def test_missing_courses_is_not_an_empty_result():
    with pytest.raises(ValidationError):
        ExtractionReport.model_validate({})


def test_clock_time_in_end_date_from_live_failure_is_corrected_before_expansion():
    calls = 0

    async def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            feedback = [
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, (RetryPromptPart, ToolReturnPart))
            ]
            assert "date_end_text" in feedback[-1]
            assert "calendar date" in feedback[-1]
        fields = {**quoted_course(), "date_end_text": "7:50 PM" if calls == 1 else ""}
        return ModelResponse([ToolCallPart("extract_courses", {"courses": [fields]})])

    async def scenario():
        usage = RunUsage()
        result = await CourseExtractor(FunctionModel(respond), usage, 4).extract(
            generic_page(), REQUEST
        )
        assert result[0].title == "Adult Coach Clinic"
        assert result[0].sessions[0].ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
        assert usage.requests == 2

    asyncio.run(scenario())


def test_persistent_clock_time_in_end_date_fails_instead_of_publishing_empty_calendar():
    async def respond(messages, info):
        fields = {**quoted_course(), "date_end_text": "7:50 PM"}
        return ModelResponse([ToolCallPart("extract_courses", {"courses": [fields]})])

    async def scenario():
        usage = RunUsage()
        extractor = CourseExtractor(FunctionModel(respond), usage, 4)
        with pytest.raises(UnexpectedModelBehavior):
            await extractor.extract(generic_page(), REQUEST)
        assert usage.requests == 2

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("required,good_source", [(False, True), (True, True), (False, False)])
def test_invalid_optional_source_does_not_abort_independent_verified_sources(required, good_source):
    bad_url = "https://unreadable.example.org/courses"
    calls = [
        ToolCallPart("search_courses", {"city": REQUEST.city}),
        ToolCallPart("read_source", {"source_id": identity(bad_url)}),
    ]
    if good_source:
        calls.append(ToolCallPart("read_source", {"source_id": identity(URL)}))
    calls.append(ToolCallPart("finish_discovery", {"outcome": "sources_checked"}))
    plan = iter(calls)

    async def respond(messages, info):
        if any(tool.name == "extract_courses" for tool in info.output_tools):
            invalid = {**quoted_course(), "date_end_text": "7:50 PM"}
            return ModelResponse([ToolCallPart("extract_courses", {"courses": [invalid]})])
        return ModelResponse([next(plan)])

    async def read(url):
        if url == bad_url:
            return Page(bad_url, "Unvalidated source", generic_page().text, ())
        return source_fixture("discovery_ongoing.html")

    async def scenario():
        model, usage = FunctionModel(respond), RunUsage()
        candidates = [{"url": bad_url, "title": "Unvalidated course page"}]
        if good_source:
            candidates.append({"url": URL, "title": "Published timetable"})
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 12),
            (bad_url,) if required else (),
            reader=read,
            searcher=AsyncMock(return_value=candidates),
        )
        if required or not good_source:
            with pytest.raises(AgentDiscoveryError, match="previous results retained"):
                await run_discovery_agent(REQUEST, backend, model, usage=usage)
        else:
            run = await run_discovery_agent(REQUEST, backend, model, usage=usage)
            result = backend.result(run, REQUEST)
            assert len(result["courses"]) == 2
            assert all(course["source_url"] == URL for course in result["courses"])
            assert run.partial and result["partial"]
            assert any(
                event.source_id == identity(bad_url) and event.outcome == "unavailable"
                for event in run.audit
            )
        assert backend.sources[identity(bad_url)].status == "unavailable"
        assert usage.requests == len(calls) + 2  # One bounded correction, no hidden retry loop.

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [ModelHTTPError(503, "test-model"), UsageLimitExceeded("Synthetic budget limit")],
)
def test_source_isolation_does_not_hide_model_outages_or_budget_exhaustion(failure):
    async def scenario():
        extractor = CourseExtractor(planning_model(), RunUsage(), 12)
        extractor.extract = AsyncMock(side_effect=failure)
        backend = PublicSourceBackend(extractor, reader=AsyncMock(return_value=generic_page()))
        ref = backend.register(URL, "Course page", 0)
        with pytest.raises(type(failure)):
            await backend.read(ref.id, REQUEST)
        assert backend.sources[ref.id].status != "checked"

    asyncio.run(scenario())


def test_all_evidence_errors_returned_in_one_bounded_correction():
    calls = 0

    async def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            fields = {**quoted_course(), "price": "$999", "date_start_text": "November 3, 2026"}
        else:
            feedback = [
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, (RetryPromptPart, ToolReturnPart))
            ]
            assert len(feedback) == 1
            assert "courses[0].price" in feedback[0]
            assert "courses[0].date_start_text" in feedback[0]
            fields = quoted_course()
        return ModelResponse([ToolCallPart("extract_courses", {"courses": [fields]})])

    async def scenario():
        usage = RunUsage()
        extractor = CourseExtractor(FunctionModel(respond), usage, 4)
        result = await extractor.extract(generic_page(), REQUEST)
        assert len(result) == 1
        assert usage.requests == 2

    asyncio.run(scenario())


def test_evidence_span_retains_intervening_cancellation_text():
    page = Page(
        URL,
        "Example Club",
        "\n".join(
            [
                "Adult Coach Clinic",
                "NTRP 2.5 - 3.0",
                "Mondays November 2, 2026 - November 30, 2026",
                "No class November 16, 2026",
                "$54",
                "6:30 PM - 7:50 PM",
            ]
        ),
        (),
    )
    raw = ExtractedCourse.model_validate(
        {
            **quoted_course(),
            "evidence_end_line": 5,
            "date_end_text": "November 30, 2026",
            "weekdays": ["Mondays"],
        }
    )
    result = grounded_course(raw, page, REQUEST)
    assert result is not None
    assert "No class November 16, 2026" in result.evidence
    assert result.sessions == ()


@pytest.mark.parametrize(
    "changes", [{"level": "4.0"}, {"venue": "Other Venue"}, {"club": "Other Club"}]
)
def test_other_course_metadata_cannot_be_borrowed_from_page(changes):
    async def respond(messages, info):
        return ModelResponse(
            [ToolCallPart("extract_courses", {"courses": [{**quoted_course(), **changes}]})]
        )

    async def scenario():
        page = generic_page()
        page = Page(page.url, page.title, page.text + "\nOther Venue 4.0 Other Club", ())
        extractor = CourseExtractor(FunctionModel(respond), RunUsage(), 4)
        courses = await extractor.extract(page, REQUEST)
        assert len(courses) == 1
        assert getattr(courses[0], next(iter(changes))) == ""
        assert courses[0].price == "$54"
        assert courses[0].sessions[0].starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
        if "level" in changes:
            assert "confirm suitability" in courses[0].schedule_note

    asyncio.run(scenario())


def test_unquoted_weekday_is_not_silently_accepted():
    async def respond(messages, info):
        return ModelResponse(
            [
                ToolCallPart(
                    "extract_courses",
                    {
                        "courses": [{**quoted_course(), "weekdays": ["Tuesday"]}],
                    },
                )
            ]
        )

    async def scenario():
        extractor = CourseExtractor(FunctionModel(respond), RunUsage(), 4)
        with pytest.raises(UnexpectedModelBehavior):
            await extractor.extract(generic_page(), REQUEST)

    asyncio.run(scenario())


def test_unsupported_extraction_fails_refresh_instead_of_publishing_empty_success():
    async def respond(messages, info):
        return ModelResponse(
            [
                ToolCallPart(
                    "extract_courses",
                    {"courses": [{**quoted_course(), "evidence_end_line": 0, "price": "$999"}]},
                )
            ]
        )

    async def scenario():
        extractor = CourseExtractor(FunctionModel(respond), RunUsage(), 4)
        with pytest.raises(UnexpectedModelBehavior, match="retries|validation"):
            await extractor.extract(generic_page(), REQUEST)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "updates",
    [
        {"evidence_start_line": 3, "evidence_end_line": 0},
        {"evidence_start_line": 1},
        {"title_line": 999},
        {"title_line": 10**30},
        {"evidence_end_line": 999},
        {"evidence_end_line": 10**30},
    ],
)
def test_unowned_or_invalid_evidence_span_rejected(updates):
    raw = ExtractedCourse.model_validate({**quoted_course(), **updates})
    assert grounded_course(raw, generic_page(), REQUEST) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("availability", "full"),
        ("source_id", "other"),
        ("kind", "registration"),
        ("signup_url", "https://other.example.org/"),
        ("title", "Invented Clinic"),
    ],
)
def test_model_schema_cannot_self_assign_authority(field, value):
    with pytest.raises(ValidationError):
        ExtractedCourse.model_validate({**quoted_course(), field: value})


def test_links_include_cross_site_registration_and_pdf_not_youth_navigation():
    links = (
        PageLink("https://registration.example.org/Online/Portal/Index/1", "Register"),
        PageLink("https://club.example.org/uploads/one.pdf", "Adult clinic schedule"),
        PageLink("https://club.example.org/kids", "Youth tennis"),
        PageLink("https://club.example.org/news", "News"),
    )
    assert (
        related_links(Page(URL, "Club", "", tuple(x.url for x in links), link_details=links))
        == links[:2]
    )


def test_source_budget_marks_partial_without_admitting_extra_urls():
    backend = PublicSourceBackend(CourseExtractor(planning_model(), RunUsage(), 12), source_limit=1)
    first = backend.register(URL, "Original", 0)
    assert backend.register(URL, "Different label", 1) == first
    assert backend.register("https://another.example.org/", "Another", 0) is None
    assert backend.truncated and len(backend.sources) == 1


def test_empty_app_shell_is_unavailable_not_a_successful_empty_catalog():
    async def scenario():
        backend = PublicSourceBackend(
            CourseExtractor(planning_model(), RunUsage(), 12),
            reader=AsyncMock(return_value=Page(URL, "Portal", "Portal", ())),
        )
        ref = backend.register(URL, "Portal", 0)
        with pytest.raises(SourceUnavailable):
            await backend.read(ref.id, REQUEST)
        assert backend.sources[ref.id].status == "unavailable"

    asyncio.run(scenario())


@pytest.mark.parametrize("error_type", [SourceUnavailable, TransientSourceError])
def test_search_failure_can_use_explicit_sources_but_is_partial(error_type):
    async def scenario():
        backend = PublicSourceBackend(
            CourseExtractor(planning_model(), RunUsage(), 12),
            (URL,),
            searcher=AsyncMock(side_effect=error_type("source failed")),
        )
        result = await backend.search(REQUEST.city, REQUEST)
        assert result.sources[0].id == identity(URL)
        assert backend.search_failed

    asyncio.run(scenario())


def test_agent_can_read_user_sources_during_search_outage_without_claiming_full_coverage():
    async def scenario():
        model, usage = planning_model(), RunUsage()
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 12),
            (URL,),
            searcher=AsyncMock(side_effect=TransientSourceError("search unavailable")),
            reader=AsyncMock(return_value=generic_page()),
        )
        run = await run_discovery_agent(REQUEST, backend, model, usage=usage)
        result = backend.result(run, REQUEST)
        assert result["partial"] and result["search_failed"]
        assert result["courses"][0]["title"] == "Adult Coach Clinic"
        assert result["courses"][0]["sessions"][0]["starts_at"] == "2026-11-02T18:30:00-08:00"
        assert result["courses"][0]["availability"] == "Not checked"
        assert result["sources"][0]["status"] == "checked"

    asyncio.run(scenario())


@pytest.mark.benchmark
def test_annual_pdf_adapter_preserves_qualified_prices_in_agent_result():
    async def scenario():
        model, usage = planning_model(), RunUsage()
        page = Page(
            URL,
            "Public annual timetable",
            (FIXTURES / "discovery_annual_table.txt").read_text(),
            (),
            content_type="application/pdf",
        )
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 12),
            (URL,),
            reader=AsyncMock(return_value=page),
            searcher=no_search,
        )
        run = await run_discovery_agent(REQUEST, backend, model, usage=usage)
        result = backend.result(run, REQUEST)
        assert usage.requests == 3  # Planner only: no model interpretation of table columns.
        assert len(result["courses"]) == 3  # 3.5-4.0 row does not match a 3.0 request.
        assert all(session.price_usd is None for session in run.sessions)
        assert all("MEMBER:" in course["price"] for course in result["courses"])
        assert all(course["sessions"] for course in result["courses"])
        assert all(course["evidence_ids"] for course in result["courses"])
        assert not result["partial"]

    asyncio.run(scenario())


def test_refined_searches_have_separate_cache_entries():
    async def scenario():
        searcher = AsyncMock(return_value=[])
        backend = PublicSourceBackend(
            CourseExtractor(planning_model(), RunUsage(), 12), searcher=searcher
        )
        state = ResearchState(REQUEST, backend, DiscoveryLimits())
        await state.search_courses(REQUEST.city)
        await state.search_courses(REQUEST.city, "registration")
        await state.search_courses(REQUEST.city, "registration")
        assert searcher.await_count == 2
        assert searcher.call_args.args[-1] == "registration"

    asyncio.run(scenario())


def test_stop_cancels_active_agent_task(monkeypatch):
    started, cleaned, stop = threading.Event(), threading.Event(), threading.Event()

    async def slow(*args):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    monkeypatch.setattr("tennis_overview.agent_discovery.discover_async", slow)

    async def scenario():
        task = asyncio.create_task(run_until_stopped(AppConfig(), {}, stop))
        while not started.is_set():
            await asyncio.sleep(0)
        stop.set()
        with pytest.raises(AgentDiscoveryError, match="interrupted"):
            await asyncio.wait_for(task, 2)
        assert cleaned.is_set()

    asyncio.run(scenario())


def test_agent_worker_preserves_previous_snapshot_on_failure(tmp_path, monkeypatch):
    app = AppConfig(database_path=str(tmp_path / "bookings.db"), discovery_agent_enabled=True)
    service = DiscoveryService(app)
    row = service.add({"query": REQUEST.city})
    snapshot = {"courses": [], "sources": [], "checked_at": "2026-10-25T00:00:00Z"}
    with service.connect() as conn:
        conn.execute("UPDATE locations SET result=? WHERE id=?", (json.dumps(snapshot), row))

    def fail(*args):
        raise AgentDiscoveryError("Agent discovery failed; previous results retained.")

    monkeypatch.setattr("tennis_overview.agent_discovery.discover_with_agent", fail)
    assert service.run_one()
    assert service.list()[0]["result"] == snapshot
    assert service.list()[0]["status"] == "error"
    assert not (tmp_path / "bookings.db").exists()


def test_opt_in_dispatches_to_agent(monkeypatch):
    monkeypatch.setattr(
        "tennis_overview.agent_discovery.discover_with_agent", lambda *args: {"agent": True}
    )
    assert discover(AppConfig(discovery_agent_enabled=True), {}) == {"agent": True}
