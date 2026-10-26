from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from tennis_overview.discovery import (
    DiscoveryError,
    DiscoveryService,
    infer,
    validate_course,
    discover,
)
from tennis_overview.models import AppConfig
from tennis_overview.public_web import Page, public_addresses, public_url


@pytest.fixture
def page():
    return Page(
        "https://club.example.org/adults",
        "Example Club",
        """Example Club
Adult Doubles Clinic
NTRP 3.0
Court 2
$30
Mondays October 26, 2026 - November 9, 2026 7:30 PM - 9:00 PM
""",
        ("https://club.example.org/register",),
    )


@pytest.fixture
def course():
    return dict(
        title="Adult Doubles Clinic",
        club="Example Club",
        level="NTRP 3.0",
        venue="Court 2",
        price="$30",
        signup_url="https://club.example.org/register",
        schedule_text="Mondays October 26, 2026 - November 9, 2026 7:30 PM - 9:00 PM",
        year_text="2026",
        date_start_text="October 26, 2026",
        date_end_text="November 9, 2026",
        start_time_text="7:30 PM",
        end_time_text="9:00 PM",
        weekdays=["Mon"],
    )


def test_recurrence_and_dst_are_expanded_in_code(page, course):
    result = validate_course(course, page, "America/Los_Angeles", date(2026, 10, 25))
    assert [s["starts_at"] for s in result["sessions"]] == [
        "2026-10-26T19:30:00-07:00",
        "2026-11-02T19:30:00-08:00",
        "2026-11-09T19:30:00-08:00",
    ]
    assert result["availability"] == "Not checked"
    assert result["signup_url"] == page.url


@pytest.mark.parametrize(
    "field,value",
    [
        ("year_text", "2027"),
        ("date_start_text", "October 28, 2026"),
        ("weekdays", ["Sun"]),
        ("end_time_text", "7:30 PM"),
        ("schedule_text", "Invented schedule"),
        ("start_time_text", "7:30"),
    ],
)
def test_unsupported_schedule_cannot_create_calendar_session(page, course, field, value):
    course[field] = value
    result = validate_course(course, page, "America/Los_Angeles", date(2026, 10, 25))
    assert result["sessions"] == []


def test_unknown_fields_and_fabricated_links_not_exported(page, course):
    course.update(price="$999", signup_url="javascript:alert(1)", secret="private-code", seats=8)
    result = validate_course(course, page, "America/Los_Angeles", date(2026, 10, 25))
    assert result["price"] == ""
    assert result["signup_url"] == page.url
    assert "secret" not in result and "seats" not in result
    assert validate_course({"title": "Invented"}, page, "UTC", date.today()) is None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/a",
        "http://[::1]/",
        "http://169.254.169.254/",
        "https://model.example.ts.net/",
        "https://user:pass@example.org",
        "https://example.org:8123/a",
        "https://example.org/?api_key=abc",
        "https://example.org/Online/PublicIcal/Index/1/2/secret",
    ],
)
def test_private_sources_rejected(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_dns_rebinding_and_mixed_addresses_rejected(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError):
        public_addresses("club.example.org", 443)


def test_store_preserves_snapshot_on_inference_failure(tmp_path, monkeypatch):
    service = DiscoveryService(AppConfig(database_path=str(tmp_path / "tennis.db")))
    row_id = service.add({"query": "Example City", "timezone": "America/New_York"})
    result = {"courses": [], "sources": [], "checked_at": "2026-10-25T00:00:00+00:00"}
    monkeypatch.setattr("tennis_overview.discovery.discover", lambda *a: result)
    assert service.run_one()
    assert service.list()[0]["result"] == result
    with service.connect() as conn:
        conn.execute("UPDATE locations SET status='queued' WHERE id=?", (row_id,))

    def fail(*a):
        raise DiscoveryError("Cannot reach the inference service; previous results were retained.")

    monkeypatch.setattr("tennis_overview.discovery.discover", fail)
    assert service.run_one()
    assert service.list()[0]["status"] == "error"
    assert service.list()[0]["result"] == result
    assert not (tmp_path / "tennis.db").exists()  # Bookings are untouched.


def test_store_validates_and_deduplicates_locations(tmp_path):
    service = DiscoveryService(AppConfig(database_path=str(tmp_path / "test.db")))
    for payload in [
        {"query": "x"},
        {"query": "City", "timezone": "invalid"},
        {"query": "City", "sources": ["http://localhost/"]},
    ]:
        with pytest.raises(ValueError):
            service.add(payload)
    row_id = service.add({"query": "City", "sources": ["https://club.example.org/"]})
    with pytest.raises(ValueError):
        service.add({"query": "City"})
    service.refresh(row_id)
    service.delete(row_id)
    assert service.list() == []


def test_inference_auth_errors_are_redacted():
    app = AppConfig(
        discovery_api_url="https://model.example.org/v1", discovery_api_key="secret-key"
    )
    with patch("tennis_overview.discovery.build_opener") as opener:
        opener.return_value.open.side_effect = HTTPError(
            "https://secret.invalid", 401, "secret-key", {}, None
        )
        with pytest.raises(DiscoveryError, match="authentication failed") as error:
            infer(app, [{"role": "user", "content": "public course text"}])
        assert "secret-key" not in str(error.value)
        req = opener.return_value.open.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer secret-key"
        assert "secret-key" not in req.data.decode()


def test_inference_requires_tls_and_rejects_truncated_json():
    app = AppConfig(discovery_api_url="http://model.example.org/v1", discovery_api_key="test")
    with pytest.raises(DiscoveryError, match="HTTPS"):
        infer(app, [])
    with patch("tennis_overview.discovery.build_opener") as opener:
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"choices": [{"finish_reason": "length", "message": {"content": '{"courses":[]}'}}]}
        ).encode()
        with pytest.raises(DiscoveryError, match="did not finish"):
            infer(replace(app, discovery_api_url="https://model.example.org/v1"), [])


def test_search_failure_with_explicit_source_is_partial(page, course, monkeypatch):
    def failed(*a):
        raise TimeoutError()

    monkeypatch.setattr("tennis_overview.discovery.search_courses", failed)
    monkeypatch.setattr("tennis_overview.discovery.fetch_page", lambda url: page)
    monkeypatch.setattr("tennis_overview.discovery.extract", lambda *a: [course])
    result = discover(
        AppConfig(),
        {"query": "Example City", "timezone": "UTC", "level": "3.0", "sources": [page.url]},
    )
    assert result["partial"] and result["search_failed"]
    assert result["sources"][0]["status"] == "checked"
    assert len(result["courses"]) == 1


def test_no_sources_reports_failure(monkeypatch):
    monkeypatch.setattr("tennis_overview.discovery.search_courses", lambda *a: [])
    with pytest.raises(DiscoveryError, match="no sources"):
        discover(AppConfig(), {"query": "City", "timezone": "UTC", "level": "", "sources": []})


@pytest.mark.parametrize(
    "offered,requested,expected",
    [
        ("NTRP 2.5 - 3.0", "NTRP 3.0", True),
        ("NTRP 1.5 - 2.0", "NTRP 3.0", False),
        ("3.5-4.5", "3.0", False),
        ("3.0+", "4.0", True),
        ("All levels", "3.0", True),
    ],
)
def test_numeric_skill_filter(offered, requested, expected):
    from tennis_overview.discovery import matches_level

    assert matches_level({"level": offered}, requested) is expected


def test_full_weekday_names_are_supported(page, course):
    course["weekdays"] = ["Monday"]
    result = validate_course(course, page, "UTC", date(2026, 10, 25))
    assert len(result["sessions"]) == 3


def test_evidence_line_numbers_ground_schedule(page, course):
    course["evidence_lines"] = [5]
    course["schedule_text"] = "Model paraphrased this incorrectly"
    assert len(validate_course(course, page, "UTC", date(2026, 10, 25))["sessions"]) == 3
    course["evidence_lines"] = [9000]
    assert validate_course(course, page, "UTC", date(2026, 10, 25))["sessions"] == []


def test_numeric_prefix_is_not_source_evidence(page, course):
    course["price"] = "$3"
    result = validate_course(course, page, "UTC", date(2026, 10, 25))
    assert result["price"] == ""


def test_holiday_exclusions_do_not_become_guessed_sessions(page, course):
    course["schedule_text"] += "; skip November 2"
    page = replace(page, text=page.text.rstrip() + "; skip November 2")
    assert validate_course(course, page, "UTC", date(2026, 10, 25))["sessions"] == []


def test_navigation_stays_in_source_section_and_one_hop(monkeypatch):
    root = "https://operator.example.org/example-city/"
    adult = root + "adult-lessons/"
    other_city = "https://operator.example.org/other-city/adult-lessons/"
    recursive = adult + "more-classes/"
    independent = "https://another.example.org/adults"
    pages = {
        root: Page(root, "City", "Public page", (other_city, adult)),
        adult: Page(adult, "Lessons", "Public page", (recursive,)),
        independent: Page(independent, "Another club", "Public page", ()),
    }
    visited = []

    def fetch(url):
        visited.append(url)
        return pages[url]

    monkeypatch.setattr("tennis_overview.discovery.fetch_page", fetch)
    monkeypatch.setattr("tennis_overview.discovery.extract", lambda *a: [])
    monkeypatch.setattr(
        "tennis_overview.discovery.search_courses",
        lambda *a: [{"url": root, "title": "City"}, {"url": independent, "title": "Another"}],
    )
    result = discover(
        AppConfig(), {"query": "Example City", "timezone": "UTC", "level": "", "sources": []}
    )
    assert visited == [root, adult, independent]
    assert not result["partial"]


def test_price_requires_evidence_for_this_course(page, course):
    page = replace(page, text=page.text + "Another session costs $120.\n")
    course["price"] = "$120"
    assert validate_course(course, page, "UTC", date(2026, 10, 25))["price"] == ""
    course["price"] = "$30"
    course["evidence_lines"] = [4, 5]
    assert validate_course(course, page, "UTC", date(2026, 10, 25))["price"] == "$30"
