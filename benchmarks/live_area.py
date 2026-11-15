"""Run an anonymous area search without exposing reference answers to the agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from pydantic_ai.usage import RunUsage

from tennis_overview.course_extraction import CourseExtractor
from tennis_overview.discovery_agent import (
    LIVE_DISCOVERY_LIMITS,
    AgentDiscoveryError,
    DiscoveryRequest,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend, identity
from tennis_overview.private_model import PrivateModelConfig, private_model


async def observe(request: DiscoveryRequest) -> dict[str, Any]:
    config = PrivateModelConfig(
        base_url=os.environ["TENNIS_DISCOVERY_API_URL"],
        model=os.environ["TENNIS_DISCOVERY_MODEL"],
        api_key=SecretStr(os.environ["TENNIS_DISCOVERY_API_KEY"]),
    )
    usage = RunUsage()
    limits = LIVE_DISCOVERY_LIMITS
    async with private_model(config) as model:
        trace: dict[str, list[str]] = {}

        def observe_stage(stage: str, url: str) -> None:
            stages = trace.setdefault(identity(url), [])
            if stage not in stages:
                stages.append(stage)

        backend = PublicSourceBackend(
            CourseExtractor(model, usage, limits.model_requests),
            source_limit=limits.sources,
            trace=observe_stage,
        )
        started = time.monotonic()
        try:
            run = await run_discovery_agent(request, backend, model, usage=usage, limits=limits)
        except AgentDiscoveryError as error:
            return {
                "run_failed": True,
                "failure": "read_budget"
                if "read budget exhausted" in str(error)
                else "agent_error",
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "model_requests": usage.requests,
                "source_statuses": dict(Counter(s.status for s in backend.sources.values())),
                "sources": [{"url": s.url, "status": s.status} for s in backend.sources.values()],
                "courses": [],
                "link_stages": trace,
            }
        result = backend.result(run, request)
        result["link_stages"] = trace
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        result["audit_counts"] = dict(Counter(f"{e.tool}:{e.outcome}" for e in run.audit))
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True)
    parser.add_argument("--level", required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = DiscoveryRequest(
        city=args.city, level=args.level, start=args.date, timezone=args.timezone
    )
    os.umask(0o077)
    with args.output.open("x", encoding="utf-8") as output:
        result = asyncio.run(observe(request))
        json.dump(result, output, allow_nan=False)
    if result.get("run_failed"):
        raise SystemExit("Live search failed; no result to score.")


if __name__ == "__main__":
    main()
