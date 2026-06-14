from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..models import TennisEvent
from .browser import BrowserPortalScraper


class AccuSportViewScraper(BrowserPortalScraper):
    candidate_paths = (
        "/my",
        "/",
        "/calendar",
        "/schedule",
        "/bookings",
        "/reservations",
        "/my-bookings",
        "/classes",
        "/profile",
    )
    candidate_clicks = BrowserPortalScraper.candidate_clicks + (
        "My Courts",
        "Court Bookings",
        "Lessons",
        "Clinics",
    )

    def _collect_from_json(self, payloads: list[tuple[str, Any]]) -> list[TennisEvent]:
        return []

    def _collect_from_page(self, page: Any, source_url: str) -> list[TennisEvent]:
        events = self._collect_ics_links(page, source_url)
        events.extend(self._collect_reservation_cards(page, source_url))
        return events

    def _collect_reservation_cards(self, page: Any, source_url: str) -> list[TennisEvent]:
        try:
            text = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return []
        return self._events_from_body_text(text, source_url)

    def _events_from_body_text(self, text: str, source_url: str) -> list[TennisEvent]:
        active_text = re.split(r"\bPAST RESERVATIONS\b", text, maxsplit=1, flags=re.I)[0]
        lines = [line.strip() for line in active_text.splitlines() if line.strip()]
        events: list[TennisEvent] = []
        seen: set[str] = set()
        for index, line in enumerate(lines):
            if not _DATE_LINE_RE.search(line):
                continue
            title = _nearest_title(lines, index)
            nearby_lines = lines[max(0, index - 4) : min(len(lines), index + 5)]
            card_text = "\n".join(
                [title, *nearby_lines]
            )
            event = self._event_from_card_text(card_text, source_url)
            if event:
                key = f"{event.starts_at.isoformat()}|{event.ends_at.isoformat()}|{event.location}"
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)
        return events

    def _event_from_card_text(self, text: str, source_url: str) -> TennisEvent | None:
        match = re.search(
            r"(?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*"
            r"(?P<date>\d{2}/\d{2}/\d{4}),\s*"
            r"(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*-\s*"
            r"(?P<end>\d{1,2}:\d{2}\s*[AP]M),\s*"
            r"(?P<location>[^\n\r]+)",
            text,
            re.I,
        )
        if not match:
            return None
        title = _first_line(text) or "Rent/Private"
        zone = ZoneInfo(self.club.timezone)
        event_date = datetime.strptime(match.group("date"), "%m/%d/%Y").date()
        start_time = datetime.strptime(match.group("start").upper(), "%I:%M %p").time()
        end_time = datetime.strptime(match.group("end").upper(), "%I:%M %p").time()
        starts_at = datetime.combine(event_date, start_time, zone)
        ends_at = datetime.combine(event_date, end_time, zone)
        if ends_at <= starts_at:
            ends_at += timedelta(days=1)
        location = match.group("location").strip()
        instructor = _instructor_from_card_text(text, match.group(0))
        access_code = _access_code_from_card_text(text)
        raw = {"card_text": text[:1000]}
        if instructor:
            raw["instructor"] = instructor
        if access_code:
            raw["access_code"] = access_code
        return TennisEvent(
            club_id=self.club.id,
            external_id=f"accusportview-{event_date}-{starts_at.strftime('%H%M')}-{_slug(location)}",
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            timezone=self.club.timezone,
            location=location,
            category="reservation",
            source_url=source_url,
            raw=raw,
        )


def _clean_lines(value: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _first_line(value: str) -> str:
    for line in value.splitlines():
        clean = line.strip()
        if clean:
            return clean
    return ""


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


_DATE_LINE_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s*\d{2}/\d{2}/\d{4},\s*"
    r"\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M,\s*",
    re.I,
)


def _nearest_title(lines: list[str], date_line_index: int) -> str:
    for previous in reversed(lines[max(0, date_line_index - 4) : date_line_index]):
        clean = previous.strip()
        if _is_reservation_noise_line(clean):
            continue
        if clean:
            return clean
    return "Rent/Private"


def _is_reservation_noise_line(value: str) -> bool:
    if _is_pin_or_reference_line(value):
        return True
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}", value):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?\s*h(?:ours?)?", value, re.I):
        return True
    if re.fullmatch(r"\$\d+(?:\.\d{2})?", value):
        return True
    title_terms = r"\b(lesson|clinic|class|doubles|singles|strategy|private|rent|reservation|court|tennis|pickle)\b"
    if re.search(title_terms, value, re.I):
        return False
    if re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}", value):
        return True
    return False


def _is_pin_or_reference_line(value: str) -> bool:
    return bool(re.fullmatch(r"#?\d{3,8}#?", value.strip()))


def _access_code_from_card_text(text: str) -> str | None:
    for line in text.splitlines():
        clean = line.strip()
        if _is_access_code_line(clean):
            return clean
    return None


def _is_access_code_line(value: str) -> bool:
    return bool(re.fullmatch(r"#?\d{3,8}#", value.strip()))


def _instructor_from_card_text(text: str, date_line: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        date_index = lines.index(date_line.strip())
    except ValueError:
        return None
    for candidate in lines[date_index + 1 :]:
        if _DATE_LINE_RE.search(candidate):
            return None
        if _is_instructor_noise_line(candidate):
            continue
        if re.search(r"\b(Rent/Private|Reservation|Court Booking)\b", candidate, re.I):
            return None
        return candidate
    return None


def _is_instructor_noise_line(value: str) -> bool:
    if _is_pin_or_reference_line(value):
        return True
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}\s*-\s*\d{2}/\d{2}/\d{4}", value):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?\s*h(?:ours?)?", value, re.I):
        return True
    if re.fullmatch(r"\$\d+(?:\.\d{2})?", value):
        return True
    return False
