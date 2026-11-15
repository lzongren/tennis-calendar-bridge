"""Cancellable document reads for the agent; credentials never enter the child env."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date
from dataclasses import dataclass

from pydantic import TypeAdapter

from .discovery_agent import SourceUnavailable, TransientSourceError
from .public_web import Page, public_url
from .venue_schedules import Reader, Trace, index_scope, venue_details

MAX_OUTPUT = 1_000_000


async def _read_output(process: asyncio.subprocess.Process) -> bytes:
    assert process.stdout is not None
    output = bytearray()
    while chunk := await process.stdout.read(16_384):
        output.extend(chunk)
        if len(output) > MAX_OUTPUT:
            raise SourceUnavailable("Public document output exceeded limit")
    await process.wait()
    return bytes(output)


async def read_public_document(url: str, *, timeout: float = 20) -> Page:
    """Kill and reap the reader on timeout/cancellation, including blocked DNS reads.

    A child process limits parser damage and resource use, not an OS security sandbox.
    It performs public GETs only with validated DNS and no inherited API credentials.
    """
    url = public_url(url)
    output = await run_public_worker("document", url.encode("utf-8"), timeout=timeout)
    try:
        page = TypeAdapter(Page).validate_json(output)
        public_url(page.url)
        return page
    except ValueError:
        raise SourceUnavailable("Public document could not be read") from None


async def search_public_courses(
    city: str,
    level: str,
    year: int,
    focus: str = "overview",
    *,
    subject: str = "",
    on_date: date | None = None,
    venue_renderer: Reader | None = None,
    trace: Trace | None = None,
) -> list[dict[str, str]]:
    """Anonymous public search, without inheriting model or portal credentials."""
    if on_date is not None and focus == "registration" and not subject:
        try:
            payload = json.dumps(
                {"city": city, "level": level, "date": on_date.isoformat()}
            ).encode()
            raw = await run_public_worker("directory", payload, timeout=30)
            rows = TypeAdapter(list[dict[str, str]]).validate_json(raw)
            if 0 < len(rows) <= 12:
                cards = [
                    {
                        "url": public_url(r["url"]),
                        "title": r["title"][:240],
                        "listing": public_url(r["listing"]),
                        "club": r["club"][:240],
                    }
                    for r in rows
                    if "venue" not in r
                ]

                async def resolve(cutoff: dict[str, str]) -> list[dict[str, str]]:
                    async def index(
                        club: str, town: str, day: date, title: str
                    ) -> list[dict[str, str]]:
                        return await venue_index(club, town, day, title, trace=trace)

                    try:
                        async with asyncio.timeout(32):
                            return await venue_details(
                                cutoff,
                                on_date,
                                index=index,
                                read=read_public_document,
                                render=venue_renderer,
                                trace=trace or (lambda *_: None),
                            )
                    except (SourceUnavailable, TimeoutError, ValueError, KeyError):
                        return []

                cutoffs = [r for r in rows if "venue" in r][:2]
                recovered = await asyncio.gather(*(resolve(r) for r in cutoffs))
                merged: dict[str, dict[str, str]] = {}
                for r in [*(v for group in recovered for v in group), *cards]:
                    merged.setdefault(r["url"], r)
                if merged:
                    return list(merged.values())[:8]
        except (SourceUnavailable, ValueError, KeyError):
            # A missing/unsupported region does not disable ordinary public search.
            pass
    output = await run_public_worker(
        "search",
        json.dumps(
            {
                "city": city,
                "level": level,
                "year": year,
                "focus": focus,
                **({"subject": subject} if subject else {}),
                **({"on_date": on_date.isoformat()} if on_date else {}),
            }
        ).encode("utf-8"),
        timeout=20,
    )
    try:
        results = TypeAdapter(list[dict[str, str]]).validate_json(output)
        if len(results) > 8:
            raise ValueError("Search result limit exceeded")
        return [{"url": public_url(row["url"]), "title": row["title"][:240]} for row in results]
    except (ValueError, KeyError):
        raise SourceUnavailable("Public search failed") from None


async def refine_public_courses(subject: str, city: str, year: int) -> list[dict[str, str]]:
    from .public_web import validate_search_subject

    return await search_public_courses(
        city, "", year, "registration", subject=validate_search_subject(subject)
    )


@dataclass(frozen=True)
class IndexPage:
    anchors: list[str]
    rows: list[dict[str, str]]
    recognized: bool


async def venue_index(
    club: str, town: str, day: date, title: str, *, trace: Trace | None = None
) -> list[dict[str, str]]:
    scope = index_scope(club, day, title)

    async def lookup(variant: str) -> list[dict[str, str]]:
        stage = f"venue_index_{variant}"
        payload = json.dumps(
            {"club": club, "town": town, "day": day.isoformat(), "title": title, "variant": variant}
        ).encode()
        try:
            raw = await run_public_worker("venue_index", payload, timeout=12)
            if not title:
                return TypeAdapter(list[dict[str, str]]).validate_json(raw)[:8]
            page = TypeAdapter(IndexPage).validate_json(raw)
        except (SourceUnavailable, ValueError):
            if trace:
                trace(stage + "_unavailable", scope)
            return []
        if trace:
            trace(stage + ("_ok" if page.recognized else "_unavailable"), scope)
            for url in page.anchors:
                trace(stage + "_anchor", url)
            for r in page.rows:
                trace(stage + "_parsed", r["href"])
        return page.rows

    if not title:
        return await lookup("weekday")
    weekly = await lookup("weekday")
    return weekly + await lookup("dated")


async def run_public_worker(mode: str, payload: bytes, *, timeout: float) -> bytes:
    if mode not in {"document", "search", "directory", "venue_index"} or len(payload) > 4096:
        raise ValueError("Invalid public worker request")
    if not 0 < timeout <= 60:
        raise ValueError("Document timeout must be between zero and 60 seconds")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-m",
        "tennis_overview.public_document_worker",
        mode,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ},
        cwd=tempfile.gettempdir(),
    )
    try:
        async with asyncio.timeout(timeout):
            assert process.stdin is not None
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            output = await _read_output(process)
        if mode == "search" and process.returncode == 75:
            raise TransientSourceError("Public search temporarily unavailable")
        if process.returncode != 0:
            raise SourceUnavailable("Public document could not be read")
        return output
    except TimeoutError:
        if mode == "search":
            raise TransientSourceError("Public search temporarily unavailable") from None
        raise SourceUnavailable("Public document could not be read") from None
    except (ValueError, OSError):
        raise SourceUnavailable("Public document could not be read") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
