"""Optional authenticated public search; no model credentials or provider fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import unquote

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .discovery_agent import AgentDiscoveryError, SourceUnavailable, TransientSourceError
from .discovery_sources import Searcher
from .public_documents import search_public_courses
from .public_web import course_search_query, public_url

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
MAX_RESPONSE_BYTES = 256_000


class _Hit(BaseModel):
    model_config = ConfigDict(strict=True)
    title: str
    url: str


class _WebResults(BaseModel):
    results: list[_Hit] = Field(max_length=8)


class _SearchResponse(BaseModel):
    web: _WebResults


@dataclass(frozen=True)
class BraveSearch:
    api_key: SecretStr = field(repr=False)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        key = self.api_key.get_secret_value()
        if not 1 <= len(key) <= 4096 or any(not 33 <= ord(c) <= 126 for c in key):
            raise AgentDiscoveryError("Configure a valid dedicated search API key.")

    async def __call__(self, city: str, level: str, year: int, focus: str) -> list[dict[str, str]]:
        query = course_search_query(city, level, year, focus)
        key = self.api_key.get_secret_value()
        if key in query:
            raise AgentDiscoveryError("Credential-bearing search query blocked.")
        try:
            async with asyncio.timeout(20):
                async with httpx.AsyncClient(
                    transport=self.transport
                    or httpx.AsyncHTTPTransport(retries=0, trust_env=False),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=12,
                ) as client:
                    async with client.stream(
                        "GET",
                        ENDPOINT,
                        params={"q": query, "count": 8, "result_filter": "web"},
                        headers={"X-Subscription-Token": key, "Accept": "application/json"},
                    ) as response:
                        if response.status_code in {502, 503, 504}:
                            if "retry-after" in response.headers:
                                raise SourceUnavailable("Search API asked to retry later.")
                            raise TransientSourceError("Public search temporarily unavailable")
                        if response.status_code == 429:
                            # Do not immediately retry a quota/rate limit or evade it via another provider.
                            raise SourceUnavailable("Search API rate limit reached; retry later.")
                        if response.status_code != 200:
                            raise SourceUnavailable(
                                "Search API request failed; check configuration."
                            )
                        if (
                            response.headers.get("content-type", "").split(";", 1)[0].lower()
                            != "application/json"
                        ):
                            raise SourceUnavailable("Search API returned an invalid response.")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_RESPONSE_BYTES:
                                raise SourceUnavailable(
                                    "Search API response exceeded the size limit."
                                )
                        if key.encode() in body:
                            raise SourceUnavailable("Credential-bearing search response blocked.")
                        result = _SearchResponse.model_validate_json(body)
                        if any(
                            key in hit.title or key in unquote(hit.url)
                            for hit in result.web.results
                        ):
                            raise SourceUnavailable("Credential-bearing search response blocked.")
                        return [
                            {"url": public_url(hit.url), "title": hit.title[:240]}
                            for hit in result.web.results
                        ]
        except (TimeoutError, httpx.TransportError):
            raise TransientSourceError("Public search temporarily unavailable") from None
        except ValueError:
            # Validation errors and response bodies may include credentials or source text.
            raise SourceUnavailable("Search API returned an invalid response.") from None


def configured_searcher(provider: str, api_key: str) -> Searcher:
    if provider == "public":
        return search_public_courses
    if provider == "brave":
        return BraveSearch(SecretStr(api_key))
    raise AgentDiscoveryError("Use a supported discovery search provider: public or brave.")
