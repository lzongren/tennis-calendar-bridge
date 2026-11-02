"""Public source registry and adapters for the bounded research agent."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from .course_evidence import SessionEvidence
from .course_extraction import CourseCandidate, CourseExtractor
from .discovery_agent import (
    DiscoveryRequest,
    DiscoveryRun,
    SearchResults,
    SourceDocument,
    SourceRef,
    SourceUnavailable,
    TransientSourceError,
)
from .public_documents import read_public_document, search_public_courses
from .public_web import Page, PageLink, public_url

Reader = Callable[[str], Awaitable[Page]]
Searcher = Callable[[str, str, int, str], Awaitable[list[dict[str, str]]]]


def identity(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def related_links(page: Page) -> tuple[PageLink, ...]:
    """Preserve operator-linked PDFs and cross-site signup references, not just one site."""
    candidates = page.link_details or tuple(PageLink(url, "") for url in page.links)
    selected = []
    for link in candidates:
        description = link.title + " " + urlsplit(link.url).path
        if link.url == page.url or re.search(r"pickleball|youth|junior|camp", description, re.I):
            continue
        if not re.search(
            r"adult|tennis|clinic|schedule|register|registration|portal|bookclass|bookcourse|csc",
            description,
            re.I,
        ):
            continue
        rank = 0 if re.search(r"register|portal|bookclass|bookcourse", description, re.I) else 1
        selected.append((rank, link))
    return tuple(link for _, link in sorted(selected, key=lambda item: item[0]))


@dataclass
class PublicSource:
    ref: SourceRef
    url: str
    depth: int
    status: str = "not_read"
    page: Page | None = None


@dataclass
class PublicSourceBackend:
    extractor: CourseExtractor
    initial_urls: tuple[str, ...] = ()
    reader: Reader = read_public_document
    searcher: Searcher = search_public_courses
    source_limit: int = 24
    sources: dict[str, PublicSource] = field(default_factory=dict)
    details: dict[str, CourseCandidate] = field(default_factory=dict)
    search_failed: bool = False
    truncated: bool = False

    def register(self, url: str, title: str, depth: int) -> SourceRef | None:
        url = public_url(url)
        key = identity(url)
        if key not in self.sources:
            if len(self.sources) >= self.source_limit:
                self.truncated = True
                return None
            self.sources[key] = PublicSource(SourceRef(id=key, title=title[:240]), url, depth)
        return self.sources[key].ref

    async def search(self, city: str, request: DiscoveryRequest) -> SearchResults:
        candidates = (
            [{"url": url, "title": "Added course source"} for url in self.initial_urls]
            if city.casefold() == request.city.casefold()
            else []
        )
        try:
            candidates += await self.searcher(
                city, request.level, request.start.year, request.search_focus
            )
        except TransientSourceError:
            raise
        except SourceUnavailable:
            self.search_failed = True
            if not candidates:
                raise
        refs: dict[str, SourceRef] = {}
        for candidate in candidates:
            try:
                ref = self.register(candidate["url"], candidate["title"], 0)
            except (KeyError, ValueError):
                continue
            if ref:
                refs[ref.id] = ref
        returned = tuple(refs.values())[:12]
        return SearchResults(
            sources=returned,
            required_ids=tuple(
                ref.id for ref in returned if self.sources[ref.id].url in self.initial_urls
            )[:4],
        )

    async def read(self, source_id: str, request: DiscoveryRequest) -> SourceDocument:
        if source_id not in self.sources:
            raise SourceUnavailable("Undiscovered source")
        source = self.sources[source_id]
        try:
            page = await self.reader(source.url)
            # An empty JS/login shell is not evidence that the operator offers no courses.
            if not page.links and len(page.text.split()) < 5:
                raise SourceUnavailable("Source needs dynamic or authenticated access")
            source.page = page
            courses = await self.extractor.extract(page, request)
            records: dict[str, SessionEvidence] = {}
            partial = len(courses) >= 8
            for course in courses[:8]:
                occurrences = course.sessions or (None,)
                for occurrence in occurrences:
                    key = identity(
                        course.id
                        + "\n"
                        + (occurrence.starts_at.isoformat() if occurrence else "undated")
                    )
                    if len(records) >= 64:
                        partial = True
                        break
                    # Generic extraction and published timetables establish candidates,
                    # not registration status. Authority is never a model-selected field.
                    record = SessionEvidence(
                        source_id=source_id,
                        session_key=key,
                        kind="seasonal",
                        title=course.title,
                        starts_at=occurrence.starts_at if occurrence else None,
                        ends_at=occurrence.ends_at if occurrence else None,
                        price_usd=usd_price(course.price),
                    )
                    if key in records and records[key] != record:
                        raise SourceUnavailable("Ambiguous source occurrence")
                    records[key] = record
                    self.details[key] = course
            linked: dict[str, SourceRef] = {}
            if source.depth < 2:
                for link in related_links(page)[:6]:
                    ref = self.register(
                        link.url, link.title or "Linked course source", source.depth + 1
                    )
                    if ref:
                        linked[ref.id] = ref
            partial = partial or len(related_links(page)) > 6 or self.truncated
            required = []
            for course in courses[:8]:
                # Only deterministic adapters can supply a course-owned registration URL.
                # A general portal link is useful navigation, not an event identity.
                if course.signup_url != page.url and course.signup_url in page.links:
                    ref = self.register(course.signup_url, course.title, source.depth + 1)
                    if ref:
                        linked[ref.id] = ref
                        required.append(ref.id)
            source.status = "checked"
            return SourceDocument(
                source=source.ref,
                text=page.text,
                records=tuple(records.values()),
                links=tuple(linked.values()),
                required_ids=tuple(dict.fromkeys(required)),
                partial=partial,
            )
        except SourceUnavailable:
            source.status = "unavailable"
            raise

    def result(self, run: DiscoveryRun, request: DiscoveryRequest) -> dict[str, Any]:
        courses: dict[str, dict[str, Any]] = {}
        for session in run.sessions:
            detail = self.details[session.session_key]
            key = identity(
                detail.id + str(session.price_usd) + session.availability + str(session.reasons)
            )
            if key not in courses:
                courses[key] = {
                    **detail.model_dump(mode="json"),
                    "id": key,
                    "title": session.title,
                    "price": f"${session.price_usd:.2f}" if session.price_usd is not None else "",
                    "availability": "Not checked"
                    if session.availability == "unknown"
                    else session.availability,
                    "waitlist_count": session.waitlist_count,
                    "user_waitlist_position": None,
                    "evidence_ids": list(session.evidence_ids),
                    "sessions": [],
                    "needs_confirmation": session.needs_confirmation,
                }
            if session.starts_at and session.ends_at:
                courses[key]["sessions"].append(
                    {
                        "starts_at": session.starts_at.isoformat(),
                        "ends_at": session.ends_at.isoformat(),
                    }
                )
        return {
            "courses": list(courses.values()),
            "sources": [
                {
                    "id": source.ref.id,
                    "url": source.page.url if source.page else source.url,
                    "title": source.page.title if source.page else source.ref.title,
                    "status": source.status,
                }
                for source in self.sources.values()
            ],
            "window_start": request.start.isoformat(),
            "window_end": (request.start + timedelta(days=27)).isoformat(),
            "partial": run.partial or self.search_failed or self.truncated,
            "search_failed": self.search_failed,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "agent": {
                "framework": "pydantic-ai",
                "requests": run.model_requests,
                "tool_calls": run.tool_calls,
            },
        }


def usd_price(text: str) -> Decimal | None:
    # Do not flatten membership-specific, per-series or multi-currency prices.
    match = re.fullmatch(r"(?:USD\s*)?\$(\d+(?:\.\d{2})?)", text.strip())
    return Decimal(match.group(1)) if match else None
