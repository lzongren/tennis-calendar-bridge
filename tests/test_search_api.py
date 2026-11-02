from __future__ import annotations

import asyncio
import gzip
import json
from unittest.mock import AsyncMock

import httpx2 as httpx
import pytest
from pydantic import SecretStr

from tennis_overview.discovery_agent import (
    AgentDiscoveryError,
    SourceUnavailable,
    TransientSourceError,
)
from tennis_overview.models import AppConfig
from tennis_overview.public_documents import search_public_courses
from tennis_overview.search_api import (
    ENDPOINT,
    MAX_RESPONSE_BYTES,
    BraveSearch,
    configured_searcher,
)

KEY = "synthetic-search-api-secret"
URL = "https://club.example.org/classes"


def payload(hits=None):
    return {
        "web": {"results": hits if hits is not None else [{"url": URL, "title": "Adult clinics"}]}
    }


def run(handler, **kwargs):
    search = BraveSearch(SecretStr(KEY), httpx.MockTransport(handler))
    return asyncio.run(search("Example City", "3.0", 2026, kwargs.get("focus", "overview")))


def test_api_uses_only_fixed_endpoint_and_search_key(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid")
    monkeypatch.setenv("TENNIS_DISCOVERY_API_KEY", "different-model-secret")
    seen = []

    def handler(request):
        seen.append(request)
        assert str(request.url).split("?", 1)[0] == ENDPOINT
        assert request.method == "GET"
        assert dict(request.url.params) == {
            "q": "adult tennis group clinics near Example City",
            "count": "8",
            "result_filter": "web",
        }
        assert request.headers["x-subscription-token"] == KEY
        assert "authorization" not in request.headers
        assert "different-model-secret" not in str(request.headers)
        assert KEY not in str(request.url) and request.content == b""
        return httpx.Response(200, json=payload())

    assert run(handler) == [{"url": URL, "title": "Adult clinics"}]
    assert len(seen) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 429])
def test_redirects_auth_and_limits_do_not_retry_or_fallback(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "http://127.0.0.1/private", "Retry-After": "3600"},
            text=KEY,
        )

    with pytest.raises(SourceUnavailable) as caught:
        run(handler)
    assert not isinstance(caught.value, TransientSourceError)
    assert KEY not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [502, 503, 504])
def test_transient_api_errors_are_redacted_for_existing_bounded_retry(status):
    with pytest.raises(TransientSourceError) as caught:
        run(lambda request: httpx.Response(status, text=KEY))
    assert KEY not in str(caught.value)


def test_transport_timeout_is_redacted():
    def handler(request):
        raise httpx.ReadTimeout(KEY, request=request)

    with pytest.raises(TransientSourceError) as caught:
        run(handler)
    assert KEY not in str(caught.value)


def test_server_retry_after_does_not_trigger_immediate_retry():
    with pytest.raises(SourceUnavailable, match="retry later") as caught:
        run(lambda request: httpx.Response(503, headers={"Retry-After": "120"}))
    assert not isinstance(caught.value, TransientSourceError)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"web": {}},
        payload([{"title": 4, "url": URL}]),
        payload([{"title": "Private", "url": "http://127.0.0.1/"}]),
        payload([{"title": "Private", "url": "https://host.example.ts.net/"}]),
        payload([{"title": "Private", "url": "https://club.example.org/?token=private"}]),
        payload([{"title": "Too many", "url": URL}] * 9),
    ],
)
def test_malformed_or_unsafe_results_are_not_successful_empty_search(response):
    with pytest.raises(SourceUnavailable):
        run(lambda request: httpx.Response(200, json=response))


def test_explicit_empty_result_is_supported():
    assert run(lambda request: httpx.Response(200, json=payload([]))) == []


@pytest.mark.parametrize("encoded", [False, True])
def test_credential_echo_never_becomes_model_input(encoded):
    data = json.dumps(payload([{"title": KEY, "url": URL}]))
    if encoded:
        data = data.replace("synthetic", "\\u0073ynthetic")
    with pytest.raises(SourceUnavailable, match="Credential-bearing"):
        run(
            lambda request: httpx.Response(
                200, content=data, headers={"content-type": "application/json"}
            )
        )


@pytest.mark.parametrize("compressed", [False, True])
def test_response_limit_applies_to_decoded_bytes(compressed):
    body = b"x" * (MAX_RESPONSE_BYTES + 1)
    headers = {"content-type": "application/json"}
    if compressed:
        body = gzip.compress(body)
        headers["content-encoding"] = "gzip"
    with pytest.raises(SourceUnavailable, match="size limit"):
        run(lambda request: httpx.Response(200, content=body, headers=headers))


def test_html_response_rejected():
    with pytest.raises(SourceUnavailable, match="invalid response"):
        run(lambda request: httpx.Response(200, text="<html>challenge</html>"))


def test_cancellation_closes_response_and_propagates():
    class WaitingStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'{"web":'
            await asyncio.Future()

        async def aclose(self):
            self.closed = True

    async def scenario():
        stream = WaitingStream()

        async def handler(request):
            return httpx.Response(200, stream=stream, headers={"content-type": "application/json"})

        search = BraveSearch(SecretStr(KEY), httpx.MockTransport(handler))
        task = asyncio.create_task(search("Example City", "3.0", 2026, "overview"))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed

    asyncio.run(scenario())


def test_total_deadline_closes_a_stalled_response(monkeypatch):
    original_timeout = asyncio.timeout
    deadlines = []
    closed = []

    def bounded_timeout(seconds):
        deadlines.append(seconds)
        return original_timeout(0.01)

    class StalledStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"web":'
            await asyncio.Future()

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr("tennis_overview.search_api.asyncio.timeout", bounded_timeout)
    with pytest.raises(TransientSourceError):
        run(
            lambda request: httpx.Response(
                200, stream=StalledStream(), headers={"content-type": "application/json"}
            )
        )
    assert deadlines == [20]
    assert closed


def test_config_defaults_do_not_enable_api_or_expose_secrets():
    assert configured_searcher("public", KEY) is search_public_courses
    assert AppConfig().discovery_search_provider == "public"
    assert KEY not in repr(BraveSearch(SecretStr(KEY)))
    assert KEY not in repr(AppConfig(discovery_search_api_key=KEY))
    with pytest.raises(AgentDiscoveryError):
        configured_searcher("brave", "")
    with pytest.raises(AgentDiscoveryError) as caught:
        configured_searcher(KEY, KEY)
    assert KEY not in str(caught.value)


@pytest.mark.parametrize("key", ["", " space", "bad\r\nHeader: injected", "x" * 4097])
def test_bad_keys_rejected_before_network(key):
    with pytest.raises(AgentDiscoveryError):
        BraveSearch(SecretStr(key))


def test_query_cannot_contain_key():
    handler = AsyncMock()
    search = BraveSearch(SecretStr(KEY), httpx.MockTransport(handler))
    with pytest.raises(AgentDiscoveryError, match="Credential-bearing"):
        asyncio.run(search(KEY, "3.0", 2026, "overview"))
    handler.assert_not_called()
