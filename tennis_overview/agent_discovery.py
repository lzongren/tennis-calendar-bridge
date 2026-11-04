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
    LIVE_DISCOVERY_LIMITS,
    DiscoveryRequest,
    run_discovery_agent,
)
from .discovery_sources import PublicSourceBackend
from .models import AppConfig
from .private_model import PrivateModelConfig, private_model


async def discover_async(app: AppConfig, location: dict[str, Any]) -> dict[str, Any]:
    request = DiscoveryRequest(
        city=location["query"],
        level=location["level"],
        timezone=location["timezone"],
        start=datetime.now(ZoneInfo(location["timezone"])).date(),
    )
    limits = LIVE_DISCOVERY_LIMITS
    usage = RunUsage()
    config = PrivateModelConfig(
        base_url=app.discovery_api_url,
        model=app.discovery_model,
        api_key=SecretStr(app.discovery_api_key),
    )
    async with private_model(config) as model:
        extractor = CourseExtractor(model, usage, limits.model_requests)
        backend = PublicSourceBackend(
            extractor, tuple(location["sources"]), source_limit=limits.sources
        )
        if app.discovery_browser_url or app.discovery_browser_key:
            from .browser_client import BrowserClient

            browser = BrowserClient(app.discovery_browser_url, SecretStr(app.discovery_browser_key))
            backend.reader = browser.read
            backend.searcher = browser.search
            backend.refiner = browser.refine
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
