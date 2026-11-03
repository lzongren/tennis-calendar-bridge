"""Narrow exceptions for browser-generated anonymous catalog queries, never actions.

The agent cannot call these endpoints or supply these bodies. The rendered site's
read-only UI generates them. Unknown POST endpoints and fields remain denied.
"""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .browser_protocol import AUTH_HOST, read_url

Filter = Annotated[str, Field(max_length=200)]
Number = Annotated[int, Field(ge=0, le=1_000_000)]
Price = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]
PUBLIC_PORTAL_HOST = "backend.courtreserve.com"


class EventListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: Filter = Field(alias="FilterText")
    hide_unavailable: bool = Field(alias="HideIneligibleAndFullEvents")
    event_types: Filter = Field(alias="EventTypeIdsString")
    tags: Filter = Field(alias="EventTagIdsString")
    sessions: Filter = Field(alias="SessionIdsString")
    instructors: Filter = Field(alias="InstructorIdsString")
    registration_type: Number | None = Field(alias="EventRegistrationTypeId")
    time_of_day: Filter = Field(alias="TimeOfDayString")
    weekdays: Filter = Field(alias="DayOfWeeksString")
    dates: Filter = Field(alias="DatesString")
    start_date: Filter | None = Field(alias="CustomDate_StartString")
    end_date: Filter | None = Field(alias="CustomDate_EndString")
    start_time: Filter | None = Field(alias="FilterTimeOfADayStartString_React")
    end_time: Filter | None = Field(alias="FilterTimeOfADayEndString_React")
    min_price: Price | None = Field(alias="MinPrice")
    max_price: Price | None = Field(alias="MaxPrice")
    sort: Number = Field(alias="EventSortBy")
    defaults: bool = Field(alias="ApplyFilterDefaults")


class PortalHomeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def validate_catalog_query(url: str, body: bytes) -> None:
    parsed = urlsplit(read_url(url, document=False))
    locale = parse_qsl(parsed.query, keep_blank_values=True)
    if len(body) > 4096:
        raise ValueError("Catalog query exceeds body budget")
    if parsed.hostname == PUBLIC_PORTAL_HOST and parsed.path == "/api/dashboard/portal-home":
        params = dict(locale)
        if (
            len(locale) != 2
            or set(params) != {"id", "RequestData"}
            or not re.fullmatch(r"[0-9]{1,10}", params["id"])
            or not 0 < len(params["RequestData"]) <= 512
            or not all(32 <= ord(char) < 127 for char in params["RequestData"])
        ):
            raise ValueError("Unsupported anonymous portal query")
        PortalHomeQuery.model_validate_json(body or b"{}")
        return
    if (
        parsed.hostname != AUTH_HOST
        or len(locale) > 1
        or any(
            k != "uiCulture" or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", v)
            for k, v in locale
        )
    ):
        raise ValueError("Not an approved anonymous catalog query")
    if re.fullmatch(r"/Online/EventsApi/ApiLoadEvents/\d+", parsed.path):
        EventListQuery.model_validate_json(body)
    else:
        raise ValueError("Not an approved anonymous catalog query")
