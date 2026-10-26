from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.client import HTTPConnection

import pytest

from tennis_overview.models import AppConfig, Config
from tennis_overview.server import TennisServer, _dashboard_html


@pytest.fixture
def server(tmp_path, monkeypatch):
    # No search or inference during tests, even if discovery is enabled.
    monkeypatch.setattr("tennis_overview.discovery.DiscoveryService.start", lambda self: None)
    config = Config(
        AppConfig(
            database_path=str(tmp_path / "test.db"),
            admin_token="test-admin",
            base_path="/tennis",
            discovery_enabled=True,
            discovery_api_key="test-private-key",
        ),
        [],
        "test",
    )
    server = TennisServer(("127.0.0.1", 0), config)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)


def request(server, method, path, payload=None, auth=None):
    conn = HTTPConnection(*server.server_address, timeout=5)
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Bearer " + auth
    conn.request(method, path, json.dumps(payload) if payload is not None else None, headers)
    response = conn.getresponse()
    body = response.read().decode()
    conn.close()
    assert "test-private-key" not in body
    return response.status, body


def test_discovery_page_and_api_use_subpath_without_exposing_secrets(server):
    status, body = request(server, "GET", "/tennis/discover")
    assert status == 200
    assert 'const API="/tennis/api/discovery"' in body
    assert 'href="/tennis/"' in body
    assert "test-admin" not in body
    status, body = request(server, "GET", "/tennis/api/discovery")
    assert json.loads(body) == {"enabled": True, "locations": []}


def test_writes_require_bearer_auth_and_do_not_accept_query_tokens(server):
    payload = {"action": "add", "query": "Example City"}
    assert request(server, "POST", "/tennis/api/discovery", payload)[0] == 401
    assert request(server, "POST", "/tennis/api/discovery?token=test-admin", payload)[0] == 401
    status, body = request(server, "POST", "/tennis/api/discovery", payload, "test-admin")
    assert status == 202 and json.loads(body)["id"] > 0
    status, body = request(server, "GET", "/tennis/api/discovery")
    assert len(json.loads(body)["locations"]) == 1
    assert (
        request(
            server, "POST", "/tennis/api/discovery", {"action": "delete", "id": 1}, "test-admin"
        )[0]
        == 202
    )


def test_disabled_and_malformed_requests(server):
    assert request(server, "POST", "/tennis/api/discovery", [], "test-admin")[0] == 400
    server.config = replace(server.config, app=replace(server.config.app, discovery_enabled=False))
    assert (
        request(server, "POST", "/tennis/api/discovery", {"action": "add"}, "test-admin")[0] == 503
    )


def test_dashboard_only_shows_discovery_when_enabled(server):
    assert "/tennis/discover" in _dashboard_html(server.config, [], [])
    config = replace(server.config, app=replace(server.config.app, discovery_enabled=False))
    assert "/tennis/discover" not in _dashboard_html(config, [], [])
