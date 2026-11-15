from pathlib import Path

import pytest
from ddgs.exceptions import DDGSException

from tennis_overview.search_layouts import LayoutAwareYahoo, search_index, venue_index_page


@pytest.mark.benchmark
def test_alternate_search_layout_retains_results_and_decodes_redirects():
    html = (Path(__file__).parent / "fixtures/discovery_benchmark/search_layouts.html").read_text()
    engine = LayoutAwareYahoo(timeout=12)
    rows = engine.post_extract_results(engine.extract_results(html))
    assert [(row.title, row.href) for row in rows] == [
        ("Adult clinics", "https://club.example.org/adults"),
        ("Adult schedule", "https://academy.example.org/schedule.pdf"),
    ]


def test_classic_search_layout_preserves_snippet():
    html = '<div class="relsrch"><div class="compTitle"><a href="https://club.example.org/"><h3>Adult clinics</h3></a></div><div class="compText">Group lessons</div></div>'
    rows = LayoutAwareYahoo(timeout=12).extract_results(html)
    assert len(rows) == 1
    assert rows[0].href == "https://club.example.org/"
    assert rows[0].body == "Group lessons"


@pytest.mark.benchmark
def test_malformed_result_does_not_join_neighboring_titles_or_urls() -> None:
    html = (
        Path(__file__).parent / "fixtures/discovery_benchmark/search_result_ownership.html"
    ).read_text()
    rows = LayoutAwareYahoo(timeout=12).extract_results(html)
    assert [(row.title, row.href, row.body) for row in rows] == [
        (
            "Adult tennis directory",
            "https://directory.example.org/adults",
            "Group lessons near Example City",
        ),
        ("Adult clinics", "https://club.example.org/classes", "Weekly classes"),
        ("Adult schedule", "https://academy.example.org/schedule", ""),
    ]


@pytest.mark.benchmark
def test_mixed_layout_keeps_each_result_in_document_order() -> None:
    html = """<section class="algo"><div class="compTitle">
      <h3><a href="https://one.example.org/">First</a></h3></div></section>
      <div class="relsrch"><div class="compTitle">
      <a href="https://two.example.org/"><h3>Second</h3></a></div></div>"""
    rows = LayoutAwareYahoo(timeout=12).extract_results(html)
    assert [(row.title, row.href) for row in rows] == [
        ("First", "https://one.example.org/"),
        ("Second", "https://two.example.org/"),
    ]


@pytest.mark.benchmark
def test_result_cannot_borrow_nested_heading_or_outer_anchor() -> None:
    html = """<div class="relsrch"><section class="algo"><div class="compTitle">
      <h3><a href="https://club.example.org/">Owned</a></h3></div></section></div>
      <a href="https://unrelated.example.org/"><div class="relsrch">
      <div class="compTitle"><h3>Unlinked heading</h3></div></div></a>"""
    rows = LayoutAwareYahoo(timeout=12).extract_results(html)
    assert [(row.title, row.href) for row in rows] == [
        ("Owned", "https://club.example.org/"),
    ]


@pytest.mark.parametrize(
    "body", ["<p>No recognized result structure</p>", "<h3>Verify access</h3>"]
)
def test_unrecognized_search_page_does_not_become_empty_success(monkeypatch, body):
    monkeypatch.setattr(LayoutAwareYahoo, "request", lambda *a, **kw: body)
    with pytest.raises(DDGSException, match="unavailable"):
        search_index("Example City adult tennis")


def test_missing_search_response_is_retryable_not_empty(monkeypatch):
    monkeypatch.setattr(LayoutAwareYahoo, "request", lambda *a, **kw: None)
    with pytest.raises(DDGSException, match="unavailable"):
        search_index("Example City adult tennis")


def test_search_layout_size_is_bounded():
    with pytest.raises(ValueError, match="size limit"):
        LayoutAwareYahoo(timeout=12).extract_results("x" * 4_000_001)


def test_venue_page_reports_unparsed_anchor_and_keeps_ninth_card(monkeypatch):
    root = "https://courtreserve.com/players/events/e-"
    stray = root + "99"
    html = f'<a href="{stray}">unowned</a>' + "".join(
        f'<section class="algo"><div class="compTitle"><h3><a href="{root}{i}">Course {i}</a></h3></div></section>'
        for i in range(1, 10)
    )
    monkeypatch.setattr(LayoutAwareYahoo, "request", lambda *a, **kw: html)
    page = venue_index_page("Example public tennis")
    assert stray in page["anchors"]
    assert [r["href"] for r in page["rows"]] == [root + str(i) for i in range(1, 10)]


def test_explicit_provider_empty_is_distinct_from_an_unrecognized_page(monkeypatch):
    empty = "<h2>Search Results</h2><p>We did not find results for: <b>Example City</b></p>"
    monkeypatch.setattr(LayoutAwareYahoo, "request", lambda *a, **kw: empty)
    assert venue_index_page("A public search") == {"anchors": (), "recognized": True, "rows": []}
    monkeypatch.setattr(LayoutAwareYahoo, "request", lambda *a, **kw: "<h1>Verify access</h1>")
    with pytest.raises(DDGSException):
        venue_index_page("A public search")


def test_secondary_index_uses_only_organic_result_title_links(monkeypatch):
    from tennis_overview.search_layouts import brave_venue_page

    event = "https://courtreserve.com/players/events/e-77"
    html = f"""<div data-type="web">
    <a href="{event}"><div class="search-snippet-title">Adult clinic</div></a>
    <a href="{event}1">Related course</a></div>
    <div data-type="ad"><a href="{event}2"><div class="search-snippet-title">Ad</div></a></div>"""
    monkeypatch.setattr(
        "tennis_overview.search_layouts.fetch_resource",
        lambda url, _: (url, "text/html", html.encode()),
    )
    page = brave_venue_page("Group tennis")
    assert page == {
        "recognized": True,
        "rows": [{"title": "Adult clinic", "href": event}],
        "anchors": [event],
        "organic_count": 1,
    }
    monkeypatch.setattr(
        "tennis_overview.search_layouts.fetch_resource",
        lambda url, _: (url, "text/html", b"<h1>Verify access</h1>"),
    )
    with pytest.raises(ValueError):
        brave_venue_page("Group tennis")
    monkeypatch.setattr(
        "tennis_overview.search_layouts.fetch_resource",
        lambda url, _: (url, "text/html", b"<div>Too few matches were found</div>"),
    )
    assert brave_venue_page("Group tennis")["organic_count"] == 0
