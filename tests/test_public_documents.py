from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from tennis_overview.discovery_agent import SourceUnavailable
from tennis_overview.public_documents import MAX_OUTPUT, read_public_document, search_public_courses
from tennis_overview.public_pdf import page_from_pdf
from tennis_overview.public_web import Page, search_courses

URL = "https://club.example.org/fall.pdf"


def test_legacy_search_query_is_preserved_and_agent_focus_is_explicit(monkeypatch):
    search = MagicMock(return_value=[])
    monkeypatch.setattr("ddgs.DDGS", lambda **kwargs: MagicMock(text=search))
    assert search_courses("Example City", "3.0", 2026) == []
    assert search.call_args.args[0] == "Example City adult tennis clinics classes schedule"
    assert search_courses("Example City", "3.0", 2026, "overview") == []
    assert search.call_args.args[0] == "adult tennis group clinics near Example City"
    with pytest.raises(ValueError, match="Invalid search focus"):
        search_courses("Example City", "3.0", 2026, "arbitrary query")
    assert search.call_count == 2


def pdf_bytes(text="Adult Clinic", links=(), *, pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET".encode())
        page[NameObject("/Contents")] = stream
        page[NameObject("/Annots")] = ArrayObject(
            [
                DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/Annot"),
                        NameObject("/Subtype"): NameObject("/Link"),
                        NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4),
                        NameObject("/A"): DictionaryObject(
                            {
                                NameObject("/S"): NameObject("/URI"),
                                NameObject("/URI"): TextStringObject(link),
                            }
                        ),
                    }
                )
                for link in links
            ]
        )
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.benchmark
def test_pdf_retains_source_row_and_registration_reference():
    # Sanitized row shape from the real fall clinic PDF. This gate verifies
    # retrieval, NOT date inference, registration availability or a current price.
    row = "Coach Clinic 2.5 - 3.0 MONDAYS 6:30-7:50PM 10/19-12/7 $52"
    booking = "https://booking.example.org/Online/Portal/Index/1"
    page = page_from_pdf(URL, pdf_bytes(row, (booking,)))
    assert row in page.text
    assert page.text.startswith("[PDF page 1]")
    assert page.links == (booking,)
    assert page.content_type == "application/pdf"
    assert page.schedules == ()  # No invented course dates or availability.


def test_pdf_private_links_ignored_and_plain_links_retained():
    page = page_from_pdf(
        URL,
        pdf_bytes(
            "Register https://booking.example.org/classes",
            ("file:///etc/passwd", "http://127.0.0.1/", "https://example.org/?token=private"),
        ),
    )
    assert page.links == ("https://booking.example.org/classes",)


@pytest.mark.parametrize("options", [{"pages": 13}, {"password": "password"}, {"text": ""}])
def test_pdf_unsupported_documents_fail_closed(options):
    with pytest.raises(ValueError):
        page_from_pdf(URL, pdf_bytes(**options))


def test_pdf_invalid_or_oversized_bytes_rejected():
    for body in (b"not a pdf", b"%PDF-" + b"x" * 4_000_000):
        with pytest.raises(ValueError):
            page_from_pdf(URL, body)


def test_pdf_long_text_is_rejected_not_silently_truncated():
    with pytest.raises(ValueError, match="text exceeds"):
        page_from_pdf(URL, pdf_bytes("word " * 5000))


def fake_process(monkeypatch, *, body=None, status=0, blocked=False):
    process = MagicMock()
    process.returncode = None
    process.stdin.drain = AsyncMock()
    process.stdout.read = AsyncMock(side_effect=[body or b"{}", b""])

    async def wait():
        process.returncode = status
        return status

    process.wait = AsyncMock(side_effect=wait)
    if blocked:

        async def hang(_):
            await asyncio.Future()

        process.stdout.read = AsyncMock(side_effect=hang)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    return process, spawn


def test_document_child_does_not_inherit_credentials_or_proxy(monkeypatch):
    page = Page(URL, "PDF", "Public course", (), content_type="application/pdf")
    process, spawn = fake_process(monkeypatch, body=json.dumps(asdict(page)).encode())
    monkeypatch.setenv("TENNIS_DISCOVERY_API_KEY", "test-secret")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    assert asyncio.run(read_public_document(URL)) == page
    assert spawn.call_args.args == (
        sys.executable,
        "-I",
        "-B",
        "-m",
        "tennis_overview.public_document_worker",
        "document",
    )
    assert set(spawn.call_args.kwargs["env"]) <= {"PATH", "LANG", "LC_ALL"}
    assert spawn.call_args.kwargs["stderr"] == asyncio.subprocess.DEVNULL
    assert process.stdin.write.call_args.args == (URL.encode(),)
    process.kill.assert_not_called()
    process.wait.assert_awaited()


@pytest.mark.parametrize(
    "body,status", [(b"bad json", 0), (b"{}", 1), (b"x" * (MAX_OUTPUT + 1), 0)]
)
def test_document_failure_is_redacted_and_reaped(monkeypatch, body, status):
    process, _ = fake_process(monkeypatch, body=body, status=status)
    with pytest.raises(SourceUnavailable) as caught:
        asyncio.run(read_public_document(URL))
    assert URL not in str(caught.value) and body[:20].decode() not in str(caught.value)
    process.wait.assert_awaited()


def test_document_timeout_kills_and_reaps(monkeypatch):
    process, _ = fake_process(monkeypatch, blocked=True)
    with pytest.raises(SourceUnavailable):
        asyncio.run(read_public_document(URL, timeout=0.01))
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


def test_document_cancellation_kills_and_reaps(monkeypatch):
    process, spawn = fake_process(monkeypatch, blocked=True)

    async def scenario():
        task = asyncio.create_task(read_public_document(URL))
        while not spawn.called:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


def test_private_document_rejected_before_process_creation(monkeypatch):
    _, spawn = fake_process(monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(read_public_document("http://127.0.0.1/"))
    spawn.assert_not_awaited()


def test_real_worker_entrypoint_rejects_private_input():
    # Exercise the installed isolated entry point without any external request.
    import subprocess

    result = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "tennis_overview.public_document_worker"],
        input=b"http://127.0.0.1/private",
        capture_output=True,
        timeout=10,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 1
    assert result.stdout == b"" and result.stderr == b""


def test_cancellation_reaps_an_actual_stalled_process(monkeypatch):
    original_spawn = asyncio.create_subprocess_exec

    async def scenario():
        child = await original_spawn(
            sys.executable,
            "-I",
            "-c",
            "import time; time.sleep(60)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=child))
        task = asyncio.create_task(read_public_document(URL))
        try:
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
            assert child.returncode is not None
            with pytest.raises(ProcessLookupError):
                os.kill(child.pid, 0)
        finally:
            if child.returncode is None:
                child.kill()
            await child.wait()

    asyncio.run(scenario())


def test_linux_worker_installs_cpu_memory_and_file_limits(monkeypatch):
    import resource

    from tennis_overview.public_document_worker import set_limits

    limits = MagicMock()
    monkeypatch.setattr("resource.setrlimit", limits)
    monkeypatch.setattr("sys.platform", "linux")
    set_limits()
    observed = {call.args[0]: call.args[1] for call in limits.call_args_list}
    assert observed[resource.RLIMIT_CPU] == (10, 10)
    assert observed[resource.RLIMIT_AS] == (512 * 1024 * 1024,) * 2
    assert observed[resource.RLIMIT_CORE] == (0, 0)
    assert observed[resource.RLIMIT_FSIZE] == (0, 0)


def test_search_uses_same_isolated_worker_and_validates_urls(monkeypatch):
    results = [{"url": "https://club.example.org/classes", "title": "Public classes"}]
    process, spawn = fake_process(monkeypatch, body=json.dumps(results).encode())
    assert (
        asyncio.run(search_public_courses("Example City", "3.0", 2026, "registration")) == results
    )
    assert spawn.call_args.args[-1] == "search"
    payload = json.loads(process.stdin.write.call_args.args[0])
    assert payload == {
        "city": "Example City",
        "level": "3.0",
        "year": 2026,
        "focus": "registration",
    }


def test_search_result_private_destination_is_rejected(monkeypatch):
    fake_process(
        monkeypatch, body=json.dumps([{"url": "http://127.0.0.1/", "title": "Private"}]).encode()
    )
    with pytest.raises(SourceUnavailable, match="search failed"):
        asyncio.run(search_public_courses("Example City", "3.0", 2026))
