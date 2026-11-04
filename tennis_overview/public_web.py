"""Bounded public-page retrieval. Never send portal cookies or inference credentials."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from dataclasses import dataclass
from itertools import islice
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

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
class PageLink:
    url: str
    title: str
    relation: Literal["anchor", "same_as"] = "anchor"


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    links: tuple[str, ...]
    heading: str = ""
    schedules: tuple[ScheduleBlock, ...] = ()
    link_details: tuple[PageLink, ...] = ()
    content_type: str = "text/html"
    partial: bool = False
    required_links: tuple[str, ...] = ()


def fetch_page(url: str) -> Page:
    url, _, body = fetch_resource(url, ("text/html", "text/plain"))
    return page_from_html(url, body)


def fetch_resource(url: str, accepted: tuple[str, ...]) -> tuple[str, str, bytes]:
    """Read one bounded public resource, validating and pinning every redirect hop."""
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
                    "Accept": ",".join(accepted),
                    "Accept-Encoding": "identity",
                },
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                raise ValueError("Source page unavailable")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in accepted:
                raise ValueError("Unsupported source content type")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Source page too large")
        finally:
            conn.close()
            sock.close()
        return url, content_type, body
    raise ValueError("Too many source redirects")


def facility_references(soup: BeautifulSoup, url: str) -> tuple[PageLink, ...]:
    """Read bounded inline identity references, never resolve JSON-LD contexts."""
    contexts = {
        "https://schema.org",
        "http://schema.org",
        "https://schema.org/",
        "http://schema.org/",
    }
    types = {"SportsActivityLocation", "LocalBusiness"}
    links: dict[str, PageLink] = {}
    for script in soup.select('script[type="application/ld+json"]')[:8]:
        raw = script.get_text()
        if len(raw) > 65_536:
            continue
        try:
            document = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        context = document.get("@context") if isinstance(document, dict) else None
        nodes = document.get("@graph", [document]) if isinstance(document, dict) else document
        if not isinstance(nodes, list):
            continue
        for node in nodes[:32]:
            if not isinstance(node, dict):
                continue
            node_context = node.get("@context", context)
            if not isinstance(node_context, str) or node_context not in contexts:
                continue
            node_types = node.get("@type", [])
            if isinstance(node_types, str):
                node_types = [node_types]
            if not isinstance(node_types, list) or not any(
                isinstance(kind, str) and kind in types for kind in node_types
            ):
                continue
            identity_url = node.get("url")
            if not isinstance(identity_url, str):
                continue
            try:
                if public_url(identity_url).rstrip("/") != public_url(url).rstrip("/"):
                    continue
            except ValueError:
                continue
            references = node.get("sameAs", [])
            if isinstance(references, str):
                references = [references]
            if not isinstance(references, list):
                continue
            name = node.get("name")
            title = "Facility reference" + (": " + name[:200] if isinstance(name, str) else "")
            for reference in references[:8]:
                if not isinstance(reference, str):
                    continue
                try:
                    link = public_url(reference)
                except ValueError:
                    continue
                if link.rstrip("/") != url.rstrip("/"):
                    links.setdefault(link, PageLink(link, title, "same_as"))
                if len(links) == 8:
                    return tuple(links.values())
    return tuple(links.values())


def anchor_title(anchor: Tag) -> str:
    """Keep a generic button's local heading without borrowing another card's title."""
    label = anchor.get_text(" ", strip=True)[:240]
    if not re.fullmatch(
        r"register(?: now)?|reserve\s*/\s*register|sign up|enroll(?: now)?|"
        r"book now|learn more|view (?:details|schedules?)",
        label,
        re.I,
    ):
        return label
    child = anchor
    for _ in range(3):
        parent = child.parent
        if not isinstance(parent, Tag) or parent.name in {"html", "body", "[document]"}:
            break
        children = list(islice(parent.children, 33))
        if len(children) > 32:
            break
        headings = [
            node
            for node in children
            if isinstance(node, Tag) and node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}
        ]
        if headings:
            heading = headings[0]
            preceding = []
            for sibling in children:
                if sibling is child:
                    break
                preceding.append(sibling)
            if (
                len(headings) != 1
                or heading.name == "h1"
                or not any(node is heading for node in preceding)
                or len(parent.get_text(" ", strip=True)) > 500
            ):
                break
            context = heading.get_text(" ", strip=True)
            if context:
                return context[: 238 - len(label)] + ": " + label
            break
        child = parent
    return label


def page_from_html(url: str, body: bytes) -> Page:
    soup = BeautifulSoup(body, "html.parser")
    schedules = schedule_blocks(soup, url)
    references = facility_references(soup, url)
    title = soup.title.get_text(" ", strip=True) if soup.title else urlsplit(url).hostname or ""
    heading = soup.h1.get_text(" ", strip=True) if soup.h1 else ""
    # Navigation can contain hundreds of links before the actual course content.
    # Apply the link budget after removing chrome, just as we do for source text.
    for node in soup.select("script, style, nav, footer, header, noscript, svg, [role=navigation]"):
        node.decompose()
    links: dict[str, PageLink] = {link.url: link for link in references}
    for anchor in soup.find_all("a", href=True):
        try:
            link = public_url(urljoin(url, str(anchor["href"])))
            if link not in links:
                links[link] = PageLink(link, anchor_title(anchor))
        except ValueError:
            pass
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return Page(
        url,
        title[:240],
        text[:24000],
        tuple(links)[:150],
        heading[:240],
        schedules,
        tuple(links.values())[:150],
    )


def search_subject(title: str) -> str:
    """Turn observed public metadata into words, never search operators or instructions."""
    subject = " ".join("".join(c if c.isalnum() else " " for c in title).split())
    return subject if 3 <= len(subject) <= 120 and len(subject.split()) <= 16 else ""


def validate_search_subject(subject: str) -> str:
    if not subject or search_subject(subject) != subject:
        raise ValueError("Use bounded plain words for an observed source subject")
    return subject


def search_courses(
    location: str, level: str, year: int, focus: str | None = None, *, subject: str = ""
) -> list[dict[str, str]]:
    from ddgs import DDGS
    from ddgs.engines import ENGINES

    queries = {
        None: f"{location} adult tennis clinics classes schedule",
        "overview": f"adult tennis group clinics near {location}",
        "registration": f"{location} adult tennis classes registration",
        "schedule": f"{location} adult tennis class schedule {year}",
    }
    if focus not in queries:
        raise ValueError("Invalid search focus")
    query = queries[focus]
    if subject:
        if focus != "registration":
            raise ValueError("Source refinement requires registration focus")
        query = f"{validate_search_subject(subject)} {location} adult tennis clinics {year}"
    # Keep legacy behavior separate. Explicit selection avoids DDGS's unrestricted
    # provider fallback; Bing is disabled in the evaluated DDGS release.
    backend = "bing,duckduckgo" if focus is None else "yahoo"
    if focus is not None and backend not in ENGINES["text"]:
        # DDGS otherwise silently switches an unavailable backend to "auto".
        raise ValueError("Configured public search backend is unavailable")
    if focus is None:
        results = DDGS(timeout=12).text(query, max_results=8, backend=backend)
    else:
        from .search_layouts import search_index

        results = search_index(query)
    candidates = []
    for item in results:
        try:
            url = public_url(item["href"])
        except (ValueError, KeyError):
            continue
        candidates.append({"url": url, "title": item.get("title", "")[:240]})
    return candidates
