from unittest.mock import MagicMock

import pytest

from tennis_overview.public_web import fetch_page, page_from_html, MAX_BYTES


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


@pytest.mark.benchmark
def test_course_links_survive_large_site_navigation():
    # Regression from the real multi-city operator page: its header exhausted
    # the old 150-link budget before the body schedule and registration links.
    navigation = "".join(f'<a href="/city-{i}/">City {i}</a>' for i in range(180))
    body = f"""<title>Example Tennis</title><header><nav>{navigation}</nav></header>
    <h1>Example City</h1><a href="/uploads/fall-clinics.pdf">Fall Clinics</a>
    <a href="https://booking.example.org/portal/1">Register</a>
    <footer><a href="/newsletter">Newsletter</a></footer>"""
    page = page_from_html("https://club.example.org/", body.encode())
    assert page.links == (
        "https://club.example.org/uploads/fall-clinics.pdf",
        "https://booking.example.org/portal/1",
    )
    assert [link.title for link in page.link_details] == ["Fall Clinics", "Register"]
    assert "City 179" not in page.text and "Newsletter" not in page.text


def test_link_metadata_is_bounded_filtered_and_deduplicated():
    body = """<div role="navigation"><a href="/nav">Navigation</a></div>
    <a href="https://private.example.ts.net/">Private</a>
    <a href="/classes#one">Course</a><a href="/classes#two">Duplicate</a>"""
    body += "".join(f'<a href="/course-{i}">{"x" * 300}</a>' for i in range(200))
    page = page_from_html("https://club.example.org/", body.encode())
    assert len(page.links) == len(page.link_details) == 150
    assert page.link_details[0].title == "Course"
    assert all(len(link.title) <= 240 for link in page.link_details)


@pytest.mark.parametrize("content_type", ["text/html-malware", "application/pdf", "image/png"])
def test_legacy_reader_rejects_unexpected_mime(monkeypatch, content_type):
    mocked_connection(monkeypatch, headers={"Content-Type": content_type})
    with pytest.raises(ValueError, match="content type"):
        fetch_page("https://club.example.org/")
