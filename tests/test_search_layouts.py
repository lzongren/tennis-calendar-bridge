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


def test_classic_search_layout_still_uses_library_parser():
    html = '<div class="relsrch"><div class="compTitle"><a href="https://club.example.org/"><h3>Adult clinics</h3></a></div><div class="compText">Group lessons</div></div>'
    rows = LayoutAwareYahoo(timeout=12).extract_results(html)
    assert len(rows) == 1
    assert rows[0].href == "https://club.example.org/"
    assert rows[0].body == "Group lessons"


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
