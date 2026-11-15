"""Read the occurrence-owned definition list on public CourtReserve event pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag


def event_path(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc in {"courtreserve.com", "player.courtreserve.com"}:
        if re.fullmatch(r"/players/events/e-\d+", parts.path) and not parts.query:
            return parts.path
    return None


@dataclass(frozen=True)
class PlayerEvent:
    title: str
    start: str
    end: str
    price: str
    spots: int
    level: str


def player_event(soup: BeautifulSoup, url: str) -> PlayerEvent | None:
    if not event_path(url) or len(titles := soup.find_all("h1")) != 1:
        return None
    lists = [dl for dl in soup.find_all("dl") if dl.find("dt", string="Spots left")]
    if len(lists) != 1:
        return None
    fields: dict[str, Tag] = {}
    for label in lists[0].find_all("dt", recursive=False):
        key = label.get_text(strip=True)
        value = label.find_next_sibling()
        if key in fields or not value or value.name != "dd":
            return None
        fields[key] = value
    try:
        if fields["Sport"].get_text(strip=True).casefold() != "tennis" or not re.search(
            r"\badult\b", fields["Type"].get_text(strip=True), re.I
        ):
            return None
        times = []
        for key in ("Starts", "Ends"):
            nodes = fields[key].find_all("time", datetime=True)
            if len(nodes) != 1:
                return None
            times.append(datetime.fromisoformat(str(nodes[0]["datetime"])))
        start, end = times
        if start.utcoffset() is None or end.utcoffset() is None or end <= start:
            return None
        price = re.fullmatch(
            r"(\$\d+(?:\.\d{2})?) USD", fields["Registration fee"].get_text(strip=True)
        )
        spots = int(fields["Spots left"].get_text(strip=True))
        capacity = int(fields["Capacity"].get_text(strip=True))
        if not price or not 0 <= spots <= capacity:
            return None
    except (KeyError, ValueError):
        return None
    title = titles[0].get_text(" ", strip=True)
    level_text = soup.find(string=re.compile(r"^\s*Level:\s*NTRP\b", re.I))
    level = str(level_text).strip().splitlines()[0][:100] if level_text else ""
    return (
        PlayerEvent(title, start.isoformat(), end.isoformat(), price[1], spots, level)
        if title
        else None
    )
