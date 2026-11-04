from __future__ import annotations

import asyncio
import socket
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from benchmarks import source_discovery
from benchmarks.network_guard import install_model_only_guard
from tennis_overview.discovery_agent import SourceUnavailable
from tennis_overview.discovery_sources import identity
from tennis_overview.private_model import PrivateModelConfig


def test_benchmark_network_audit_blocks_non_model_network_and_processes(monkeypatch):
    hooks = []
    monkeypatch.setattr(sys, "addaudithook", hooks.append)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("100.64.0.8", 443))]
    )
    counts = install_model_only_guard("https://model.example.ts.net/v1")
    audit = hooks[0]
    audit("socket.getaddrinfo", ("model.example.ts.net", 443))
    audit("socket.connect", (None, ("100.64.0.8", 443)))
    for event, args in [
        ("socket.getaddrinfo", ("collector.invalid", 443)),
        ("socket.connect", (None, ("100.64.0.9", 443))),
        ("socket.connect", (None, ("100.64.0.8", 8443))),
        ("subprocess.Popen", ("shell",)),
    ]:
        with pytest.raises(PermissionError):
            audit(event, args)
    assert counts == {
        "model_connections": 1,
        "blocked_dns": 1,
        "blocked_connections": 2,
        "blocked_processes": 1,
    }


@pytest.mark.parametrize("case", ["anonymous", "clinic-search"])
def test_live_benchmark_distinguishes_supplied_url_from_search(monkeypatch, case):
    client = Mock(read=AsyncMock(), search=AsyncMock())
    monkeypatch.setenv("TENNIS_DISCOVERY_BROWSER_URL", "https://browser.example.ts.net")
    monkeypatch.setenv("TENNIS_DISCOVERY_BROWSER_KEY", "synthetic-browser-key-for-tests-only")
    monkeypatch.setattr(source_discovery, "BrowserClient", lambda *args: client)
    monkeypatch.setattr(source_discovery, "CourseExtractor", lambda *args: Mock())

    @asynccontextmanager
    async def model_context(config):
        yield TestModel()

    async def inspect_backend(request, backend, model, **kwargs):
        from tennis_overview.discovery_agent import LIVE_DISCOVERY_LIMITS

        assert kwargs["limits"] is LIVE_DISCOVERY_LIMITS
        assert request.city == "San Jose, CA"
        await backend.reader("https://club.example.org/adults")
        client.read.assert_awaited_once_with("https://club.example.org/adults")
        results = await backend.searcher(
            request.city, request.level, request.start.year, "overview"
        )
        if case == "clinic-search":
            assert request.search_mode == "area"
            assert backend.initial_urls == ()
            client.search.assert_awaited_once_with(
                request.city, request.level, request.start.year, "overview"
            )
            assert source_discovery.BROWSER_URL not in request.model_dump_json()
        else:
            assert request.search_mode == "source_check"
            assert backend.initial_urls == (source_discovery.BROWSER_URL,)
            assert results == []
            client.search.assert_not_awaited()
        raise SourceUnavailable("Synthetic stopped run")

    monkeypatch.setattr(source_discovery, "private_model", model_context)
    monkeypatch.setattr(source_discovery, "run_discovery_agent", inspect_backend)
    config = PrivateModelConfig(
        base_url="https://model.example.ts.net/v1",
        model="synthetic-model",
        api_key=SecretStr("synthetic-model-key-for-tests-only"),
    )
    rows = asyncio.run(
        source_discovery.evaluate(
            config,
            live=True,
            repeats=1,
            case=case,
            source_url=source_discovery.BROWSER_URL if case == "anonymous" else "",
            expected_url=source_discovery.BROWSER_URL if case == "clinic-search" else "",
        )
    )
    assert rows[0]["error"] == "SourceUnavailable"
    assert [m["stage"] for m in rows[0]["stage_metrics"]["measurements"]] == [
        "source_read",
        "search",
    ]


@pytest.mark.parametrize(
    "flags",
    [[], ["--live-web"], ["--live-web", "--source-url", source_discovery.BROWSER_URL]],
)
def test_search_benchmark_rejects_replay_and_preseeded_sources(monkeypatch, tmp_path, flags):
    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark", "--case", "clinic-search", "--output", str(tmp_path / "out.json"), *flags],
    )
    with pytest.raises(SystemExit) as error:
        source_discovery.main()
    assert error.value.code == 2
    assert not (tmp_path / "out.json").exists()


def test_search_score_requires_same_club_event_and_occurrence():
    anchor = source_discovery.BROWSER_URL
    assert source_discovery.same_occurrence(anchor.replace("&tab=dates", ""), anchor)
    assert not source_discovery.same_occurrence(anchor.replace("resId=123", "resId=456"), anchor)
    assert not source_discovery.same_occurrence(anchor.replace("/12345/", "/54321/"), anchor)
    assert not source_discovery.same_occurrence(anchor.replace("/EXAMPLE?", "/OTHER?"), anchor)


@pytest.mark.benchmark
@pytest.mark.parametrize("case", ["anonymous", "extraction-deadline"])
def test_measured_replay_keeps_original_oracle_and_isolates_repetitions(monkeypatch, case):
    turns = iter(
        [
            ToolCallPart("search_courses", {"city": "Example City"}),
            *(
                [ToolCallPart("read_source", {"source_id": identity(source_discovery.REPLAY_URL)})]
                if case == "extraction-deadline"
                else []
            ),
            ToolCallPart("read_source", {"source_id": identity(source_discovery.BROWSER_URL)}),
            ToolCallPart("finish_discovery", {"outcome": "sources_checked"}),
        ]
        * 2
    )

    async def respond(messages, info):
        return ModelResponse([next(turns)])

    @asynccontextmanager
    async def model_context(config):
        yield FunctionModel(respond)

    monkeypatch.setattr(source_discovery, "private_model", model_context)
    config = PrivateModelConfig(
        base_url="http://127.0.0.1:8123/v1",
        model="synthetic-model",
        api_key=SecretStr("synthetic-model-key-for-tests-only"),
    )
    rows = asyncio.run(source_discovery.evaluate(config, live=False, repeats=2, case=case))
    assert len(rows) == 2
    for row in rows:
        assert all(row["checks"].values())
        extra = 1 if case == "extraction-deadline" else 0
        assert row["requests"] == 3 + extra and row["tool_calls"] == 2 + extra
        assert [m["stage"] for m in row["stage_metrics"]["measurements"]] == [
            "planner_model",
            "search",
            *(["planner_model", "source_read"] if extra else []),
            "planner_model",
            "source_read",
            "planner_model",
        ]
        assert row["stage_metrics"]["dropped"] == 0
