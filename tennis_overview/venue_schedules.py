"""Resolve a truncated catalog through the operator's public schedule, not a seed URL."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from urllib.parse import urlsplit

from .browser_protocol import read_url
from .discovery_agent import SourceUnavailable
from .player_event import event_path
from .public_web import Page, search_subject, validate_search_subject

Reader = Callable[[str], Awaitable[Page]]
Index = Callable[[str, str, date, str], Awaitable[list[dict[str, str]]]]
Trace = Callable[[str, str], None]
CLOCK = re.compile(r"(\d{1,2}:\d{2})\s*(AM|PM)?\s*[-–—]\s*(\d{1,2}:\d{2})\s*(AM|PM)", re.I)
PROGRAM = re.compile(r"adult|class|clinic|schedule|program|session", re.I)


def venue_query(club: str, town: str, day: str, title: str) -> str:
    venue = validate_search_subject(club)
    place = validate_search_subject(town)
    requested = date.fromisoformat(day)
    if title:
        # A weekly timetable may be indexed under an earlier occurrence. The
        # index supplies links only; date, time and seats must come from the detail.
        return f'site:courtreserve.com/players/events "{validate_search_subject(title)}" "{venue}" {requested:%A}'
    return f"{venue} {place} adult tennis class schedule {requested:%A %B %d %Y}"


def venue_site(url: str, club: str) -> bool:
    """Conservative first-party check. An abbreviation or directory title is not ownership."""
    try:
        parts = urlsplit(read_url(url))
    except ValueError:
        return False
    brand = "".join(c for c in club.casefold() if c.isalnum())
    labels = (parts.hostname or "").removeprefix("www.").split(".")
    return len(labels) == 2 and labels[0] == brand and not parts.query


def weekly_rows(page: Page, day: date) -> list[tuple[str, str, str]]:
    """Only accept adjacent time/title rows under the selected weekday with an observed link."""
    lines = [line.strip() for line in page.text.splitlines() if line.strip()]
    result = []
    for i, line in enumerate(lines):
        if line.casefold() != day.strftime("%A").casefold():
            continue
        j = i + 1
        while j + 1 < len(lines) and (clock := CLOCK.fullmatch(lines[j])):
            title = lines[j + 1]
            links = [
                link for link in page.link_details if link.title.casefold() == title.casefold()
            ]
            if search_subject(title) and links:
                try:
                    times = [
                        datetime.strptime(v + p.upper(), "%I:%M%p").strftime("%H:%M")
                        for v, p in [(clock[1], clock[2] or clock[4]), (clock[3], clock[4])]
                    ]
                except ValueError:
                    break
                if times[0] < times[1]:
                    result.append((title, times[0], times[1]))
            j += 2
    return list(dict.fromkeys(result))[:3]


def matches_row(page: Page, club: str, day: date, title: str, start: str, end: str) -> bool:
    event = page.player_event
    if not event or event.title.casefold() != title.casefold():
        return False
    a, b = datetime.fromisoformat(event.start), datetime.fromisoformat(event.end)
    return (
        a.date() == b.date() == day
        and a.strftime("%H:%M") == start
        and b.strftime("%H:%M") == end
        and any(
            link.title.casefold() == club.casefold()
            and urlsplit(link.url).netloc in {"courtreserve.com", "player.courtreserve.com"}
            and urlsplit(link.url).path.startswith("/players/courts/us/")
            for link in page.link_details
        )
    )


async def venue_details(
    cutoff: dict[str, str],
    day: date,
    *,
    index: Index,
    read: Reader,
    render: Reader | None,
    trace: Trace,
) -> list[dict[str, str]]:
    club, town = search_subject(cutoff["venue"]), search_subject(cutoff["town"])
    if not club or not town:
        return []
    trace("directory_cutoff", cutoff["listing"])
    sites = await index(club, town, day, "")
    urls = [r["href"] for r in sites if venue_site(r["href"], club)]
    urls.sort(key=lambda url: not bool(PROGRAM.search(urlsplit(url).path)))
    for url in list(dict.fromkeys(urls))[:2]:
        try:
            schedule = await read(url)
        except SourceUnavailable:
            continue
        if not venue_site(schedule.url, club) or club.casefold() not in schedule.title.casefold():
            continue
        trace("venue_site", schedule.url)
        rows = weekly_rows(schedule, day)
        if not rows and render:
            try:
                schedule = await render(schedule.url)
            except SourceUnavailable:
                continue
            if not venue_site(schedule.url, club):
                continue
            rows = weekly_rows(schedule, day)
        if not rows:
            continue
        trace("venue_schedule", schedule.url)
        found = []
        for title, start, end in rows:
            links = [
                link.url
                for link in schedule.link_details
                if link.title.casefold() == title.casefold()
            ]
            # The operator can publish a series-registration page instead of a single
            # occurrence. Resolve it on the *observed* publisher, never by guessing IDs.
            direct = [link for link in links if event_path(link)]
            portals = [
                link
                for link in links
                if urlsplit(link).netloc == "app.courtreserve.com"
                and re.fullmatch(r"/Online/Events/Details/\d+/[A-Za-z0-9]+", urlsplit(link).path)
            ]
            if not direct and not portals:
                continue
            if portals:
                trace("venue_series_link", portals[0])
            candidates = list(direct)
            if not direct:
                try:
                    series = await read(read_url(portals[0]))
                    candidates.extend(
                        link.url for link in series.link_details if event_path(link.url)
                    )
                except (SourceUnavailable, ValueError):
                    pass
                try:
                    indexed = await index(club, town, day, search_subject(title))
                except SourceUnavailable:
                    indexed = []

                def relevance(row: dict[str, str]) -> tuple[bool, bool]:
                    label = row.get("title", "").casefold()
                    return title.casefold() not in label, town.casefold() not in label

                candidates.extend(
                    r["href"] for r in sorted(indexed, key=relevance) if event_path(r["href"])
                )
            for candidate in list(dict.fromkeys(candidates))[:4]:
                trace("venue_detail_candidate", candidate)
                try:
                    detail = await read(candidate)
                except SourceUnavailable:
                    continue
                if matches_row(detail, cutoff["venue"], day, title, start, end):
                    trace("venue_detail_match", detail.url)
                    found.append(
                        {
                            "url": detail.url,
                            "title": title,
                            "club": cutoff["venue"],
                            "listing": schedule.url,
                            "slot_start": start,
                            "slot_end": end,
                        }
                    )
                    break
        return found
    return []
