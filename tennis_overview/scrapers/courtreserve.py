from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from ..models import TennisEvent
from .base import ScraperError
from .browser import BrowserPortalScraper
from .calendar_feed import CalendarFeedScraper


class CourtReserveScraper(BrowserPortalScraper):
    candidate_clicks = BrowserPortalScraper.candidate_clicks + (
        "Reservations",
        "My Reservations",
        "Events",
        "Programs",
        "My Events",
    )

    def fetch(self) -> list[TennisEvent]:
        if "calendar_feed_env" in self.club.options:
            return CalendarFeedScraper(
                self.club, self.data_dir, self.headless, self.lookahead_days
            ).fetch()
        return super().fetch()

    def _check_login_page(self, page: Any, response: Any) -> None:
        title = page.title().casefold()
        headers = response.headers if response is not None else {}
        blocked = "cloudflare" in title and any(
            marker in title for marker in ("attention required", "access denied")
        )
        if blocked or headers.get("cf-mitigated", "").casefold() == "challenge":
            status = f" (HTTP {response.status})" if response is not None else ""
            raise ScraperError(
                f"CourtReserve blocked this server at its Cloudflare access check{status}. "
                "The login form was not reached; credentials were not submitted. "
                "Ask the club or CourtReserve support to allow server access. "
                "CourtReserve's official Calendar Feed is an alternative for calendar subscriptions."
            )
        super()._check_login_page(page, response)

    def _candidate_urls(self) -> list[str]:
        portal_id = self.club.options.get("portal_id")
        if portal_id:
            paths = [
                f"/Online/Portal/Index/{portal_id}",
                f"/Online/Reservations/Index/{portal_id}",
                f"/Online/Events/List/{portal_id}",
                f"/Online/Classes/Index/{portal_id}",
                f"/Online/Account/MyProfile/{portal_id}",
            ]
            return [urljoin(self.club.base_url, path) for path in paths]
        return super()._candidate_urls()
