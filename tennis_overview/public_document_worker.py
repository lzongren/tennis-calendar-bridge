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


if __name__ == "__main__":
    raise SystemExit(main())
