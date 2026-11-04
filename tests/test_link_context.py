from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from tennis_overview.discovery_agent import DiscoveryRequest
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.public_web import PageLink, page_from_html

URL = "https://operator.example.org/register"


def parse(body):
    return page_from_html(URL, body.encode())


@pytest.mark.benchmark
def test_repeated_registration_buttons_keep_their_own_location_heading():
    parsed = parse(
        "<h1>Register for classes</h1><main>"
        '<div><h5>North City</h5><div><a href="https://portal.example.org/1">'
        "RESERVE / REGISTER</a></div></div>"
        '<div><h5>South City</h5><div><a href="https://portal.example.org/2">'
        "RESERVE / REGISTER</a></div></div></main>"
    )
    assert parsed.link_details == (
        PageLink("https://portal.example.org/1", "North City: RESERVE / REGISTER"),
        PageLink("https://portal.example.org/2", "South City: RESERVE / REGISTER"),
    )
    assert parsed.required_links == ()
    assert parsed.text.count("RESERVE / REGISTER") == 2


@pytest.mark.parametrize(
    "body",
    [
        '<h1>Global title</h1><a href="/portal">Register</a>',
        '<div><h5>Other city</h5></div><div><a href="/portal">Register</a></div>',
        '<div><a href="/portal">Register</a><h5>Following heading</h5></div>',
        '<div><h5>Ambiguous city</h5><h5>Another city</h5><a href="/portal">Register</a></div>',
        '<div><h5>Far away</h5><div><div><div><a href="/portal">Register</a></div></div></div></div>',
        "<div><h5>Huge container</h5><p>" + "x" * 600 + '</p><a href="/portal">Register</a></div>',
        "<div><h5>Many siblings</h5>"
        + "<span>x</span>" * 33
        + '<a href="/portal">Register</a></div>',
    ],
)
def test_unowned_or_ambiguous_headings_are_not_attached(body):
    assert parse(body).link_details[0].title == "Register"


@pytest.mark.parametrize("label", ["South City registration", "Adult clinic details", ""])
def test_descriptive_or_empty_anchor_is_not_rewritten(label):
    assert (
        parse(f'<div><h5>South City</h5><a href="/portal">{label}</a></div>').link_details[0].title
        == label
    )


def test_heading_is_bounded_data_and_does_not_admit_unsafe_targets():
    parsed = parse(
        "<div><h5>" + "z" * 200 + '</h5><a href="/portal">Register now</a></div>'
        '<div><h5>Private source</h5><a href="http://127.0.0.1/admin">Register</a></div>'
    )
    assert len(parsed.link_details) == 1
    assert len(parsed.link_details[0].title) <= 240
    assert parsed.link_details[0].title.endswith(": Register now")
    assert parsed.link_details[0].relation == "anchor"


@pytest.mark.benchmark
def test_registration_source_labels_survive_backend_without_becoming_course_facts():
    async def scenario():
        page = parse(
            '<section><h2>South City</h2><a href="https://portal.example.org/2">Register</a></section>'
        )
        backend = PublicSourceBackend(
            Mock(extract=AsyncMock(return_value=())), reader=AsyncMock(return_value=page)
        )
        source = backend.register(URL, "Locations", 0)
        query = DiscoveryRequest(
            city="South City", level="3.0", start=date(2026, 11, 1), timezone="America/Los_Angeles"
        )
        document = await backend.read(source.id, query)
        assert document.links[0].title == "South City: Register"
        assert document.navigation_ids == (document.links[0].id,)
        assert not document.records and not document.required_ids

    asyncio.run(scenario())


@pytest.mark.benchmark
@pytest.mark.parametrize("correct_location,refine", [(True, False), (False, False), (True, True)])
def test_location_navigation_benchmark_checks_original_facts_not_just_label(
    correct_location, refine, monkeypatch
):
    from benchmarks import source_discovery

    refiner = AsyncMock(return_value=[])

    def respond(messages, info):
        refinement = next(
            (tool for tool in info.function_tools if tool.name == "refine_source"), None
        )
        if refine and refinement is not None and not refiner.await_count:
            source_id = refinement.parameters_json_schema["properties"]["source_id"]["enum"][0]
            return ModelResponse([ToolCallPart("refine_source", {"source_id": source_id})])
        tool = next((tool for tool in info.function_tools if tool.name == "read_source"), None)
        if tool is None:
            return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])
        legal = tool.parameters_json_schema["properties"]["source_id"]["enum"]
        target = identity(source_discovery.LOCATION_PORTAL)
        detail = identity(source_discovery.BROWSER_URL)
        if correct_location:
            selected = target if target in legal else detail if detail in legal else legal[0]
        else:
            selected = next((sid for sid in legal if sid != target), legal[0])
        assert selected in legal
        return ModelResponse([ToolCallPart("read_source", {"source_id": selected})])

    @asynccontextmanager
    async def model(config):
        yield FunctionModel(respond)

    monkeypatch.setattr(source_discovery, "private_model", model)
    monkeypatch.setattr(source_discovery, "empty_refinement", refiner)
    rows = asyncio.run(
        source_discovery.evaluate(None, live=False, repeats=1, case="location-navigation")
    )
    assert rows[0]["checks"]["location_selected_first"] is correct_location
    assert rows[0]["checks"]["original_registration_facts"]
    assert all(rows[0]["checks"].values()) is correct_location
    assert rows[0]["read_sources"] == 8
    assert refiner.await_count == int(refine)
