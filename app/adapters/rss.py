from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urljoin

from ..config import Source
from ..models import ArticleRef, normalize_text
from ..net import Fetcher

_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+0000"
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    return None


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(node: ET.Element, *names: str) -> ET.Element | None:
    for child in node:
        if _localname(child.tag) in names:
            return child
    return None


def _text_of(node: ET.Element, *names: str) -> str | None:
    for child in node:
        if _localname(child.tag) in names:
            value = normalize_text("".join(child.itertext()))
            if value:
                return value
    return None


def parse_feed(xml_text: str, source: Source, limit: int | None = None) -> list[ArticleRef]:
    root = ET.fromstring(xml_text)
    entries: list[ET.Element] = []
    for node in root.iter():
        if _localname(node.tag) in {"item", "entry"}:
            entries.append(node)

    refs: list[ArticleRef] = []
    seen: set[str] = set()
    for entry in entries:
        title = _text_of(entry, "title")
        link = _text_of(entry, "link")
        if not link:
            link_node = _child(entry, "link")
            if link_node is not None:
                link = link_node.attrib.get("href")
        if not title or not link:
            continue
        absolute = urljoin(source.url, link.strip())
        if absolute in seen:
            continue
        seen.add(absolute)
        published = parse_date(
            _text_of(entry, "pubdate", "published", "updated", "date")
        )
        refs.append(
            ArticleRef(title=normalize_text(title), url=absolute, source=source.name, published_at=published)
        )
        if limit and len(refs) >= limit:
            break
    return refs


class RssAdapter:
    def list_articles(self, source: Source, fetcher: Fetcher, limit: int | None = None) -> list[ArticleRef]:
        payload = fetcher.text(source.url)
        return parse_feed(payload, source, limit=limit)
