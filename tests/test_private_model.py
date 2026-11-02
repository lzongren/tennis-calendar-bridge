from __future__ import annotations

import asyncio
import gzip
import json

import httpx2 as httpx
import pytest
from pydantic import SecretStr, ValidationError
from pydantic_ai import Agent, ToolOutput

from tennis_overview.course_extraction import ExtractionReport

from tennis_overview.discovery_agent import AgentDiscoveryError
from tennis_overview.private_model import BoundedModelTransport, PrivateModelConfig, private_model

KEY = SecretStr("fake-key-only-for-test-fixtures")
ENDPOINT = "https://model.example.ts.net/v1"


def test_private_alias_receives_complete_nested_schema_without_refs():
    async def check():
        def handler(request):
            payload = json.loads(request.content)
            schema = payload["tools"][0]["function"]["parameters"]
            assert "$ref" not in json.dumps(schema)
            assert "$defs" not in schema
            assert "courses" in schema["required"]
            course = schema["properties"]["courses"]["items"]
            assert course["additionalProperties"] is False
            assert set(course["required"]) >= {
                "title_line",
                "evidence_start_line",
                "evidence_end_line",
                "year_text",
                "date_start_text",
                "date_end_text",
                "start_time_text",
                "end_time_text",
                "weekdays",
            }
            assert course["properties"]["evidence_start_line"]["minimum"] == 0
            assert course["properties"]["evidence_end_line"]["type"] == "integer"
            return httpx.Response(
                200,
                json={
                    "id": "fixture",
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
                                        "id": "call_fixture",
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

        config = PrivateModelConfig(base_url=ENDPOINT, model="local-alias", api_key=KEY)
        async with private_model(config, transport=httpx.MockTransport(handler)) as model:
            agent = Agent(model, output_type=ToolOutput(ExtractionReport, name="extract_courses"))
            result = await agent.run("No courses on this synthetic page.")
            assert result.output.courses == ()

    asyncio.run(check())


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://model.example.ts.net/v1",
        "https://user:pass@model.example.ts.net/v1",
        "https://model.example.ts.net/v1?key=secret",
        "http://127.0.0.1:0/v1",
    ],
)
def test_private_model_rejects_unsafe_endpoints(url):
    with pytest.raises(ValidationError):
        PrivateModelConfig(base_url=url, model="fixture", api_key=KEY)


def test_secret_not_in_config_representation():
    config = PrivateModelConfig(base_url=ENDPOINT, model="fixture", api_key=KEY)
    assert KEY.get_secret_value() not in repr(config)
    assert KEY.get_secret_value() not in config.model_dump_json()


@pytest.mark.parametrize(
    "target,body",
    [
        ("https://other.example.ts.net/v1/chat/completions", b"{}"),
        (ENDPOINT + "/models", b"{}"),
        (ENDPOINT + "/chat/completions", KEY.get_secret_value().encode()),
    ],
)
def test_transport_blocks_wrong_destinations_and_key_in_prompt(target, body):
    async def check():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(
            transport=BoundedModelTransport(httpx.MockTransport(handler), ENDPOINT, KEY)
        ) as client:
            with pytest.raises(AgentDiscoveryError):
                await client.post(target, content=body)
        assert not calls

    asyncio.run(check())


@pytest.mark.parametrize("compressed", [False, True])
def test_response_size_limit_including_compression(compressed):
    async def check():
        body = b"a" * 256001

        def handler(request):
            return httpx.Response(
                200,
                content=gzip.compress(body) if compressed else body,
                headers={"content-encoding": "gzip"} if compressed else {},
            )

        async with httpx.AsyncClient(
            transport=BoundedModelTransport(httpx.MockTransport(handler), ENDPOINT, KEY)
        ) as client:
            with pytest.raises(AgentDiscoveryError, match="size limit"):
                await client.post(ENDPOINT + "/chat/completions", content=b"{}")

    asyncio.run(check())


@pytest.mark.parametrize("status", [307, 429, 503])
def test_client_does_not_follow_redirect_or_retry(monkeypatch, status):
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters

    async def check():
        calls = []

        def handler(request):
            calls.append(request)
            assert request.headers["authorization"] == "Bearer " + KEY.get_secret_value()
            assert "x-stainless-os" not in request.headers
            assert "x-stainless-arch" not in request.headers
            return httpx.Response(status, headers={"Location": "https://collector.invalid"})

        config = PrivateModelConfig(base_url=ENDPOINT, model="fixture", api_key=KEY)
        async with private_model(config, transport=httpx.MockTransport(handler)) as model:
            with pytest.raises(Exception):
                await model.request(
                    [ModelRequest(parts=[UserPromptPart("public text")])],
                    None,
                    ModelRequestParameters(),
                )
        assert len(calls) == 1
        assert str(calls[0].url) == ENDPOINT + "/chat/completions"

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    platform_probes = []
    monkeypatch.setattr(
        "openai._base_client.get_platform", lambda: platform_probes.append(True) or "Unknown"
    )
    asyncio.run(check())
    assert platform_probes == []
