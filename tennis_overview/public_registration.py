"""Read-only adapter for public USTA/ClubSpark course detail pages, not member portals."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dateutil.parser import parse

from .public_web import Page


def registration_courses(page: Page, zone: str, start: date) -> list[dict[str, Any]] | None:
    parsed = urlsplit(page.url)
    if parsed.hostname != "playtennis.usta.com" or not re.search(
        r"/Coaching/(?:Course|Summary)/", parsed.path, re.I
    ):
        return None  # Other public pages use the generic model extractor.
    if not page.heading:
        return None
    lines = page.text.splitlines()
    sessions: dict[str, dict[str, str]] = {}
    evidence = []
    dated = False
    for i, line in enumerate(lines):
        if not re.fullmatch(
            r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,\s*\w+\s+\d{1,2},\s*20\d{2}", line
        ):
            continue
        dated = True
        try:
            day = parse(line).date()
            if not start <= day < start + timedelta(days=28):
                continue
            if day.strftime("%a").lower() != line[:3].lower():
                continue
            for time_line in lines[i + 1 : i + 4]:
                match = re.fullmatch(
                    r"(\d{1,2}:\d{2}\s*[ap]m)\s*[-\u2013]\s*(\d{1,2}:\d{2}\s*[ap]m)",
                    time_line,
                    re.I,
                )
                if not match:
                    continue
                begins, ends = [
                    datetime.combine(day, parse(t).time(), ZoneInfo(zone)) for t in match.groups()
                ]
                if ends <= begins or any(
                    dt.astimezone(timezone.utc).astimezone(ZoneInfo(zone)) != dt
                    for dt in (begins, ends)
                ):
                    continue
                sessions[begins.isoformat()] = {
                    "starts_at": begins.isoformat(),
                    "ends_at": ends.isoformat(),
                }
                evidence.append(f"{line} {time_line}")
                break
        except (ValueError, OverflowError):
            continue
    if not dated:
        return None
    if not sessions:
        return []  # Explicitly past/out-of-window courses are not undated suggestions.

    def label_value(label: str) -> str:
        try:
            return lines[lines.index(label) + 1][:240]
        except (ValueError, IndexError):
            return ""

    return [
        {
            "id": hashlib.sha256(page.url.encode()).hexdigest()[:20],
            "title": page.heading,
            "club": page.title.split(" - Programming")[0],
            "venue": label_value("Where to find us"),
            "level": label_value("Level:"),
            "price": label_value("Price:"),
            "signup_url": page.url,
            "source_url": page.url,
            "availability": "Not checked",
            "evidence": "\n".join(dict.fromkeys(evidence)),
            "sessions": sorted(sessions.values(), key=lambda s: s["starts_at"]),
        }
    ]
