from __future__ import annotations

import copy
import hashlib
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from ..models import TennisEvent
from ..timeutils import utc_now
from .base import ScraperError
from .browser import BrowserPortalScraper


# The member portal's ApplicationsQuery, restricted to calendar fields. Filters
# come from the authenticated /my request so membership/cancellation rules stay intact.
_SCHEDULE_QUERY = """
query ApplicationsQuery($individualFilter: implementation_document_bool_exp,
                        $groupFilter: implementation_document_bool_exp) {
  individual: implementation_document(where: $individualFilter) {
    id
    implementations(where: {cancel_id: {_is_null: true}}) {
      datetime duration place court_id
      client_datum { client { id } }
    }
    coaches { coach { employee { full_name } } }
    client_remote_accesses { client_id pin }
  }
  group: implementation_document(where: $groupFilter) {
    id
    implementations(where: {cancel_id: {_is_null: true}}) {
      datetime duration place court_id
      client_datum { client { id } }
    }
    group { name group_view { name } }
    groupMainCoach { employee { full_name } }
    client_remote_accesses { client_id pin }
    schedule_slot_reserves(where: {canceled: {_eq: false}}) {
      canceled
      schedule_slots {
        datetime_start datetime_end
        court { name }
      }
    }
  }
}
"""


class AccuSportViewScraper(BrowserPortalScraper):
    def fetch(self) -> list[TennisEvent]:
        username = os.getenv(self.club.username_env, "").strip()
        password = os.getenv(self.club.password_env, "")
        if not username or not password:
            raise ScraperError(
                f"Missing credentials: set {self.club.username_env} and {self.club.password_env}."
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScraperError(
                "Install Playwright and its Chromium browser before syncing."
            ) from exc

        debug_dir = self.data_dir / "debug" / self.club.id / utc_now().strftime("%Y%m%dT%H%M%SZ")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(timezone_id=self.club.timezone, locale="en-US")
            page = context.new_page()
            try:
                page.goto(
                    self.club.login_url or self.club.base_url,
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
                self._login(page, username, password)
                return self._fetch_member_schedule(page)
            except Exception as exc:
                debug_dir.mkdir(parents=True, exist_ok=True)
                artifact = self._save_artifacts(page, debug_dir, "schedule-error")
                # Provider errors can contain request headers or personal payloads.
                message = str(exc) if isinstance(exc, ScraperError) else type(exc).__name__
                raise ScraperError(
                    f"AccuSportView schedule sync failed: {message}", str(artifact)
                ) from exc
            finally:
                context.close()
                browser.close()

    def _fetch_member_schedule(self, page: Any) -> list[TennisEvent]:
        source_url = urljoin(self.club.base_url, "/my")
        with page.expect_response(_is_schedule_response, timeout=45_000) as pending:
            page.goto(source_url, wait_until="domcontentloaded", timeout=45_000)
        observed = pending.value
        if not observed.ok:
            raise ScraperError("The member reservations request failed.")

        lower, upper = self._window()
        request = observed.request
        variables, client_id = _schedule_variables(request.post_data_json, lower, upper)
        # The portal requires its browser session; APIRequestContext replays can
        # receive an anonymous GraphQL schema even with the same request headers.
        headers = {
            key: value
            for key, value in request.all_headers().items()
            if key.startswith("x-") or key == "authorization"
        }
        response = page.evaluate(
            """async ({url, body, headers}) => {
                const result = await fetch(url, {
                    method: 'POST', credentials: 'include',
                    headers: {...headers, 'content-type': 'application/json'},
                    body: JSON.stringify(body), signal: AbortSignal.timeout(45000)
                });
                return {ok: result.ok, payload: await result.json()};
            }""",
            {
                "url": observed.url,
                "body": {"query": _SCHEDULE_QUERY, "variables": variables},
                "headers": headers,
            },
        )
        if not response["ok"]:
            raise ScraperError("Could not load the complete member reservations window.")
        return self._events_from_schedule(response["payload"], client_id, source_url, lower, upper)

    def _window(self) -> tuple[datetime, datetime]:
        today = (
            utc_now()
            .astimezone(ZoneInfo(self.club.timezone))
            .replace(hour=0, minute=0, second=0, microsecond=0)
        )
        return today - timedelta(days=1), today + timedelta(days=self.lookahead_days + 1)

    def _events_from_schedule(
        self,
        payload: Any,
        client_id: str,
        source_url: str,
        lower: datetime,
        upper: datetime,
    ) -> list[TennisEvent]:
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ScraperError("The member reservations response contains API errors.")
        data = payload.get("data")
        if not isinstance(data, dict) or any(
            not isinstance(data.get(kind), list) for kind in ("individual", "group")
        ):
            raise ScraperError("The member reservations response format has changed.")

        events: dict[str, TennisEvent] = {}
        try:
            for kind in ("individual", "group"):
                for record in data[kind]:
                    record_id = record["id"]
                    if not isinstance(record_id, str) or not record_id:
                        raise ValueError("missing booking ID")
                    implementations = record["implementations"]
                    if not isinstance(implementations, list) or not implementations:
                        raise ValueError("missing booking sessions")
                    title = "Rent/Private"
                    if kind == "group":
                        group = record["group"]
                        title = (group.get("group_view") or {}).get("name") or group.get("name")
                    if not isinstance(title, str) or not title.strip():
                        raise ValueError("missing course title")
                    raw = _booking_details(record, kind, client_id)
                    for item in implementations:
                        if item["client_datum"]["client"]["id"] != client_id:
                            continue
                        start, end = _session_times(item, self.club.timezone)
                        if not lower <= start < upper:
                            continue
                        # For groups, `place` is often the course description.
                        # Only a court ID establishes that it is a physical court.
                        identity_location = (
                            (item.get("place") or None)
                            if (kind == "individual" or item.get("court_id"))
                            else None
                        )
                        if identity_location is not None and not isinstance(identity_location, str):
                            raise ValueError("invalid court location")
                        location = identity_location
                        if kind == "group" and not item.get("court_id"):
                            location = _scheduled_courts(record, start, end, self.club.timezone)
                        # A booking can have multiple accounting rows for one session.
                        # Preserve the pre-enrichment identity so court updates do
                        # not cancel/recreate events in existing calendar subscriptions.
                        occurrence = f"{start.isoformat()}|{identity_location or ''}"
                        suffix = hashlib.sha256(occurrence.encode()).hexdigest()[:16]
                        external_id = f"accusportview-{kind}-{record_id}-{suffix}"
                        event = TennisEvent(
                            club_id=self.club.id,
                            external_id=external_id,
                            title=title.strip(),
                            starts_at=start,
                            ends_at=end,
                            timezone=self.club.timezone,
                            location=location,
                            category="class" if kind == "group" else "reservation",
                            source_url=source_url,
                            raw=raw,
                        )
                        if external_id in events and events[external_id] != event:
                            raise ValueError("conflicting booking sessions")
                        events[external_id] = event
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise ScraperError(
                "A member reservation has incomplete or inconsistent calendar fields."
            ) from exc
        return sorted(events.values(), key=lambda event: (event.starts_at, event.title))


def _is_schedule_response(response: Any) -> bool:
    if urlsplit(response.url).path.rstrip("/") != "/api/v1/graphql":
        return False
    try:
        request = response.request.post_data_json
        return (
            isinstance(request, dict)
            and "query ApplicationsQuery(" in request.get("query", "")
            and isinstance(request.get("variables", {}).get("individualFilter"), dict)
            and isinstance(request.get("variables", {}).get("groupFilter"), dict)
        )
    except (ValueError, TypeError, AttributeError):
        return False


def _schedule_variables(
    request: dict[str, Any],
    lower: datetime,
    upper: datetime,
) -> tuple[dict[str, Any], str]:
    try:
        variables = copy.deepcopy(request["variables"])
        individual = variables["individualFilter"]
        group = variables["groupFilter"]
        client_id = individual["client_id"]["_eq"]
        if not isinstance(client_id, str) or not client_id:
            raise ValueError("missing member filter")
        if group["implementations"]["client_id"]["_eq"] != client_id:
            raise ValueError("mismatched member filters")
        for kind, service in ((individual, "rent"), (group, "group")):
            if (
                kind["is_archive"]["_eq"] is not False
                or kind["schedule_slot_reserves"]["canceled"]["_eq"] is not False
                or kind["implementations"]["cancel_id"]["_is_null"] is not True
                or kind["implementations"]["service"]["_eq"] != service
            ):
                raise ValueError("missing active reservation filters")
            kind["implementations"]["datetime"] = {
                "_gte": lower.strftime("%Y-%m-%d %H:%M:%S"),
                "_lt": upper.strftime("%Y-%m-%d %H:%M:%S"),
            }
        if group["implementations"]["operation"]["_eq"] != "sell":
            raise ValueError("missing enrollment filter")
        # Drop UI toggles; both booking families must always be requested.
        return {"individualFilter": individual, "groupFilter": group}, client_id
    except (KeyError, TypeError, ValueError) as exc:
        raise ScraperError("Could not verify the portal's member reservation filters.") from exc


def _session_times(item: dict[str, Any], timezone_name: str) -> tuple[datetime, datetime]:
    timestamp = item["datetime"]
    duration = item["duration"]
    if (
        not isinstance(timestamp, str)
        or len(timestamp) <= 10
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError("invalid session time")
    start = _local_datetime(timestamp, timezone_name)
    end = (start.astimezone(timezone.utc) + timedelta(minutes=duration)).astimezone(start.tzinfo)
    return start, end


def _local_datetime(value: Any, timezone_name: str) -> datetime:
    if not isinstance(value, str) or len(value) <= 10:
        raise ValueError("invalid calendar timestamp")
    zone = ZoneInfo(timezone_name)
    parsed = datetime.fromisoformat(value)
    # AccuSportView uses local wall times without an offset, unlike generic APIs.
    return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)


def _scheduled_courts(
    record: dict[str, Any], start: datetime, end: datetime, timezone_name: str
) -> str | None:
    reserves = record["schedule_slot_reserves"]
    if not isinstance(reserves, list):
        raise ValueError("invalid court reservations")
    courts: set[str] = set()
    for reserve in reserves:
        if reserve["canceled"] is True:
            continue
        if reserve["canceled"] is not False or not isinstance(reserve["schedule_slots"], list):
            raise ValueError("invalid court reservation")
        for slot in reserve["schedule_slots"]:
            slot_start = _local_datetime(slot["datetime_start"], timezone_name)
            slot_end = _local_datetime(slot["datetime_end"], timezone_name)
            if slot_end <= slot_start:
                raise ValueError("invalid court slot interval")
            # A session comprises short slots. Only overlapping slots from this
            # booking belong to it, never an adjacent or another day's session.
            if slot_start < end and slot_end > start:
                name = slot["court"]["name"]
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("invalid court name")
                courts.add(name.strip())
    return ", ".join(sorted(courts)) or None


def _booking_details(record: dict[str, Any], kind: str, client_id: str) -> dict[str, str]:
    coaches = (
        [record.get("groupMainCoach")]
        if kind == "group"
        else [item.get("coach") for item in record.get("coaches", [])]
    )
    names = [
        coach["employee"]["full_name"].strip()
        for coach in coaches
        if coach and coach.get("employee") and coach["employee"].get("full_name")
    ]
    raw = {}
    if names:
        raw["instructor"] = ", ".join(dict.fromkeys(names))
    pins = [
        str(access["pin"]).strip()
        for access in record.get("client_remote_accesses", [])
        if access.get("client_id") == client_id and access.get("pin")
    ]
    if pins:
        raw["access_code"] = ", ".join(dict.fromkeys(pins))
    return raw
