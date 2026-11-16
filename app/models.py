from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_WS = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return _WS.sub(" ", (value or "").replace("\u00a0", " ")).strip()


def content_hash(title: str, body: str) -> str:
    payload = f"{normalize_text(title).lower()}\n{normalize_text(body)[:20000].lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ArticleRef:
    title: str
    url: str
    source: str
    published_at: str | None = None


@dataclass
class ExtractedArticle:
    title: str
    text: str
    author: str | None = None
    published_at: str | None = None
    language: str | None = None
    image_url: str | None = None
    document_links: list[str] = field(default_factory=list)


@dataclass
class Enrichment:
    summary: str
    topics: list[str] = field(default_factory=list)
    sentiment: str = "neutral"


@dataclass
class DocumentRecord:
    url: str
    article_url: str
    title: str
    kind: str
    mime: str
    size_bytes: int
    local_path: str | None = None
    sha256: str | None = None


@dataclass
class Article:
    url: str
    title: str
    source: str
    country: str
    language: str
    summary: str
    topics: list[str]
    sentiment: str
    author: str | None
    published_at: str | None
    fetched_at: str
    word_count: int
    content_hash: str
    image_url: str | None = None
    documents: list[DocumentRecord] = field(default_factory=list) 

    @property
    def topics_text(self) -> str:
        return ", ".join(self.topics)
