from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock

import pytest

from tennis_overview.browser_client import BrowserClient
from tennis_overview.discovery_agent import SourceUnavailable
from tennis_overview.player_event import PlayerEvent
from tennis_overview.public_web import Page, PageLink
from tennis_overview.venue_schedules import (
    matches_row,
    venue_details,
    venue_query,
    venue_site,
    weekly_rows,
)

DAY = date(2030, 6, 11)
CLUB = "Example Racquet Club"
SITE = "https://www.exampleracquetclub.org/adult"
PORTAL = "https://app.courtreserve.com/Online/Events/Details/123/XYZ"
EVENT = "https://courtreserve.com/players/events/e-123"
NAME = "Evening Doubles"
LINK = PageLink(PORTAL, NAME)
SCHEDULE = Page(
    SITE,
    CLUB + " | Adult Tennis",
    "Tuesday\n6:30-8:00 PM\nEvening Doubles\nWednesday\n7:30-9:00 PM\nEvening Doubles",
    (PORTAL,),
    link_details=(LINK,),
)
DETAIL = Page(
    EVENT,
    NAME,
    "Adult tennis class",
    (),
    player_event=PlayerEvent(
        NAME, "2030-06-11T18:30:00-07:00", "2030-06-11T20:00:00-07:00", "$25", 2, "NTRP 3.0"
    ),
    link_details=(
        PageLink("https://courtreserve.com/players/courts/us/california/example/club", CLUB),
    ),
)


def test_weekday_requires_adjacent_time_title_and_published_link():
    assert weekly_rows(SCHEDULE, DAY) == [(NAME, "18:30", "20:00")]
    assert not weekly_rows(replace(SCHEDULE, link_details=()), DAY)
    assert not weekly_rows(
        replace(SCHEDULE, text="Tuesday\nOther program\n6:30-8:00 PM\nEvening Doubles"), DAY
    )


@pytest.mark.parametrize(
    "club,day,title,start,end",
    [
        ("Another club", DAY, NAME, "18:30", "20:00"),
        (CLUB, date(2030, 6, 18), NAME, "18:30", "20:00"),
        (CLUB, DAY, NAME, "18:30", "21:00"),
        (CLUB, DAY, "Other class", "18:30", "20:00"),
    ],
)
def test_marketplace_occurrence_must_match_the_operator_row(club, day, title, start, end):
    assert not matches_row(DETAIL, club, day, title, start, end)


def test_truncated_venue_to_rendered_schedule_to_verified_occurrence():
    index = AsyncMock(
        side_effect=[
            [{"href": "https://directory.example.org/adult"}, {"href": SITE}],
            [{"href": EVENT}],
        ]
    )
    read = AsyncMock(side_effect=[replace(SCHEDULE, text="Welcome"), DETAIL])
    render = AsyncMock(return_value=SCHEDULE)
    stages = []
    rows = asyncio.run(
        venue_details(
            {"listing": "https://example.org/listing", "venue": CLUB, "town": "Example City"},
            DAY,
            index=index,
            read=read,
            render=render,
            trace=lambda s, u: stages.append(s),
        )
    )
    assert rows == [
        {
            "club": CLUB,
            "url": EVENT,
            "title": NAME,
            "listing": SITE,
            "slot_start": "18:30",
            "slot_end": "20:00",
        }
    ]
    assert [c.args[0] for c in read.await_args_list] == [SITE, EVENT]
    render.assert_awaited_once_with(SITE)
    assert "venue_detail_match" in stages
    assert index.await_args_list[-1].args == (CLUB, "Example City", DAY, NAME)


def test_rank_all_results_before_read_cap_and_deduplicate_aliases():
    wrong = EVENT + "0"
    indexed = [
        {"href": wrong, "title": NAME},
        {"href": wrong.replace("://court", "://player.court"), "title": NAME},
    ]
    indexed.extend({"href": EVENT + str(i), "title": "Other"} for i in range(1, 8))
    indexed.append(
        {"href": EVENT, "title": "Evening doubles", "snippet": CLUB + " in Example City"}
    )
    read = AsyncMock(side_effect=[SCHEDULE, DETAIL])
    stages = []
    result = asyncio.run(
        venue_details(
            {"listing": "https://example.org/list", "venue": CLUB, "town": "Example City"},
            DAY,
            index=AsyncMock(side_effect=[[{"href": SITE}], indexed]),
            read=read,
            render=None,
            trace=lambda s, u: stages.append((s, u)),
        )
    )
    assert result[0]["url"] == EVENT
    assert [call.args[0] for call in read.await_args_list] == [SITE, EVENT]
    assert len([url for stage, url in stages if stage == "venue_detail_selected"]) == 4
    assert ("venue_detail_rank_cut", EVENT + "7") in stages
    assert all(
        not url.startswith("https://player.")
        for stage, url in stages
        if stage == "venue_detail_selected"
    )


def test_first_party_and_public_query_bounds():
    assert venue_site(SITE, CLUB)
    assert not venue_site("https://exampleracquetclub.org.fake.com/adult", CLUB)
    assert not venue_site("https://www.exampleracquetclub.org/account/login", CLUB)
    with pytest.raises(ValueError):
        venue_query(CLUB, "Place", DAY.isoformat(), "site:example.com")
    with pytest.raises(SourceUnavailable):
        asyncio.run(BrowserClient.render_venue(None, PORTAL))
