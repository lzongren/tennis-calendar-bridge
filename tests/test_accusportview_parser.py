from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import pytest

from tennis_overview import db, sync
from tennis_overview.calendar import make_ics, parse_ics_events
from tennis_overview.models import AppConfig, Config
from tennis_overview.scrapers import ScraperError, make_scraper
from tennis_overview.scrapers import accusportview
from tennis_overview.scrapers.accusportview import (
    _schedule_variables,
    _is_schedule_response,
)


ZONE = ZoneInfo("America/Los_Angeles")
LOWER = datetime(2026, 9, 6, tzinfo=ZONE)
UPPER = datetime(2026, 12, 6, tzinfo=ZONE)
SOURCE = "https://example.my.accusportview.com/my"


@pytest.fixture
def schedule():
    path = Path(__file__).parent / "fixtures/accusportview/member-reservations.json"
    return json.loads(path.read_text())


def parse(scraper, schedule):
    return scraper._events_from_schedule(
        schedule["response"], "example-member", SOURCE, LOWER, UPPER
    )


def test_adapter_uses_configured_club_and_timezone(accusportview_scraper, schedule):
    accusportview_scraper.club = replace(
        accusportview_scraper.club,
        id="another-club",
        timezone="America/New_York",
    )
    events = parse(accusportview_scraper, schedule)
    assert all(event.club_id == "another-club" for event in events)
    assert all(event.timezone == "America/New_York" for event in events)
    assert events[0].starts_at.isoformat() == "2026-09-07T19:30:00-04:00"
    assert events[0].location == "Court D"


def test_all_booking_types_reach_storage_and_calendar(accusportview_scraper, schedule, tmp_path):
    events = parse(accusportview_scraper, schedule)
    assert [
        (
            event.title,
            event.starts_at.strftime("%m/%d %H:%M"),
            event.ends_at.strftime("%H:%M"),
            event.location,
        )
        for event in events
    ] == [
        ("Tennis Adult Tactics", "09/07 19:30", "20:30", "Court D"),
        ("Rent/Private", "09/09 19:00", "21:00", "Court A"),
        ("Tennis Adult Doubles Strategy", "09/10 19:30", "21:00", None),
        ("Tennis Adult Open Play", "09/10 20:30", "22:30", None),
        ("Rent/Private", "09/11 19:00", "22:00", "Court B"),
        ("Mixed Doubles Flex League", "10/01 20:00", "22:00", "Court C"),
        ("Mixed Doubles Flex League", "10/08 20:00", "22:00", "Court C"),
    ]
    assert all(event.starts_at.utcoffset() == timedelta(hours=-7) for event in events)
    assert events[0].raw == {"instructor": "Example Coach Beta", "access_code": "1111#"}
    assert events[1].raw == {
        "instructor": "Example Coach Alpha",
        "access_code": "0000#",
    }
    assert events[2].raw == {
        "instructor": "Example Coach Gamma",
        "access_code": "2222#",
    }
    assert events[3].raw == events[4].raw == {}

    conn = db.connect(str(tmp_path / "events.db"))
    try:
        db.upsert_events(conn, events)
        db.upsert_events(conn, events)
        stored = db.list_events(conn, LOWER, UPPER)
    finally:
        conn.close()
    assert len(stored) == 7
    ics = make_ics(stored, "Example Tennis").replace("\r\n ", "")
    restored = parse_ics_events(ics, "example", "America/Los_Angeles", SOURCE)
    assert [(e.title, e.starts_at, e.ends_at, e.location) for e in restored] == [
        (e.title, e.starts_at, e.ends_at, e.location) for e in events
    ]
    assert "Instructor: Example Coach Beta" in ics
    assert "Access code: 1111#" in ics
    assert "SUMMARY:1111#" not in ics
    assert "LOCATION:Example Coach" not in ics
    assert "LOCATION:Court D" in ics


@pytest.mark.parametrize(
    "title",
    [
        "Tennis Adult Social Event",
        "Tennis Adult Competitive PP",
        "Cardio",
        "Junior Camp",
        "Clinic",
        "Match Play",
        "New Course Type",
    ],
)
def test_group_types_do_not_depend_on_title_keywords(accusportview_scraper, schedule, title):
    schedule["response"]["data"]["group"][0]["group"]["group_view"]["name"] = title
    assert parse(accusportview_scraper, schedule)[0].title == title


def test_same_time_distinct_bookings_are_not_collapsed(accusportview_scraper, schedule):
    record = copy.deepcopy(schedule["response"]["data"]["group"][0])
    record["id"] = "another-booking"
    schedule["response"]["data"]["group"].append(record)
    events = parse(accusportview_scraper, schedule)
    assert len(events) == len({event.external_id for event in events}) == 8


def test_other_participants_and_their_pins_are_excluded(accusportview_scraper, schedule):
    record = schedule["response"]["data"]["group"][0]
    record["client_remote_accesses"].insert(0, {"client_id": "other-member", "pin": "9999#"})
    other = copy.deepcopy(record["implementations"][0])
    other["client_datum"]["client"]["id"] = "other-member"
    other["datetime"] = "2026-09-08T12:00:00"
    record["implementations"].append(other)
    events = parse(accusportview_scraper, schedule)
    assert len(events) == 7
    assert events[0].raw["access_code"] == "1111#"


def test_absent_group_view_uses_course_name(accusportview_scraper, schedule):
    assert parse(accusportview_scraper, schedule)[-1].title == "Mixed Doubles Flex League"


def test_identity_survives_coach_title_and_duration_updates(accusportview_scraper, schedule):
    before = parse(accusportview_scraper, schedule)[0]
    record = schedule["response"]["data"]["group"][0]
    record["group"]["group_view"]["name"] = "Updated Tactics"
    record["implementations"][0]["place"] = "Updated Tactics (course description)"
    record["groupMainCoach"]["employee"]["full_name"] = "Replacement Coach"
    record["implementations"][0]["duration"] = 90
    after = parse(accusportview_scraper, schedule)[0]
    assert after.external_id == before.external_id
    assert after.ends_at - before.ends_at == timedelta(minutes=30)


def test_court_enrichment_and_reassignment_update_existing_calendar_event(
    accusportview_scraper, schedule, tmp_path
):
    record = schedule["response"]["data"]["group"][0]
    reserves = record["schedule_slot_reserves"]
    record["schedule_slot_reserves"] = []
    original = parse(accusportview_scraper, schedule)[0]
    assert original.location is None
    record["schedule_slot_reserves"] = reserves
    enriched = parse(accusportview_scraper, schedule)[0]
    for slot in reserves[0]["schedule_slots"]:
        slot["court"]["name"] = "Court E"
    reassigned = parse(accusportview_scraper, schedule)[0]
    assert original.external_id == enriched.external_id == reassigned.external_id
    assert enriched.location == "Court D"
    assert reassigned.location == "Court E"
    assert original.raw == enriched.raw == reassigned.raw

    conn = db.connect(str(tmp_path / "court-update.db"))
    try:
        for event in (original, enriched, reassigned):
            db.upsert_events(conn, [event])
        stored = db.list_events(conn, LOWER, UPPER)
    finally:
        conn.close()
    assert len(stored) == 1
    ics = make_ics(stored, "Example Tennis").replace("\r\n ", "")
    assert "LOCATION:Court E" in ics
    assert "LOCATION:Court D" not in ics
    uid = next(line for line in ics.splitlines() if line.startswith("UID:"))
    assert uid in make_ics([original], "Example Tennis").replace("\r\n ", "")


def test_court_slots_are_scoped_to_session_and_deduplicated(accusportview_scraper, schedule):
    record = schedule["response"]["data"]["group"][0]
    slots = record["schedule_slot_reserves"][0]["schedule_slots"]
    slots.extend(
        [
            {
                "datetime_start": "2026-09-07T19:30:00",
                "datetime_end": "2026-09-07T20:30:00",
                "court": {"name": " Court A "},
            },
            {
                "datetime_start": "2026-09-07T19:00:00",
                "datetime_end": "2026-09-07T19:30:00",
                "court": {"name": "Before"},
            },
            {
                "datetime_start": "2026-09-07T20:30:00",
                "datetime_end": "2026-09-07T21:00:00",
                "court": {"name": "After"},
            },
            {
                "datetime_start": "2026-09-14T19:30:00",
                "datetime_end": "2026-09-14T20:30:00",
                "court": {"name": "Next week"},
            },
        ]
    )
    record["schedule_slot_reserves"].append(
        {
            "canceled": True,
            "schedule_slots": [
                {
                    "datetime_start": "2026-09-07T19:30:00",
                    "datetime_end": "2026-09-07T20:30:00",
                    "court": {"name": "Canceled"},
                }
            ],
        }
    )
    event = parse(accusportview_scraper, schedule)[0]
    assert event.location == "Court A, Court D"
    assert "LOCATION:Court A\\, Court D" in make_ics([event], "Example Tennis")


@pytest.mark.parametrize(
    "timestamp, slot_start, slot_end",
    [
        ("2026-09-07T19:30:00", "2026-09-08T02:30:00Z", "2026-09-08T03:30:00Z"),
        ("2026-11-02T19:30:00", "2026-11-03T03:30:00Z", "2026-11-03T04:30:00Z"),
        ("2026-09-07T23:30:00", "2026-09-08T00:00:00", "2026-09-08T00:30:00"),
    ],
)
def test_court_slots_respect_offsets_dst_and_midnight(
    accusportview_scraper, schedule, timestamp, slot_start, slot_end
):
    record = schedule["response"]["data"]["group"][0]
    record["implementations"][0]["datetime"] = timestamp
    record["schedule_slot_reserves"][0]["schedule_slots"] = [
        {
            "datetime_start": slot_start,
            "datetime_end": slot_end,
            "court": {"name": "Court D"},
        }
    ]
    event = next(
        e for e in parse(accusportview_scraper, schedule) if e.title == "Tennis Adult Tactics"
    )
    assert event.location == "Court D"


@pytest.mark.parametrize(
    "reserves",
    [
        None,
        {},
        [{"canceled": False}],
        [{"canceled": "false", "schedule_slots": []}],
        [{"canceled": False, "schedule_slots": None}],
        [
            {
                "canceled": False,
                "schedule_slots": [
                    {
                        "datetime_start": "2026-09-07T19:30:00",
                        "datetime_end": "2026-09-07T19:30:00",
                        "court": {"name": "Court D"},
                    }
                ],
            }
        ],
        [
            {
                "canceled": False,
                "schedule_slots": [
                    {
                        "datetime_start": "2026-09-07T19:30:00",
                        "datetime_end": "2026-09-07T20:30:00",
                        "court": None,
                    }
                ],
            }
        ],
        [
            {
                "canceled": False,
                "schedule_slots": [
                    {
                        "datetime_start": "2026-09-07T19:30:00",
                        "datetime_end": "2026-09-07T20:30:00",
                        "court": {"name": " "},
                    }
                ],
            }
        ],
    ],
)
def test_invalid_court_assignments_fail_snapshot(accusportview_scraper, schedule, reserves):
    schedule["response"]["data"]["group"][0]["schedule_slot_reserves"] = reserves
    with pytest.raises(ScraperError, match="incomplete or inconsistent"):
        parse(accusportview_scraper, schedule)


def test_missing_court_assignments_fail_snapshot(accusportview_scraper, schedule):
    del schedule["response"]["data"]["group"][0]["schedule_slot_reserves"]
    with pytest.raises(ScraperError, match="incomplete or inconsistent"):
        parse(accusportview_scraper, schedule)


@pytest.mark.parametrize(
    ("timestamp", "duration", "start", "end"),
    [
        (
            "2026-11-03T19:30:00",
            60,
            "2026-11-03T19:30:00-08:00",
            "2026-11-03T20:30:00-08:00",
        ),
        (
            "2026-09-08T02:30:00+00:00",
            60,
            "2026-09-07T19:30:00-07:00",
            "2026-09-07T20:30:00-07:00",
        ),
        (
            "2026-09-07T23:30:00",
            120,
            "2026-09-07T23:30:00-07:00",
            "2026-09-08T01:30:00-07:00",
        ),
    ],
)
def test_timezones_and_overnight_duration(timestamp, duration, start, end):
    parsed = accusportview._session_times({"datetime": timestamp, "duration": duration}, ZONE.key)
    assert tuple(value.isoformat() for value in parsed) == (start, end)


@pytest.mark.parametrize("duration", [None, 0, -60, True, "90", float("nan"), float("inf")])
def test_invalid_duration_fails_entire_snapshot(accusportview_scraper, schedule, duration):
    schedule["response"]["data"]["group"][0]["implementations"][0]["duration"] = duration
    with pytest.raises(ScraperError, match="incomplete or inconsistent"):
        parse(accusportview_scraper, schedule)


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"user": {"title": "Tennis", "start": "2026-09-07T20:38:00"}}},
        {"data": {"individual": [], "group": None}},
        {"data": {"individual": []}},
        {
            "data": {"individual": [], "group": []},
            "errors": [{"message": "private API detail"}],
        },
    ],
)
def test_unrelated_or_partial_payloads_are_never_a_successful_empty_sync(
    accusportview_scraper, payload
):
    with pytest.raises(ScraperError) as failure:
        accusportview_scraper._events_from_schedule(payload, "example-member", SOURCE, LOWER, UPPER)
    assert "private API detail" not in str(failure.value)


def test_verified_empty_snapshot_is_valid(accusportview_scraper):
    assert (
        accusportview_scraper._events_from_schedule(
            {"data": {"individual": [], "group": []}},
            "example-member",
            SOURCE,
            LOWER,
            UPPER,
        )
        == []
    )


def test_window_expands_both_types_preserving_member_and_cancellation_filters(schedule):
    request = schedule["request"]
    before = copy.deepcopy(request)
    request["variables"]["fetchGroup"] = False
    variables, client_id = _schedule_variables(request, LOWER, UPPER)
    assert client_id == "example-member"
    assert "fetchGroup" not in variables
    for key in ("individualFilter", "groupFilter"):
        expected = before["variables"][key]
        expected["implementations"]["datetime"] = {
            "_gte": "2026-09-06 00:00:00",
            "_lt": "2026-12-06 00:00:00",
        }
        assert variables[key] == expected
        assert (
            request["variables"][key]["implementations"]["datetime"]["_gte"]
            == "2026-09-01 00:00:00"
        )


@pytest.mark.parametrize("key", ["client_id", "is_archive", "schedule_slot_reserves"])
def test_missing_member_or_active_filters_abort(schedule, key):
    del schedule["request"]["variables"]["individualFilter"][key]
    with pytest.raises(ScraperError, match="member reservation filters"):
        _schedule_variables(schedule["request"], LOWER, UPPER)


def test_mismatched_member_filters_abort(schedule):
    schedule["request"]["variables"]["groupFilter"]["implementations"]["client_id"]["_eq"] = "other"
    with pytest.raises(ScraperError, match="member reservation filters"):
        _schedule_variables(schedule["request"], LOWER, UPPER)


def test_factory_propagates_configured_lookahead(accusportview_scraper, tmp_path, monkeypatch):
    monkeypatch.setattr(
        accusportview, "utc_now", lambda: datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    )
    scraper = make_scraper(accusportview_scraper.club, tmp_path, lookahead_days=45)
    lower, upper = scraper._window()
    assert lower.isoformat() == "2026-09-29T00:00:00-07:00"
    assert upper.isoformat() == "2026-11-15T00:00:00-08:00"


def test_provider_request_is_not_confused_with_profile_json(schedule):
    request = schedule["request"]
    request["query"] = accusportview._SCHEDULE_QUERY
    response = SimpleNamespace(
        url="https://api.example.com/api/v1/graphql/",
        request=SimpleNamespace(post_data_json=request),
    )
    assert _is_schedule_response(response)
    request["query"] = "query ProfileQuery { user { id } }"
    assert not _is_schedule_response(response)


def test_bad_snapshot_preserves_existing_events_then_empty_snapshot_cancels(
    accusportview_scraper,
    schedule,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(db, "utc_now", lambda: LOWER)
    config = Config(
        AppConfig(database_path=str(tmp_path / "sync.db")),
        [accusportview_scraper.club],
        "test",
    )
    monkeypatch.setattr(sync, "make_scraper", lambda *args, **kwargs: accusportview_scraper)
    monkeypatch.setattr(
        accusportview_scraper, "fetch", lambda: parse(accusportview_scraper, schedule)
    )
    assert sync.sync_all(config)[0].events_seen == 7
    schedule["response"]["data"]["group"] = None
    assert sync.sync_all(config)[0].status == "error"
    conn = db.connect(config.app.database_path)
    try:
        assert len(db.list_events(conn, LOWER, UPPER)) == 7
        schedule["response"] = {"data": {"individual": [], "group": []}}
        assert sync.sync_all(config)[0].status == "success"
        assert db.list_events(conn, LOWER, UPPER) == []
    finally:
        conn.close()


def test_authenticated_fetch_replays_browser_context_and_expands_month(
    accusportview_scraper,
    schedule,
    monkeypatch,
):
    request_body = copy.deepcopy(schedule["request"])
    request_body["query"] = accusportview._SCHEDULE_QUERY
    request_body["variables"]["fetchGroup"] = False
    headers = {
        "cookie": "example-session",
        "x-app-type": "example-member-portal",
        "origin": "https://example.my.accusportview.com",
        "content-length": "999",
    }
    observed = SimpleNamespace(
        ok=True,
        url="https://api.example.com/api/v1/graphql/",
        request=SimpleNamespace(post_data_json=request_body, all_headers=lambda: headers),
    )
    calls = []

    class Page:
        def evaluate(self, script, arguments):
            assert "credentials: 'include'" in script
            assert arguments["url"] == observed.url
            headers, data = arguments["headers"], arguments["body"]
            assert "schedule_slot_reserves(where: {canceled: {_eq: false}})" in data["query"]
            assert "court { name }" in data["query"]
            assert "cookie" not in headers
            assert headers["x-app-type"] == "example-member-portal"
            assert "content-length" not in headers
            assert "fetchGroup" not in data["variables"]
            for kind in ("individualFilter", "groupFilter"):
                assert data["variables"][kind]["implementations"]["datetime"] == {
                    "_gte": "2026-09-06 00:00:00",
                    "_lt": "2026-12-06 00:00:00",
                }
            calls.append(arguments["url"])
            return {"ok": True, "payload": schedule["response"]}

        @contextmanager
        def expect_response(self, predicate, *, timeout):
            assert predicate(observed)
            yield SimpleNamespace(value=observed)

        def goto(self, url, **kwargs):
            assert url == SOURCE

    monkeypatch.setattr(accusportview_scraper, "_window", lambda: (LOWER, UPPER))
    events = accusportview_scraper._fetch_member_schedule(Page())
    assert len(events) == 7
    assert len(calls) == 1
    assert all("example-session" not in json.dumps(event.raw) for event in events)
    assert request_body["variables"] == {
        **schedule["request"]["variables"],
        "fetchGroup": False,
    }


def test_bounded_snapshot_excludes_outside_dates(accusportview_scraper, schedule):
    events = accusportview_scraper._events_from_schedule(
        schedule["response"],
        "example-member",
        SOURCE,
        datetime(2026, 9, 10, tzinfo=ZONE),
        datetime(2026, 10, 1, tzinfo=ZONE),
    )
    assert [event.title for event in events] == [
        "Tennis Adult Doubles Strategy",
        "Tennis Adult Open Play",
        "Rent/Private",
    ]


def test_conflicting_accounting_rows_fail_instead_of_picking_one(accusportview_scraper, schedule):
    schedule["response"]["data"]["individual"][0]["implementations"][1]["duration"] = 30
    with pytest.raises(ScraperError, match="incomplete or inconsistent"):
        parse(accusportview_scraper, schedule)
