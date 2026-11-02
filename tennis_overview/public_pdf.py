"""Text-only PDF reading. No embedded actions, scripts, files or OCR are executed."""

from __future__ import annotations

import io
import re
from urllib.parse import urlsplit

from pypdf import PdfReader

from .public_web import MAX_BYTES, Page, PageLink, public_url

MAX_PAGES = 12
MAX_TEXT = 24_000


def page_from_pdf(url: str, body: bytes) -> Page:
    """Call in the resource-limited document worker, never in the service process."""
    url = public_url(url)
    if len(body) > MAX_BYTES or not body.startswith(b"%PDF-"):
        raise ValueError("Invalid or oversized PDF")
    reader = PdfReader(io.BytesIO(body), strict=True)
    if reader.is_encrypted or not 0 < len(reader.pages) <= MAX_PAGES:
        raise ValueError("Encrypted or oversized PDF is unsupported")
    text_parts: list[str] = []
    links: dict[str, PageLink] = {}

    def add_link(value: str) -> None:
        try:
            link = public_url(value)
        except ValueError:
            return
        if len(links) < 150:
            links.setdefault(link, PageLink(link, "PDF reference"))

    for number, page in enumerate(reader.pages, start=1):
        # Decompression itself is untrusted work; the worker imposes CPU/memory limits.
        contents = page.get_contents()
        if contents and len(contents.get_data()) > MAX_BYTES:
            raise ValueError("PDF content stream exceeds limit")
        text = page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False)
        text_parts.append(f"[PDF page {number}]\n{text.strip()}")
        if sum(len(part) for part in text_parts) + 2 * (len(text_parts) - 1) > MAX_TEXT:
            raise ValueError("PDF text exceeds limit")
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            action = annotation.get("/A")
            if action is not None:
                action = action.get_object()
                if action.get("/S") == "/URI" and isinstance(action.get("/URI"), str):
                    add_link(action["/URI"])
        for match in re.finditer(r"https?://[^\s<>\[\]]+", text):
            add_link(match.group().rstrip(".,;)"))
    text = "\n\n".join(text_parts)
    if not any(character.isalnum() for part in text_parts for character in part.split("\n", 1)[1]):
        raise ValueError("PDF has no readable text; OCR is not enabled")
    title = str(reader.metadata.title or "") if reader.metadata else ""
    title = title.strip()[:240] or urlsplit(url).path.rsplit("/", 1)[-1][:240]
    return Page(
        url,
        title,
        text,
        tuple(links),
        link_details=tuple(links.values()),
        content_type="application/pdf",
    )
