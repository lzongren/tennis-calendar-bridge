"""Parse a rendered CourtReserve event summary without reading account controls."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from bs4.element import Tag

from .browser_documents import BrowserDocument
from .course_evidence import Availability
from .course_extraction import CourseCandidate, Occurrence, accepted_course
from .discovery_agent import DiscoveryRequest, SourceUnavailable
from .public_web import Page, public_url


@dataclass(frozen=True)
class RegistrationObservation:
    page: Page
    course: CourseCandidate | None
    availability: Availability
    waitlist_count: int | None
    partial: bool


def _text(root: Tag, selector: str, *, required: bool = True) -> str:
    nodes = root.select(selector)
    if len(nodes) != 1:
        if not required and not nodes:
            return ""
        raise SourceUnavailable("Missing or ambiguous browser course field")
    value = nodes[0].get_text(" ", strip=True)
    if not value or len(value) > 500:
        raise SourceUnavailable("Invalid browser course field")
    return value


def _date(label: str, captured: date) -> date:
    match = re.fullmatch(
        r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th)?(?:, (20\d{2}))?",
        label,
    )
    if not match:
        raise SourceUnavailable("Unrecognized registration date")
    weekday, month, day, year = match.groups()
    # CourtReserve's observed current-year detail label omits the year. Only a
    # fresh capture supplies that context; the requested search year never does.
    try:
        result = datetime.strptime(f"{month} {day} {year or captured.year}", "%b %d %Y").date()
    except ValueError:
        raise SourceUnavailable("Invalid registration date") from None
    if result.strftime("%a") != weekday:
        raise SourceUnavailable("Registration weekday disagrees with date")
    return result


def _times(label: str, day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    match = re.fullmatch(
        r"(\d{1,2}:\d{2})\s*([ap])m?\s*[-\u2013]\s*(\d{1,2}:\d{2})\s*([ap])m?",
        label,
        re.I,
    )
    if not match:
        raise SourceUnavailable("Unrecognized registration time")
    parts = match.groups()
    try:
        start, end = (
            datetime.combine(
                day,
                datetime.strptime(parts[i] + parts[i + 1].upper() + "M", "%I:%M%p").time(),
                zone,
            )
            for i in (0, 2)
        )
    except ValueError:
        raise SourceUnavailable("Invalid registration time") from None
    if end <= start or any(
        dt.astimezone(timezone.utc).astimezone(zone) != dt
        or dt.replace(fold=1).utcoffset() != dt.utcoffset()
        for dt in (start, end)
    ):
        raise SourceUnavailable("Ambiguous or invalid registration interval")
    return start, end


def courtreserve_observation(
    document: BrowserDocument, request: DiscoveryRequest, *, now: datetime
) -> RegistrationObservation:
    """Read only the selected occurrence. Other series dates are not inferred."""
    try:
        url = public_url(document.url)
    except ValueError:
        raise SourceUnavailable("Unsafe browser course URL") from None
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.port not in (None, 443)
        or parsed.hostname != "app.courtreserve.com"
        or not re.fullmatch(r"/Online/Events/Details/\d+/[A-Za-z0-9]+", parsed.path)
        or len({key for key, _ in query}) != len(query)
        or any(
            not (
                (key == "resId" and value.isascii() and value.isdigit())
                or (key == "tab" and value in {"dates", "registration"})
            )
            for key, value in query
        )
    ):
        raise SourceUnavailable("Unsupported browser course page")
    if (
        document.captured_at.tzinfo is None
        or now.tzinfo is None
        or not -timedelta(seconds=5)
        <= now.astimezone(timezone.utc) - document.captured_at.astimezone(timezone.utc)
        <= timedelta(minutes=15)
        or len(document.html.encode()) > 128_000
    ):
        raise SourceUnavailable("Stale or invalid browser observation")
    soup = BeautifulSoup(document.html, "html.parser")
    for node in soup.select("script, style, nav, footer, button, [hidden], [aria-hidden='true']"):
        node.decompose()
    roots = soup.select('[data-testid="events-details-page"]')
    if len(roots) != 1:
        raise SourceUnavailable("Browser page is not a course detail")
    root = roots[0]
    heading = _text(root, '[data-testid="events-details-header"] h1')
    category = _text(root, '[data-testid="events-details-category"]')
    date_text = _text(root, '[data-testid="events-details-date"]')
    time_text = _text(root, '[data-testid="events-details-time"]')
    price = _text(root, '[data-testid="events-details-price-single"]', required=False)
    status = _text(root, '[data-testid="events-details-slots"]', required=False)
    # This allowlist, not the full authenticated DOM or page title, reaches the model.
    page = Page(
        url,
        heading,
        "\n".join((heading, category, date_text, time_text, price, status)),
        (),
        heading,
    )
    partial = bool(root.select('[data-testid="events-details-dates-list"]'))
    series_label = _text(root, '[data-testid="events-details-tab-dates"]', required=False)
    if series_label:
        series = re.fullmatch(r"Dates \((\d{1,4})\)", series_label)
        if not series:
            raise SourceUnavailable("Unrecognized registration series")
        partial = partial or int(series[1]) > 1
    zone = ZoneInfo(request.timezone)
    day = _date(date_text, document.captured_at.astimezone(zone).date())
    start, end = _times(time_text, day, zone)
    availability: Availability = "unknown"
    count = None
    full = re.fullmatch(r"Full(?:\s*-\s*(\d{1,5}) Registrant\(s\) Waitlisted)?", status, re.I)
    if full:
        availability = "full"
        count = int(full[1]) if full[1] is not None else None
    elif status.casefold() in {"cancelled", "canceled"}:
        availability = "cancelled"
    # A Register/Waitlisted/Join Waitlist button does not establish capacity or queue position.
    if not re.fullmatch(r"\$\d{1,6}(?:\.\d{2})?", price):
        price = ""
    level = re.search(r"NTRP\s+(\d\.\d\s*-\s*\d\.\d|\d\.\d)", heading, re.I)
    course = None
    if (
        re.search(r"\badult\b", category, re.I)
        and re.search(r"\btennis\b", category, re.I)
        and not re.search(r"junior|youth|pickleball", category + " " + heading, re.I)
        and request.start <= day < request.start + timedelta(days=28)
    ):
        course = accepted_course(
            {
                "id": hashlib.sha256((parsed.path + start.isoformat()).encode()).hexdigest()[:24],
                "title": heading,
                "level": level[1] if level else "",
                "price": f"${Decimal(price[1:]):.2f}" if price else "",
                "signup_url": url,
                "source_url": url,
                "evidence": page.text,
                "sessions": (Occurrence(starts_at=start, ends_at=end),),
                "schedule_basis": "registration_detail",
                "schedule_note": "Observed " + document.captured_at.isoformat(),
            },
            request,
        )
    return RegistrationObservation(page, course, availability, count, partial)
