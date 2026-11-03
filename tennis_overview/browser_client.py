"""VPS client for the private browser worker; fail closed and preserve old results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import SecretStr

from .browser_documents import BrowserDocument
from .browser_protocol import (
    AUTH_HOST,
    MAX_RESPONSE,
    SEARCH_UNAVAILABLE,
    SOURCE_ERRORS,
    BrowserReply,
    BrowserRequest,
    read_url,
)
from .discovery_agent import (
    AgentDiscoveryError,
    SourceFailureReason,
    SourceUnavailable,
    TransientSourceError,
)
from .private_model import PrivateModelConfig
from .public_web import Page, PageLink
from .public_documents import read_public_document


@dataclass
class BrowserClient:
    endpoint: str
    key: SecretStr
    transport: httpx.AsyncBaseTransport | None = None

    def __post_init__(self) -> None:
        # Same private-only URL boundary as inference, with a separate credential.
        self.endpoint = PrivateModelConfig.private_endpoint(self.endpoint)
        if len(self.key.get_secret_value()) < 32:
            raise ValueError("A dedicated browser credential is required")

    async def call(self, request: BrowserRequest) -> BrowserReply:
        body = request.model_dump_json()
        if self.key.get_secret_value() in body:
            raise AgentDiscoveryError("Credential-bearing browser request blocked")
        try:
            async with httpx.AsyncClient(
                transport=self.transport, trust_env=False, follow_redirects=False, timeout=60
            ) as client:
                async with client.stream(
                    "POST",
                    self.endpoint + "/v1/read",
                    headers={
                        "Authorization": "Bearer " + self.key.get_secret_value(),
                        "Content-Type": "application/json",
                    },
                    content=body,
                ) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE:
                            raise AgentDiscoveryError("Browser response exceeded size limit")
                    if response.status_code != 200:
                        error = json.loads(raw).get("error")
                        if (
                            response.status_code == 503
                            and request.operation == "search"
                            and error == SEARCH_UNAVAILABLE
                        ):
                            # The research loop owns the single bounded retry.
                            raise TransientSourceError("Public search temporarily unavailable")
                        if (
                            response.status_code == 503
                            and request.operation == "read"
                            and isinstance(error, str)
                            and error in SOURCE_ERRORS
                        ):
                            # A known page failure is scoped to this source. Unknown
                            # and legacy worker failures still abort the whole run.
                            raise SourceUnavailable(
                                "Browser source unavailable: " + error,
                                reason=SourceFailureReason(error),
                            )
                        if error == "browser_attention_required":
                            raise AgentDiscoveryError(
                                "Private browser requires manual website verification; previous results retained"
                            )
                        if error == "login_required":
                            raise AgentDiscoveryError(
                                "Private browser login required; previous results retained"
                            )
                        if error == "membership_required":
                            raise AgentDiscoveryError(
                                "Provider restricts this catalog to members; previous results retained"
                            )
                        raise AgentDiscoveryError(
                            "Private browser unavailable; previous results retained"
                        )
                    return BrowserReply.model_validate_json(raw)
        except (ValueError, httpx.HTTPError, AttributeError):
            raise AgentDiscoveryError(
                "Private browser unavailable; previous results retained"
            ) from None

    async def read(self, url: str) -> Page | BrowserDocument:
        if urlsplit(url).hostname != AUTH_HOST:
            try:
                public = await read_public_document(url)
                if len(public.text.split()) >= 5:
                    return public  # Keep PDF/card ownership and existing deterministic adapters.
            except SourceUnavailable:
                pass
        reply = await self.call(BrowserRequest(operation="read", url=url))
        read_url(reply.url)
        for link in reply.links:
            read_url(link.url)
        if reply.kind == "browser" and reply.captured_at:
            if reply.url != url:
                raise AgentDiscoveryError("Unexpected course-detail browser destination")
            return BrowserDocument(reply.url, reply.title, reply.html, reply.captured_at)
        if reply.kind != "page":
            raise AgentDiscoveryError("Unexpected browser response")
        return Page(
            reply.url,
            reply.title,
            reply.text,
            tuple(link.url for link in reply.links),
            link_details=tuple(PageLink(link.url, link.title) for link in reply.links),
            partial=reply.partial,
            required_links=tuple(link.url for link in reply.links) if reply.partial else (),
        )

    async def search(self, city: str, level: str, year: int, focus: str) -> list[dict[str, str]]:
        request = BrowserRequest.model_validate(
            {"operation": "search", "city": city, "level": level, "year": year, "focus": focus}
        )
        reply = await self.call(request)
        if reply.kind != "search":
            raise AgentDiscoveryError("Unexpected browser search response")
        return [{"url": read_url(link.url), "title": link.title} for link in reply.links]
