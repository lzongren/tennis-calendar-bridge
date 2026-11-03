"""Compatibility for the two observed layouts of the public Yahoo result page."""

from __future__ import annotations

from bs4 import BeautifulSoup
from ddgs.engines.yahoo import Yahoo
from ddgs.exceptions import DDGSException
from ddgs.results import TextResult


class LayoutAwareYahoo(Yahoo):
    def extract_results(self, html_text: str) -> list[TextResult]:
        if len(html_text.encode()) > 4_000_000:
            raise ValueError("Search page exceeds size limit")
        results = [row for row in super().extract_results(html_text) if row.title and row.href]
        if results:
            return results
        soup = BeautifulSoup(html_text, "html.parser")
        # The alternate layout uses section.algo instead of div.relsrch.
        # Keep related-query suggestions and account/navigation links out.
        for card in soup.select("section.algo"):
            heading = card.select_one(".compTitle h3")
            if heading is None:
                continue
            anchor = heading.select_one("a[href]") or heading.find_parent("a", href=True)
            if anchor is None:
                continue
            title = heading.get_text(" ", strip=True)
            if title:
                results.append(TextResult(title=title, href=str(anchor["href"])))
        return results


def search_index(query: str) -> list[dict[str, str]]:
    """Use the existing engine transport/redirect decoder, never provider fallback."""
    rows = LayoutAwareYahoo(timeout=12).search(query)
    if not rows:
        # An unrecognized or blocked page is not evidence of zero matching courses.
        raise DDGSException("Public search results unavailable")
    return [{"title": row.title, "href": row.href} for row in rows[:8]]
