from __future__ import annotations

from pathlib import Path

from ..models import ClubConfig
from .accusportview import AccuSportViewScraper
from .base import BaseScraper, ScraperError
from .clubautomation import ClubAutomationScraper
from .courtreserve import CourtReserveScraper


SCRAPERS: dict[str, type[BaseScraper]] = {
    "accusportview": AccuSportViewScraper,
    "clubautomation": ClubAutomationScraper,
    "courtreserve": CourtReserveScraper,
}


def make_scraper(club: ClubConfig, data_dir: Path, headless: bool = True) -> BaseScraper:
    scraper_class = SCRAPERS.get(club.provider)
    if scraper_class is None:
        supported = ", ".join(sorted(SCRAPERS))
        raise ScraperError(f"Unsupported provider '{club.provider}'. Supported: {supported}.")
    return scraper_class(club, data_dir, headless=headless)
