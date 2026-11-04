"""Public source registry and adapters for the bounded research agent."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic_ai.exceptions import UnexpectedModelBehavior

from .browser_documents import BrowserDocument
from .course_evidence import SessionEvidence
from .course_extraction import CourseCandidate, CourseExtractor
from .courtreserve_discovery import RegistrationOccurrence, courtreserve_observation
from .discovery_geography import nearby_cities
from .discovery_agent import (
    DiscoveryRequest,
    DiscoveryRun,
    SearchResults,
    SourceDocument,
    SourceRef,
    SourceFailureReason,
    SourceUnavailable,
)
from .public_documents import read_public_document, search_public_courses
from .public_web import Page, PageLink, public_url

Reader = Callable[[str], Awaitable[Page | BrowserDocument]]
Searcher = Callable[[str, str, int, str], Awaitable[list[dict[str, str]]]]

# Six live queries admit at most 24 results, leaving room in the 48-source
# registry for operator-linked schedules and registration details.
AREA_RESULTS_PER_QUERY = 4
REGISTRATION_LINK = re.compile(r"register|registration|portal|bookclass|bookcourse", re.I)
SCHEDULE_LINK = re.compile(r"schedule|timetable|calendar", re.I)


def identity(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def related_links(page: Page) -> tuple[PageLink, ...]:
    """Preserve operator-linked PDFs and cross-site signup references, not just one site."""
    candidates = page.link_details or tuple(PageLink(url, "") for url in page.links)
    selected = []
    for link in candidates:
        path = urlsplit(link.url).path
        description = link.title + " " + path
        if link.url == page.url or re.search(r"pickleball|youth|junior|camp", description, re.I):
            continue
        if not re.search(
            r"adult|tennis|clinic|schedule|timetable|calendar|register|registration|portal|bookclass|bookcourse|csc",
            description,
            re.I,
        ):
            continue
        if REGISTRATION_LINK.search(description):
            rank = 0
        elif SCHEDULE_LINK.search(description) or (
            path.lower().endswith(".pdf")
            and re.search(r"adult|clinic|csc", description, re.I)
            and not re.search(r"rating|rules|policy|policies", description, re.I)
        ):
            rank = 1
        else:
            rank = 2
        selected.append((rank, link))
    return tuple(link for _, link in sorted(selected, key=lambda item: item[0]))


@dataclass
class PublicSource:
    ref: SourceRef
    url: str
    depth: int
    status: str = "not_read"
    page: Page | None = None
    failure_reason: SourceFailureReason | None = None


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
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def register(self, url: str, title: str, depth: int) -> SourceRef | None:
        url = public_url(url)
        key = identity(url)
        if key not in self.sources:
            if len(self.sources) >= self.source_limit:
                self.truncated = True
                return None
            self.sources[key] = PublicSource(
                SourceRef(id=key, title=title[:240], url=url), url, depth
            )
        return self.sources[key].ref

    async def search(self, city: str, request: DiscoveryRequest) -> SearchResults:
        initial_urls = self.initial_urls if city.casefold() == request.city.casefold() else ()
        try:
            candidates = await self.searcher(
                city, request.level, request.start.year, request.search_focus
            )
        except SourceUnavailable:
            if not initial_urls:
                raise
            self.search_failed = True
            candidates = []
        refs: dict[str, SourceRef] = {}
        required = []
        for url in initial_urls:
            try:
                ref = self.register(url, "Added course source", 0)
            except ValueError:
                continue
            if ref:
                refs[ref.id] = ref
                required.append(ref.id)
        query_limit = (
            AREA_RESULTS_PER_QUERY if request.search_mode == "area" else max(0, 12 - len(refs))
        )
        admitted = 0
        for candidate in candidates:
            try:
                url = public_url(candidate["url"])
                title = candidate["title"]
                if identity(url) in refs:
                    continue
                if admitted >= query_limit:
                    self.truncated = True
                    continue
                ref = self.register(url, title, 0)
            except (KeyError, ValueError):
                continue
            if ref:
                refs[ref.id] = ref
                admitted += 1
        returned = tuple(refs.values())[:12]
        return SearchResults(
            sources=returned,
            nearby_cities=nearby_cities(request.city, request.timezone)
            if request.search_mode == "area" and city.casefold() == request.city.casefold()
            else (),
            partial=request.search_mode == "area" or self.truncated,
            required_ids=tuple(dict.fromkeys(required))[:4],
        )

    async def read(
        self, source_id: str, request: DiscoveryRequest, *, deadline: float | None = None
    ) -> SourceDocument:
        if source_id not in self.sources:
            raise SourceUnavailable("Undiscovered source")
        source = self.sources[source_id]
        try:
            document = await self.reader(source.url)
            registration = None
            evidence_unavailable = False
            if isinstance(document, BrowserDocument):
                try:
                    observed_url = public_url(document.url)
                except ValueError:
                    raise SourceUnavailable("Unsafe browser course URL") from None
                if observed_url != source.url:
                    raise SourceUnavailable("Browser observation does not match requested source")
                registration = courtreserve_observation(document, request, now=self.clock())
                page = registration.page
                observations = (
                    RegistrationOccurrence(
                        registration.course, registration.availability, registration.waitlist_count
                    ),
                    *registration.additional,
                )
                owned: dict[str, RegistrationOccurrence] = {}
                for item in observations:
                    if item.course is None:
                        continue
                    previous = owned.get(item.course.id)
                    if (
                        previous is not None
                        and previous.course is not None
                        and (
                            previous.course.sessions != item.course.sessions
                            or previous.course.price != item.course.price
                            or previous.availability != item.availability
                            or previous.waitlist_count != item.waitlist_count
                        )
                    ):
                        raise SourceUnavailable("Conflicting registration occurrence")
                    owned[item.course.id] = item
                courses: tuple[CourseCandidate, ...] = tuple(
                    item.course for item in owned.values() if item.course is not None
                )
                registration_facts = {
                    item.course.id: (item.availability, item.waitlist_count)
                    for item in owned.values()
                    if item.course is not None
                }
            else:
                page = document
                if not page.links and len(page.text.split()) < 5:
                    raise SourceUnavailable("Source needs dynamic or authenticated access")
                extraction_timeout = asyncio.timeout_at(deadline)
                try:
                    if page.required_links:
                        courses = ()
                    elif deadline is not None and asyncio.get_running_loop().time() >= deadline:
                        courses = ()
                        evidence_unavailable = True
                    else:
                        async with extraction_timeout:
                            courses = await self.extractor.extract(page, request)
                        if extraction_timeout.expired():
                            courses = ()
                            evidence_unavailable = True
                except TimeoutError:
                    if not extraction_timeout.expired():
                        raise
                    # Only our local extraction deadline permits navigation-only
                    # recovery. External cancellation and model outages propagate.
                    courses = ()
                    evidence_unavailable = True
                except UnexpectedModelBehavior:
                    # Invalid facts do not invalidate independently observed navigation.
                    # Transport/budget failures still abort; no extraction retry is added.
                    courses = ()
                    evidence_unavailable = True
            source.page = page
            records: dict[str, SessionEvidence] = {}
            partial = (
                evidence_unavailable
                or page.partial
                or len(courses) >= 8
                or bool(registration and registration.partial)
            )
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
                        kind="registration" if registration else "seasonal",
                        title=course.title,
                        starts_at=occurrence.starts_at if occurrence else None,
                        ends_at=occurrence.ends_at if occurrence else None,
                        price_usd=usd_price(course.price),
                        availability=registration_facts[course.id][0]
                        if registration
                        else "unknown",
                        waitlist_count=registration_facts[course.id][1] if registration else None,
                    )
                    if key in records and records[key] != record:
                        raise SourceUnavailable("Ambiguous source occurrence")
                    records[key] = record
                    self.details[key] = course
            linked: dict[str, SourceRef] = {}
            navigation = []
            ranked_links = related_links(page)
            if source.depth < 2:
                for link in ranked_links[:6]:
                    ref = self.register(
                        link.url, link.title or "Linked course source", source.depth + 1
                    )
                    if ref:
                        linked[ref.id] = ref
                        # A general signup route needs an area-search attempt, not
                        # successful course verification or an authenticated fallback.
                        if not registration and REGISTRATION_LINK.search(
                            link.title + " " + urlsplit(link.url).path
                        ):
                            navigation.append(ref.id)
            partial = partial or len(ranked_links) > 6 or self.truncated
            required = []
            for url in page.required_links:
                if url not in page.links:
                    raise SourceUnavailable("Invalid browser detail reference")
                ref = self.register(url, "Course registration detail", source.depth + 1)
                if ref:
                    linked[ref.id] = ref
                    required.append(ref.id)
                else:
                    partial = True
            for course in courses[:8]:
                # Only deterministic adapters can supply a course-owned registration URL.
                # A general portal link is useful navigation, not an event identity.
                if course.signup_url != page.url and course.signup_url in page.links:
                    ref = self.register(course.signup_url, course.title, source.depth + 1)
                    if ref:
                        linked[ref.id] = ref
                        required.append(ref.id)
            if evidence_unavailable and not linked:
                raise SourceUnavailable(
                    "Course evidence could not be validated",
                    reason=SourceFailureReason.EVIDENCE_INVALID,
                )
            source.status = "unavailable" if evidence_unavailable else "checked"
            source.failure_reason = (
                SourceFailureReason.EVIDENCE_INVALID if evidence_unavailable else None
            )
            return SourceDocument(
                source=source.ref,
                text=page.text,
                records=tuple(records.values()),
                links=tuple(linked.values()),
                required_ids=tuple(dict.fromkeys(required)),
                navigation_ids=tuple(dict.fromkeys(navigation)),
                partial=partial,
                evidence_unavailable=evidence_unavailable,
            )
        except SourceUnavailable as error:
            source.status = "unavailable"
            source.failure_reason = error.reason
            raise

    def result(self, run: DiscoveryRun, request: DiscoveryRequest) -> dict[str, Any]:
        # Tool deadlines cancel adapter coroutines before they can record a failure.
        unavailable = {event.source_id for event in run.audit if event.outcome == "unavailable"}
        sources = []
        for source in self.sources.values():
            status = "unavailable" if source.ref.id in unavailable else source.status
            reason = source.failure_reason or SourceFailureReason.PAGE_UNAVAILABLE
            sources.append(
                {
                    "id": source.ref.id,
                    "url": source.page.url if source.page else source.url,
                    "title": source.page.title if source.page else source.ref.title,
                    "status": status,
                    "failure_reason": reason.value if status == "unavailable" else None,
                }
            )
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
                    "price": f"${session.price_usd:.2f}"
                    if session.price_usd is not None
                    else detail.price
                    if usd_price(detail.price) is None
                    else "",
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
            "sources": sources,
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
