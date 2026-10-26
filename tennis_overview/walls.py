"""Curated public practice-wall locations, independent of bookings and inference."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


STATUSES = {"official_practice_wall", "community_report", "handball_wall"}


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("Expected nonempty catalog text (at most 2000 characters).")
    return value.strip()


def _url(value: Any) -> str:
    value = _text(value)
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Catalog source URLs must be HTTPS without credentials.")
    return value


def validate_catalog(raw: Any) -> dict[str, Any]:
    """Allowlist fields so an operator's extra metadata cannot reach the browser."""
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("Unsupported wall catalog version.")
    result: dict[str, Any] = {"version": 1, "locations": [], "leads": []}
    ids: set[str] = set()
    for collection in ("locations", "leads"):
        rows = raw.get(collection, [])
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError("Expected at most 500 catalog entries per collection.")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Expected a catalog object.")
            item: dict[str, Any] = {
                key: _text(row.get(key)) for key in ("id", "name", "city", "notes")
            }
            if not re.fullmatch(r"[a-z0-9-]{1,80}", item["id"]) or item["id"] in ids:
                raise ValueError("Catalog IDs must be unique lowercase slugs.")
            ids.add(item["id"])
            item["reviewed_on"] = date.fromisoformat(_text(row.get("reviewed_on"))).isoformat()
            sources = row.get("sources")
            if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
                raise ValueError("Every entry needs public source evidence.")
            item["sources"] = [
                {"label": _text(source.get("label")), "url": _url(source.get("url"))}
                for source in sources
                if isinstance(source, dict)
            ]
            if len(item["sources"]) != len(sources):
                raise ValueError("Invalid source evidence.")
            if collection == "locations":
                if row.get("status") not in STATUSES:
                    raise ValueError("Invalid evidence status.")
                item["status"] = row["status"]
                for key, bound in (("lat", 90), ("lon", 180)):
                    number = row.get(key)
                    if (
                        isinstance(number, bool)
                        or not isinstance(number, (float, int))
                        or abs(number) > bound
                        or not math.isfinite(number)
                    ):
                        raise ValueError("Invalid map coordinate.")
                    item[key] = number
                if row.get("precision") not in {"venue", "wall"}:
                    raise ValueError("Coordinate precision is required.")
                item["precision"] = row["precision"]
                item["address"] = _text(row.get("address"))
                item["coordinate_source"] = _url(row.get("coordinate_source"))
            result[collection].append(item)
    return result


def load_catalog(path: str = "") -> dict[str, Any]:
    source = Path(path) if path else files("tennis_overview").joinpath("catalogs/walls.json")
    return validate_catalog(json.loads(source.read_text(encoding="utf-8")))
