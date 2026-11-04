from pathlib import Path

import pytest
from ddgs.exceptions import DDGSException

from tennis_overview.search_layouts import LayoutAwareYahoo, search_index


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
