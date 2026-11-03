from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from tennis_overview.browser_query_policy import validate_catalog_query
from tennis_overview.browser_runtime import ReadNetwork
from tennis_overview.browser_network import Resource

URL = "https://app.courtreserve.com/Online/EventsApi/ApiLoadEvents/12345"
HOME = "https://backend.courtreserve.com/api/dashboard/portal-home?id=12345&RequestData=synthetic-public-context"
QUERY = {
    "FilterText": "",
    "HideIneligibleAndFullEvents": False,
    "EventTypeIdsString": "",
    "EventTagIdsString": "",
    "SessionIdsString": "",
    "InstructorIdsString": "",
    "EventRegistrationTypeId": None,
    "TimeOfDayString": "",
    "DayOfWeeksString": "",
    "DatesString": "",
    "CustomDate_StartString": None,
    "CustomDate_EndString": None,
    "FilterTimeOfADayStartString_React": None,
    "FilterTimeOfADayEndString_React": None,
    "MinPrice": None,
    "MaxPrice": None,
    "EventSortBy": 0,
    "ApplyFilterDefaults": True,
}


def test_only_observed_anonymous_catalog_query_shapes_are_allowed():
    validate_catalog_query(URL, json.dumps(QUERY).encode())
    validate_catalog_query(URL + "?uiCulture=en-US", json.dumps(QUERY).encode())
    validate_catalog_query(HOME, b"{}")
    validate_catalog_query(HOME, b"")


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/Online/EventsApi/ApiLoadEvents/12345",
        "https://app.courtreserve.com/Online/EventsApi/ApiRegister/12345",
        "https://app.courtreserve.com/Online/EventsApi/ApiJoinWaitlist/12345",
        "https://app.courtreserve.com/Online/EventsApi/ApiCancel/12345",
        "https://app.courtreserve.com/Online/Payment/12345",
        URL + "?action=register",
        URL + "?uiCulture=en-US&uiCulture=en-US",
        URL + "?uiCulture=",
        URL + "/anything",
    ],
)
def test_query_policy_cannot_authorize_other_paths(url):
    with pytest.raises(ValueError):
        validate_catalog_query(url, json.dumps(QUERY).encode())


@pytest.mark.parametrize(
    "change",
    [
        {"action": "register"},
        {"UserId": 123},
        {"FilterText": {"action": "pay"}},
        {"FilterText": "x" * 201},
        {"EventSortBy": True},
        {"EventSortBy": -1},
        {"ApplyFilterDefaults": "true"},
        {"MaxPrice": float("nan")},
    ],
)
def test_query_body_rejects_unknown_fields_wrong_types_and_unbounded_values(change):
    with pytest.raises(ValueError):
        validate_catalog_query(URL, json.dumps(QUERY | change).encode())


@pytest.mark.parametrize("body", [b"x" * 4097, b"[]", b"null", b'{"UserId":123}'])
def test_portal_query_accepts_only_empty_object(body):
    with pytest.raises(ValueError):
        validate_catalog_query(HOME, body)


@pytest.mark.parametrize(
    "url",
    [
        HOME.replace("backend.courtreserve.com", "other.example"),
        HOME.replace("portal-home", "register"),
        HOME + "&UserId=1",
        HOME + "&id=12345",
        HOME.replace("id=12345", "id=invalid"),
        HOME.replace("synthetic-public-context", "x" * 513),
    ],
)
def test_public_portal_query_is_not_a_general_cross_origin_exception(url):
    with pytest.raises(ValueError):
        validate_catalog_query(url, b"{}")


@pytest.mark.parametrize("portal,anonymous", [(True, False), (False, True)])
def test_read_query_exception_never_applies_to_authenticated_or_other_sites(
    monkeypatch, portal, anonymous
):
    fetch = Mock(side_effect=AssertionError("Unexpected outbound POST"))
    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    request = Mock(
        url=URL, method="POST", post_data_buffer=json.dumps(QUERY).encode(), resource_type="fetch"
    )
    request.is_navigation_request.return_value = False
    route = Mock(request=request, abort=AsyncMock())
    asyncio.run(ReadNetwork(portal, anonymous=anonymous).handle(route))
    route.abort.assert_awaited_once()
    fetch.assert_not_called()


def test_anonymous_query_reaches_relay_with_exact_validated_body(monkeypatch):
    body = json.dumps(QUERY).encode()
    fetch = Mock(return_value=Resource(200, {"content-type": "application/json"}, b"{}"))
    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    request = Mock(url=URL, method="POST", post_data_buffer=body, resource_type="fetch")
    request.is_navigation_request.return_value = False
    request.all_headers = AsyncMock(return_value={"content-type": "application/json"})
    route = Mock(request=request, abort=AsyncMock(), fulfill=AsyncMock())
    asyncio.run(ReadNetwork(True).handle(route))
    route.abort.assert_not_awaited()
    route.fulfill.assert_awaited_once()
    assert fetch.call_args.kwargs["post_body"] == body


def test_member_only_catalog_is_not_a_successful_empty_result(monkeypatch):
    body = json.dumps(QUERY).encode()
    reply = json.dumps(
        {
            "isValid": False,
            "message": "You need to be a member of this organization to access this module!",
        }
    ).encode()
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        Mock(return_value=Resource(200, {"content-type": "application/json"}, reply)),
    )
    request = Mock(url=URL, method="POST", post_data_buffer=body, resource_type="fetch")
    request.is_navigation_request.return_value = False
    request.all_headers = AsyncMock(return_value={"content-type": "application/json"})
    route = Mock(request=request, abort=AsyncMock(), fulfill=AsyncMock())
    network = ReadNetwork(True)
    asyncio.run(network.handle(route))
    assert network.membership_required and not network.login_required
