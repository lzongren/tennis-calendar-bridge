from __future__ import annotations

from pathlib import Path

from tennis_overview.models import ClubConfig
from tennis_overview.scrapers.browser import BrowserPortalScraper


class FakePage:
    def __init__(self) -> None:
        self.waited_selectors: list[tuple[str, str, int]] = []

    def wait_for_selector(self, selector: str, state: str, timeout: int) -> None:
        self.waited_selectors.append((selector, state, timeout))


def test_login_waits_for_react_rendered_courtreserve_fields(tmp_path: Path) -> None:
    scraper = BrowserPortalScraper(
        ClubConfig(
            id="example-courtreserve",
            name="Example CourtReserve Club",
            provider="courtreserve",
            base_url="https://app.courtreserve.com/Online/Portal/Index/0000",
            username_env="EXAMPLE_USERNAME",
            password_env="EXAMPLE_PASSWORD",
        ),
        tmp_path,
    )
    page = FakePage()

    scraper._wait_for_login_form(page)

    selectors = [selector for selector, _, _ in page.waited_selectors]
    assert len(selectors) == 2
    assert "input[name='email']" in selectors[0]
    assert "input[name='password']" in selectors[1]
    assert all(state == "visible" for _, state, _ in page.waited_selectors)
