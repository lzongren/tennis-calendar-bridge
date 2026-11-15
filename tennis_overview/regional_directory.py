"""Bounded, anonymous discovery from CourtReserve's public regional HTML listings."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from geographiclib.geodesic import Geodesic
from geonamescache import GeonamesCache

from .discovery_geography import gazetteer, resolve_city
from .player_event import event_path
from .public_web import fetch_resource

ORIGIN = "https://courtreserve.com"
RADIUS_METERS = 40_000
MAX_TOWNS = 6
MAX_RESULTS = 8
MAX_PAGES_PER_TOWN = 2
OTHER = re.compile(
    r"\b(pickleball|padel|golf|junior|academy|kids|youth|red ball|orange ball|green ball|private|pvt|fitness)\b",
    re.I,
)
GROUP = re.compile(
    r"\b(adult|clinic|drills?|doubles|group|tennis|strokes?|backhand|forehand|live ball)\b", re.I
)


def read_listing(url: str) -> bytes:
    final, _, body = fetch_resource(url, ("text/html",))
    if final.rstrip("/") != url.rstrip("/"):
        raise ValueError("Directory redirected away from requested listing")
    return body


def facilities(body: bytes, state: str) -> list[tuple[str, str, str]]:
    """Take the town label and route slug from the same tennis facility card."""
    towns = []
    for article in BeautifulSoup(body, "html.parser").select("article"):
        anchor = article.select_one("h3 a[href]")
        location = article.find("p")
        if not anchor or not location:
            continue
        link = urlsplit(urljoin(ORIGIN, str(anchor["href"])))
        match = re.fullmatch(
            rf"/players/courts/us/{re.escape(state)}/([a-z0-9-]+)/[a-z0-9-]+", link.path
        )
        if link.netloc == "courtreserve.com" and match:
            towns.append(
                (
                    match[1],
                    location.get_text(" ", strip=True).split(",")[0].casefold(),
                    anchor.get_text(" ", strip=True).casefold(),
                )
            )
    return towns


def dated_cards(
    page: BeautifulSoup, day: date, level: str, clubs: set[str]
) -> list[tuple[int, str, str, str]]:
    """Unknown ratings are verified on the detail; never borrow adjacent card fields."""
    from .discovery import matches_level

    rows: list[tuple[int, str, str, str]] = []
    for card in page.select("article"):
        anchors = card.select("h3 a[href]")
        times = card.select("time[datetime]")
        if len(anchors) != 1 or len(times) != 1:
            continue
        venues = [
            p.get_text(" ", strip=True).split("·")[0].strip()
            for p in card.find_all("p")
            if "·" in p.get_text()
        ]
        if len(venues) != 1 or venues[0].casefold() not in clubs:
            continue
        try:
            start = datetime.fromisoformat(str(times[0]["datetime"]))
        except ValueError:
            continue
        title = anchors[0].get_text(" ", strip=True)
        url = urljoin(ORIGIN, str(anchors[0]["href"]))
        if start.date() != day or not event_path(url) or not title or OTHER.search(title):
            continue
        if not matches_level({"title": title}, level):
            continue
        rows.append((0 if GROUP.search(title) else 1, title[:240], url, venues[0]))
    return rows


def next_listing(page: BeautifulSoup, base: str) -> str | None:
    """Follow published pagination only, on this city listing and never a detail or action URL."""
    links = page.select("a[rel~=next][href], link[rel~=next][href]")
    choices = set()
    for link in links:
        url = urljoin(base, str(link["href"]))
        target = urlsplit(url)
        if (
            target.scheme == "https"
            and target.netloc == "courtreserve.com"
            and target.path == urlsplit(base).path
            and not target.fragment
            and re.fullmatch(r"page=[1-9][0-9]{0,3}", target.query)
        ):
            choices.add(url)
    return next(iter(choices)) if len(choices) == 1 else None


def regional_courses(
    city: str, level: str, day: date, *, read: Callable[[str], bytes] = read_listing
) -> list[dict[str, str]]:
    center = resolve_city(city)
    if not center or center["countrycode"] != "US":
        return []
    state_code = center["admin1code"]
    state_name = gazetteer().get_us_states()[state_code]["name"].lower().replace(" ", "-")
    root = f"{ORIGIN}/players/tennis-courts/us/{state_name}"
    venues = facilities(read(root), state_name)
    towns = {slug: town for slug, town, _ in venues}
    names = set(towns.values())
    # Include smaller suburbs represented in facility listings, without expanding
    # every large city's normal web searches or using the visitor's IP location.
    local: dict[str, float] = {}
    for town in GeonamesCache(min_city_population=5000).get_cities().values():
        if town["countrycode"] != "US" or town["admin1code"] != state_code:
            continue
        name = town["name"].casefold()
        if name not in names or abs(town["latitude"] - center["latitude"]) > 0.4:
            continue
        meters = Geodesic.WGS84.Inverse(
            center["latitude"], center["longitude"], town["latitude"], town["longitude"]
        )["s12"]
        if meters <= RADIUS_METERS:
            local[name] = meters
    ranked = sorted((local[name], slug) for slug, name in towns.items() if name in local)
    hits: list[tuple[int, int, float, str, str, str, str]] = []
    for meters, slug in ranked[:MAX_TOWNS]:
        listing: str | None = f"{ORIGIN}/players/events/us/{state_name}/{slug}"
        seen: set[str] = set()
        clubs = {club for town, _, club in venues if town == slug}
        within_rank: dict[int, int] = defaultdict(int)
        while listing and listing not in seen and len(seen) < MAX_PAGES_PER_TOWN:
            seen.add(listing)
            try:
                page = BeautifulSoup(read(listing), "html.parser")
            except (ValueError, OSError):
                break
            for rank, title, url, club in dated_cards(page, day, level, clubs):
                hits.append((rank, within_rank[rank], meters, title, url, listing, club))
                within_rank[rank] += 1
            listing = next_listing(page, listing)
    unique: dict[str, dict[str, str]] = {}
    for _, _, _, title, url, listing, club in sorted(hits):
        unique.setdefault(url, {"url": url, "title": title, "listing": listing, "club": club[:240]})
    return list(unique.values())[:MAX_RESULTS]
