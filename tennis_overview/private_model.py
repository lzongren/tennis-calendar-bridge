"""Explicit private inference transport, with bounded responses and no redirects."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2 as httpx
from openai import AsyncOpenAI
from pydantic import Field, SecretStr, field_validator
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer, ModelProfile
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

from .course_evidence import EvidenceModel
from .discovery_agent import AgentDiscoveryError


class PrivateModelConfig(EvidenceModel):
    base_url: str
    model: str = Field(min_length=1, max_length=160)
    api_key: SecretStr
    request_seconds: float = Field(default=60, gt=0, le=180, allow_inf_nan=False)

    @field_validator("api_key")
    @classmethod
    def nonempty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Private inference authentication is required")
        return value

    @field_validator("base_url")
    @classmethod
    def private_endpoint(cls, value: str) -> str:
        value = value.rstrip("/")
        parsed = urlsplit(value)
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        tailnet = (parsed.hostname or "").endswith(".ts.net")
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or parsed.port == 0
            or not (
                (parsed.scheme == "https" and tailnet) or (parsed.scheme == "http" and loopback)
            )
            or any(character.isspace() for character in value)
        ):
            raise ValueError("Use a private Tailscale HTTPS or loopback HTTP model endpoint")
        return value


class BoundedModelTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, endpoint: str, key: SecretStr) -> None:
        self.inner = inner
        self.url = httpx.URL(endpoint + "/chat/completions")
        self.key = key

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url != self.url or request.method != "POST":
            raise AgentDiscoveryError("Unexpected inference destination blocked.")
        if self.key.get_secret_value().encode() in request.content:
            raise AgentDiscoveryError("Credential-bearing inference payload blocked.")
        response = await self.inner.handle_async_request(request)
        body = bytearray()
        try:
            # Bound decoded bytes too: a compressed response must not bypass the limit.
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 256_000:
                    raise AgentDiscoveryError("Inference response exceeded the size limit.")
            headers = dict(response.headers)
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            return httpx.Response(response.status_code, headers=headers, content=bytes(body))
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()


class PrivateOpenAIClient(AsyncOpenAI):
    """Omit optional OS/architecture diagnostics from the private model API."""

    def platform_headers(self) -> dict[str, str]:
        return {}


@asynccontextmanager
async def private_model(
    config: PrivateModelConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[OpenAIChatModel]:
    """Create one explicit client; no env proxy, implicit key, retry or model fallback."""
    bounded = BoundedModelTransport(
        transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        config.base_url,
        config.api_key,
    )
    async with httpx.AsyncClient(
        transport=bounded,
        trust_env=False,
        follow_redirects=False,
        timeout=config.request_seconds,
    ) as http:
        client = PrivateOpenAIClient(
            base_url=config.base_url,
            api_key=config.api_key.get_secret_value(),
            http_client=http,
            timeout=config.request_seconds,
            max_retries=0,
        )
        # SDK 3.42.1 eagerly probes platform.platform(), which can spawn uname.
        # No public opt-out exists; pin the SDK and regression-test this small shim.
        client._platform = "Unknown"
        async with client:
            yield OpenAIChatModel(
                config.model,
                provider=OpenAICompatibleProvider(
                    "private-tennis",
                    openai_client=client,
                    # Local aliases need not encode a vendor. Inline nested tool
                    # schemas without changing their local validation contract.
                    # Tool forcing is not a reliable decoder constraint on the
                    # evaluated local runtime. Let Pydantic AI validate/retry
                    # typed outputs instead of requiring server-side enforcement.
                    profile=ModelProfile(
                        json_schema_transformer=InlineDefsJsonSchemaTransformer,
                        supports_forced_tool_choice=False,
                    ),
                ),
            )
