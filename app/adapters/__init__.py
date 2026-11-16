from __future__ import annotations

from .rss import RssAdapter, parse_feed, parse_date
from .html import HtmlAdapter

__all__ = ["RssAdapter", "HtmlAdapter", "parse_feed", "parse_date", "get_adapter"]


def get_adapter(kind: str):
    kind = (kind or "rss").lower()
    if kind == "rss":
        return RssAdapter()
    if kind == "html":
        return HtmlAdapter()
    raise ValueError(f"unsupported source type: {kind}")
