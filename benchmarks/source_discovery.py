"""Opt-in source-backed agent checks: real model, replay or explicitly live public web."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import SecretStr
from pydantic_ai.usage import RunUsage

from tennis_overview.course_extraction import CourseExtractor
from tennis_overview.discovery_agent import DiscoveryLimits, DiscoveryRequest, run_discovery_agent
from tennis_overview.discovery_sources import PublicSourceBackend
from tennis_overview.private_model import PrivateModelConfig, private_model
from tennis_overview.public_web import Page, page_from_html

from .network_guard import install_model_only_guard

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
REPLAY_URL = "https://club.example.org/adults"


def error_types(error: BaseException) -> list[str]:
    """Bounded structural diagnostics, never exception messages or response bodies."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    names: list[str] = []
    while pending and len(seen) < 12:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        names.append(type(current).__name__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:4])
        if current.__context__ is not None:
            pending.append(current.__context__)
    return names


async def empty_search(city: str, level: str, year: int, focus: str) -> list[dict[str, str]]:
    return []


async def evaluate(
    config: PrivateModelConfig, *, live: bool, repeats: int, case: str = "ongoing"
) -> list[dict[str, Any]]:
    observations = []
    async with private_model(config) as model:
        for repetition in range(repeats):
            request = DiscoveryRequest(
                city="San Jose, CA" if live else "Example City",
                level="NTRP 3.0",
                timezone="America/Los_Angeles",
                start=date(2026, 11, 1),
            )
            usage = RunUsage()
            limits = DiscoveryLimits(
                model_requests=24, tool_calls=30, tool_seconds=60, total_seconds=300
            )
            backend = PublicSourceBackend(CourseExtractor(model, usage, limits.model_requests))
            if not live:
                page = page_from_html(
                    REPLAY_URL, (FIXTURES / "discovery_ongoing.html").read_bytes()
                )
                if case == "quoted":
                    page = Page(
                        REPLAY_URL,
                        "Example Club",
                        "\n".join(
                            [
                                "Adult Coach Clinic",
                                "NTRP 2.5 - 3.0",
                                "$54",
                                "Monday November 2, 2026 6:30 PM - 7:50 PM",
                            ]
                        ),
                        (),
                    )

                async def replay_read(url: str) -> Page:
                    if url != REPLAY_URL:
                        raise ValueError("Replay source outside fixture")
                    return page

                backend.reader = replay_read
                backend.searcher = empty_search
                backend.initial_urls = (REPLAY_URL,)
            started = time.monotonic()
            try:
                run = await run_discovery_agent(request, backend, model, usage=usage, limits=limits)
                result = backend.result(run, request)
                courses = result["courses"]
                target = "www.sjsrc.com" if live else "club.example.org"
                matching = [
                    course
                    for course in courses
                    if urlsplit(course["source_url"]).hostname == target
                ]
                begins = {
                    session["starts_at"] for course in matching for session in course["sessions"]
                }
                checks = {
                    "wednesday_timetable": "2026-11-04T19:00:00-08:00" in begins,
                    "thursday_timetable": "2026-11-05T18:00:00-08:00" in begins,
                    "not_bookings": bool(matching)
                    and all(course["availability"] == "Not checked" for course in matching),
                    "source_evidence": bool(matching)
                    and all(course["evidence_ids"] for course in matching),
                    "request_budget": usage.requests <= limits.model_requests,
                }
                if case == "quoted":
                    checks.pop("wednesday_timetable")
                    checks.pop("thursday_timetable")
                    checks["quoted_time"] = "2026-11-02T18:30:00-08:00" in begins
                    checks["quoted_end"] = any(
                        session["ends_at"] == "2026-11-02T19:50:00-08:00"
                        for course in matching
                        for session in course["sessions"]
                    )
                    checks["quoted_price"] = any(course["price"] == "$54.00" for course in matching)
                observation = {
                    "repetition": repetition + 1,
                    "checks": checks,
                    "seconds": round(time.monotonic() - started, 2),
                    "requests": usage.requests,
                    "tool_calls": run.tool_calls,
                    "courses": len(courses),
                    "partial": result["partial"],
                    "denied_tool_attempts": sum(event.outcome == "denied" for event in run.audit),
                    "read_sources": sum(
                        source["status"] == "checked" for source in result["sources"]
                    ),
                }
            except Exception as exc:
                observation = {
                    "repetition": repetition + 1,
                    "error": type(exc).__name__,
                    "checks": {"completed": False},
                    "error_types": error_types(exc),
                    "requests": usage.requests,
                    "seconds": round(time.monotonic() - started, 2),
                    "registered_sources": len(backend.sources),
                    "read_sources": sum(s.status == "checked" for s in backend.sources.values()),
                }
            observations.append(observation)
            print(json.dumps(observation), flush=True)
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-web", action="store_true")
    parser.add_argument("--case", choices=("ongoing", "quoted"), default="ongoing")
    parser.add_argument("--repeats", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.live_web and args.case != "ongoing":
        parser.error("The quoted transcription is a replay case, not a live public page")
    config = PrivateModelConfig(
        base_url=os.environ["TENNIS_DISCOVERY_API_URL"],
        model=os.environ["TENNIS_DISCOVERY_MODEL"],
        api_key=SecretStr(os.environ["TENNIS_DISCOVERY_API_KEY"]),
    )
    os.umask(0o077)
    audit = install_model_only_guard(config.base_url) if not args.live_web else None
    with args.output.open("x", encoding="utf-8") as output:
        rows = asyncio.run(
            evaluate(config, live=args.live_web, repeats=args.repeats, case=args.case)
        )
        json.dump(
            {"live_web": args.live_web, "case": args.case, "rows": rows, "network_audit": audit},
            output,
            indent=2,
        )
    if not all(all(row["checks"].values()) for row in rows):
        raise SystemExit(1)
    if audit and any(value for key, value in audit.items() if key.startswith("blocked_")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
