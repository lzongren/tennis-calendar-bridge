from __future__ import annotations

import base64
import os

import pytest
from playwright.sync_api import expect, sync_playwright

from test_walls import wall_server as wall_server


# Deterministic local tile, never exercise public tile servers from CI.
TILE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL") or None)
        yield browser
        browser.close()


def open_page(browser, wall_server, width=1280, fail_tiles=False, fail_library=False):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.set_default_timeout(10000)
    page.route(
        "https://tile.openstreetmap.org/**",
        lambda route: (
            route.abort() if fail_tiles else route.fulfill(content_type="image/png", body=TILE)
        ),
    )
    if fail_library:
        page.route("**/vendor/leaflet/leaflet.js", lambda route: route.abort())
    page.goto(f"http://127.0.0.1:{wall_server.server_port}/tennis/walls")
    return page


@pytest.mark.parametrize("width", [1280, 390])
def test_map_selection_filters_and_navigation(browser, wall_server, width):
    page = open_page(browser, wall_server, width)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    expect(page.locator(".wall-pin")).to_have_count(3)
    expect(page.locator("#count")).to_have_text("3 of 3 mapped locations")
    expect(page.locator(".leaflet-tile-loaded").first).to_be_visible()
    expect(page.locator(".leaflet-control-attribution")).to_contain_text("OpenStreetMap")
    map_box = page.locator("#map").bounding_box()
    fit_box = page.locator("#fit").bounding_box()
    assert map_box and fit_box
    assert map_box["x"] <= fit_box["x"] <= map_box["x"] + map_box["width"] - fit_box["width"]
    assert map_box["y"] <= fit_box["y"] <= map_box["y"] + map_box["height"] - fit_box["height"]
    page.get_by_role("button", name="2 Marijane Hamann Park").click()
    expect(page.get_by_role("button", name="2 Marijane Hamann Park")).to_be_focused()
    expect(page.locator("#detail-marijane-hamann-park")).to_be_visible()
    expect(page.locator(".wall-pin.active")).to_have_attribute("title", "Marijane Hamann Park")
    expect(
        page.locator("#detail-marijane-hamann-park").get_by_role("link", name="Google Maps")
    ).to_have_attribute(
        "href",
        "https://www.google.com/maps/dir/?api=1&destination=Marijane%20Hamann%20Park%2C%202747%20Westfield%20Ave.%2C%20San%20Jose%2C%20CA",
    )
    page.get_by_role("button", name="Show all filtered locations").click()
    page.get_by_role("button", name="Backesto Park", exact=True).click()
    expect(page.locator("#detail-backesto-park")).to_be_visible()
    page.get_by_label("Evidence", exact=True).select_option("official_practice_wall")
    expect(page.locator(".wall-pin")).to_have_count(1)
    expect(page.locator("#results .location")).to_have_count(1)
    page.get_by_label("Search locations").fill("no such place")
    expect(page.locator(".wall-pin")).to_have_count(0)
    expect(page.locator("#results")).to_contain_text("No mapped locations match")
    page.get_by_label("Search locations").fill("")
    page.get_by_label("Evidence", exact=True).select_option("")
    page.get_by_label("City", exact=True).select_option("San Jose, CA")
    expect(page.locator(".wall-pin")).to_have_count(2)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
    page.close()


@pytest.mark.parametrize("failure", ["tiles", "library"])
def test_map_failures_keep_list_usable(browser, wall_server, failure):
    page = open_page(
        browser, wall_server, fail_tiles=failure == "tiles", fail_library=failure == "library"
    )
    expect(page.locator("#map-status")).to_be_visible()
    expect(page.locator("#results .location")).to_have_count(3)
    page.get_by_role("button", name="3 Backesto Park").click()
    expect(
        page.locator("#detail-backesto-park").get_by_role("link", name="Apple Maps")
    ).to_be_visible()
    page.close()


def test_catalog_failure_is_not_empty_success(browser, wall_server):
    page = browser.new_page()
    page.route("**/api/walls", lambda route: route.fulfill(status=503, body="{}"))
    page.goto(f"http://127.0.0.1:{wall_server.server_port}/tennis/walls")
    expect(page.locator("#error")).to_be_visible()
    expect(page.locator("#count")).to_have_text("Locations unavailable")
    page.close()


def test_empty_catalog_and_untrusted_text(browser, wall_server):
    from tennis_overview.walls import load_catalog

    page = browser.new_page()
    page.route("**/api/walls", lambda route: route.fulfill(json={"locations": [], "leads": []}))
    page.goto(f"http://127.0.0.1:{wall_server.server_port}/tennis/walls")
    expect(page.locator("#count")).to_have_text("0 of 0 mapped locations")
    expect(page.locator("#map")).to_have_text("No mapped locations in this catalog.")
    expect(page.locator("#error")).to_be_hidden()
    page.unroute("**/api/walls")
    catalog = load_catalog()
    catalog["locations"][0]["name"] = '<img src=x onerror="window.injected=true">'
    page.route("**/api/walls", lambda route: route.fulfill(json=catalog))
    page.route(
        "https://tile.openstreetmap.org/**",
        lambda route: route.fulfill(content_type="image/png", body=TILE),
    )
    page.reload()
    expect(page.locator("#results .location")).to_have_count(3)
    expect(page.locator("#results img")).to_have_count(0)
    assert page.evaluate("window.injected === undefined")
    page.close()
