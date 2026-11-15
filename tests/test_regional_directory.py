from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
from bs4 import BeautifulSoup

from tennis_overview.discovery_agent import SourceUnavailable
from tennis_overview.public_documents import search_public_courses
from tennis_overview.regional_directory import (
    ORIGIN,
    dated_cards,
    facilities,
    next_listing,
    regional_courses,
)

DAY = date(2030, 6, 11)
VENUE = "Example Racquet Club"


def card(
    key: int, *, title: str = "Adult clinic", day: str = "2030-06-11", club: str = VENUE
) -> str:
    return (
        f'<article><h3><a href="/players/events/e-{key}">{title}</a></h3>'
        f'<p><time datetime="{day}T18:00:00">Evening</time></p>'
        f"<p>{club} · Example City · $40</p></article>"
    )


def facility(slug: str, label: str, state: str = "california") -> str:
    return f'<article><h3><a href="/players/courts/us/{state}/{slug}/club">{VENUE}</a></h3><p>{label}, CA</p></article>'


def test_admit_only_card_owned_town_club_date_and_compatible_rating() -> None:
    markup = "".join(
        (
            card(10, title="Adult clinic 3.0"),
            card(11, title="Adult clinic 4.5"),
            card(12, day="2030-06-12"),
            card(13, club="Other Sports Club"),
            card(14, title="Junior clinic"),
            card(15, title="Golf clinic"),
            card(16).replace("/players/events/e-16", "https://example.org/players/events/e-16"),
        )
    ).encode()
    assert dated_cards(
        BeautifulSoup(markup, "html.parser"), DAY, "NTRP 2.5-3.5", {VENUE.casefold()}
    ) == [(0, "Adult clinic 3.0", ORIGIN + "/players/events/e-10", VENUE)]


def test_directory_expansion_uses_city_distance_and_observed_slugs() -> None:
    region = ORIGIN + "/players/tennis-courts/us/california"
    near = ORIGIN + "/players/events/us/california/santa-clara"
    far = ORIGIN + "/players/events/us/california/fresno"
    requests: list[str] = []

    def read(url: str) -> bytes:
        requests.append(url)
        if url == region:
            return (facility("santa-clara", "Santa Clara") + facility("fresno", "Fresno")).encode()
        if url == near:
            return b'<a rel="next" href="?page=2">Next</a>'
        assert url == near + "?page=2"
        return card(38).encode()

    result = regional_courses("San Jose, CA", "3.0", DAY, read=read)
    assert requests == [region, near, near + "?page=2"]
    assert far not in requests
    assert result == [
        {
            "url": ORIGIN + "/players/events/e-38",
            "title": "Adult clinic",
            "club": VENUE,
            "listing": near + "?page=2",
        }
    ]


@pytest.mark.parametrize(
    "heading, last, expected",
    [
        ("48+ upcoming programs", "2030-06-11", True),
        ("48+ upcoming programs", "2030-06-12", False),
        ("1 upcoming program", "2030-06-11", False),
    ],
)
def test_only_publisher_capped_listing_ending_by_requested_day_triggers_lookup(
    heading, last, expected
):
    cutoff = []

    def read(url):
        if "/tennis-courts/" in url:
            return facility("santa-clara", "Santa Clara").encode()
        return (f"<h1>{heading}</h1>" + card(10, day=last)).encode()

    regional_courses("San Jose, CA", "3.0", DAY, read=read, cutoffs=cutoff)
    assert bool(cutoff) is expected
    if expected:
        assert cutoff[0]["venue"] == VENUE.casefold()


@pytest.mark.parametrize(
    "href", ["https://example.org/?page=2", "?action=book", "/players/events/e-66", ""]
)
def test_only_explicit_same_listing_pagination(href: str) -> None:
    soup = BeautifulSoup(f'<a rel="next" href="{href}">Next</a>', "html.parser")
    assert next_listing(soup, ORIGIN + "/players/events/us/california/example") is None


def test_foreign_facility_links_and_unsupported_regions_do_not_generate_urls() -> None:
    html = facility("fresno", "Fresno").replace('href="/', 'href="https://example.org/')
    assert facilities(html.encode(), "california") == []
    assert regional_courses("Unknown City, XX", "", DAY, read=lambda _: pytest.fail()) == []


@pytest.mark.parametrize("missing", [b"[]", SourceUnavailable("No directory")])
def test_missing_directory_falls_back_to_the_normal_search(monkeypatch, missing) -> None:
    worker = AsyncMock(
        side_effect=[missing, b'[{"url":"https://example.org/clinic","title":"Group clinic"}]']
    )
    monkeypatch.setattr("tennis_overview.public_documents.run_public_worker", worker)
    assert (
        asyncio.run(
            search_public_courses("Example City", "3.0", DAY.year, "registration", on_date=DAY)
        )[0]["title"]
        == "Group clinic"
    )
    assert [call.args[0] for call in worker.await_args_list] == ["directory", "search"]
