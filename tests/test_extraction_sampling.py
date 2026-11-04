from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import Mock

import httpx2 as httpx
import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from benchmarks import source_discovery
from benchmarks.source_discovery import configure_extraction_sampling
from tennis_overview.course_extraction import CourseExtractor
from tennis_overview.discovery_agent import DiscoveryRequest
from tennis_overview.discovery_sources import identity
from tennis_overview.private_model import PrivateModelConfig, private_model
from tennis_overview.public_web import Page


@pytest.mark.parametrize("profile", ["baseline", "qwen3-coder"])
def test_sampling_profile_changes_only_extractor_wire_parameters(profile):
    async def scenario():
        payloads = []

        def respond(request):
            payload = json.loads(request.content)
            payloads.append(payload)
            return httpx.Response(
                200,
                json={
                    "id": "synthetic",
                    "created": 0,
                    "model": "local-alias",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "synthetic-call",
                                        "type": "function",
                                        "function": {
                                            "name": "extract_courses",
                                            "arguments": '{"courses": []}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
            )

        config = PrivateModelConfig(
            base_url="https://model.example.ts.net/v1",
            model="local-alias",
            api_key=SecretStr("synthetic-test-key"),
        )
        async with private_model(config, transport=httpx.MockTransport(respond)) as model:
            extractor = CourseExtractor(model, RunUsage(), 2)
            original = dict(extractor.agent.model_settings)
            configure_extraction_sampling(extractor, profile)
            unchanged = CourseExtractor(model, RunUsage(), 2)
            assert unchanged.agent.model_settings == original
            await extractor.extract(
                Page(
                    "https://club.example.org/", "Public overview", "No courses on this page.", ()
                ),
                DiscoveryRequest(city="Example City", start=date(2026, 11, 1), timezone="UTC"),
            )
        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["max_completion_tokens"] == 3072
        assert payload["parallel_tool_calls"] is False and payload["tool_choice"] == "auto"
        assert [tool["function"]["name"] for tool in payload["tools"]] == ["extract_courses"]
        if profile == "baseline":
            assert payload["temperature"] == 0
            assert not {"top_p", "top_k", "repetition_penalty"} & payload.keys()
        else:
            assert payload["temperature"] == 0.7 and payload["top_p"] == 0.8
            assert payload["top_k"] == 20 and payload["repetition_penalty"] == 1.05

    asyncio.run(scenario())


def test_sampler_preserves_other_settings_without_mutating_the_input():
    extractor = CourseExtractor(
        FunctionModel(lambda messages, info: ModelResponse([])), RunUsage(), 3
    )
    settings = {**extractor.agent.model_settings, "extra_body": {"fixture": True}}
    extractor.agent.model_settings = settings
    configure_extraction_sampling(extractor, "qwen3-coder")
    assert settings["temperature"] == 0 and settings["extra_body"] == {"fixture": True}
    assert extractor.agent.model_settings["max_tokens"] == 3072
    assert extractor.agent.model_settings["extra_body"]["fixture"] is True
    assert extractor.request_limit == 3
    with pytest.raises(ValueError, match="Unknown"):
        configure_extraction_sampling(extractor, "arbitrary")


@pytest.mark.parametrize("settings", [lambda ctx: {}, {"extra_body": "not-an-object"}])
def test_sampler_rejects_unsupported_settings_before_inference(settings):
    extractor = CourseExtractor(
        FunctionModel(lambda messages, info: ModelResponse([])), RunUsage(), 3
    )
    extractor.agent.model_settings = settings
    with pytest.raises(ValueError, match="Sampling requires"):
        configure_extraction_sampling(extractor, "qwen3-coder")
    assert extractor.agent.model_settings is settings


@pytest.mark.benchmark
@pytest.mark.parametrize("profile", ["baseline", "qwen3-coder"])
def test_sampling_retains_original_course_oracle_and_planner_settings(monkeypatch, profile):
    requests = []

    def respond(messages, info):
        names = {tool.name for tool in [*info.function_tools, *info.output_tools]}
        if "extract_courses" in names:
            assert info.model_settings["max_tokens"] == 3072
            assert info.model_settings["temperature"] == (0.7 if profile == "qwen3-coder" else 0)
            return ModelResponse(
                [
                    ToolCallPart(
                        "extract_courses",
                        {
                            "courses": [
                                {
                                    "title_line": 0,
                                    "level": "NTRP 2.5 - 3.0",
                                    "price": "$54",
                                    "evidence_start_line": 0,
                                    "evidence_end_line": 3,
                                    "year_text": "2026",
                                    "date_start_text": "November 2, 2026",
                                    "date_end_text": "",
                                    "start_time_text": "6:30 PM",
                                    "end_time_text": "7:50 PM",
                                    "weekdays": ["Monday"],
                                }
                            ]
                        },
                    )
                ]
            )
        assert info.model_settings["temperature"] == 0 and info.model_settings["max_tokens"] == 1024
        requests.append(names)
        if len(requests) == 1:
            return ModelResponse([ToolCallPart("search_courses", {"city": "Example City"})])
        if len(requests) == 2:
            return ModelResponse(
                [
                    ToolCallPart(
                        "read_source",
                        {"source_id": identity(source_discovery.REPLAY_URL)},
                    )
                ]
            )
        return ModelResponse([ToolCallPart("finish_discovery", {"outcome": "sources_checked"})])

    @asynccontextmanager
    async def model(config):
        yield FunctionModel(respond)

    monkeypatch.setattr(source_discovery, "private_model", model)
    rows = asyncio.run(
        source_discovery.evaluate(
            Mock(),
            live=False,
            repeats=1,
            case="quoted",
            extractor_sampling=profile,
        )
    )
    assert all(rows[0]["checks"].values())
