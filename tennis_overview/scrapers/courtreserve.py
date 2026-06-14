from __future__ import annotations

from urllib.parse import urljoin

from .browser import BrowserPortalScraper


class CourtReserveScraper(BrowserPortalScraper):
    candidate_clicks = BrowserPortalScraper.candidate_clicks + (
        "Reservations",
        "My Reservations",
        "Events",
        "Programs",
        "My Events",
    )

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
