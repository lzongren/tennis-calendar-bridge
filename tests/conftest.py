from __future__ import annotations

from pathlib import Path

import pytest

from tennis_overview.models import ClubConfig
from tennis_overview.scrapers.accusportview import AccuSportViewScraper
from tennis_overview.scrapers.clubautomation import ClubAutomationScraper


@pytest.fixture
def clubautomation_scraper(tmp_path: Path) -> ClubAutomationScraper:
    return ClubAutomationScraper(
        ClubConfig(
            id="example-clubautomation",
            name="Example Club Automation Club",
            provider="clubautomation",
            base_url="https://clubautomation.example.com/",
            username_env="EXAMPLE_CLUBAUTOMATION_USERNAME",
            password_env="EXAMPLE_CLUBAUTOMATION_PASSWORD",
            timezone="America/Los_Angeles",
        ),
        tmp_path,
    )


@pytest.fixture
def accusportview_scraper(tmp_path: Path) -> AccuSportViewScraper:
    return AccuSportViewScraper(
        ClubConfig(
            id="example-accusportview",
            name="Example AccuSportView Club",
            provider="accusportview",
            base_url="https://example.my.accusportview.com/",
            username_env="EXAMPLE_ACCUSPORTVIEW_USERNAME",
            password_env="EXAMPLE_ACCUSPORTVIEW_PASSWORD",
            timezone="America/Los_Angeles",
        ),
        tmp_path,
    )
