"""Wire-level and local-authority checks for opt-in required read-tool hints."""

from __future__ import annotations

import asyncio
import json

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from benchmarks.agent_discovery import ReplayBackend, request, score
from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    SearchResults,
    SourceRef,
    run_discovery_agent,
)
from tennis_overview.private_model import PrivateModelConfig, private_model


class ChoiceBackend(ReplayBackend):
    async def search(self, city, request):
        self.search_calls.append(city)
        return SearchResults(
            sources=(
                self.documents["seasonal-schedule"].source,
                SourceRef(id="other", title="Another course source"),
            )
        )


@pytest.mark.benchmark
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("outcome", ["valid", "unknown-name", "unknown-id", "provider-rejection"])
def test_required_read_choice_is_opt_in_and_does_not_bypass_local_validation(enabled, outcome):
    async def scenario():
        backend = ChoiceBackend("baseline")
        requests = []

        def respond(incoming):
            payload = json.loads(incoming.content)
            requests.append(payload)
            names = [tool["function"]["name"] for tool in payload["tools"]]
            sole_read = names == ["read_source"]
            expected = "required" if enabled and sole_read else "auto"
            assert payload["tool_choice"] == expected, (names, payload["tool_choice"], expected)
            assert "fixture-private-key" not in json.dumps(payload)
            if outcome == "provider-rejection":
                return httpx.Response(422, json={"error": {"message": "private diagnostic"}})
            name = "read_source" if sole_read else "finish_discovery"
            args = (
                {"source_id": "seasonal-schedule"} if sole_read else {"outcome": "sources_checked"}
            )
            if outcome == "unknown-name":
                name = "searchread_source"
            elif outcome == "unknown-id":
                args = {"source_id": "undiscovered"}
            return httpx.Response(
                200,
                json={
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "local-alias",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "fixture-call",
                                        "type": "function",
                                        "function": {"name": name, "arguments": json.dumps(args)},
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
            api_key=SecretStr("fixture-private-key"),
            require_read_tool=enabled,
        )
        async with private_model(config, transport=httpx.MockTransport(respond)) as model:
            query = request().model_copy(update={"search_mode": "area", "nearby_cities": ()})
            if outcome == "valid":
                run = await run_discovery_agent(
                    query, backend, model, limits=DiscoveryLimits(searches=3)
                )
                checks = score(run, "baseline")
                assert all(checks[key] for key in ("facts", "evidence", "retrieval", "safe_tools"))
                assert run.partial  # The unrelated optional source was not read.
                assert len(requests) == 2
                assert backend.read_calls == ["seasonal-schedule", "dated-registration"]
            else:
                with pytest.raises(AgentDiscoveryError) as failure:
                    await run_discovery_agent(
                        query, backend, model, limits=DiscoveryLimits(searches=3)
                    )
                assert "private diagnostic" not in str(failure.value)
                assert not backend.read_calls
                assert 1 <= len(requests) <= 3

    asyncio.run(scenario())
