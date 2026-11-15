from typing import Any

import pytest

from benchmarks.score_live_area import Reference, score


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
