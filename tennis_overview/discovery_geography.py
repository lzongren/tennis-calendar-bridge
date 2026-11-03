"""Offline, bounded city expansion. Never infer a location from model output."""

from __future__ import annotations

import re
from functools import lru_cache

from geographiclib.geodesic import Geodesic
from geonamescache import GeonamesCache
from geonamescache.types import City


@lru_cache(maxsize=1)
def gazetteer() -> GeonamesCache:
    return GeonamesCache(min_city_population=15000)


def resolve_city(query: str, timezone: str = "") -> City | None:
    """Accept an unambiguous exact city/alias, optionally qualified by state/country."""
    if len(query) > 120 or any(ord(c) < 32 or c in "/\\:" for c in query):
        return None
    text = re.sub(r"\s+and nearby(?: cities)?$", "", query.strip(), flags=re.I)
    parts = [part.strip().casefold() for part in text.split(",")]
    if not 1 <= len(parts) <= 3 or not all(parts):
        return None
    data = gazetteer()
    countries = data.get_countries()
    states = {state["code"]: state for state in data.get_us_states().values()}
    found: dict[int, City] = {}
    candidates = data.search_cities(parts[0], contains_search=False, case_sensitive=False)
    candidates += data.search_cities(
        parts[0], attribute="name", contains_search=False, case_sensitive=False
    )
    for city in candidates:
        country = countries[city["countrycode"]]
        qualifiers = {country[key].casefold() for key in ("iso", "iso3", "name")}
        if city["countrycode"] == "US" and city["admin1code"] in states:
            state = states[city["admin1code"]]
            qualifiers.update((state["code"].casefold(), state["name"].casefold()))
        if all(part in qualifiers for part in parts[1:]):
            found[city["geonameid"]] = city
    if len(found) > 1 and timezone:
        found = {key: city for key, city in found.items() if city["timezone"] == timezone}
    return next(iter(found.values())) if len(found) == 1 else None


def city_label(city: City) -> str:
    country = city["countrycode"]
    region = city["admin1code"] if country == "US" else gazetteer().get_countries()[country]["name"]
    return f"{city['name']}, {region}"


@lru_cache(maxsize=64)
def nearby_cities(query: str, timezone: str = "") -> tuple[str, ...]:
    """Two nearest same-country city centers within 25km, not exhaustive or driving distance."""
    center = resolve_city(query, timezone)
    if center is None:
        return ()
    ranked: list[tuple[float, int, str]] = []
    for city in gazetteer().get_cities().values():
        if city["geonameid"] == center["geonameid"] or city["countrycode"] != center["countrycode"]:
            continue
        # Conservative latitude prefilter; the library computes the actual WGS84 distance.
        if abs(city["latitude"] - center["latitude"]) > 0.25:
            continue
        meters = Geodesic.WGS84.Inverse(
            center["latitude"], center["longitude"], city["latitude"], city["longitude"]
        )["s12"]
        label = city_label(city)
        if 1000 <= meters <= 25000 and len(label) <= 120:
            ranked.append((meters, city["geonameid"], label))
    return tuple(row[2] for row in sorted(ranked)[:2])
