"""Application entry point for source-backed Pydantic AI discovery."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_ai.usage import RunUsage

from .course_extraction import CourseExtractor
from .discovery_agent import (
    AgentDiscoveryError,
    DiscoveryLimits,
    DiscoveryRequest,
    run_discovery_agent,
)
from .discovery_sources import PublicSourceBackend
from .models import AppConfig
from .private_model import PrivateModelConfig, private_model
from .search_api import configured_searcher


async def discover_async(app: AppConfig, location: dict[str, Any]) -> dict[str, Any]:
    request = DiscoveryRequest(
        city=location["query"],
        level=location["level"],
        timezone=location["timezone"],
        start=datetime.now(ZoneInfo(location["timezone"])).date(),
    )
    limits = DiscoveryLimits(model_requests=24, tool_calls=30, tool_seconds=60, total_seconds=300)
    usage = RunUsage()
    searcher = configured_searcher(app.discovery_search_provider, app.discovery_search_api_key)
    config = PrivateModelConfig(
        base_url=app.discovery_api_url,
        model=app.discovery_model,
        api_key=SecretStr(app.discovery_api_key),
    )
    async with private_model(config) as model:
        extractor = CourseExtractor(model, usage, limits.model_requests)
        backend = PublicSourceBackend(extractor, tuple(location["sources"]), searcher=searcher)
        result = await run_discovery_agent(request, backend, model, limits=limits, usage=usage)
        return backend.result(result, request)


async def run_until_stopped(
    app: AppConfig,
    location: dict[str, Any],
    stop: threading.Event | None,
) -> dict[str, Any]:
    if stop and stop.is_set():
        raise AgentDiscoveryError("Discovery interrupted; previous results retained.")
    task = asyncio.create_task(discover_async(app, location))
    try:
        while not task.done():
            if stop and stop.is_set():
                task.cancel()
                raise AgentDiscoveryError("Discovery interrupted; previous results retained.")
            await asyncio.wait({task}, timeout=0.1)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def discover_with_agent(
    app: AppConfig,
    location: dict[str, Any],
    stop: threading.Event | None = None,
) -> dict[str, Any]:
    return asyncio.run(run_until_stopped(app, location, stop))
