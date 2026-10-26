"""Bounded public-page retrieval. Never send portal cookies or inference credentials."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

MAX_BYTES = 4_000_000


def public_url(url: str) -> str:
    if len(url) > 2048:
        raise ValueError("Source URL is too long")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("Only public HTTP(S) pages are supported")
    if parsed.hostname.lower() == "localhost" or parsed.hostname.lower().endswith(
        (".localhost", ".local", ".internal", ".ts.net", ".lan")
    ):
        raise ValueError("Private hosts cannot be course sources")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError("Private hosts cannot be course sources")
    # Personal subscription and login URLs must never become discovery sources.
    if any(
        part in parsed.path.lower() for part in ("publicical", "/calendar/", "/login", "/account/")
    ):
        raise ValueError("Use a public course page, not a private feed or login page")
    if parsed.query:
        from urllib.parse import parse_qsl

        if any(
            any(word in key.lower() for word in ("token", "key", "password", "secret", "auth"))
            for key, _ in parse_qsl(parsed.query)
        ):
            raise ValueError("Credential-bearing URLs are not supported")
        if any(
            key.lower() == "action"
            and value.lower() in {"addcart", "cancel", "delete", "logout", "purchase", "book"}
            for key, value in parse_qsl(parsed.query)
        ):
            raise ValueError("Use a course information page, not an action URL")
    return urlunsplit(parsed._replace(fragment=""))


def public_addresses(host: str, port: int) -> list[str]:
    addresses = list(
        dict.fromkeys(
            str(row[4][0]) for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        )
    )
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Source must resolve only to public addresses")
    return addresses


@dataclass(frozen=True)
class ScheduleBlock:
    title: str
    level: str
    text: str
    signup_url: str
    price: str = ""


def schedule_blocks(soup: BeautifulSoup, url: str) -> tuple[ScheduleBlock, ...]:
    """Preserve card -> weekday group -> term -> registration link ownership."""
    blocks = []
    for card in soup.select(".gs-stacked-card"):
        heading, level = card.select_one("h2"), card.select_one(".level-tag")
        if not heading or not level:
            continue
        for group in card.select(".session-group"):
            day = group.select_one(".day-label")
            if not day:
                continue
            for row in group.select(".s-line"):
                info, anchor = row.select_one(".s-info"), row.select_one("a[href]")
                if not info or not anchor:
                    continue
                try:
                    signup = public_url(urljoin(url, str(anchor["href"])))
                except ValueError:
                    continue
                blocks.append(
                    ScheduleBlock(
                        heading.get_text(" ", strip=True),
                        level.get_text(" ", strip=True),
                        day.get_text(" ", strip=True) + "\n" + info.get_text(" ", strip=True),
                        signup,
                    )
                )
    return tuple(blocks[:80])


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    links: tuple[str, ...]
    heading: str = ""
    schedules: tuple[ScheduleBlock, ...] = ()


def fetch_page(url: str) -> Page:
    for _ in range(4):
        url = public_url(url)
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = public_addresses(host, port)
        conn = http.client.HTTPConnection(host, port, timeout=12)
        # Pin the validated address; do not resolve again at connection time (DNS rebinding).
        sock = socket.create_connection((addresses[0], port), timeout=12)
        try:
            conn.sock = (
                ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                if parsed.scheme == "https"
                else sock
            )
            conn.request(
                "GET",
                urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
                headers={
                    "Host": parsed.netloc,
                    "User-Agent": "TennisCalendarBridge/0.2 (+public-course-discovery)",
                    "Accept": "text/html,text/plain",
                    "Accept-Encoding": "identity",
                },
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                raise ValueError("Source page unavailable")
            if not any(
                t in response.getheader("Content-Type", "") for t in ("text/html", "text/plain")
            ):
                raise ValueError("Source is not an HTML or text page")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Source page too large")
        finally:
            conn.close()
            sock.close()
        return page_from_html(url, body)
    raise ValueError("Too many source redirects")


def page_from_html(url: str, body: bytes) -> Page:
    soup = BeautifulSoup(body, "html.parser")
    schedules = schedule_blocks(soup, url)
    links = []
    for anchor in soup.find_all("a", href=True):
        try:
            link = public_url(urljoin(url, str(anchor["href"])))
            if link not in links:
                links.append(link)
        except ValueError:
            pass
    title = soup.title.get_text(" ", strip=True) if soup.title else urlsplit(url).hostname or ""
    heading = soup.h1.get_text(" ", strip=True) if soup.h1 else ""
    for node in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        node.decompose()
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return Page(url, title[:240], text[:24000], tuple(links[:150]), heading[:240], schedules)


def search_courses(location: str, level: str, year: int) -> list[dict[str, str]]:
    from ddgs import DDGS

    query = f"{location} adult tennis clinics classes schedule"
    results = DDGS(timeout=12).text(query, max_results=8, backend="bing,duckduckgo")
    candidates = []
    for item in results:
        try:
            url = public_url(item["href"])
        except (ValueError, KeyError):
            continue
        candidates.append({"url": url, "title": item.get("title", "")[:240]})
    return candidates
