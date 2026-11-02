"""Cancellable document reads for the agent; credentials never enter the child env."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

from pydantic import TypeAdapter

from .discovery_agent import SourceUnavailable
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
    if not 0 < timeout <= 60:
        raise ValueError("Document timeout must be between zero and 60 seconds")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-m",
        "tennis_overview.public_document_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ},
        cwd=tempfile.gettempdir(),
    )
    try:
        async with asyncio.timeout(timeout):
            assert process.stdin is not None
            process.stdin.write(url.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            output = await _read_output(process)
        if process.returncode != 0:
            raise SourceUnavailable("Public document could not be read")
        page = TypeAdapter(Page).validate_json(output)
        public_url(page.url)
        return page
    except (TimeoutError, ValueError, OSError):
        raise SourceUnavailable("Public document could not be read") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
