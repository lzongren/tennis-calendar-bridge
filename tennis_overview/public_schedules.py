"""Published weekly timetables are discovery candidates, never confirmed bookings."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import parse

from .public_web import Page, ScheduleBlock

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY = re.compile(r"\b(" + "|".join(d + "s?" for d in WEEKDAYS) + r")\b", re.I)
TIME = r"(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*[ap]m"
TIMES = re.compile(r"(" + TIME + r")\s*[-\u2013\u2014]\s*(" + TIME + r")", re.I)
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATE = MONTH + r"\s+\d{1,2}(?:,?\s+20\d{2})?"
RANGE = re.compile(r"(" + DATE + r")\s*[-\u2013\u2014]\s*(" + DATE + r")", re.I)


def _ongoing_blocks(page: Page) -> list[ScheduleBlock] | None:
    if not re.search(r"\b(?:adult\s+)?clinics?\s+(?:are\s+)?ongoing\b", page.text, re.I):
        return None
    blocks = []
    lines = page.text.splitlines()
    starts = [i for i, line in enumerate(lines) if DAY.match(line)] + [len(lines)]
    for first, last in zip(starts, starts[1:]):
        text = " ".join(lines[first:last])
        if "Adult Clinic:" not in text or not TIMES.search(text):
            continue
        title = text.split("Adult Clinic:", 1)[0].strip()
        level_pattern = r"\d\.\d\s*[-\u2013]\s*\d\.\d\+?"
        level = re.search(level_pattern + r"(?=\s+level)", text) or re.search(level_pattern, title)
        # Exclude contacts and unrelated policy text from the quoted course block.
        text = text.split("Contact Coach", 1)[0].strip()
        blocks.append(ScheduleBlock(title, level.group() if level else "", text, page.url))
    return blocks or None


def _sessions(
    block: ScheduleBlock, zone: str, start: date, ongoing: bool
) -> tuple[list[dict[str, str]], str, str]:
    text = block.text
    days = {WEEKDAYS.index(m.group().rstrip("s").capitalize()) for m in DAY.finditer(text)}
    times = list(TIMES.finditer(text))
    if len(days) != 1 or len(times) != 1:
        return [], "unresolved", "Weekday or time is ambiguous."
    weekday = next(iter(days))
    begins, ends = [parse(t).time() for t in times[0].groups()]
    if ends <= begins:
        return [], "unresolved", "Time range needs confirmation."
    ranges = list(RANGE.finditer(text))
    first, last = start, start + timedelta(days=27)
    basis = "weekly_timetable"
    if len(ranges) == 1:
        left, right = ranges[0].groups()
        years = re.findall(r"\b20\d{2}\b", ranges[0].group())
        possibilities = []
        for year in [int(years[0])] if years else range(start.year - 1, start.year + 2):
            try:
                a = parse(left, default=datetime(year, 1, 1)).date()
                b = parse(right, default=datetime(a.year, 1, 1)).date()
                if b < a and not re.search(r"\b20\d{2}\b", right):
                    b = b.replace(year=b.year + 1)
            except ValueError:
                continue
            if not 0 <= (b - a).days <= 180:
                continue
            if not years and (a.weekday() != weekday or b.weekday() != weekday):
                continue
            if not years and not (a <= last and b >= first):
                continue
            possibilities.append((a, b))
        if len(possibilities) != 1:
            return [], "unresolved", "The schedule year cannot be resolved uniquely."
        first, last = possibilities[0]
        basis = "published_dates" if years else "year_inferred"
    elif ranges or re.search(DATE + r"|\b20\d{2}\b", text, re.I) or not ongoing:
        return [], "unresolved", "The term dates need confirmation."
    if re.search(r"\b(skip|except|excluding|holiday|cancelled|canceled)\b|no class", text, re.I):
        return [], "unresolved", "The schedule includes exclusions that need confirmation."
    all_days = [
        first + timedelta(days=i)
        for i in range((last - first).days + 1)
        if (first + timedelta(days=i)).weekday() == weekday
    ]
    count = re.search(r"\b(\d+)\s+Classes\b", text, re.I)
    if count and int(count.group(1)) != len(all_days):
        return [], "unresolved", "Published class count differs from the weekly dates."
    sessions = []
    for day in all_days:
        if not start <= day < start + timedelta(days=28):
            continue
        a, b = [datetime.combine(day, t, ZoneInfo(zone)) for t in (begins, ends)]
        if any(
            dt.astimezone(timezone.utc).astimezone(ZoneInfo(zone)) != dt
            or dt.replace(fold=1).utcoffset() != dt.utcoffset()
            for dt in (a, b)
        ):
            continue
        sessions.append({"starts_at": a.isoformat(), "ends_at": b.isoformat()})
    note = {
        "weekly_timetable": "Expanded from the club's ongoing weekly timetable; confirm the date before signing up.",
        "year_inferred": f"Year inferred as {first.year} from the weekday and term dates; confirm with the club.",
        "published_dates": "Dates published by the club; availability is not checked.",
    }[basis]
    return sessions, basis, note


def timetable_courses(page: Page, zone: str, start: date) -> list[dict[str, Any]] | None:
    ongoing = not bool(page.schedules)
    blocks = list(page.schedules) if page.schedules else _ongoing_blocks(page)
    if blocks is None:
        return None
    courses = []
    for block in blocks:
        sessions, basis, note = _sessions(block, zone, start, ongoing)
        prices = list(dict.fromkeys(re.findall(r"\$\d+(?:\.\d{2})?", block.text)))
        courses.append(
            {
                "id": hashlib.sha256(
                    f"{page.url}\n{block.title}\n{block.text}".encode()
                ).hexdigest()[:20],
                "title": block.title,
                "club": re.split(r"\s+[\u2013\u2014|]\s+", page.title)[-1],
                "venue": "",
                "level": block.level,
                "price": prices[0] if len(prices) == 1 else "",
                "signup_url": block.signup_url,
                "source_url": page.url,
                "availability": "Not checked",
                "evidence": block.text,
                "sessions": sessions,
                "schedule_basis": basis,
                "schedule_note": note,
            }
        )
    return courses
