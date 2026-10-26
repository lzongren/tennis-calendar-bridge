from __future__ import annotations

import copy
import json
import threading
from dataclasses import replace
from http.client import HTTPConnection
from hashlib import sha256

import pytest

from tennis_overview.config import load_config
from tennis_overview.models import AppConfig, Config
from tennis_overview.server import TennisServer, _dashboard_html
from tennis_overview.walls import load_catalog, validate_catalog
from tennis_overview.walls_ui import ASSETS, wall_asset, walls_html


@pytest.mark.parametrize(
    "name,digest",
    [
        ("leaflet.js", "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"),
        ("leaflet.css", "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6"),
    ],
)
def test_vendored_leaflet_matches_upstream_integrity(name, digest):
    asset = wall_asset("/wall-assets/vendor/leaflet/" + name)
    assert asset is not None
    assert sha256(asset[0]).hexdigest() == digest


@pytest.fixture
def wall_server(tmp_path, monkeypatch):
    monkeypatch.setattr("tennis_overview.discovery.DiscoveryService.start", lambda self: None)
    config = Config(
        AppConfig(database_path=str(tmp_path / "test.db"), base_path="/tennis"), [], "test"
    )
    server = TennisServer(("127.0.0.1", 0), config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def get(server, path, method="GET"):
    conn = HTTPConnection(*server.server_address, timeout=5)
    conn.request(method, path)
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return result


def test_seed_evidence_and_unmapped_leads():
    data = load_catalog()
    assert len(data["locations"]) == 3
    assert len(data["leads"]) == 4
    by_id = {row["id"]: row for row in data["locations"]}
    assert by_id["campbell-community-center"]["status"] == "official_practice_wall"
    assert by_id["marijane-hamann-park"]["status"] == "community_report"
    assert by_id["backesto-park"]["status"] == "handball_wall"
    assert all(row["precision"] == "venue" for row in by_id.values())
    assert all("lat" not in row for row in data["leads"])


@pytest.mark.parametrize(
    "key,value",
    [
        ("lat", 91),
        ("lat", float("nan")),
        ("lon", float("inf")),
        ("lon", -181),
        ("lat", True),
        ("lat", 10**400),
        ("status", "confirmed"),
        ("precision", "exact-enough"),
        ("coordinate_source", "javascript:alert(1)"),
        ("coordinate_source", "https://user:secret@example.com"),
        ("sources", []),
        ("sources", [None]),
        ("reviewed_on", "yesterday"),
        ("name", ""),
    ],
)
def test_rejects_bad_catalog_values(key, value):
    raw = load_catalog()
    raw["locations"][0][key] = value
    with pytest.raises(ValueError):
        validate_catalog(raw)


def test_duplicates_and_unrecognized_fields():
    raw = load_catalog()
    raw["locations"].append(copy.deepcopy(raw["locations"][0]))
    with pytest.raises(ValueError):
        validate_catalog(raw)
    raw = load_catalog()
    raw["secret"] = "not-for-output"
    raw["locations"][0]["operator_notes"] = "not-for-output"
    raw["leads"][0]["lat"] = 1
    assert "not-for-output" not in json.dumps(validate_catalog(raw))
    assert "lat" not in validate_catalog(raw)["leads"][0]


def test_custom_catalog_config_and_empty_catalog(tmp_path, monkeypatch):
    path = tmp_path / "walls.json"
    path.write_text('{"version":1,"locations":[],"leads":[]}')
    monkeypatch.setenv("TENNIS_WALLS_FILE", str(path))
    assert load_config("config/clubs.example.toml").app.walls_catalog_path == str(path)
    assert load_catalog(str(path))["locations"] == []


@pytest.mark.parametrize("base", ["", "/tennis", "/nested/tennis"])
def test_base_path_and_secret_free_page(base):
    config = Config(AppConfig(base_path=base, admin_token="test-private-admin"), [], "test")
    page = walls_html(config)
    assert f'data-api="{base}/api/walls"' in page
    assert f'src="{base}/wall-assets/walls.js?v=' in page
    assert "__SCRIPT_VERSION__" not in page and "__STYLE_VERSION__" not in page
    assert "test-private-admin" not in page
    assert f'href="{base}/walls"' in _dashboard_html(config, [], [])


def test_routes_head_and_asset_allowlist(wall_server):
    for path in ("/tennis/walls", "/tennis/walls/", "/tennis/api/walls"):
        status, headers, body = get(wall_server, path)
        assert status == 200
        head_status, head_headers, head = get(wall_server, path, "HEAD")
        assert head_status == 200 and head == b""
        assert int(head_headers["Content-Length"]) == len(body)
    for name, mime in ASSETS.items():
        status, headers, body = get(wall_server, "/tennis/wall-assets/" + name)
        assert status == 200 and body
        assert headers["Content-Type"] == mime
    for path in ("../data/walls.json", "walls.html", "vendor/leaflet/LICENSE", "%2e%2e/.env"):
        assert wall_asset("/wall-assets/" + path) is None
        assert get(wall_server, "/tennis/wall-assets/" + path)[0] == 404


def test_bad_custom_catalog_does_not_leak_or_fallback(wall_server, tmp_path):
    secret = tmp_path / "private-file-name.json"
    secret.write_text("PRIVATE_CATALOG_CONTENT")
    wall_server.config = replace(
        wall_server.config, app=replace(wall_server.config.app, walls_catalog_path=str(secret))
    )
    status, _, body = get(wall_server, "/tennis/api/walls")
    assert status == 503
    assert json.loads(body) == {"error": "Wall catalog unavailable."}
    assert str(secret).encode() not in body and b"PRIVATE_CATALOG_CONTENT" not in body


def test_extreme_coordinate_returns_catalog_error(wall_server, tmp_path):
    path = tmp_path / "walls.json"
    catalog = load_catalog()
    catalog["locations"][0]["lat"] = 10**400
    path.write_text(json.dumps(catalog))
    wall_server.config = replace(
        wall_server.config, app=replace(wall_server.config.app, walls_catalog_path=str(path))
    )
    status, _, body = get(wall_server, "/tennis/api/walls")
    assert status == 503
    assert json.loads(body) == {"error": "Wall catalog unavailable."}
