from unittest.mock import MagicMock

import pytest

from tennis_overview.public_web import fetch_page, MAX_BYTES


def mocked_connection(monkeypatch, status=200, headers=None, body=b"<h1>Adult Clinic</h1>"):
    headers = headers or {"Content-Type": "text/html"}
    connection = MagicMock()
    response = connection.getresponse.return_value
    response.status = status
    response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
    response.read.return_value = body
    monkeypatch.setattr(
        "tennis_overview.public_web.http.client.HTTPConnection", lambda *a, **k: connection
    )
    monkeypatch.setattr("tennis_overview.public_web.public_addresses", lambda *a: ["93.184.216.34"])
    connect = MagicMock()
    monkeypatch.setattr("tennis_overview.public_web.socket.create_connection", connect)
    monkeypatch.setattr("tennis_overview.public_web.ssl.create_default_context", MagicMock())
    return connection, connect


def test_public_fetch_pins_address_and_keeps_canonical_host(monkeypatch):
    connection, connect = mocked_connection(
        monkeypatch, body=b"<h1>Adult Clinic</h1><script>private-script</script><p>Tuesday</p>"
    )
    page = fetch_page("https://club.example.org/classes")
    assert connect.call_args.args[0] == ("93.184.216.34", 443)
    assert connection.request.call_args.kwargs["headers"]["Host"] == "club.example.org"
    assert "Authorization" not in connection.request.call_args.kwargs["headers"]
    assert page.heading == "Adult Clinic" and "private-script" not in page.text


def test_redirect_cannot_reach_private_service(monkeypatch):
    connection, connect = mocked_connection(
        monkeypatch, status=302, headers={"Location": "http://127.0.0.1:8123/"}
    )
    with pytest.raises(ValueError):
        fetch_page("https://club.example.org/")
    assert connect.call_count == 1
    assert connection.close.called


def test_large_page_rejected(monkeypatch):
    mocked_connection(monkeypatch, body=b"x" * (MAX_BYTES + 1))
    with pytest.raises(ValueError, match="too large"):
        fetch_page("https://club.example.org/")
