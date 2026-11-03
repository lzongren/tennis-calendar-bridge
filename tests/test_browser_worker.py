from __future__ import annotations

import asyncio
import http.client
import json
import os
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx2 as httpx
import pytest
from pydantic import SecretStr, ValidationError

from tennis_overview.browser_client import BrowserClient
from tennis_overview.browser_documents import BrowserDocument
from tennis_overview.browser_network import Resource, get_resource
from tennis_overview.browser_protocol import BrowserReply, BrowserRequest, read_url
from tennis_overview.browser_runtime import BrowserReadError, read_browser
from tennis_overview.browser_worker import WorkerServer, execute, handler_class
from tennis_overview.discovery_agent import AgentDiscoveryError, DiscoveryRequest
from tennis_overview.discovery_sources import PublicSourceBackend
from tennis_overview.public_web import Page

KEY = "synthetic-browser-key-" + "x" * 32
BROWSER_CHANNEL = os.getenv("PLAYWRIGHT_CHANNEL") or "chromium"
URL = "https://app.courtreserve.com/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates"
HTML = (Path(__file__).parent / "fixtures/discovery_benchmark/courtreserve_detail.html").read_text()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/",
        "https://127.0.0.1/",
        "https://100.64.0.1/",
        "https://192.168.1.1/",
        "https://localhost/",
        "https://example.org:8443/",
        "https://device.example.ts.net/",
        "https://example.org/?token=secret",
        "https://example.org/delete/1",
        "https://example.org/?action=book",
        "https://app.courtreserve.com/Online/Account/LogIn/12345",
        URL + "&user=private",
        URL + "&tab=registration",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_read_rejects_private_action_and_credential_urls(url):
    with pytest.raises(ValueError):
        read_url(url)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "click", "selector": "Register"},
        {"operation": "read", "url": URL, "script": "secret"},
        {"operation": "search", "city": "City", "url": URL, "year": 2026},
        {"operation": "search", "city": "City"},
        {"operation": "search", "city": "City", "year": True},
    ],
)
def test_protocol_has_no_action_or_script_escape(payload):
    with pytest.raises(ValidationError):
        BrowserRequest.model_validate(payload)


@contextmanager
def server(tmp_path):
    instance = WorkerServer(("127.0.0.1", 0), handler_class(KEY, tmp_path))
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def send(port, method, path, body=None, key=KEY):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Authorization": "Bearer " + key} if key else {}
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_http_auth_validation_and_redaction(tmp_path, monkeypatch):
    execute = Mock(return_value={"error": "login_required"})
    monkeypatch.setattr("tennis_overview.browser_worker.execute", execute)
    with server(tmp_path) as port:
        assert send(port, "GET", "/healthz", key="")[0] == 401
        assert send(port, "GET", "/healthz", key="wrong")[0] == 401
        status, body = send(port, "GET", "/healthz")
        assert status == 200 and json.loads(body)["authentication"] == "not_verified"
        assert send(port, "POST", "/v1/click", "{}")[0] == 404
        assert (
            send(port, "POST", "/v1/read", '{"operation":"read","url":"file:///secret"}')[0] == 400
        )
        assert send(port, "POST", "/v1/read", "x" * 4097)[0] == 400
        status, body = send(port, "POST", "/v1/read", json.dumps({"operation": "read", "url": URL}))
        assert status == 503 and json.loads(body) == {"error": "login_required"}
    execute.assert_called_once()
    assert KEY.encode() not in body and URL.encode() not in body


def test_busy_request_does_not_launch_second_browser(tmp_path, monkeypatch):
    active, finish = threading.Event(), threading.Event()
    results = []

    def execute(*args):
        active.set()
        finish.wait(3)
        return {"error": "source_unavailable"}

    monkeypatch.setattr("tennis_overview.browser_worker.execute", execute)
    with server(tmp_path) as port:
        body = json.dumps({"operation": "read", "url": URL})
        first = threading.Thread(
            target=lambda: results.append(send(port, "POST", "/v1/read", body))
        )
        first.start()
        assert active.wait(2)
        assert send(port, "POST", "/v1/read", body) == (503, b'{"error": "busy"}')
        finish.set()
        first.join()
    assert results[0][0] == 503


def test_browser_client_keeps_credentials_at_fixed_endpoint():
    seen = []

    def respond(request):
        seen.append(request)
        assert str(request.url) == "https://worker.example.ts.net:8444/v1/read"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert KEY not in request.content.decode()
        reply = BrowserReply(
            kind="browser", url=URL, html=HTML, captured_at=datetime.now(timezone.utc)
        )
        return httpx.Response(200, json=reply.model_dump(mode="json"))

    client = BrowserClient(
        "https://worker.example.ts.net:8444", SecretStr(KEY), httpx.MockTransport(respond)
    )
    result = asyncio.run(client.read(URL))
    assert isinstance(result, BrowserDocument)
    assert len(seen) == 1 and KEY not in repr(client)


@pytest.mark.parametrize(
    "status,payload",
    [
        (302, {"error": "redirect"}),
        (401, {"error": "unauthorized"}),
        (503, {"error": "login_required"}),
        (503, {"error": "membership_required"}),
        (503, {"error": "browser_attention_required"}),
        (200, {"kind": "invalid"}),
    ],
)
def test_worker_failure_never_becomes_empty_success(status, payload):
    client = BrowserClient(
        "http://127.0.0.1:8134",
        SecretStr(KEY),
        httpx.MockTransport(
            lambda request: httpx.Response(
                status, json=payload, headers={"location": "https://elsewhere.example"}
            )
        ),
    )
    with pytest.raises(AgentDiscoveryError):
        asyncio.run(client.read(URL))


def test_worker_response_is_bounded():
    client = BrowserClient(
        "http://127.0.0.1:8134",
        SecretStr(KEY),
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 192001)),
    )
    with pytest.raises(AgentDiscoveryError, match="size limit"):
        asyncio.run(client.read(URL))


def test_existing_public_reader_is_preserved(monkeypatch):
    original = Page(
        "https://club.example/schedule.pdf",
        "Schedule",
        "published course facts with enough words",
        (),
    )
    reader = AsyncMock(return_value=original)
    monkeypatch.setattr("tennis_overview.browser_client.read_public_document", reader)
    client = BrowserClient(
        "http://127.0.0.1:8134",
        SecretStr(KEY),
        httpx.MockTransport(lambda request: pytest.fail("unnecessary browser call")),
    )
    assert asyncio.run(client.read(original.url)) is original


def test_worker_offline_aborts_even_with_configured_public_sources():
    from datetime import date

    client = BrowserClient(
        "http://127.0.0.1:8134",
        SecretStr(KEY),
        httpx.MockTransport(lambda request: httpx.Response(503, json={"error": "unavailable"})),
    )
    backend = PublicSourceBackend(Mock(), ("https://club.example/",), searcher=client.search)
    request = DiscoveryRequest(
        city="Example City", level="3.0", timezone="America/Los_Angeles", start=date(2026, 11, 2)
    )
    with pytest.raises(AgentDiscoveryError, match="previous results retained"):
        asyncio.run(backend.search(request.city, request))
    assert not backend.sources


def test_proxy_peer_guard_requires_exact_authorized_address(tmp_path):
    from http.server import ThreadingHTTPServer

    instance = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler_class(KEY, tmp_path, frozenset({"100.64.0.2"}))
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        for value, expected in [
            ("100.64.0.2", 200),
            ("100.64.0.3", 403),
            ("100.64.0.2, 100.64.0.3", 403),
        ]:
            conn = http.client.HTTPConnection("127.0.0.1", instance.server_address[1])
            conn.request(
                "GET",
                "/healthz",
                headers={"Authorization": "Bearer " + KEY, "X-Forwarded-For": value},
            )
            response = conn.getresponse()
            assert response.status == expected
            response.read()
            conn.close()
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def test_dns_relay_pins_ip_and_does_not_forward_third_party_cookies(monkeypatch):
    sock, conn, response = Mock(), Mock(), Mock()
    response.status, response.read.return_value = 200, b"ok"
    response.getheaders.return_value = [
        ("Content-Type", "text/html"),
        ("Set-Cookie", "private=secret"),
    ]
    response.headers.get_all.return_value = ["private=secret; Path=/; Secure"]
    conn.getresponse.return_value = response
    dial = Mock(return_value=sock)
    monkeypatch.setattr(
        "tennis_overview.browser_network.public_addresses", lambda host, port: ["93.184.216.34"]
    )
    monkeypatch.setattr("tennis_overview.browser_network.socket.create_connection", dial)
    monkeypatch.setattr(
        "tennis_overview.browser_network.http.client.HTTPConnection", lambda *args, **kwargs: conn
    )
    monkeypatch.setattr(
        "tennis_overview.browser_network.ssl.create_default_context",
        lambda: Mock(wrap_socket=Mock(return_value=sock)),
    )
    result = get_resource(
        "https://cdn.example/app.js",
        {"cookie": "private", "authorization": KEY, "x-requested-with": "XMLHttpRequest"},
        portal_session=True,
    )
    dial.assert_called_once_with(("93.184.216.34", 443), timeout=8)
    assert "cookie" not in conn.request.call_args.kwargs["headers"]
    assert "x-requested-with" not in conn.request.call_args.kwargs["headers"]
    assert KEY not in str(conn.request.call_args)
    assert conn.request.call_args.kwargs["headers"]["User-Agent"] == (
        "TennisCalendarBridge/0.2 (+public-course-discovery)"
    )
    assert "set-cookie" not in result.headers
    result = get_resource(
        URL,
        {
            "cookie": "portal-session",
            "x-requested-with": "XMLHttpRequest",
            "reactsubmit": "true",
            "uiculture": "en-US\r\nx-extra: reject",
            "authorization": KEY,
            "requestdata": "not-forwarded",
        },
        portal_session=True,
    )
    assert conn.request.call_args.kwargs["headers"]["cookie"] == "portal-session"
    assert conn.request.call_args.kwargs["headers"]["x-requested-with"] == "XMLHttpRequest"
    assert conn.request.call_args.kwargs["headers"]["reactsubmit"] == "true"
    assert (
        not {"authorization", "requestdata", "uiculture"}
        & conn.request.call_args.kwargs["headers"].keys()
    )
    assert result.headers["set-cookie"] == "private=secret; Path=/; Secure"


def test_real_browser_sanitizes_anonymous_course_and_blocks_writes(tmp_path, monkeypatch):
    calls = []
    page = (
        HTML
        + """<nav>synthetic-account-email</nav><button>Unsubscribe</button>
    <script>fetch('/Online/Delete/1', {method:'POST'}).catch(()=>{});
    fetch('https://127.0.0.1/secret').catch(()=>{});
    fetch('https://app.courtreserve.com/private', {method:'POST'}).catch(()=>{});
    new WebSocket('wss://app.courtreserve.com/socket');</script>"""
    )

    def fetch(url, headers, *, portal_session):
        calls.append(url)
        return Resource(200, {"content-type": "text/html"}, page.encode())

    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    result = asyncio.run(
        read_browser(
            BrowserRequest(operation="read", url=URL), tmp_path / "profile", channel=BROWSER_CHANNEL
        )
    )
    assert result.kind == "browser"
    assert "synthetic-account" not in result.html and "Unsubscribe" not in result.html
    assert "$54.00" in result.html and "Waitlisted" in result.html
    assert calls == [URL]
    assert not (tmp_path / "profile").exists()


def test_real_browser_login_redirect_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(
            302, {"location": "https://app.courtreserve.com/Online/Account/LogIn/12345"}, b""
        ),
    )
    with pytest.raises(BrowserReadError, match="login_required"):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL),
                tmp_path / "profile",
                channel=BROWSER_CHANNEL,
            )
        )


def test_real_browser_403_requires_attention_not_empty_courses(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    local_state = profile / "Local State"
    local_state.write_text("{}")
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(403, {"content-type": "text/html"}, b"<h1>Forbidden</h1>"),
    )
    with pytest.raises(BrowserReadError, match="browser_attention_required"):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL),
                tmp_path / "profile",
                channel=BROWSER_CHANNEL,
            )
        )
    assert local_state.read_text() == "{}"
    assert list(profile.iterdir()) == [local_state]
    assert not list(tmp_path.glob("profile-read-*"))


def test_membership_error_is_preserved_without_a_login_redirect(tmp_path, monkeypatch):
    async def read(context, request, network):
        assert not network.login_required
        network.membership_required = True
        raise BrowserReadError("source_unavailable")

    monkeypatch.setattr("tennis_overview.browser_runtime._read", read)
    with pytest.raises(BrowserReadError, match="^membership_required$"):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL),
                tmp_path / "unused",
                channel=BROWSER_CHANNEL,
            )
        )
    assert not (tmp_path / "unused").exists()


def test_authenticated_profile_preserves_native_keychain(tmp_path, monkeypatch):
    from playwright.async_api import BrowserType
    from tennis_overview.browser_runtime import _read_browser

    observed = []
    original = BrowserType.launch_persistent_context

    async def launch(self, *args, **kwargs):
        observed.append(kwargs.get("ignore_default_args"))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(BrowserType, "launch_persistent_context", launch)
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(200, {"content-type": "text/html"}, HTML.encode()),
    )
    asyncio.run(
        _read_browser(
            BrowserRequest(operation="read", url=URL),
            tmp_path / "profile",
            channel=BROWSER_CHANNEL,
            timeout_seconds=10,
        )
    )
    expected = ["--use-mock-keychain", "--password-store=basic"] if sys.platform == "darwin" else []
    assert observed == [expected]


def test_anonymous_read_does_not_touch_even_an_active_login_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("human-browser")
    snapshot = Mock(side_effect=AssertionError("Anonymous read opened login state"))
    monkeypatch.setattr("tennis_overview.browser_runtime.profile_snapshot", snapshot)
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(200, {"content-type": "text/html"}, HTML.encode()),
    )
    assert (
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL), profile, channel=BROWSER_CHANNEL
            )
        ).kind
        == "browser"
    )
    snapshot.assert_not_called()


@pytest.mark.parametrize("code", ["browser_attention_required", "source_unavailable"])
def test_no_login_escalation_for_403_or_parser_errors(tmp_path, monkeypatch, code):
    reader = AsyncMock(side_effect=BrowserReadError(code))
    snapshot = Mock(side_effect=AssertionError("Unexpected login escalation"))
    monkeypatch.setattr("tennis_overview.browser_runtime._read_browser", reader)
    monkeypatch.setattr("tennis_overview.browser_runtime.profile_snapshot", snapshot)
    with pytest.raises(BrowserReadError, match=code):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL), tmp_path, allow_authenticated=True
            )
        )
    assert reader.call_args.args[1] is None
    snapshot.assert_not_called()


def test_login_fallback_requires_explicit_permission(tmp_path, monkeypatch):
    reader = AsyncMock(side_effect=BrowserReadError("login_required"))
    snapshot = Mock(side_effect=AssertionError("Unexpected login escalation"))
    monkeypatch.setattr("tennis_overview.browser_runtime._read_browser", reader)
    monkeypatch.setattr("tennis_overview.browser_runtime.profile_snapshot", snapshot)
    with pytest.raises(BrowserReadError, match="login_required"):
        asyncio.run(read_browser(BrowserRequest(operation="read", url=URL), tmp_path))
    snapshot.assert_not_called()


def test_allowed_login_fallback_happens_only_after_anonymous_attempt(tmp_path, monkeypatch):
    reply = BrowserReply(kind="browser", url=URL, html=HTML)
    reader = AsyncMock(side_effect=[BrowserReadError("login_required"), reply])
    monkeypatch.setattr("tennis_overview.browser_runtime._read_browser", reader)
    result = asyncio.run(
        read_browser(
            BrowserRequest(operation="read", url=URL),
            tmp_path / "profile",
            allow_authenticated=True,
        )
    )
    assert result is reply
    assert reader.call_args_list[0].args[1] is None
    copy = reader.call_args_list[1].args[1]
    assert copy != tmp_path / "profile" and not copy.exists()


def test_anonymous_session_cookies_survive_bootstrap_only_within_job(tmp_path, monkeypatch):
    from urllib.parse import quote

    portal = "https://app.courtreserve.com/Online/Portal/Index/12345?returnUrl=" + quote(
        "/Online/Events/Details/12345/EXAMPLE?resId=123&tab=dates", safe=""
    )
    sessions = []

    def fetch(url, headers, *, portal_session):
        cookie = headers.get("cookie", "")
        if url == URL and "session=anonymous" not in cookie:
            sessions.append("fresh")
            return Resource(302, {"location": portal}, b"")
        if url == portal:
            return Resource(
                302,
                {
                    "location": URL,
                    "set-cookie": "session=anonymous; Path=/; Secure; HttpOnly\norg=example; Path=/; Secure",
                },
                b"",
            )
        assert "session=anonymous" in cookie and "org=example" in cookie
        return Resource(200, {"content-type": "text/html"}, HTML.encode())

    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    for _ in range(2):
        assert (
            asyncio.run(
                read_browser(
                    BrowserRequest(operation="read", url=URL),
                    tmp_path / "unused",
                    channel=BROWSER_CHANNEL,
                )
            ).kind
            == "browser"
        )
    assert sessions == ["fresh", "fresh"]
    assert not (tmp_path / "unused").exists()


@pytest.mark.benchmark
def test_anonymous_real_case_replay_preserves_facts_without_inventing_waitlist(
    tmp_path, monkeypatch
):
    from datetime import date
    from tennis_overview.courtreserve_discovery import courtreserve_observation

    fixture = (
        Path(__file__).parent / "fixtures/discovery_benchmark/courtreserve_anonymous_detail.html"
    ).read_text()
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(200, {"content-type": "text/html"}, fixture.encode()),
    )
    reply = asyncio.run(
        read_browser(
            BrowserRequest(operation="read", url=URL),
            tmp_path / "never-read",
            channel=BROWSER_CHANNEL,
        )
    )
    captured = datetime(2026, 11, 3, 6, 16, tzinfo=timezone.utc)
    result = courtreserve_observation(
        BrowserDocument(reply.url, reply.title, reply.html, captured),
        DiscoveryRequest(
            city="Example City",
            level="3.0",
            timezone="America/Los_Angeles",
            start=date(2026, 11, 1),
        ),
        now=captured,
    )
    assert result.course is not None
    session = result.course.sessions[0]
    assert session.starts_at.isoformat() == "2026-11-02T18:30:00-08:00"
    assert session.ends_at.isoformat() == "2026-11-02T19:50:00-08:00"
    assert result.course.price == "$54.00"
    assert result.availability == "full" and result.waitlist_count is None
    assert result.partial and len(result.course.sessions) == 1
    assert not (tmp_path / "never-read").exists()


@pytest.mark.parametrize(
    "target",
    [
        "https://other.example/",
        "//other.example/",
        "/Online/Events/List/999",
        "/Online/Account/LogIn/12345",
        "/Online/Events/Details/12345/EXAMPLE?token=secret",
        "/Online/Portal/Index/12345?returnUrl=recursive",
        "/Online/Events/List/12345?returnUrl=recursive",
    ],
)
def test_bootstrap_rejects_unsafe_return_destinations(target):
    from urllib.parse import quote

    with pytest.raises(ValueError):
        read_url(
            "https://app.courtreserve.com/Online/Portal/Index/12345?returnUrl="
            + quote(target, safe="")
        )


def test_real_public_browser_uses_no_portal_profile(tmp_path, monkeypatch):
    calls = []

    def fetch(url, headers, *, portal_session):
        calls.append(portal_session)
        return Resource(
            200,
            {"content-type": "text/html"},
            b"<h1>Adult Tennis</h1><p>Published adult group course information available here.</p>",
        )

    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    result = asyncio.run(
        read_browser(
            BrowserRequest(operation="read", url="https://club.example.org/classes"),
            tmp_path / "unused-profile",
            channel=BROWSER_CHANNEL,
        )
    )
    assert result.kind == "page" and "Adult Tennis" in result.text
    assert not (tmp_path / "unused-profile").exists()
    assert calls and not any(calls)


def test_child_timeout_terminates_and_reaps_without_inheriting_keys(tmp_path, monkeypatch):
    import subprocess
    from tennis_overview.browser_worker import ACTIVE

    process = Mock(pid=12345)
    process.poll.return_value = None
    process.communicate.side_effect = subprocess.TimeoutExpired("browser", 50)
    process.wait.side_effect = lambda **kwargs: setattr(process.poll, "return_value", 0)
    spawn = Mock(return_value=process)
    kill = Mock()
    monkeypatch.setenv("TENNIS_DISCOVERY_BROWSER_KEY", KEY)
    monkeypatch.setenv("TENNIS_DISCOVERY_API_KEY", "private-model-key")
    monkeypatch.setattr("tennis_overview.browser_worker.subprocess.Popen", spawn)
    monkeypatch.setattr("tennis_overview.browser_worker.os.killpg", kill)
    assert execute(BrowserRequest(operation="read", url=URL), tmp_path) == {"error": "timeout"}
    assert KEY not in str(spawn.call_args) and "private-model-key" not in str(spawn.call_args)
    assert spawn.call_args.kwargs["start_new_session"]
    assert kill.call_count == 1 and process.wait.call_count == 1
    assert not ACTIVE
    assert not list(tmp_path.glob("browser-job-*"))


def test_real_browser_timeout_releases_dedicated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(200, {"content-type": "text/html"}, b"<h1>Loading</h1>"),
    )
    request = BrowserRequest(operation="read", url=URL)
    profile = tmp_path / "profile"
    with pytest.raises(BrowserReadError):
        asyncio.run(read_browser(request, profile, channel=BROWSER_CHANNEL, timeout_seconds=0.05))
    monkeypatch.setattr(
        "tennis_overview.browser_runtime.get_resource",
        lambda *args, **kwargs: Resource(200, {"content-type": "text/html"}, HTML.encode()),
    )
    assert asyncio.run(read_browser(request, profile, channel=BROWSER_CHANNEL)).kind == "browser"
