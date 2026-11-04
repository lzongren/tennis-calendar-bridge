from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.discovery_agent import DiscoveryRequest, run_discovery_agent
from tennis_overview.discovery_sources import PublicSourceBackend, identity, related_links
from tennis_overview.public_web import Page, PageLink, page_from_html

DIRECTORY = "https://directory.example.org/club"
OPERATOR = "https://operator.example.org/"
DETAIL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"


def entity(**overrides):
    return {
        "@type": "SportsActivityLocation",
        "url": DIRECTORY,
        "name": "Example Activities",
        "sameAs": [OPERATOR],
        **overrides,
    }


def page(document, extra=""):
    body = (
        '<h1>Example Activities</h1><script type="application/ld+json">'
        + json.dumps(document)
        + "</script>"
        + extra
    )
    return page_from_html(DIRECTORY, body.encode())


@pytest.mark.benchmark
def test_public_directory_graph_retains_facility_reference_not_publisher():
    document = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "url": DIRECTORY,
                "sameAs": ["https://publisher.example.org/"],
            },
            entity(),
        ],
    }
    parsed = page(document, '<a href="/other-tennis-club">Other tennis club</a>')
    assert parsed.links == (OPERATOR, "https://directory.example.org/other-tennis-club")
    assert parsed.link_details[0] == PageLink(
        OPERATOR, "Facility reference: Example Activities", "same_as"
    )
    assert related_links(parsed)[0].url == OPERATOR
    assert "sameAs" not in parsed.text and not parsed.required_links
    assert TypeAdapter(Page).validate_json(json.dumps(asdict(parsed))) == parsed


@pytest.mark.parametrize("shape", ["single", "list", "graph"])
def test_inline_shapes_and_string_reference_are_supported(shape):
    node = entity(sameAs=OPERATOR)
    if shape == "graph":
        document = {"@context": "https://schema.org", "@graph": [node]}
    else:
        node["@context"] = "https://schema.org"
        document = [node] if shape == "list" else node
    assert page(document).links == (OPERATOR,)


@pytest.mark.parametrize(
    "changes",
    [
        {"@context": "https://untrusted.example.org/context"},
        {"@context": {"sameAs": "https://untrusted.example.org/predicate"}},
        {"@type": "Organization"},
        {"@type": {"bad": "SportsActivityLocation"}},
        {"url": "https://directory.example.org/another-club"},
        {"url": None},
        {"sameAs": {"url": OPERATOR}},
    ],
)
def test_unrelated_or_ambiguous_entities_do_not_add_links(changes):
    assert not page({"@context": "https://schema.org", **entity(), **changes}).links


@pytest.mark.parametrize(
    "reference",
    [
        "http://127.0.0.1/private",
        "https://private.example.ts.net/",
        "https://example.org/?token=synthetic",
        "https://example.org/Online/PublicIcal/Index/1",
        "javascript:alert(1)",
        "/relative",
        {"@id": OPERATOR},
        None,
    ],
)
def test_reference_admission_uses_existing_public_url_policy(reference):
    parsed = page({"@context": "https://schema.org", **entity(sameAs=[reference, OPERATOR])})
    assert parsed.links == (OPERATOR,)


def test_references_are_bounded_deduplicated_and_do_not_displace_registration_priority():
    refs = [OPERATOR, OPERATOR + "#duplicate", *[f"https://example.org/{i}" for i in range(20)]]
    parsed = page(
        {"@context": "https://schema.org", **entity(name="x" * 1000, sameAs=refs)},
        '<a href="/registration">Register</a><a href="/schedule.pdf">Schedule</a>',
    )
    assert len([link for link in parsed.link_details if link.relation == "same_as"]) == 7
    assert all(len(link.title) <= 240 for link in parsed.link_details)
    assert [link.title for link in related_links(parsed)[:2]] == ["Register", "Schedule"]


@pytest.mark.parametrize("raw", ["{broken", "[" * 1100, '"' + "x" * 65_536 + '"'])
def test_bad_metadata_does_not_break_ordinary_course_links(raw):
    parsed = page_from_html(
        DIRECTORY,
        (
            '<script type="application/ld+json">'
            + raw
            + '</script><a href="/adult">Adult clinic</a>'
        ).encode(),
    )
    assert parsed.links == ("https://directory.example.org/adult",)


@pytest.mark.benchmark
def test_directory_reference_reaches_original_anonymous_course_facts_in_replay():
    directory = page(
        {"@context": "https://schema.org", "@graph": [entity()]},
        '<a href="https://other.example.org/tennis">Other tennis directory</a>',
    )
    operator = Page(
        OPERATOR, "Adult clinics", "Public registration", (DETAIL,), required_links=(DETAIL,)
    )
    detail = BrowserDocument(
        DETAIL,
        "Clinic",
        (
            Path(__file__).parent
            / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
        ).read_text(),
        datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc),
    )
    reads = []

    async def read(url):
        reads.append(url)
        return {DIRECTORY: directory, OPERATOR: operator, DETAIL: detail}[url]

    backend = PublicSourceBackend(
        Mock(extract=AsyncMock(return_value=())),
        reader=read,
        searcher=AsyncMock(return_value=[{"url": DIRECTORY, "title": "Public facility directory"}]),
        clock=lambda: detail.captured_at,
    )

    def respond(messages, info):
        if reads == [DIRECTORY]:
            tool = next(tool for tool in info.function_tools if tool.name == "read_source")
            assert tool.parameters_json_schema["properties"]["source_id"]["enum"] == [
                identity(OPERATOR)
            ]
            return ModelResponse([ToolCallPart("read_source", {"source_id": identity(OPERATOR)})])
        assert reads == [DIRECTORY, OPERATOR, DETAIL]
        return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])

    query = DiscoveryRequest(
        city="Example City",
        level="2.5-3.0",
        start=date(2026, 11, 1),
        timezone="America/Los_Angeles",
        search_mode="area",
    )
    run = asyncio.run(run_discovery_agent(query, backend, FunctionModel(respond)))
    assert backend.initial_urls == ()
    assert reads == [DIRECTORY, OPERATOR, DETAIL]
    assert len(run.sessions) == 1
    session = run.sessions[0]
    assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
    assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
    assert session.price_usd == 54 and session.availability == "full"
    assert session.waitlist_count is None and session.user_waitlist_position is None
