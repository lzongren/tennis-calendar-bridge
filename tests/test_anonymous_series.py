from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from benchmarks.source_discovery import series_checks

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.browser_network import Resource
from tennis_overview.browser_protocol import BrowserRequest
from tennis_overview.browser_runtime import read_browser
from tennis_overview.courtreserve_discovery import courtreserve_observation
from tennis_overview.course_extraction import CourseExtractor
from tennis_overview.discovery_agent import (
    DiscoveryRequest,
    ResearchState,
    SourceUnavailable,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity

HTML = (
    Path(__file__).parent / "fixtures/discovery_benchmark/courtreserve_anonymous_series.html"
).read_text()
URL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
NOW = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
REQUEST = DiscoveryRequest(
    city="Example City", level="3.0", timezone="America/Los_Angeles", start=date(2026, 11, 3)
)


async def records(html=HTML, request=REQUEST):
    backend = PublicSourceBackend(
        AsyncMock(),
        reader=AsyncMock(return_value=BrowserDocument(URL, "Course", html, NOW)),
        clock=lambda: NOW,
    )
    ref = backend.register(URL, "Clinic", 0)
    document = await backend.read(ref.id, request)
    backend.extractor.extract.assert_not_awaited()
    return document


@pytest.mark.benchmark
def test_real_anonymous_series_survives_past_selected_occurrence():
    document = asyncio.run(records())
    assert [r.starts_at.isoformat() for r in document.records] == [
        f"2026-11-{day:02d}T18:30:00-08:00" for day in (9, 16, 23, 30)
    ]
    assert all(r.ends_at.hour == 19 and r.ends_at.minute == 50 for r in document.records)
    assert [r.availability for r in document.records] == ["full", "unknown", "unknown", "unknown"]
    assert all(str(r.price_usd) == "54.00" for r in document.records)
    assert all(r.waitlist_count is None and r.kind == "registration" for r in document.records)
    assert document.partial  # Visible rows do not establish exhaustive enumeration.
    assert len({r.session_key for r in document.records}) == 4
    assert "Mon, Nov 16" in document.text


@pytest.mark.benchmark
def test_agent_series_output_matches_independent_occurrence_oracle():
    async def scenario():
        turns = iter(
            [
                ToolCallPart("search_courses", {"city": REQUEST.city}),
                ToolCallPart("read_source", {"source_id": identity(URL)}),
                ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
            ]
        )

        async def respond(messages, info):
            assert not any(tool.name == "extract_courses" for tool in info.output_tools)
            return ModelResponse([next(turns)])

        model, usage = FunctionModel(respond), RunUsage()
        backend = PublicSourceBackend(
            CourseExtractor(model, usage, 12),
            (URL,),
            reader=AsyncMock(return_value=BrowserDocument(URL, "", HTML, NOW)),
            searcher=AsyncMock(return_value=[]),
            clock=lambda: NOW,
        )
        request = REQUEST.model_copy(update={"search_mode": "source_check"})
        run = await run_discovery_agent(request, backend, model, usage=usage)
        result = backend.result(run, request)
        assert all(series_checks(result["courses"], URL).values())
        assert usage.requests == 3 and result["partial"]
        # Regressions must fail the oracle even with valid source and type schemas.
        result["courses"][0]["price"] = "$99.00"
        assert not series_checks(result["courses"], URL)["row_owned_facts"]
        assert not series_checks([], URL)["row_owned_facts"]

    asyncio.run(scenario())


def test_rows_never_inherit_selected_price_status_or_time():
    soup = BeautifulSoup(HTML, "html.parser")
    soup.select_one('[data-testid="events-details-date-row-price-1"]').decompose()
    soup.select_one('[data-testid="events-details-date-row-time-2"]').string = "8:00p - 9:00p"
    document = asyncio.run(records(str(soup)))
    assert document.records[1].price_usd is None
    assert document.records[1].availability == "unknown"
    assert document.records[2].starts_at.hour == 20


def test_planner_receives_counts_not_unreadable_session_identifiers():
    document = asyncio.run(records())
    result = ResearchState.model_document(document)
    assert result["candidate_session_count"] == 4
    assert "candidate_session_keys" not in result
    assert all(record.session_key not in str(result) for record in document.records)
    assert result["source_id"] == identity(URL)


@pytest.mark.parametrize(
    "change", ["hidden", "missing_time", "bad_weekday", "foreign_field", "duplicate_field"]
)
def test_malformed_row_is_omitted_without_losing_other_verified_dates(change):
    soup = BeautifulSoup(HTML, "html.parser")
    row = soup.select_one('[data-testid="events-details-date-row-0"]')
    time = row.select_one('[data-testid="events-details-date-row-time-0"]')
    if change == "hidden":
        row["hidden"] = ""
    elif change == "missing_time":
        time.decompose()
    elif change == "foreign_field":
        time["data-testid"] = "events-details-date-row-time-1"
    elif change == "duplicate_field":
        row.append(BeautifulSoup(str(time), "html.parser"))
    else:
        row.select_one('[data-testid="events-details-date-row-date-0"]').string = "TUE 9 NOV"
    document = asyncio.run(records(str(soup)))
    assert [r.starts_at.day for r in document.records] == [16, 23, 30]
    assert document.partial


def test_yearless_rows_do_not_borrow_requested_year():
    document = asyncio.run(records(request=REQUEST.model_copy(update={"start": date(2027, 11, 3)})))
    assert not document.records


def test_normalized_source_url_is_used_for_all_series_rows():
    observation = courtreserve_observation(
        BrowserDocument(URL + "#synthetic-private-fragment", "", HTML, NOW), REQUEST, now=NOW
    )
    assert all(
        item.course is None or item.course.signup_url == URL for item in observation.additional
    )
    assert "synthetic-private" not in str(observation)


@pytest.mark.parametrize("conflict", [False, True])
def test_repeated_row_deduplicates_or_rejects_conflicting_facts(conflict):
    soup = BeautifulSoup(HTML, "html.parser")
    row = soup.select_one('[data-testid="events-details-date-row-0"]')
    duplicate = BeautifulSoup(str(row), "html.parser")
    if conflict:
        duplicate.select_one('[data-testid="events-details-date-row-price-0"]').string = "$99.00"
    soup.select_one('[data-testid="events-details-dates-list"]').append(duplicate)
    if conflict:
        with pytest.raises(SourceUnavailable, match="Conflicting registration"):
            asyncio.run(records(str(soup)))
    else:
        assert len(asyncio.run(records(str(soup))).records) == 4


def test_real_browser_sends_only_visible_row_fields_without_action_controls(tmp_path, monkeypatch):
    soup = BeautifulSoup(HTML, "html.parser")
    row = soup.select_one('[data-testid="events-details-date-row-1"]')
    row.append(
        BeautifulSoup(
            "<button>synthetic-private-action</button><span>synthetic-private-account</span>",
            "html.parser",
        )
    )
    soup.select_one('[data-testid="events-details-date-row-4"]')["hidden"] = ""
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(200, {"content-type": "text/html"}, str(soup).encode()),
    )
    reply = asyncio.run(
        read_browser(
            BrowserRequest(operation="read", url=URL),
            tmp_path / "unused",
            channel=os.environ.get("PLAYWRIGHT_CHANNEL", "chromium"),
        )
    )
    assert "synthetic-private" not in reply.html
    assert "date-row-date-0" in reply.html and "date-row-date-4" not in reply.html
    observation = courtreserve_observation(
        BrowserDocument(URL, "", reply.html, NOW), REQUEST, now=NOW
    )
    assert len(observation.additional) == 4
    assert not (tmp_path / "unused").exists()
