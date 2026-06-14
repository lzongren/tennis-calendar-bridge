from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import TennisEvent
from .timeutils import ensure_aware, utc_now


def make_ics(
    events: list[TennisEvent],
    calendar_name: str,
    timezone_name: str = "America/Los_Angeles",
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Tennis Calendar Bridge//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        f"X-WR-TIMEZONE:{_escape(timezone_name)}",
    ]
    stamp = _format_utc(utc_now())
    for event in events:
        lines.extend(_event_lines(event, stamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def parse_ics_events(text: str, club_id: str, timezone_name: str, source_url: str) -> list[TennisEvent]:
    events: list[TennisEvent] = []
    for block in _blocks(text, "VEVENT"):
        data = _parse_block(block)
        start_raw = data.get("DTSTART")
        end_raw = data.get("DTEND")
        if not start_raw:
            continue
        starts_at = _parse_ics_datetime(start_raw, timezone_name)
        ends_at = _parse_ics_datetime(end_raw, timezone_name) if end_raw else starts_at + timedelta(hours=1)
        title = data.get("SUMMARY", "Tennis")
        events.append(
            TennisEvent(
                club_id=club_id,
                external_id=data.get("UID"),
                title=title,
                starts_at=starts_at,
                ends_at=ends_at,
                timezone=timezone_name,
                location=data.get("LOCATION"),
                category=data.get("CATEGORIES"),
                source_url=source_url,
                status="cancelled" if data.get("STATUS") == "CANCELLED" else "confirmed",
                raw=data,
            )
        )
    return events


def _event_lines(event: TennisEvent, stamp: str) -> list[str]:
    uid_source = event.external_id or f"{event.club_id}-{event.title}-{event.starts_at.isoformat()}"
    uid = re.sub(r"[^A-Za-z0-9_.@-]+", "-", uid_source)
    description_parts = [f"Club: {event.club_id}"]
    instructor = _event_instructor(event)
    if instructor:
        description_parts.append(f"Instructor: {instructor}")
    if event.category:
        description_parts.append(f"Type: {event.category}")
    if event.source_url:
        description_parts.append(f"Source: {event.source_url}")
    lines = [
        "BEGIN:VEVENT",
        f"UID:{_escape(uid)}@tennis-calendar-bridge",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_format_utc(event.starts_at)}",
        f"DTEND:{_format_utc(event.ends_at)}",
        f"SUMMARY:{_escape(event.title)}",
        f"DESCRIPTION:{_escape(chr(10).join(description_parts))}",
    ]
    if event.location:
        lines.append(f"LOCATION:{_escape(event.location)}")
    if event.source_url:
        lines.append(f"URL:{_escape(event.source_url)}")
    if event.status == "cancelled":
        lines.append("STATUS:CANCELLED")
    lines.append("END:VEVENT")
    return lines


def _event_instructor(event: TennisEvent) -> str | None:
    value = event.raw.get("instructor")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _format_utc(value: datetime) -> str:
    aware = ensure_aware(value, "UTC").astimezone(timezone.utc)
    return aware.strftime("%Y%m%dT%H%M%SZ")


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    if len(line) <= 74:
        return line
    parts = [line[:74]]
    rest = line[74:]
    while rest:
        parts.append(" " + rest[:73])
        rest = rest[73:]
    return "\r\n".join(parts)


def _blocks(text: str, name: str) -> list[str]:
    pattern = re.compile(rf"BEGIN:{name}\s*(.*?)\s*END:{name}", re.S)
    return pattern.findall(_unfold(text))


def _unfold(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text)


def _parse_block(block: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in re.split(r"\r?\n", block):
        if not raw_line or ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.split(";", 1)[0].upper()
        data[key] = _unescape(value)
    return data


def _unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _parse_ics_datetime(value: str, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    if len(value) == 8:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=zone)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zone)
