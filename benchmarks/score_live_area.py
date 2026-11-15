"""Score a saved blind run against an independently observed, public occurrence."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from tennis_overview.player_event import event_path
from tennis_overview.discovery_sources import identity
from tennis_overview.venue_schedules import index_scope


class Reference(BaseModel):
    source_url: str
    starts_at: datetime
    ends_at: datetime
    price_usd: Decimal = Field(ge=0)
    spots_left: int = Field(gt=0, strict=True)
    club: str = ""
    title: str = ""


def public_aliases(url: str) -> set[str]:
    path = event_path(url)
    if path:
        return {f"https://{host}{path}" for host in ("courtreserve.com", "player.courtreserve.com")}
    return {url}


def diagnose(report: dict[str, Any], reference: Reference) -> str:
    if "link_stages" not in report:
        return "trace_unavailable"
    stages = {
        stage
        for url in public_aliases(reference.source_url)
        for stage in report["link_stages"].get(identity(url), [])
    }
    for stage, outcome in (
        ("extracted", "extracted"),
        ("read_ok", "read_not_extracted"),
        ("venue_detail_match", "matched_before_admission"),
        ("venue_detail_mismatch", "detail_failed_occurrence_match"),
        ("venue_detail_unavailable", "venue_detail_unavailable"),
        ("venue_detail_candidate", "venue_read_attempted"),
        ("venue_detail_selected", "selected_not_read"),
        ("venue_detail_rank_cut", "dropped_by_ranking"),
        ("venue_index_weekday_parsed", "index_result_not_selected"),
        ("venue_index_dated_parsed", "index_result_not_selected"),
        ("venue_index_brave_parsed", "index_result_not_selected"),
        ("venue_index_brave_broad_parsed", "index_result_not_selected"),
        ("venue_index_weekday_anchor", "index_anchor_not_parsed"),
        ("venue_index_dated_anchor", "index_anchor_not_parsed"),
        ("read_attempt", "read_unsuccessful"),
        ("admitted", "admitted_not_read"),
        ("page_link", "linked_not_admitted"),
        ("search_hit", "search_hit_not_admitted"),
    ):
        if stage in stages:
            return outcome
    if reference.club and reference.title:
        key = index_scope(reference.club, reference.starts_at.date(), reference.title)
        queries = report["link_stages"].get(identity(key), [])
        if "venue_index_brave_attempt" in queries:
            if (
                "venue_index_brave_broad_attempt" in queries
                and "venue_index_brave_broad_ok" not in queries
            ):
                return "venue_index_incomplete"
            if all(f"venue_index_{v}_ok" in queries for v in ("weekday", "dated", "brave")):
                return "absent_from_both_public_indexes"
            return "venue_index_incomplete"
        if all(f"venue_index_{variant}_ok" in queries for variant in ("weekday", "dated")):
            return "absent_from_both_observed_index_pages"
        if queries:
            return "venue_index_incomplete"
    return "link_not_observed"


def score(report: dict[str, Any], reference: Reference) -> dict[str, bool]:
    matches = [
        c
        for c in report["courses"]
        if c["source_url"] in public_aliases(reference.source_url)
        and c.get("evidence_ids")
        and any(
            datetime.fromisoformat(s["starts_at"]) == reference.starts_at
            and datetime.fromisoformat(s["ends_at"]) == reference.ends_at
            for s in c["sessions"]
        )
    ]
    course = matches[0] if len(matches) == 1 else {}
    price = re.fullmatch(r"\$(\d+(?:\.\d{2})?)(?: USD)?", course.get("price", ""))
    spots = course.get("spots_left")
    return {
        "occurrence_found": bool(course),
        "price_verified": price is not None and Decimal(price[1]) == reference.price_usd,
        "remaining_verified": len(course.get("sessions", [])) == 1
        and type(spots) is int
        and spots == reference.spots_left,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    reference = Reference.model_validate_json(args.reference.read_text())
    checks = score(report, reference)
    print(json.dumps({**checks, "link_stage": diagnose(report, reference)}))
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
