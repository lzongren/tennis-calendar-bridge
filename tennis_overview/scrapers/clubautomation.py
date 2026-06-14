from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..models import TennisEvent
from ..timeutils import utc_now
from .browser import BrowserPortalScraper


WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


class ClubAutomationScraper(BrowserPortalScraper):
    candidate_paths = (
        "/",
        "/member",
        "/member/index",
        "/member-portal",
        "/member-portal/account",
        "/member-portal/reservations",
        "/member-portal/classes",
        "/member-portal/programs",
        "/member-portal/calendar",
        "/calendar",
        "/reservations",
    )
    candidate_clicks = BrowserPortalScraper.candidate_clicks + (
        "My Account",
        "My Calendar",
        "My Classes",
        "Programs",
        "Court Reservations",
    )

    def _collect_from_page(self, page: Any, source_url: str) -> list[TennisEvent]:
        events = super()._collect_from_page(page, source_url)
        events.extend(self._collect_member_sidebar(page, source_url))
        return events

    def _collect_member_sidebar(self, page: Any, source_url: str) -> list[TennisEvent]:
        try:
            text = page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return []
        if "MY EVENTS" not in text and "MY REGISTRATIONS" not in text:
            return []
        events = []
        events.extend(self._parse_my_events(text, source_url))
        events.extend(self._parse_my_registrations(text, source_url))
        return events

    def _parse_my_events(self, text: str, source_url: str) -> list[TennisEvent]:
        section = _section(text, "MY EVENTS", ("MY REGISTRATIONS", "CLUB ANNOUNCEMENTS"))
        events: list[TennisEvent] = []
        zone = ZoneInfo(self.club.timezone)
        year = utc_now().astimezone(zone).year
        pattern = re.compile(
            r"\b(?P<month>[A-Z]{3})\s+(?P<day>\d{1,2})\s+"
            r"(?P<title>.+?)\s+"
            r"(?P<start>\d{1,2}:\d{2}\s*[ap]m)\s*-\s*"
            r"(?P<end>\d{1,2}:\d{2}\s*[ap]m)\s+"
            r"(?P<detail>.+?)(?=\b[A-Z]{3}\s+\d{1,2}\b|$)",
            re.I | re.S,
        )
        for match in pattern.finditer(section):
            month = _month_number(match.group("month"))
            if month is None:
                continue
            start_date = date(year, month, int(match.group("day")))
            starts_at, ends_at = _combine_times(start_date, match.group("start"), match.group("end"), zone)
            title = _clean(match.group("title"))
            detail = _clean(match.group("detail"))
            location, instructor = _detail_metadata(detail)
            events.append(
                TennisEvent(
                    club_id=self.club.id,
                    external_id=f"clubautomation-event-{starts_at.date()}-{_slug(title)}",
                    title=title,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    timezone=self.club.timezone,
                    location=location,
                    category="event",
                    source_url=source_url,
                    raw=_raw_detail(detail, instructor),
                )
            )
        return events

    def _parse_my_registrations(self, text: str, source_url: str) -> list[TennisEvent]:
        section = _section(text, "MY REGISTRATIONS", ("CLUB ANNOUNCEMENTS", "MY EVENTS"))
        events: list[TennisEvent] = []
        zone = ZoneInfo(self.club.timezone)
        pattern = re.compile(
            r"Program:\s*(?P<title>.+?)\s+"
            r"(?P<weekday>Mon(?:day)?|Tue(?:s|sday)?|Wed(?:nesday)?|Thu(?:r|rs|rsday|rday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)"
            r"\s*\|\s*(?P<start>\d{1,2}:\d{2}\s*[ap]m)\s*-\s*(?P<end>\d{1,2}:\d{2}\s*[ap]m)\s+"
            r"(?P<detail>.+?)\((?P<range_start>\d{1,2}/\d{1,2}/\d{4})\s*-\s*(?P<range_end>\d{1,2}/\d{1,2}/\d{4})\)",
            re.I | re.S,
        )
        for match in pattern.finditer(section):
            weekday = WEEKDAYS.get(match.group("weekday").lower())
            if weekday is None:
                continue
            range_start = datetime.strptime(match.group("range_start"), "%m/%d/%Y").date()
            range_end = datetime.strptime(match.group("range_end"), "%m/%d/%Y").date()
            current = range_start + timedelta(days=(weekday - range_start.weekday()) % 7)
            title = _clean(match.group("title"))
            detail = _clean(match.group("detail"))
            location, instructor = _detail_metadata(detail)
            while current <= range_end:
                starts_at, ends_at = _combine_times(current, match.group("start"), match.group("end"), zone)
                events.append(
                    TennisEvent(
                        club_id=self.club.id,
                        external_id=f"clubautomation-registration-{current}-{_slug(title)}",
                        title=title,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        timezone=self.club.timezone,
                        location=location,
                        category="registration",
                        source_url=source_url,
                        raw=_raw_detail(detail, instructor),
                    )
                )
                current += timedelta(days=7)
        return events


def _section(text: str, start_marker: str, end_markers: tuple[str, ...]) -> str:
    start = text.find(start_marker)
    if start == -1:
        return ""
    start += len(start_marker)
    ends = [text.find(marker, start) for marker in end_markers]
    ends = [end for end in ends if end != -1]
    end = min(ends) if ends else len(text)
    return text[start:end]


def _month_number(value: str) -> int | None:
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    return months.get(value[:3].lower())


def _combine_times(start_date: date, start_value: str, end_value: str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start_time = _parse_time(start_value)
    end_time = _parse_time(end_value)
    starts_at = datetime.combine(start_date, start_time, zone)
    ends_at = datetime.combine(start_date, end_time, zone)
    if ends_at <= starts_at:
        ends_at += timedelta(days=1)
    return starts_at, ends_at


def _parse_time(value: str) -> time:
    normalized = re.sub(r"\s*([AP]M)$", r" \1", _clean(value).upper())
    return datetime.strptime(normalized, "%I:%M %p").time()


def _detail_metadata(value: str) -> tuple[str | None, str | None]:
    parts = [_clean(part) for part in value.split("|") if _clean(part)]
    if not parts:
        return None, None
    last = parts[-1]
    if _looks_like_location(last):
        return last, None
    return None, last


def _raw_detail(detail: str, instructor: str | None) -> dict[str, str]:
    raw = {"clubautomation_detail": detail}
    if instructor:
        raw["instructor"] = instructor
    return raw


def _looks_like_location(value: str) -> bool:
    return bool(re.search(r"\b(court|room|field|gym|indoor|outdoor|tennis|pickle)\b", value, re.I))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
