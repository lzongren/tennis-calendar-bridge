from typing import Any

import pytest

from benchmarks.score_live_area import Reference, diagnose, score
from tennis_overview.discovery_sources import identity
from tennis_overview.venue_schedules import index_scope


@pytest.fixture
def reference() -> Reference:
    return Reference.model_validate(
        {
            "source_url": "https://club.example.org/events/group",
            "starts_at": "2030-03-15T20:00:00-07:00",
            "ends_at": "2030-03-15T21:30:00-07:00",
            "price_usd": "60.00",
            "spots_left": 3,
        }
    )


def course(reference: Reference) -> dict[str, Any]:
    return {
        "source_url": reference.source_url,
        "sessions": [
            {"starts_at": reference.starts_at.isoformat(), "ends_at": reference.ends_at.isoformat()}
        ],
        "price": "$60 USD",
        "spots_left": 3,
        "evidence_ids": ["detail"],
    }


def test_same_occurrence_and_amounts(reference: Reference) -> None:
    row = course(reference)
    assert all(score({"courses": [row]}, reference).values())
    assert not any(score({"courses": [row, row]}, reference).values())


@pytest.mark.parametrize("spots", [None, True, "3", 2])
def test_open_or_unknown_is_not_a_count(reference: Reference, spots: object) -> None:
    row = {**course(reference), "availability": "open", "spots_left": spots}
    assert not score({"courses": [row]}, reference)["remaining_verified"]


def test_no_credit_for_adjacent_session_or_source(reference: Reference) -> None:
    row = course(reference)
    report = {
        "courses": [
            {**row, "sessions": []},
            {**row, "source_url": "https://club.example.org/events/another"},
            {**row, "evidence_ids": []},
        ]
    }
    assert not any(score(report, reference).values())


def test_series_wide_count_is_not_occurrence_owned(reference: Reference) -> None:
    row = course(reference)
    row["sessions"].append(
        {"starts_at": "2030-03-22T20:00:00-07:00", "ends_at": "2030-03-22T21:30:00-07:00"}
    )
    assert not score({"courses": [row]}, reference)["remaining_verified"]


@pytest.mark.parametrize(
    ("stages", "expected"),
    [
        ([], "link_not_observed"),
        (["search_hit"], "search_hit_not_admitted"),
        (["page_link"], "linked_not_admitted"),
        (["search_hit", "admitted"], "admitted_not_read"),
        (["read_attempt"], "read_unsuccessful"),
        (["read_ok"], "read_not_extracted"),
        (["read_ok", "extracted"], "extracted"),
        (["venue_index_dated_anchor"], "index_anchor_not_parsed"),
        (["venue_index_dated_parsed", "venue_detail_rank_cut"], "dropped_by_ranking"),
        (["venue_detail_selected", "venue_detail_mismatch"], "detail_failed_occurrence_match"),
    ],
)
def test_stage_diagnosis_works_for_failed_runs(
    reference: Reference, stages: list[str], expected: str
) -> None:
    reference = reference.model_copy(
        update={"source_url": "https://player.courtreserve.com/players/events/e-42"}
    )
    report = {
        "run_failed": True,
        "courses": [],
        "link_stages": {identity("https://courtreserve.com/players/events/e-42"): stages},
    }
    assert diagnose(report, reference) == expected
    assert not any(score(report, reference).values())
    assert diagnose({}, reference) == "trace_unavailable"


def test_absence_requires_two_observed_pages_for_the_reference_course(reference):
    ref = reference.model_copy(update={"club": "Demo Club", "title": "Evening Doubles"})
    key = identity(index_scope(ref.club, ref.starts_at.date(), ref.title))
    good = ["venue_index_weekday_ok", "venue_index_dated_ok"]
    assert diagnose({"link_stages": {key: good}}, ref) == "absent_from_both_observed_index_pages"
    assert diagnose({"link_stages": {key: good[:1]}}, ref) == "venue_index_incomplete"
    assert diagnose({"link_stages": {"other": good}}, ref) == "link_not_observed"
    attempted = good + ["venue_index_brave_attempt"]
    assert diagnose({"link_stages": {key: attempted}}, ref) == "venue_index_incomplete"
    assert (
        diagnose({"link_stages": {key: attempted + ["venue_index_brave_ok"]}}, ref)
        == "absent_from_both_public_indexes"
    )
