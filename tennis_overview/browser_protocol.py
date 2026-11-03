"""Small private-worker protocol. No arbitrary browser actions or executable input."""

from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import AwareDatetime, Field, model_validator

from .course_evidence import EvidenceModel
from .public_web import public_url

Text = Annotated[str, Field(max_length=240)]
AUTH_HOST = "app.courtreserve.com"
MAX_RESPONSE = 192_000


def portal_return_path(path: str, target: str) -> bool:
    """Allow only the site's anonymous bootstrap back to a same-club read page."""
    portal = re.fullmatch(r"/Online/Portal/Index/(\d+)", path)
    if not portal or not re.fullmatch(
        rf"/Online/Events/(?:List/{portal[1]}|Details/{portal[1]}/[A-Za-z0-9]+)",
        urlsplit(target).path,
    ):
        return False
    if not target.startswith("/Online/Events/") or urlsplit(target).netloc:
        return False
    try:
        read_url("https://" + AUTH_HOST + target)
    except ValueError:
        return False
    return True


def read_url(value: str, *, document: bool = True) -> str:
    url = public_url(value)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.port not in (None, 443) or len(url) > 2048:
        raise ValueError("Only bounded public HTTPS references are supported")
    if re.search(
        r"(?:^|[/_?&=.-])(?:delete|remove|cancel|unsubscribe|logout|purchase|checkout|payment|addcart|book|submit)(?:$|[/_?&=.-])",
        parsed.path + "?" + parsed.query,
        re.I,
    ):
        raise ValueError("Action references are not readable sources")
    if document and parsed.hostname == AUTH_HOST:
        if not re.fullmatch(
            r"/Online/(?:Events/(?:List/\d+|Details/\d+/[A-Za-z0-9]+)|Portal/Index/\d+)",
            parsed.path,
        ):
            raise ValueError("Unsupported authenticated navigation")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if len({k for k, _ in pairs}) != len(pairs) or any(
            not (
                (k == "resId" and v.isascii() and v.isdigit())
                or (k == "tab" and v in {"dates", "registration"})
                or (k == "returnUrl" and portal_return_path(parsed.path, v))
            )
            for k, v in pairs
        ):
            raise ValueError("Unsupported authenticated query")
    return url


class BrowserRequest(EvidenceModel):
    operation: Literal["read", "search"]
    url: str = Field(default="", max_length=2048)
    city: Text = ""
    level: Text = ""
    year: int | None = Field(default=None, ge=2000, le=2100, strict=True)
    focus: Literal["overview", "registration", "schedule"] = "overview"

    @model_validator(mode="after")
    def validate_operation(self) -> BrowserRequest:
        if self.operation == "read":
            read_url(self.url)
            if self.city or self.level:
                raise ValueError("Read accepts a discovered URL only")
        elif self.url or not self.city.strip() or self.year is None:
            raise ValueError("Search requires a city, not a URL")
        return self


class BrowserLink(EvidenceModel):
    url: str = Field(max_length=2048)
    title: Text


class BrowserReply(EvidenceModel):
    kind: Literal["page", "browser", "search"]
    url: str = Field(default="", max_length=2048)
    title: Text = ""
    text: str = Field(default="", max_length=24000)
    html: str = Field(default="", max_length=24000)
    captured_at: AwareDatetime | None = None
    links: tuple[BrowserLink, ...] = Field(default=(), max_length=24)
    partial: bool = False
