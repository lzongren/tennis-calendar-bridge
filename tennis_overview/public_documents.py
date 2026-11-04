"""Cancellable document reads for the agent; credentials never enter the child env."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from pydantic import TypeAdapter

from .discovery_agent import SourceUnavailable, TransientSourceError
from .public_web import Page, public_url

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
    city: str, level: str, year: int, focus: str = "overview", *, subject: str = ""
) -> list[dict[str, str]]:
    """Anonymous public search, without inheriting model or portal credentials."""
    output = await run_public_worker(
        "search",
        json.dumps(
            {
                "city": city,
                "level": level,
                "year": year,
                "focus": focus,
                **({"subject": subject} if subject else {}),
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


async def run_public_worker(mode: str, payload: bytes, *, timeout: float) -> bytes:
    if mode not in {"document", "search"} or len(payload) > 4096:
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
