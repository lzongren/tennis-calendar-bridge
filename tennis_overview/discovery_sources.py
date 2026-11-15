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
from .course_extraction import CourseCandidate, CourseExtractor, Occurrence
from .discovery import matches_level
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
from .public_documents import read_public_document, refine_public_courses, search_public_courses
from .public_web import Page, PageLink, public_url, search_subject
from .player_event import event_path
from .venue_schedules import Reader as VenueReader, matches_row

Reader = Callable[[str], Awaitable[Page | BrowserDocument]]
Searcher = Callable[[str, str, int, str], Awaitable[list[dict[str, str]]]]
Refiner = Callable[[str, str, int], Awaitable[list[dict[str, str]]]]

# Six live queries admit at most 24 results, leaving room in the 48-source
# registry for operator-linked schedules and registration details.
AREA_RESULTS_PER_QUERY = 4
REGISTRATION_LINK = re.compile(r"register|registration|portal|bookclass|bookcourse", re.I)
SCHEDULE_LINK = re.compile(r"schedule|timetable|calendar", re.I)
COMPETITION = re.compile(r"\b(?:tournaments?|championships?|league nationals?)\b", re.I)


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
        is_event = bool(event_path(link.url))
        if (
            link.relation != "same_as"
            and not is_event
            and not re.search(
                r"adult|tennis|clinic|schedule|timetable|calendar|register|registration|portal|bookclass|bookcourse|csc",
                description,
                re.I,
            )
        ):
            continue
        if REGISTRATION_LINK.search(description) or is_event:
            rank = 0
        elif SCHEDULE_LINK.search(description) or (
            path.lower().endswith(".pdf")
            and re.search(r"adult|clinic|csc", description, re.I)
            and not re.search(r"rating|rules|policy|policies", description, re.I)
        ):
            rank = 1
        elif link.relation == "same_as":
            rank = 2
        else:
            rank = 3
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
    refinement_subject: str = ""
    directory_club: str = ""
    venue_slot: tuple[str, str, str] | None = None


@dataclass
class PublicSourceBackend:
    extractor: CourseExtractor
    initial_urls: tuple[str, ...] = ()
    reader: Reader = read_public_document
    searcher: Searcher | None = None
    source_limit: int = 24
    sources: dict[str, PublicSource] = field(default_factory=dict)
    details: dict[str, CourseCandidate] = field(default_factory=dict)
    search_failed: bool = False
    truncated: bool = False
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    refiner: Refiner = refine_public_courses
    trace: Callable[[str, str], None] | None = None
    venue_renderer: VenueReader | None = None

    def observed(self, stage: str, url: str) -> None:
        if self.trace is not None:
            self.trace(stage, url)

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
        else:
            # Search rediscovery can make a previously linked page a new root.
            # Keep its identity/evidence; use the shortest observed path on future reads.
            self.sources[key].depth = min(self.sources[key].depth, depth)
        self.observed("admitted", url)
        return self.sources[key].ref

    def search_for(self, request: DiscoveryRequest) -> Searcher:
        fallback = self.searcher

        async def dated_search(
            city: str, level: str, year: int, focus: str
        ) -> list[dict[str, str]]:
            if fallback and not (
                self.venue_renderer and request.search_mode == "area" and focus == "registration"
            ):
                return await fallback(city, level, year, focus)
            return await search_public_courses(
                city,
                level,
                year,
                focus,
                on_date=request.start
                if request.search_mode == "area" and focus == "registration"
                else None,
                venue_renderer=self.venue_renderer,
                trace=self.observed,
            )

        return dated_search

    async def search(self, city: str, request: DiscoveryRequest) -> SearchResults:
        initial_urls = self.initial_urls if city.casefold() == request.city.casefold() else ()
        try:
            candidates = await self.search_for(request)(
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
        if request.search_focus == "registration":
            candidates = sorted(candidates, key=lambda c: not event_path(c.get("url", "")))
        for candidate in candidates:
            try:
                url = public_url(candidate["url"])
                self.observed("search_hit", url)
                if "listing" in candidate:
                    self.observed("directory", candidate["listing"])
                    self.observed("directory_card", url)
                title = candidate["title"]
                if (
                    request.search_focus == "registration"
                    and COMPETITION.search(title + " " + urlsplit(url).path.replace("-", " "))
                    and not re.search(r"\b(?:clinics?|lessons?|class(?:es)?)\b", title, re.I)
                ):
                    continue
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
                if "listing" in candidate:
                    required.append(ref.id)
                    self.sources[ref.id].directory_club = candidate.get("club", "")
                    if "slot_start" in candidate:
                        self.sources[ref.id].venue_slot = (
                            title,
                            candidate["slot_start"],
                            candidate["slot_end"],
                        )
        returned = tuple(refs.values())[:12]
        return SearchResults(
            sources=returned,
            nearby_cities=nearby_cities(request.city, request.timezone)
            if request.search_mode == "area" and city.casefold() == request.city.casefold()
            else (),
            partial=request.search_mode == "area" or self.truncated,
            required_ids=tuple(dict.fromkeys(required))[:4],
        )

    async def refine(self, source_id: str, request: DiscoveryRequest) -> SearchResults:
        source = self.sources.get(source_id)
        if (
            request.search_mode != "area"
            or source is None
            or source.status != "checked"
            or not source.refinement_subject
        ):
            raise SourceUnavailable("Source is not eligible for refinement")
        candidates = await self.refiner(source.refinement_subject, request.city, request.start.year)
        refs: dict[str, SourceRef] = {}
        for candidate in candidates:
            try:
                url = public_url(candidate["url"])
                self.observed("search_hit", url)
                if url == source.url or identity(url) in refs:
                    continue
                if len(refs) >= AREA_RESULTS_PER_QUERY:
                    self.truncated = True
                    continue
                ref = self.register(url, candidate["title"], 0)
            except (KeyError, ValueError):
                continue
            if ref:
                refs[ref.id] = ref
        return SearchResults(sources=tuple(refs.values()), partial=True)

    async def read(
        self, source_id: str, request: DiscoveryRequest, *, deadline: float | None = None
    ) -> SourceDocument:
        if source_id not in self.sources:
            raise SourceUnavailable("Undiscovered source")
        source = self.sources[source_id]
        source.refinement_subject = ""
        self.observed("read_attempt", source.url)
        try:
            document = await self.reader(source.url)
            self.observed("read_ok", document.url)
            if isinstance(document, Page):
                for url in document.links:
                    self.observed("page_link", url)
            registration = None
            player = None
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
                    if page.player_event is not None:
                        player = page.player_event
                        detail_slot = Occurrence(
                            starts_at=datetime.fromisoformat(player.start),
                            ends_at=datetime.fromisoformat(player.end),
                        )
                        candidate = CourseCandidate(
                            id=identity(page.url + player.start),
                            title=player.title,
                            club=source.directory_club,
                            level=player.level,
                            price=player.price,
                            signup_url=page.url,
                            source_url=page.url,
                            sessions=(detail_slot,),
                            evidence="Public event detail: Starts, Ends, Registration fee, Spots left",
                        )
                        courses = (
                            (candidate,)
                            if (
                                request.start
                                <= detail_slot.starts_at.date()
                                < request.start + timedelta(days=28)
                                and matches_level(candidate.model_dump(), request.level)
                                and (
                                    source.venue_slot is None
                                    or matches_row(
                                        page,
                                        source.directory_club,
                                        request.start,
                                        *source.venue_slot,
                                    )
                                )
                                and (
                                    not source.directory_club
                                    or (
                                        detail_slot.starts_at.date() == request.start
                                        and (
                                            not re.search(r"[1-7]\.\d", request.level)
                                            or bool(re.search(r"[1-7]\.\d", player.level))
                                        )
                                    )
                                )
                            )
                            else ()
                        )
                    elif page.required_links or source.directory_club:
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
                self.observed("extracted", course.source_url)
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
                        kind="registration" if registration or player else "seasonal",
                        title=course.title,
                        starts_at=occurrence.starts_at if occurrence else None,
                        ends_at=occurrence.ends_at if occurrence else None,
                        price_usd=usd_price(course.price),
                        availability=("available" if player.spots else "full")
                        if player
                        else registration_facts[course.id][0]
                        if registration
                        else "unknown",
                        waitlist_count=registration_facts[course.id][1] if registration else None,
                        spots_left=player.spots if player else None,
                    )
                    if key in records and records[key] != record:
                        raise SourceUnavailable("Ambiguous source occurrence")
                    records[key] = record
                    self.details[key] = course
            linked: dict[str, SourceRef] = {}
            navigation = []
            references = []
            # Related programs are not other dates of the selected directory card.
            ranked_links = () if source.directory_club else related_links(page)
            for link in ranked_links[:6]:
                is_registration = bool(event_path(link.url)) or bool(
                    REGISTRATION_LINK.search(link.title + " " + urlsplit(link.url).path)
                )
                # Public directories can add a hop before the operator's signup
                # entry. Allow that final registration hop, not arbitrary crawling.
                extra_registration_hop = (
                    request.search_mode == "area" and source.depth == 2 and is_registration
                )
                if source.depth >= 2 and not extra_registration_hop:
                    partial = True
                    continue
                ref = self.register(
                    link.url, link.title or "Linked course source", source.depth + 1
                )
                if ref:
                    linked[ref.id] = ref
                    if link.relation == "same_as":
                        references.append(ref.id)
                    # A general signup route needs an area-search attempt, not
                    # successful course verification or an authenticated fallback.
                    if not registration and player is None and is_registration:
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
            if not (evidence_unavailable or records or required or navigation):
                source.refinement_subject = search_subject(
                    page.heading or page.title.split(" | ")[0]
                )
            return SourceDocument(
                source=source.ref,
                text=page.text,
                records=tuple(records.values()),
                links=tuple(linked.values()),
                required_ids=tuple(dict.fromkeys(required)),
                navigation_ids=tuple(dict.fromkeys(navigation)),
                reference_ids=tuple(dict.fromkeys(references)),
                partial=partial,
                evidence_unavailable=evidence_unavailable,
                refinement_available=bool(source.refinement_subject),
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
            if session.spots_left is not None:
                key = identity(key + session.session_key)
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
                    "spots_left": session.spots_left,
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
