"""Bounded read-only browser retrieval in an isolated child process."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from urllib.parse import urljoin, urlsplit

from playwright.async_api import BrowserContext, Page, ProxySettings, Route, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .browser_network import get_resource
from .browser_profiles import profile_snapshot
from .browser_protocol import AUTH_HOST, BrowserLink, BrowserReply, BrowserRequest, read_url
from .browser_query_policy import validate_catalog_query
from .public_web import page_from_html

FIELDS = {
    "title": '[data-testid="events-details-header"] h1',
    "category": '[data-testid="events-details-category"]',
    "date": '[data-testid="events-details-date"]',
    "time": '[data-testid="events-details-time"]',
    "price-single": '[data-testid="events-details-price-single"]',
    "slots": '[data-testid="events-details-slots"]',
    "tab-dates": '[data-testid="events-details-tab-dates"]',
}


class BrowserReadError(Exception):
    """Fixed error codes only; never propagate a portal response or account text."""


class LaunchOptions(TypedDict):
    headless: bool
    channel: str
    chromium_sandbox: bool
    proxy: ProxySettings
    env: dict[str, str | float | bool]
    args: list[str]
    timeout: float
    ignore_default_args: list[str]


async def course_summary(page: Page) -> BrowserReply:
    values: dict[str, str] = {}
    for name, selector in FIELDS.items():
        nodes = page.locator(selector)
        if await nodes.count() != 1 or not await nodes.is_visible():
            if name in {"price-single", "slots", "tab-dates"}:
                continue
            raise BrowserReadError("source_unavailable")
        value = (await nodes.inner_text()).strip()
        if len(value) > 500:
            raise BrowserReadError("source_unavailable")
        values[name] = value
    title = html.escape(values.pop("title"))
    category = html.escape(values.pop("category"))
    body = '<main data-testid="events-details-page"><div data-testid="events-details-header">'
    body += f'<h1>{title}</h1><div data-testid="events-details-category">{category}</div></div>'
    for name, value in values.items():
        body += f'<div data-testid="events-details-{name}">{html.escape(value)}</div>'
    body += "</main>"
    return BrowserReply(
        kind="browser", url=read_url(page.url), html=body, captured_at=datetime.now(timezone.utc)
    )


class ReadNetwork:
    def __init__(self, portal_session: bool, *, anonymous: bool = True) -> None:
        self.portal_session = portal_session
        self.anonymous = anonymous
        self.requests = 0
        self.bytes = 0
        self.login_required = False
        self.attention_required = False
        self.membership_required = False
        self.denied = False
        self.redirects: dict[str, str] = {}

    async def handle(self, route: Route) -> None:
        request = route.request
        self.requests += 1
        if "/account/" in urlsplit(request.url).path.lower():
            self.login_required = True
        try:
            read_url(request.url, document=request.is_navigation_request())
            if request.is_navigation_request() and request.frame.parent_frame is not None:
                raise ValueError("Embedded navigation is not a course source")
            post: dict[str, bytes] = {}
            if request.method != "GET":
                if request.method != "POST" or not self.anonymous or not self.portal_session:
                    raise ValueError("Write methods are not allowed")
                body = request.post_data_buffer or b""
                validate_catalog_query(request.url, body)
                post["post_body"] = body
            if self.requests > 150 or self.bytes > 16_000_000:
                raise ValueError("Read budget or method rejected")
            if request.resource_type in {"image", "media", "font"}:
                await route.abort()
                return
            if (
                self.portal_session
                and request.resource_type in {"document", "xhr", "fetch"}
                and urlsplit(request.url).hostname != AUTH_HOST
                and not post
            ):
                raise ValueError("Authenticated data origin rejected")
            resource = await asyncio.to_thread(
                get_resource,
                request.url,
                await request.all_headers(),
                portal_session=self.portal_session,
                **post,
            )
            if post and resource.headers.get("content-type", "").startswith("application/json"):
                payload = json.loads(resource.body)
                if isinstance(payload, dict) and payload.get("message") == (
                    "You need to be a member of this organization to access this module!"
                ):
                    self.membership_required = True
            if request.is_navigation_request() and resource.status in {401, 403}:
                self.attention_required = True
            if resource.status in {301, 302, 303, 307, 308}:
                target = urljoin(request.url, resource.headers.get("location", ""))
                if "/account/" in urlsplit(target).path.lower():
                    self.login_required = True
                read_url(target, document=request.is_navigation_request())
                # Playwright does not re-route native HTTP redirect hops. Finish
                # this document, then navigate explicitly through the same guard.
                if not request.is_navigation_request():
                    raise ValueError("Subresource redirects require separate validation")
                self.redirects[request.url] = target
                headers = {"content-type": "text/html"}
                if "set-cookie" in resource.headers:
                    headers["set-cookie"] = resource.headers["set-cookie"]
                await route.fulfill(status=200, headers=headers, body="<html></html>")
                return
            self.bytes += len(resource.body)
            await route.fulfill(
                status=resource.status, headers=resource.headers, body=resource.body
            )
        except Exception:
            self.denied = True
            await route.abort()


async def navigate(page: Page, url: str, network: ReadNetwork) -> None:
    for _ in range(8):
        await page.goto(read_url(url), wait_until="domcontentloaded", timeout=25000)
        if network.attention_required:
            raise BrowserReadError("browser_attention_required")
        target = network.redirects.pop(url, None)
        if target is None:
            return
        url = target
    raise BrowserReadError("source_unavailable")


async def _read(
    context: BrowserContext, request: BrowserRequest, network: ReadNetwork
) -> BrowserReply:
    page = await context.new_page()
    context.on("page", lambda extra: extra.close())
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.on("download", lambda download: download.cancel())
    await navigate(page, request.url, network)
    if network.portal_session:
        # Only portal/list/detail navigation; never click personal/action controls.
        if "/Portal/" in request.url:
            link = page.locator('a[href*="/Online/Events/List/"]')
            try:
                await link.first.wait_for(timeout=5000)
            except PlaywrightTimeoutError:
                await page.get_by_text("Events, Camps, And Classes", exact=True).click(timeout=5000)
            if await link.count() != 1:
                raise BrowserReadError("source_unavailable")
            read_url(urljoin(page.url, await link.get_attribute("href") or ""))
            await link.click(timeout=5000)
        if "/Events/Details/" not in request.url:
            await page.locator('[data-testid^="events-list-card-name-"]').first.wait_for(
                timeout=15000
            )
            names = page.locator('[data-testid^="events-list-card-name-"]')
            selected: list[tuple[str, str]] = []
            for index in range(min(await names.count(), 250)):
                item = names.nth(index)
                title = (await item.inner_text()).strip()
                tag = await item.get_attribute("data-testid") or ""
                if (
                    re.search(r"adult", title, re.I)
                    and re.search(r"tennis|clinic", title, re.I)
                    and not re.search(r"junior|pickleball", title, re.I)
                ):
                    selected.append((tag.removeprefix("events-list-card-name-"), title[:240]))
                if len(selected) == 4:
                    break
            list_url = read_url(page.url)
            links = []
            for course_id, title in selected:
                if not course_id.isdigit():
                    raise BrowserReadError("source_unavailable")
                await page.get_by_test_id("events-list-view-upcoming-dates-btn-" + course_id).click(
                    timeout=5000
                )
                await page.locator('[data-testid="events-details-date"]').wait_for(timeout=15000)
                links.append(BrowserLink(url=read_url(page.url), title=title))
                await navigate(page, list_url, network)
                await page.locator('[data-testid^="events-list-card-name-"]').first.wait_for(
                    timeout=15000
                )
            if not links:
                raise BrowserReadError("source_unavailable")
            return BrowserReply(
                kind="page",
                url=list_url,
                title="Adult tennis course list (partial)",
                text="Selected course detail links only; this is not complete coverage.",
                links=tuple(links),
                partial=True,
            )
        await page.locator('[data-testid="events-details-date"]').wait_for(timeout=15000)
        return await course_summary(page)
    # General public pages never use a login profile.
    await page.wait_for_load_state("networkidle", timeout=8000)
    rendered = await page.content()
    if len(rendered.encode()) > 2_000_000:
        raise BrowserReadError("source_unavailable")
    parsed = page_from_html(read_url(page.url), rendered.encode())
    if len(parsed.text.split()) < 5:
        raise BrowserReadError("source_unavailable")
    return BrowserReply(
        kind="page",
        url=parsed.url,
        title=parsed.title,
        text=parsed.text,
        links=tuple(
            BrowserLink(url=link.url, title=link.title) for link in parsed.link_details[:24]
        ),
    )


async def read_browser(
    request: BrowserRequest,
    profile: Path,
    *,
    channel: str = "chrome",
    timeout_seconds: float = 35,
    scratch: Path | None = None,
    allow_authenticated: bool = False,
) -> BrowserReply:
    try:
        return await _read_browser(request, None, channel=channel, timeout_seconds=timeout_seconds)
    except BrowserReadError as error:
        if (
            str(error) != "login_required"
            or not allow_authenticated
            or urlsplit(request.url).hostname != AUTH_HOST
        ):
            raise
    with profile_snapshot(profile, scratch) as snapshot:
        return await _read_browser(
            request, snapshot, channel=channel, timeout_seconds=timeout_seconds
        )


async def _read_browser(
    request: BrowserRequest, profile: Path | None, *, channel: str, timeout_seconds: float
) -> BrowserReply:
    portal = urlsplit(request.url).hostname == AUTH_HOST
    network = ReadNetwork(portal, anonymous=profile is None)
    clean_env: dict[str, str | float | bool] = {
        key: os.environ[key] for key in ("HOME", "PATH", "LANG", "TMPDIR") if key in os.environ
    }
    async with async_playwright() as playwright:
        # A dead proxy blocks any Chromium request escaping interception. All
        # permitted HTTP traffic goes through the DNS-pinned GET relay above.
        options: LaunchOptions = dict(
            headless=True,
            channel=channel,
            chromium_sandbox=True,
            proxy={"server": "http://127.0.0.1:9", "bypass": "<-loopback>"},
            env=clean_env,
            args=[
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                "--disable-quic",
            ],
            timeout=10000,
            # Human login uses Chrome's real macOS keychain. Playwright's mock
            # keychain cannot decrypt that profile's persisted session cookies.
            ignore_default_args=(
                ["--use-mock-keychain", "--password-store=basic"]
                if profile is not None and sys.platform == "darwin"
                else []
            ),
        )
        browser = None
        if profile is not None:
            context = await playwright.chromium.launch_persistent_context(
                str(profile), service_workers="block", accept_downloads=False, **options
            )
        else:
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context(service_workers="block", accept_downloads=False)
        try:
            await context.route("**/*", network.handle)
            await context.route_web_socket("**/*", lambda socket: socket.close())
            try:
                return await asyncio.wait_for(
                    _read(context, request, network), timeout=timeout_seconds
                )
            except Exception:
                code = "source_unavailable"
                if network.attention_required:
                    code = "browser_attention_required"
                if network.login_required:
                    code = "login_required"
                if network.membership_required:
                    code = "membership_required"
                raise BrowserReadError(code) from None
        finally:
            await context.close()
            if browser:
                await browser.close()
