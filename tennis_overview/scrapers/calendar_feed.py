from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar

from ..models import ClubConfig, TennisEvent
from ..timeutils import utc_now
from .base import BaseScraper, ScraperError


MAX_FEED_BYTES = 5_000_000
MAX_EVENTS = 10_000
FEED_TIMEOUT_SECONDS = 30


def _feed_url(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        raise ValueError("invalid URL")
    parts = urlsplit(value)
    if parts.scheme == "webcal":
        parts = parts._replace(scheme="https")
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("HTTPS feed URL required")
    parts.port  # Validate the port without including the URL in any errors.
    return urlunsplit(parts)


class _FeedRedirectHandler(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = _feed_url(urljoin(req.full_url, newurl))
            before, after = urlsplit(req.full_url), urlsplit(target)
            if (before.hostname, before.port or 443) != (
                after.hostname,
                after.port or 443,
            ):
                raise ValueError("different origin")
        except ValueError:
            raise ScraperError(
                "Calendar feed redirected to a different origin or an insecure URL. "
                "Configure the provider's final HTTPS feed URL instead."
            ) from None
        return super().redirect_request(req, fp, code, msg, headers, target)


def download_feed(url: str) -> bytes:
    try:
        request = Request(
            _feed_url(url),
            headers={
                "Accept": "text/calendar",
                "Accept-Encoding": "identity",
                "User-Agent": "TennisCalendarBridge/0.1",
            },
        )
        with build_opener(_FeedRedirectHandler()).open(
            request, timeout=FEED_TIMEOUT_SECONDS
        ) as response:
            if response.status != 200:
                raise ScraperError(f"Calendar feed request failed (HTTP {response.status}).")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_FEED_BYTES:
                raise ScraperError("Calendar feed exceeds the download size limit.")
            body = response.read(MAX_FEED_BYTES + 1)
            if len(body) > MAX_FEED_BYTES:
                raise ScraperError("Calendar feed exceeds the download size limit.")
            if length is not None and len(body) != int(length):
                raise ScraperError("Calendar feed download was incomplete.")
            return body
    except ScraperError:
        raise
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise ScraperError(f"Calendar feed request failed (HTTP {code}).") from None
    except Exception:
        # HTTP/parser exception messages may contain the bearer URL or feed data.
        raise ScraperError(
            "Calendar feed download failed. Check the private HTTPS URL and provider access."
        ) from None


class CalendarFeedScraper(BaseScraper):
    def fetch(self) -> list[TennisEvent]:
        name = self.club.options.get("calendar_feed_env")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ScraperError("calendar_feed_env must name an environment variable.")
        value = os.getenv(name, "").strip()
        if not value:
            raise ScraperError(
                f"Missing calendar feed: set {name} from your private configuration."
            )
        today = (
            utc_now()
            .astimezone(ZoneInfo(self.club.timezone))
            .replace(hour=0, minute=0, second=0, microsecond=0)
        )
        return parse_feed(
            download_feed(value),
            self.club,
            today - timedelta(days=1),
            today + timedelta(days=self.lookahead_days + 1),
        )


def _aware(value: date | datetime, zone: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=zone)
    raise ValueError("invalid calendar date")


def _validate_event(event: Any, zone: ZoneInfo) -> None:
    if not str(event.get("UID", "")).strip() or isinstance(event.get("UID"), list):
        raise ValueError("missing or duplicate UID")
    start = event.decoded("DTSTART")
    if not isinstance(start, date):
        raise ValueError("missing start")
    for key in ("DTSTART", "DTEND", "RECURRENCE-ID", "RDATE", "EXDATE"):
        properties = event.get(key, [])
        if not isinstance(properties, list):
            properties = [properties]
        for prop in properties:
            values = getattr(prop, "dts", [prop])
            for item in values:
                value = item.dt
                if prop.params.get("TZID") and isinstance(value, datetime) and value.tzinfo is None:
                    raise ValueError("unresolved timezone")
    if "DTEND" in event and "DURATION" in event:
        raise ValueError("ambiguous end")
    end = event.decoded("DTEND", None)
    if end is not None:
        if isinstance(start, datetime) != isinstance(end, datetime):
            raise ValueError("inconsistent date types")
        if _aware(end, zone).astimezone(timezone.utc) <= _aware(start, zone).astimezone(
            timezone.utc
        ):
            raise ValueError("nonpositive event duration")
    elif "DURATION" in event:
        duration = event.decoded("DURATION")
        if not isinstance(duration, timedelta) or duration <= timedelta(0):
            raise ValueError("invalid duration")
    elif isinstance(start, datetime):
        raise ValueError("timed event has no end or duration")
    if str(event.get("STATUS", "CONFIRMED")).upper() not in {
        "CONFIRMED",
        "TENTATIVE",
        "CANCELLED",
    }:
        raise ValueError("invalid status")
    rules = event.get("RRULE", [])
    if not isinstance(rules, list):
        rules = [rules]
    for rule in rules:
        # Sub-hour recurrence is not a booking schedule and can expand unboundedly.
        if str(rule.get("FREQ", [""])[0]) not in {
            "HOURLY",
            "DAILY",
            "WEEKLY",
            "MONTHLY",
            "YEARLY",
        }:
            raise ValueError("unsupported recurrence frequency")


def parse_feed(
    body: bytes, club: ClubConfig, lower: datetime, upper: datetime
) -> list[TennisEvent]:
    try:
        text = body.decode("utf-8-sig").strip()
        if not text.startswith("BEGIN:VCALENDAR") or not text.endswith("END:VCALENDAR"):
            raise ValueError("not a complete calendar")
        calendar = Calendar.from_ical(text)
        if calendar.name != "VCALENDAR" or str(calendar.get("VERSION")) != "2.0":
            raise ValueError("unsupported calendar")
        if str(calendar.get("METHOD", "PUBLISH")).upper() != "PUBLISH":
            raise ValueError("not a complete subscription snapshot")
        if any(component.errors for component in calendar.walk()):
            raise ValueError("calendar has parser errors")
        zone = ZoneInfo(club.timezone)
        components = calendar.walk("VEVENT")
        if len(components) > MAX_EVENTS:
            raise ValueError("too many events")
        recurring_uids = set()
        for component in components:
            _validate_event(component, zone)
            if any(key in component for key in ("RRULE", "RDATE", "RECURRENCE-ID")):
                recurring_uids.add(str(component["UID"]))
        # Floating local times belong to the calendar's zone, or the configured club.
        if "X-WR-TIMEZONE" not in calendar:
            calendar["X-WR-TIMEZONE"] = club.timezone
        occurrences = recurring_ical_events.of(calendar, skip_bad_series=False).between(
            lower, upper
        )
        if len(occurrences) > MAX_EVENTS:
            raise ValueError("too many occurrences")
        events: dict[str, TennisEvent] = {}
        for item in occurrences:
            start = _aware(item.decoded("DTSTART"), zone)
            end = _aware(item.decoded("DTEND"), zone)
            if not lower <= start < upper:
                continue
            if end.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
                raise ValueError("invalid occurrence duration")
            uid = str(item["UID"])
            identity = uid
            if uid in recurring_uids:
                anchor = item.decoded("RECURRENCE-ID")
                identity += "|" + _aware(anchor, zone).astimezone(timezone.utc).isoformat()
            external_id = "feed-" + hashlib.sha256(identity.encode()).hexdigest()
            event = TennisEvent(
                club_id=club.id,
                external_id=external_id,
                title=str(item.get("SUMMARY", "Tennis")).strip() or "Tennis",
                starts_at=start,
                ends_at=end,
                timezone=club.timezone,
                location=str(item["LOCATION"]) if "LOCATION" in item else None,
                category="calendar",
                source_url=club.base_url,
                status=str(item.get("STATUS", "CONFIRMED")).lower(),
                raw={"all_day": True} if not isinstance(item.decoded("DTSTART"), datetime) else {},
            )
            if external_id in events and events[external_id] != event:
                raise ValueError("conflicting occurrences")
            events[external_id] = event
        return sorted(events.values(), key=lambda event: (event.starts_at, event.external_id))
    except Exception:
        raise ScraperError(
            "Calendar feed is invalid, incomplete, or contains unsupported events. "
            "No bookings were updated."
        ) from None
