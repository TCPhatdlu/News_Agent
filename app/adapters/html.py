from __future__ import annotations

from urllib.parse import urljoin

from lxml import html as lxml_html

from ..config import Source
from ..models import ArticleRef, normalize_text
from ..net import Fetcher
from .rss import parse_date


class HtmlAdapter:
    """Adapter for sites without a usable feed, driven by XPath selectors."""

    def list_articles(self, source: Source, fetcher: Fetcher, limit: int | None = None) -> list[ArticleRef]:
        page = fetcher.text(source.url)
        return self.parse(page, source, limit=limit)

    def parse(self, page: str, source: Source, limit: int | None = None) -> list[ArticleRef]:
        tree = lxml_html.fromstring(page)
        selectors = source.selectors or {}
        item_xpath = selectors.get("item", "//article")
        link_xpath = selectors.get("link", ".//a/@href")
        title_xpath = selectors.get("title", ".//a")
        date_xpath = selectors.get("date", ".//time/@datetime")

        refs: list[ArticleRef] = []
        seen: set[str] = set()
        for item in tree.xpath(item_xpath):
            href, title = self._pick_link(item, link_xpath, title_xpath)
            if not href or not title:
                continue
            url = urljoin(source.url, href.strip())
            if url in seen:
                continue
            seen.add(url)
            dates = item.xpath(date_xpath)
            published = parse_date(dates[0] if dates and isinstance(dates[0], str) else None)
            refs.append(ArticleRef(title=title, url=url, source=source.name, published_at=published))
            if limit and len(refs) >= limit:
                break
        return refs

    @staticmethod
    def _pick_link(item, link_xpath: str, title_xpath: str) -> tuple[str | None, str]:
        links = item.xpath(link_xpath)
        titles = item.xpath(title_xpath)
        href = None
        if links:
            first = links[0]
            href = first if isinstance(first, str) else first.get("href", "")
        title = ""
        if titles:
            first = titles[0]
            title = normalize_text(first if isinstance(first, str) else first.text_content())

        if not href or not title:
            for anchor in item.xpath(".//a[@href]"):
                text = normalize_text(anchor.text_content())
                if text and anchor.get("href"):
                    return anchor.get("href"), text
        return href or None, title
