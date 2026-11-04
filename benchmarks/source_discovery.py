"""Opt-in source-backed agent checks: real model, replay or explicitly live public web."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import SecretStr
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.browser_client import BrowserClient
from tennis_overview.browser_protocol import read_url
from tennis_overview.course_extraction import CourseCandidate, CourseExtractor
from tennis_overview.discovery_agent import (
    LIVE_DISCOVERY_LIMITS,
    DiscoveryRequest,
    run_discovery_agent,
)
from tennis_overview.discovery_sources import PublicSourceBackend
from tennis_overview.private_model import PrivateModelConfig, private_model
from tennis_overview.public_web import Page, PageLink, page_from_html

from .discovery_metrics import DiscoveryMetrics, MeasuredModel, error_types
from .network_guard import install_model_only_guard

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
REPLAY_URL = "https://club.example.org/adults"
BROWSER_URL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
CAPTURE_TIME = datetime(2026, 11, 2, 18, tzinfo=timezone.utc)
ExtractionSampling = Literal["baseline", "qwen3-coder"]


def configure_extraction_sampling(extractor: CourseExtractor, profile: ExtractionSampling) -> None:
    """Benchmark-only sampling; do not change production defaults or evidence policy."""
    if profile == "baseline":
        return
    if profile != "qwen3-coder":
        raise ValueError("Unknown extraction sampling profile")
    current = extractor.agent.model_settings
    if callable(current):
        raise ValueError("Sampling requires static model settings")
    settings: ModelSettings = current or {}
    extra = settings.get("extra_body") or {}
    if not isinstance(extra, dict):
        raise ValueError("Sampling requires object-valued model settings")
    extractor.agent.model_settings = {
        **settings,
        "temperature": 0.7,
        "top_p": 0.8,
        "extra_body": {**extra, "top_k": 20, "repetition_penalty": 1.05},
    }


class StalledExtractor(CourseExtractor):
    """Controlled extraction stall; only planning uses the real model in this case."""

    cancelled = 0

    async def extract(self, page: Page, request: DiscoveryRequest) -> tuple[CourseCandidate, ...]:
        try:
            return await asyncio.Future[tuple[CourseCandidate, ...]]()
        finally:
            self.cancelled += 1


class EmptyDirectoryExtractor(CourseExtractor):
    """Fixed empty directory evidence; refinement replay measures the real planner only."""

    async def extract(self, page: Page, request: DiscoveryRequest) -> tuple[CourseCandidate, ...]:
        return ()


async def empty_search(city: str, level: str, year: int, focus: str) -> list[dict[str, str]]:
    return []


def same_occurrence(actual: str, expected: str) -> bool:
    """Compare registration identity, ignoring UI tab and query ordering."""
    found, anchor = urlsplit(actual), urlsplit(expected)
    return (
        found.scheme == anchor.scheme == "https"
        and found.netloc == anchor.netloc
        and found.path == anchor.path
        and parse_qs(found.query).get("resId") == parse_qs(anchor.query).get("resId")
    )


def series_checks(courses: list[dict[str, Any]], source_url: str) -> dict[str, bool]:
    """Frozen November 3 observation, not a promise of current availability."""
    matched = [c for c in courses if same_occurrence(c["source_url"], source_url)]
    rows = sorted(
        (s["starts_at"], s["ends_at"], c["price"], c["availability"])
        for c in matched
        for s in c["sessions"]
    )
    expected = [
        (
            f"2026-11-{day:02d}T18:30:00-08:00",
            f"2026-11-{day:02d}T19:50:00-08:00",
            "$54.00",
            "full" if day == 9 else "Not checked",
        )
        for day in (9, 16, 23, 30)
    ]
    return {
        "row_owned_facts": rows == expected,
        "clinic_identity": bool(matched)
        and all(
            re.search(r"Adult Coach.*Clinic.*NTRP 2\.5 - 3\.0.*MON 6:30PM - Fall 2", c["title"])
            is not None
            for c in matched
        ),
        "source_evidence": bool(matched) and all(c["evidence_ids"] for c in matched),
        "no_queue_inference": bool(matched)
        and all(
            c["waitlist_count"] is None and c["user_waitlist_position"] is None for c in matched
        ),
    }


async def evaluate(
    config: PrivateModelConfig,
    *,
    live: bool,
    repeats: int,
    case: str = "ongoing",
    source_url: str = "",
    expected_url: str = "",
    extractor_sampling: ExtractionSampling = "baseline",
) -> list[dict[str, Any]]:
    observations = []
    async with private_model(config) as model:
        for repetition in range(repeats):
            metrics = DiscoveryMetrics()
            planner = MeasuredModel(model, metrics, "planner_model")
            extractor = MeasuredModel(model, metrics, "extractor_model")
            request = DiscoveryRequest(
                city="San Jose, CA" if live else "Example City",
                level="NTRP 3.0",
                timezone="America/Los_Angeles",
                start=date(2026, 11, 3) if case == "anonymous-series" else date(2026, 11, 1),
                search_mode="area"
                if case == "source-refinement"
                or live
                and case not in {"anonymous", "anonymous-series"}
                else "source_check",
            )
            usage = RunUsage()
            limits = LIVE_DISCOVERY_LIMITS
            if case == "extraction-deadline":
                limits = limits.model_copy(update={"tool_seconds": 1})
            backend = PublicSourceBackend(
                CourseExtractor(extractor, usage, limits.model_requests),
                source_limit=limits.sources,
            )
            if live and case in {"anonymous", "anonymous-series", "clinic-search"}:
                client = BrowserClient(
                    os.environ["TENNIS_DISCOVERY_BROWSER_URL"],
                    SecretStr(os.environ["TENNIS_DISCOVERY_BROWSER_KEY"]),
                )
                backend.reader = client.read
                backend.refiner = client.refine
                if case in {"anonymous", "anonymous-series"}:
                    backend.searcher = empty_search
                    backend.initial_urls = (read_url(source_url),)
                else:
                    backend.searcher = client.search
            if not live:
                replay_url = (
                    BROWSER_URL
                    if case in {"browser", "anonymous", "anonymous-series"}
                    else REPLAY_URL
                )
                page: Page | BrowserDocument = page_from_html(
                    REPLAY_URL, (FIXTURES / "discovery_ongoing.html").read_bytes()
                )
                if case == "extraction-deadline":
                    page = Page(
                        REPLAY_URL,
                        "Adult tennis courses",
                        "Published adult tennis programs and registration links.",
                        (BROWSER_URL,),
                        link_details=(PageLink(BROWSER_URL, "Adult clinic registration"),),
                    )
                    backend.extractor = StalledExtractor(extractor, usage, limits.model_requests)
                    backend.clock = lambda: CAPTURE_TIME
                if case == "source-refinement":
                    page = Page(
                        REPLAY_URL,
                        "Directory",
                        "No dated courses on this public directory.",
                        (),
                        heading="Example Activities: Example City",
                    )
                    backend.extractor = EmptyDirectoryExtractor(
                        extractor, usage, limits.model_requests
                    )
                    backend.clock = lambda: CAPTURE_TIME
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
                elif case in {"browser", "anonymous", "anonymous-series"}:
                    fixture = (
                        "courtreserve_anonymous_detail.html"
                        if case == "anonymous"
                        else "courtreserve_detail.html"
                    )
                    if case == "anonymous-series":
                        fixture = "courtreserve_anonymous_series.html"
                    capture_time = (
                        datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
                        if case == "anonymous-series"
                        else CAPTURE_TIME
                    )
                    page = BrowserDocument(
                        BROWSER_URL,
                        "Synthetic browser title",
                        (FIXTURES / "discovery_benchmark" / fixture).read_text(),
                        capture_time,
                    )
                    backend.clock = lambda: capture_time

                async def replay_read(url: str) -> Page | BrowserDocument:
                    if case in {"extraction-deadline", "source-refinement"} and url == BROWSER_URL:
                        return BrowserDocument(
                            BROWSER_URL,
                            "Anonymous clinic",
                            (
                                FIXTURES / "discovery_benchmark/courtreserve_anonymous_detail.html"
                            ).read_text(),
                            CAPTURE_TIME,
                        )
                    if case == "source-refinement" and url == "https://operator.example.org/adults":
                        return Page(
                            url,
                            "Adult lessons",
                            "Dated adult clinic registration",
                            (BROWSER_URL,),
                            required_links=(BROWSER_URL,),
                        )
                    if url != replay_url:
                        raise ValueError("Replay source outside fixture")
                    return page

                backend.reader = replay_read
                backend.searcher = empty_search
                backend.initial_urls = (replay_url,)
                if case == "extraction-deadline":

                    async def replay_search(
                        city: str, level: str, year: int, focus: str
                    ) -> list[dict[str, str]]:
                        return [{"url": REPLAY_URL, "title": "Adult tennis courses"}]

                    backend.searcher = replay_search
                    backend.initial_urls = ()  # Optional overview, not a required detail.
                if case == "source-refinement":

                    async def refinement_search(
                        city: str, level: str, year: int, focus: str
                    ) -> list[dict[str, str]]:
                        return [{"url": REPLAY_URL, "title": "Public operator directory"}]

                    async def refinement(
                        subject: str, city: str, year: int
                    ) -> list[dict[str, str]]:
                        if (subject, city, year) != (
                            "Example Activities Example City",
                            "Example City",
                            2026,
                        ):
                            raise ValueError("Refinement outside observed fixture")
                        return [
                            {"url": "https://operator.example.org/adults", "title": "Adult lessons"}
                        ]

                    backend.searcher = refinement_search
                    backend.refiner = refinement
                    backend.initial_urls = ()
            configure_extraction_sampling(backend.extractor, extractor_sampling)
            reader, searcher, refiner = backend.reader, backend.searcher, backend.refiner

            async def measured_read(url: str) -> Page | BrowserDocument:
                with metrics.measure("source_read"):
                    return await reader(url)

            async def measured_search(
                city: str, level: str, year: int, focus: str
            ) -> list[dict[str, str]]:
                with metrics.measure("search"):
                    return await searcher(city, level, year, focus)

            backend.reader = measured_read
            backend.searcher = measured_search

            async def measured_refine(subject: str, city: str, year: int) -> list[dict[str, str]]:
                with metrics.measure("search"):
                    return await refiner(subject, city, year)

            backend.refiner = measured_refine
            started = time.monotonic()
            try:
                run = await run_discovery_agent(
                    request, backend, planner, usage=usage, limits=limits
                )
                result = backend.result(run, request)
                courses = result["courses"]
                target = "www.sjsrc.com" if live else "club.example.org"
                if case in {
                    "browser",
                    "anonymous",
                    "clinic-search",
                    "extraction-deadline",
                    "source-refinement",
                }:
                    target = "app.courtreserve.com"
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
                    checks["quoted_title"] = any(
                        course["title"] == "Adult Coach Clinic" for course in matching
                    )
                elif case in {
                    "browser",
                    "anonymous",
                    "clinic-search",
                    "extraction-deadline",
                    "source-refinement",
                }:
                    expected = json.loads(
                        (FIXTURES / "discovery_benchmark" / "nov2_registration.json").read_text()
                    )["expected"]
                    waitlist_count = expected["waitlist_count"] if case == "browser" else None
                    if case == "clinic-search":
                        # Other courses are expected in a genuine discovery run.
                        # Isolate the anchor by identity before checking its facts.
                        matching = [
                            course
                            for course in matching
                            if same_occurrence(course["source_url"], expected_url)
                        ]
                    # Additional row-owned dates are independent courses. Preserve
                    # the original occurrence's exact factual oracle, not list order.
                    matching = [
                        course
                        for course in matching
                        if any(
                            session["starts_at"] == expected["starts_at"]
                            for session in course["sessions"]
                        )
                    ]
                    checks = {
                        "original_registration_facts": len(matching) == 1
                        and matching[0]["sessions"]
                        == [{"starts_at": expected["starts_at"], "ends_at": expected["ends_at"]}]
                        and matching[0]["price"] == "$" + expected["price_usd"]
                        and matching[0]["availability"] == expected["availability"]
                        and matching[0]["waitlist_count"] == waitlist_count,
                        "no_personal_queue_inference": bool(matching)
                        and all(c["user_waitlist_position"] is None for c in matching),
                        "source_evidence": bool(matching)
                        and all(c["evidence_ids"] for c in matching),
                        "series_incomplete": result["partial"],
                        "request_budget": usage.requests <= limits.model_requests,
                    }
                    if case in {
                        "anonymous",
                        "clinic-search",
                        "extraction-deadline",
                        "source-refinement",
                    }:
                        checks["clinic_identity"] = len(matching) == 1 and bool(
                            re.search(
                                r"Adult Coach.*Clinic.*NTRP 2\.5 - 3\.0.*MON 6:30PM - Fall 2",
                                matching[0]["title"],
                            )
                        )
                    if case == "source-refinement":
                        checks["refinement_used_once"] = (
                            sum(
                                event.tool == "refine_source" and event.outcome == "ok"
                                for event in run.audit
                            )
                            == 1
                        )
                    if case == "extraction-deadline":
                        checks["deadline_navigation_retained"] = any(
                            source["url"] == REPLAY_URL
                            and source["status"] == "unavailable"
                            and source["failure_reason"] == "evidence_invalid"
                            for source in result["sources"]
                        )
                        checks["extraction_cancelled_once"] = (
                            isinstance(backend.extractor, StalledExtractor)
                            and backend.extractor.cancelled == 1
                        )
                if case == "anonymous-series":
                    checks = {
                        **series_checks(courses, source_url if live else BROWSER_URL),
                        "series_incomplete": result["partial"],
                        "request_budget": usage.requests <= limits.model_requests,
                    }
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
            observation["stage_metrics"] = metrics.report()
            observations.append(observation)
            print(json.dumps(observation), flush=True)
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-web", action="store_true")
    parser.add_argument(
        "--case",
        choices=(
            "ongoing",
            "quoted",
            "browser",
            "anonymous",
            "anonymous-series",
            "clinic-search",
            "extraction-deadline",
            "source-refinement",
        ),
        default="ongoing",
    )
    parser.add_argument(
        "--source-url",
        default="",
        help="Known public detail URL for the live anonymous case; not a search benchmark",
    )
    parser.add_argument(
        "--expected-source-url",
        default="",
        help="Scorer-only anchor for clinic-search; never supplied to the agent or search backend",
    )
    parser.add_argument("--repeats", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--extractor-sampling",
        choices=("baseline", "qwen3-coder"),
        default="baseline",
        help="Experimental extraction-only sampling; does not alter planner or production defaults",
    )
    args = parser.parse_args()
    if args.live_web and args.case not in {
        "ongoing",
        "anonymous",
        "anonymous-series",
        "clinic-search",
    }:
        parser.error("Transcriptions are replay cases, not live public pages")
    if args.case == "clinic-search" and not args.live_web:
        parser.error("clinic-search requires --live-web; source replay cannot establish recall")
    if bool(args.source_url) != (args.live_web and args.case in {"anonymous", "anonymous-series"}):
        parser.error("--source-url is required only for live anonymous detail/series cases")
    if bool(args.expected_source_url) != (args.case == "clinic-search"):
        parser.error("--expected-source-url is required only for clinic-search scoring")
    if args.source_url:
        read_url(args.source_url)
    if args.expected_source_url:
        read_url(args.expected_source_url)
    config = PrivateModelConfig(
        base_url=os.environ["TENNIS_DISCOVERY_API_URL"],
        model=os.environ["TENNIS_DISCOVERY_MODEL"],
        api_key=SecretStr(os.environ["TENNIS_DISCOVERY_API_KEY"]),
    )
    os.umask(0o077)
    audit = install_model_only_guard(config.base_url) if not args.live_web else None
    with args.output.open("x", encoding="utf-8") as output:
        rows = asyncio.run(
            evaluate(
                config,
                live=args.live_web,
                repeats=args.repeats,
                case=args.case,
                source_url=args.source_url,
                expected_url=args.expected_source_url,
                extractor_sampling=args.extractor_sampling,
            )
        )
        json.dump(
            {
                "live_web": args.live_web,
                "case": args.case,
                "extractor_sampling": args.extractor_sampling,
                "rows": rows,
                "network_audit": audit,
            },
            output,
            indent=2,
        )
    if not all(all(row["checks"].values()) for row in rows):
        raise SystemExit(1)
    if audit and any(value for key, value in audit.items() if key.startswith("blocked_")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
