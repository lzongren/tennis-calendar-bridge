"""Compatibility for the two observed layouts of the public Yahoo result page."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag
from ddgs.engines.yahoo import Yahoo
from ddgs.exceptions import DDGSException
from ddgs.results import TextResult
from .player_event import event_path


def _is_result_card(tag: Tag) -> bool:
    classes = tag.get_attribute_list("class")
    return (tag.name == "div" and "relsrch" in classes) or (
        tag.name == "section" and "algo" in classes
    )


def _owned_text(element: Tag, card: Tag) -> str:
    return " ".join(
        text.strip()
        for text in element.strings
        if text.strip() and text.find_parent(_is_result_card) is card
    )


class LayoutAwareYahoo(Yahoo):
    def extract_results(self, html_text: str) -> list[TextResult]:
        if len(html_text.encode()) > 4_000_000:
            raise ValueError("Search page exceeds size limit")
        results: list[TextResult] = []
        soup = BeautifulSoup(html_text, "html.parser")
        # Missing closing tags can nest subsequent cards. Descendant-wide XPath
        # joins then concatenate their titles and URLs into a single false result.
        for card in soup.select("div.relsrch, section.algo"):
            for heading in card.select(".compTitle h3"):
                if heading.find_parent(_is_result_card) is not card:
                    continue
                anchor = heading.select_one("a[href]") or heading.find_parent("a", href=True)
                if anchor is None or anchor.find_parent(_is_result_card) is not card:
                    continue
                title = _owned_text(heading, card)
                href = str(anchor["href"]).strip()
                if not title or not href:
                    continue
                snippet = next(
                    (
                        node
                        for node in card.select(".compText")
                        if node.find_parent(_is_result_card) is card
                    ),
                    None,
                )
                body = _owned_text(snippet, card) if snippet is not None else ""
                results.append(TextResult(title=title, href=href, body=body))
                break
        return results


def search_index(query: str) -> list[dict[str, str]]:
    """Use the existing engine transport/redirect decoder, never provider fallback."""
    rows = LayoutAwareYahoo(timeout=12).search(query)
    if not rows:
        # An unrecognized or blocked page is not evidence of zero matching courses.
        raise DDGSException("Public search results unavailable")
    return [{"title": row.title, "href": row.href} for row in rows[:8]]


class VenueIndexYahoo(LayoutAwareYahoo):
    """Observe public event anchors independently of result-card parsing and ranking."""

    anchors: tuple[str, ...] = ()
    explicit_empty: bool = False

    def extract_results(self, html_text: str) -> list[TextResult]:
        results = super().extract_results(html_text)
        soup = BeautifulSoup(html_text, "html.parser")
        self.explicit_empty = (
            not results
            and soup.find("h2", string="Search Results") is not None
            and any(
                str(p.contents[0]).strip() == "We did not find results for:"
                for p in soup.select("p")
                if p.contents
            )
        )
        anchors = soup.select("a[href]")
        if len(anchors) > 1024:
            raise ValueError("Venue index page exceeds anchor budget")
        decoded = self.post_extract_results(
            [TextResult(title="", href=str(a["href"]), body="") for a in anchors]
        )
        self.anchors = tuple(dict.fromkeys(r.href for r in decoded if event_path(r.href)))
        return results


def venue_index_page(query: str) -> dict[str, object]:
    engine = VenueIndexYahoo(timeout=8)
    rows = engine.search(query)
    if (not rows and not engine.explicit_empty and not engine.anchors) or len(rows or []) > 64:
        raise DDGSException("Venue index response unavailable or over budget")
    return {
        "anchors": engine.anchors,
        "recognized": bool(rows) or engine.explicit_empty,
        "rows": [
            {"title": r.title[:320], "href": r.href, "snippet": r.body[:500]}
            for r in rows or []
            if event_path(r.href)
        ],
    }
