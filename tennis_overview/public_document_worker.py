"""Fixed-purpose child process for public document I/O and untrusted PDF parsing."""

from __future__ import annotations

import json
import logging
import resource
import sys
from dataclasses import asdict


def set_limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # macOS does not implement address-space limits consistently. Production is Linux.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


def main() -> int:
    try:
        set_limits()
        logging.disable(logging.CRITICAL)
        mode = sys.argv[1] if len(sys.argv) == 2 else "document"
        if mode == "search":
            return search()
        if mode != "document":
            return 1
        from .public_pdf import page_from_pdf
        from .public_web import fetch_resource, page_from_html, public_url

        url = public_url(sys.stdin.buffer.read(2049).decode("utf-8"))
        url, content_type, body = fetch_resource(
            url, ("text/html", "text/plain", "application/pdf")
        )
        page = (
            page_from_pdf(url, body)
            if content_type == "application/pdf"
            else page_from_html(url, body)
        )
        sys.stdout.write(json.dumps(asdict(page), ensure_ascii=True))
        return 0
    except Exception:
        # Never echo URLs, source text, parser diagnostics or environment variables.
        return 1


def search() -> int:
    from ddgs.exceptions import DDGSException

    from .public_web import search_courses

    request = json.loads(sys.stdin.buffer.read(4097))
    city, level, year = request["city"], request["level"], request["year"]
    focus = request.get("focus", "overview")
    subject = request.get("subject", "")
    if (
        not isinstance(city, str)
        or not 1 <= len(city) <= 120
        or not isinstance(level, str)
        or len(level) > 80
        or type(year) is not int
        or not 2000 <= year <= 2100
        or any(ord(c) < 32 for c in city + level)
        or focus not in {"overview", "registration", "schedule"}
        or not isinstance(subject, str)
    ):
        return 1
    try:
        results = (
            search_courses(city, level, year, focus, subject=subject)
            if subject
            else search_courses(city, level, year, focus)
        )
    except DDGSException:
        # A provider failure or ambiguous no-results response is not an empty catalog.
        # EX_TEMPFAIL lets the parent use its bounded retry without exposing diagnostics.
        return 75
    sys.stdout.write(json.dumps(results, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
