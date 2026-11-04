from __future__ import annotations

import asyncio
import os

import pytest

from tennis_overview.browser_network import Resource, get_resource
from tennis_overview.browser_protocol import BrowserRequest, read_url
from tennis_overview.browser_runtime import BrowserReadError, read_browser

ORIGIN = "https://playtennis.usta.com"
PATH = "/example-academy/Coaching/Book/11111111-2222-4333-8444-555555555555"
URL = ORIGIN + PATH
COURSE = URL.replace("/Book/", "/Course/")


@pytest.mark.parametrize("document", [True, False])
def test_usta_public_course_entry_is_readable(document):
    assert read_url(URL, document=document) == URL
    assert BrowserRequest(operation="read", url=URL).url == URL


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example" + PATH,
        "https://playtennis.usta.com.other.example" + PATH,
        URL + "/checkout",
        URL + "/confirm",
        URL + "?action=book",
        URL + "?confirm=true",
        URL + "?session=1",
        URL.replace("11111111", "not-an-id"),
        URL.replace("example-academy", "delete"),
        URL.replace("example-academy", "example/checkout"),
        URL.replace("11111111", "%31111111"),
    ],
)
def test_usta_read_exception_does_not_admit_actions_or_other_hosts(url):
    with pytest.raises(ValueError):
        read_url(url)


def test_usta_post_is_rejected_before_network(monkeypatch):
    monkeypatch.setattr(
        "tennis_overview.browser_network.public_addresses",
        lambda *_: pytest.fail("A write must not reach DNS or the network"),
    )
    with pytest.raises(ValueError):
        get_resource(URL, {}, portal_session=False, post_body=b"{}")


@pytest.mark.benchmark
@pytest.mark.parametrize("legacy_entry", [False, True])
def test_browser_follows_usta_public_redirect_but_never_submits(
    legacy_entry, tmp_path, monkeypatch
):
    calls = []
    entry = URL.replace("/Coaching/Book/", "/BookCourse/") if legacy_entry else URL

    def fetch(url, headers, *, portal_session):
        assert not portal_session
        calls.append(url)
        if legacy_entry and url == entry:
            return Resource(302, {"location": URL}, b"")
        if url == URL:
            return Resource(302, {"location": COURSE}, b"")
        assert url == COURSE
        return Resource(
            200,
            {"content-type": "text/html"},
            (
                "<h1>Adult tennis clinic</h1><p>Published Tuesday evening course details.</p>"
                f"<script>fetch('{URL}', {{method:'POST', body:'{{}}'}}).catch(()=>{{}});"
                "fetch('/checkout').catch(()=>{});</script>"
            ).encode(),
        )

    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    result = asyncio.run(
        read_browser(
            BrowserRequest(operation="read", url=entry),
            tmp_path / "unused-profile",
            channel=os.getenv("PLAYWRIGHT_CHANNEL", "chromium"),
        )
    )
    assert result.url == COURSE
    assert "Adult tennis clinic" in result.text
    assert calls == ([entry, URL, COURSE] if legacy_entry else [URL, COURSE])
    assert not (tmp_path / "unused-profile").exists()


@pytest.mark.benchmark
@pytest.mark.parametrize("target", [ORIGIN + "/checkout", "https://127.0.0.1/private"])
def test_usta_redirect_cannot_escape_read_policy(target, tmp_path, monkeypatch):
    calls = []

    def fetch(url, headers, *, portal_session):
        calls.append(url)
        return Resource(302, {"location": target}, b"")

    monkeypatch.setattr("tennis_overview.browser_runtime.get_resource", fetch)
    with pytest.raises(BrowserReadError):
        asyncio.run(
            read_browser(
                BrowserRequest(operation="read", url=URL),
                tmp_path / "unused-profile",
                channel=os.getenv("PLAYWRIGHT_CHANNEL", "chromium"),
            )
        )
    assert calls == [URL]
