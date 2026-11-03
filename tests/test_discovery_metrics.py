from __future__ import annotations

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from benchmarks.discovery_metrics import DiscoveryMetrics, MeasuredModel


def test_measurements_use_monotonic_timeline_and_bound_storage():
    ticks = iter(range(1000))
    metrics = DiscoveryMetrics(clock=lambda: float(next(ticks)))
    for _ in range(130):
        with metrics.measure("search"):
            pass
    report = metrics.report()
    assert len(report["measurements"]) == 128
    assert report["dropped"] == 2
    assert report["measurements"][0] == {
        "stage": "search",
        "started_seconds": 1.0,
        "seconds": 1.0,
        "outcome": "ok",
        "input_tokens": None,
        "output_tokens": None,
    }
    # Consumers cannot mutate retained observations through a report.
    report["measurements"][0]["seconds"] = 999
    assert metrics.report()["measurements"][0]["seconds"] == 1


@pytest.mark.parametrize("stage", ["planner_model", "extractor_model"])
def test_model_measurement_forwards_requests_without_retaining_content(stage):
    messages = [ModelRequest(parts=[UserPromptPart("private-prompt-sentinel")])]
    response = ModelResponse(
        [TextPart("private-response-sentinel")],
        usage=RequestUsage(input_tokens=13, output_tokens=7),
    )

    async def respond(actual, info):
        assert actual == messages
        assert info.model_settings["temperature"] == 0
        return response

    metrics = DiscoveryMetrics()
    model = MeasuredModel(FunctionModel(respond), metrics, stage)
    result = asyncio.run(model.request(messages, {"temperature": 0}, ModelRequestParameters()))
    assert result is response
    sample = metrics.report()["measurements"][0]
    assert sample["input_tokens"] == 13 and sample["output_tokens"] == 7
    assert sample["stage"] == stage and sample["outcome"] == "ok"
    assert "private-" not in json.dumps(metrics.report())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_and_cancelled_model_calls_remain_measurable_without_error_text(cancel):
    error = asyncio.CancelledError("private-error") if cancel else ValueError("private-error")

    async def respond(messages, info):
        raise error

    metrics = DiscoveryMetrics()
    model = MeasuredModel(FunctionModel(respond), metrics, "extractor_model")
    with pytest.raises(type(error)) as caught:
        asyncio.run(model.request([], None, ModelRequestParameters()))
    assert caught.value is error
    sample = metrics.report()["measurements"][0]
    assert sample["outcome"] == ("cancelled" if cancel else "failed")
    assert sample["input_tokens"] is sample["output_tokens"] is None
    assert "private-error" not in json.dumps(metrics.report())
