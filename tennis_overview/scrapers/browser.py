from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from ..calendar import parse_ics_events
from ..models import ClubConfig, TennisEvent
from ..timeutils import ensure_aware, utc_now
from .base import BaseScraper, ScraperError


TITLE_KEYS = (
    "title",
    "name",
    "eventName",
    "reservationName",
    "bookingName",
    "className",
    "programName",
    "description",
    "subject",
)
START_KEYS = (
    "start",
    "startsAt",
    "startDate",
    "startDateTime",
    "startTime",
    "begin",
    "from",
    "reservationStart",
    "bookingStart",
    "eventStart",
)
END_KEYS = (
    "end",
    "endsAt",
    "endDate",
    "endDateTime",
    "endTime",
    "to",
    "reservationEnd",
    "bookingEnd",
    "eventEnd",
)
LOCATION_KEYS = ("location", "court", "resource", "room", "site")


class BrowserPortalScraper(BaseScraper):
    candidate_paths: tuple[str, ...] = (
        "/",
        "/account",
        "/calendar",
        "/schedule",
        "/schedules",
        "/reservations",
        "/bookings",
        "/classes",
        "/programs",
        "/events",
    )
    candidate_clicks: tuple[str, ...] = (
        "My Schedule",
        "Schedule",
        "Calendar",
        "Reservations",
        "Bookings",
        "My Bookings",
        "My Reservations",
        "Classes",
        "Programs",
        "Events",
        "Account",
    )

    def fetch(self) -> list[TennisEvent]:
        username = os.getenv(self.club.username_env, "").strip()
        password = os.getenv(self.club.password_env, "")
        if not username or not password:
            raise ScraperError(
                f"Missing credentials: set {self.club.username_env} and {self.club.password_env}."
            )

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScraperError(
                "Playwright is not installed. Run `pip install -e .` and "
                "`python -m playwright install chromium`."
            ) from exc

        debug_dir = self.data_dir / "debug" / self.club.id / utc_now().strftime("%Y%m%dT%H%M%SZ")
        debug_dir.mkdir(parents=True, exist_ok=True)
        collected: list[TennisEvent] = []
        json_payloads: list[tuple[str, Any]] = []

        def remember_response(response: Any) -> None:
            try:
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type.lower() and "/api/" not in response.url.lower():
                    return
                body = response.text()
                if len(body) > 2_000_000:
                    return
                payload = json.loads(body)
                json_payloads.append((response.url, payload))
            except Exception:
                return

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(
                timezone_id=self.club.timezone,
                locale="en-US",
                viewport={"width": 1440, "height": 1100},
            )
            page = context.new_page()
            page.on("response", remember_response)
            try:
                login_url = self.club.login_url or self.club.base_url
                page.goto(login_url, wait_until="domcontentloaded", timeout=45_000)
                self._settle(page)
                self._login(page, username, password)
                self._settle(page)

                collected.extend(self._collect_from_page(page, page.url))
                collected.extend(self._collect_from_json(json_payloads))

                for url in self._candidate_urls():
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                        self._settle(page)
                        collected.extend(self._collect_from_page(page, page.url))
                        collected.extend(self._collect_from_json(json_payloads))
                    except PlaywrightTimeoutError:
                        continue
                    except Exception:
                        continue

                for text in self.candidate_clicks:
                    try:
                        locator = page.get_by_text(re.compile(rf"^{re.escape(text)}$", re.I)).first
                        if locator.count() == 0:
                            continue
                        locator.click(timeout=5_000)
                        self._settle(page)
                        collected.extend(self._collect_from_page(page, page.url))
                        collected.extend(self._collect_from_json(json_payloads))
                    except Exception:
                        continue

                events = _dedupe_events(collected)
                if not events:
                    artifact = self._save_artifacts(page, debug_dir, "no-events")
                    raise ScraperError(
                        "Logged in, but no schedule events were detected. "
                        f"Debug artifact saved at {artifact}.",
                        str(artifact),
                    )
                return events
            except ScraperError:
                raise
            except Exception as exc:
                artifact = self._save_artifacts(page, debug_dir, "error")
                raise ScraperError(f"{type(exc).__name__}: {exc}", str(artifact)) from exc
            finally:
                context.close()
                browser.close()

    def _login(self, page: Any, username: str, password: str) -> None:
        self._fill_username(page, username)
        self._fill_password(page, password)
        clicked = self._click_login(page)
        if clicked:
            self._settle(page)

    def _fill_username(self, page: Any, username: str) -> None:
        for pattern in ("email", "e-mail", "username", "user name", "login"):
            try:
                field = page.get_by_label(re.compile(pattern, re.I)).first
                if field.count() > 0:
                    field.fill(username, timeout=5_000)
                    return
            except Exception:
                pass
        selectors = [
            "input[type='email']",
            "input[name='username']",
            "input[name='UserName']",
            "input[name='Email']",
            "input[name='email']",
            "input[id='Username']",
            "input[id='username']",
            "input[id='Email']",
            "input[id='email']",
            "input[autocomplete='username']",
        ]
        if self._fill_first_visible(page, selectors, username):
            return
        inputs = page.locator("input:not([type='hidden']):not([type='password'])")
        for index in range(inputs.count()):
            item = inputs.nth(index)
            try:
                if item.is_visible():
                    item.fill(username, timeout=5_000)
                    return
            except Exception:
                continue
        raise ScraperError("Could not find the username/email input on the login page.")

    def _fill_password(self, page: Any, password: str) -> None:
        selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[name='Password']",
            "input[id='Password']",
            "input[id='password']",
            "input[autocomplete='current-password']",
        ]
        if self._fill_first_visible(page, selectors, password):
            return
        raise ScraperError("Could not find the password input on the login page.")

    def _fill_first_visible(self, page: Any, selectors: list[str], value: str) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible():
                    locator.fill(value, timeout=5_000)
                    return True
            except Exception:
                continue
        return False

    def _click_login(self, page: Any) -> bool:
        patterns = ("log in", "login", "sign in", "submit")
        for pattern in patterns:
            try:
                button = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if button.count() > 0:
                    button.click(timeout=8_000)
                    return True
            except Exception:
                pass
        selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Log In')",
            "a:has-text('Login')",
            "a:has-text('Log In')",
        ]
        for selector in selectors:
            try:
                item = page.locator(selector).first
                if item.count() > 0 and item.is_visible():
                    item.click(timeout=8_000)
                    return True
            except Exception:
                continue
        return False

    def _settle(self, page: Any) -> None:
        for state in ("networkidle", "domcontentloaded"):
            try:
                page.wait_for_load_state(state, timeout=10_000)
            except Exception:
                pass
        time.sleep(1)

    def _candidate_urls(self) -> list[str]:
        configured = self.club.options.get("candidate_urls")
        if isinstance(configured, list):
            return [str(value) for value in configured]
        return [urljoin(self.club.base_url, path) for path in self.candidate_paths]

    def _collect_from_page(self, page: Any, source_url: str) -> list[TennisEvent]:
        events: list[TennisEvent] = []
        events.extend(self._collect_ics_links(page, source_url))
        events.extend(self._collect_dom_events(page, source_url))
        return events

    def _collect_ics_links(self, page: Any, source_url: str) -> list[TennisEvent]:
        events: list[TennisEvent] = []
        try:
            links = page.locator("a[href]").evaluate_all(
                """
                nodes => nodes.map(node => ({
                  href: node.href,
                  text: node.innerText || node.getAttribute('aria-label') || ''
                }))
                """
            )
        except Exception:
            return events
        for link in links:
            href = str(link.get("href") or "")
            text = str(link.get("text") or "")
            haystack = f"{href} {text}".lower()
            if ".ics" not in haystack and "ical" not in haystack and "calendar" not in haystack:
                continue
            try:
                response = page.context.request.get(href, timeout=15_000)
                body = response.text()
                if "BEGIN:VCALENDAR" in body:
                    events.extend(parse_ics_events(body, self.club.id, self.club.timezone, href))
            except Exception:
                continue
        return events

    def _collect_dom_events(self, page: Any, source_url: str) -> list[TennisEvent]:
        try:
            items = page.locator(
                "[class*='event' i], [class*='reservation' i], [class*='booking' i], "
                "[class*='appointment' i], [class*='class' i], [data-start], [data-date]"
            ).evaluate_all(
                """
                nodes => nodes.slice(0, 500).map(node => ({
                  text: node.innerText || node.textContent || '',
                  start: node.getAttribute('data-start') || node.getAttribute('data-date') || '',
                  end: node.getAttribute('data-end') || '',
                  title: node.getAttribute('title') || node.getAttribute('aria-label') || '',
                  href: node.href || ''
                }))
                """
            )
        except Exception:
            return []

        events: list[TennisEvent] = []
        for item in items:
            event = self._event_from_dom_item(item, source_url)
            if event:
                events.append(event)
        return events

    def _event_from_dom_item(self, item: dict[str, Any], source_url: str) -> TennisEvent | None:
        title = _clean_text(str(item.get("title") or ""))
        text = _clean_text(str(item.get("text") or ""))
        if not title:
            title = _first_meaningful_line(text)
        start = _parse_datetime_value(item.get("start"), self.club.timezone)
        end = _parse_datetime_value(item.get("end"), self.club.timezone)
        if not start:
            parsed = _parse_datetime_from_text(text, self.club.timezone)
            if parsed:
                start, end = parsed
        if not start:
            return None
        if not end or end <= start:
            end = start + timedelta(hours=1)
        if not title:
            title = "Tennis"
        href = str(item.get("href") or "") or source_url
        return TennisEvent(
            club_id=self.club.id,
            title=title[:160],
            starts_at=start,
            ends_at=end,
            timezone=self.club.timezone,
            location=None,
            category=None,
            source_url=href,
            raw={"dom_text": text[:1000]},
        )

    def _collect_from_json(self, payloads: list[tuple[str, Any]]) -> list[TennisEvent]:
        events: list[TennisEvent] = []
        for source_url, payload in payloads:
            events.extend(_events_from_payload(payload, self.club, source_url))
        return events

    def _save_artifacts(self, page: Any, debug_dir: Path, prefix: str) -> Path:
        html_path = debug_dir / f"{prefix}.html"
        screenshot_path = debug_dir / f"{prefix}.png"
        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            return screenshot_path
        except Exception:
            return html_path


def _events_from_payload(payload: Any, club: ClubConfig, source_url: str) -> list[TennisEvent]:
    found: list[TennisEvent] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            event = _event_from_dict(value, club, source_url)
            if event:
                found.append(event)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def _event_from_dict(value: dict[str, Any], club: ClubConfig, source_url: str) -> TennisEvent | None:
    start_raw = _pick(value, START_KEYS)
    if start_raw is None:
        return None
    start = _parse_datetime_value(start_raw, club.timezone)
    if not start:
        return None
    end = _parse_datetime_value(_pick(value, END_KEYS), club.timezone)
    if not end or end <= start:
        end = start + timedelta(hours=1)

    title = str(_pick(value, TITLE_KEYS) or "Tennis").strip()
    location = _pick(value, LOCATION_KEYS)
    external_id = _pick(value, ("id", "eventId", "reservationId", "bookingId", "classId", "uid"))
    status_raw = str(_pick(value, ("status", "state")) or "").lower()
    status = "cancelled" if "cancel" in status_raw else "confirmed"
    return TennisEvent(
        club_id=club.id,
        external_id=str(external_id) if external_id is not None else None,
        title=title[:160],
        starts_at=start,
        ends_at=end,
        timezone=club.timezone,
        location=str(location) if location is not None else None,
        category=str(_pick(value, ("type", "category", "eventType")) or "") or None,
        source_url=source_url,
        status=status,
        raw=value,
    )


def _pick(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lower = {str(key).lower(): key for key in value.keys()}
    for key in keys:
        actual = lower.get(key.lower())
        if actual is not None:
            return value[actual]
    return None


def _parse_datetime_value(value: Any, timezone_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=ZoneInfo(timezone_name))
    raw = str(value).strip()
    if not raw:
        return None
    match = re.search(r"/Date\((\d+)", raw)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=ZoneInfo(timezone_name))
    if raw.endswith("Z"):
        try:
            return datetime.fromisoformat(raw[:-1] + "+00:00")
        except ValueError:
            pass
    try:
        return ensure_aware(datetime.fromisoformat(raw), timezone_name)
    except ValueError:
        pass
    formats = [
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M %p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %H:%M",
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=ZoneInfo(timezone_name))
        except ValueError:
            continue
    return None


def _parse_datetime_from_text(text: str, timezone_name: str) -> tuple[datetime, datetime | None] | None:
    month = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    date_time = re.search(
        rf"{month}\s+\d{{1,2}},?\s+\d{{4}}[^\d]{{0,8}}\d{{1,2}}:\d{{2}}\s*[AP]M",
        text,
        re.I,
    )
    if date_time:
        start = _parse_datetime_value(date_time.group(0).replace(",", ""), timezone_name)
        if start:
            return start, None
    numeric = re.search(
        r"\d{1,2}/\d{1,2}/\d{2,4}[^\d]{1,8}\d{1,2}:\d{2}\s*[AP]M",
        text,
        re.I,
    )
    if numeric:
        start = _parse_datetime_value(numeric.group(0), timezone_name)
        if start:
            return start, None
    return None


def _dedupe_events(events: list[TennisEvent]) -> list[TennisEvent]:
    now = utc_now()
    seen: set[tuple[str, str, str]] = set()
    unique: list[TennisEvent] = []
    for event in events:
        if event.ends_at < now - timedelta(days=1):
            continue
        key = (
            event.club_id,
            event.starts_at.isoformat(),
            event.ends_at.isoformat(),
            event.title.lower().strip(),
            (event.location or "").lower().strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _first_meaningful_line(text: str) -> str:
    for line in re.split(r"[\r\n]+", text):
        clean = _clean_text(line)
        if len(clean) >= 3:
            return clean
    return ""
