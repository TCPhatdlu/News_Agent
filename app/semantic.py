"""Semantic de-duplication: group articles from different outlets that cover the same story.

Exact duplicates are caught by content_hash. This layer catches *rewrites*: two papers reporting
the same event with different wording. Each new article is embedded and compared (cosine) with
recent lead articles; above the threshold it joins that story's cluster (articles.cluster_of)
instead of appearing as a separate item. Like Google News: one story, "+N nguồn khác".

Embedders:
  hash    (default) hashed word uni+bigrams, pure Python, no model needed
  ollama  /api/embed with an embedding model (e.g. nomic-embed-text, bge-m3)
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

DIMS = 4096
_TOKEN = re.compile(r"\w+", re.UNICODE)
# frequent Vietnamese function words carry no topical signal
STOPWORDS = frozenset(
    "và của là có được cho với các những một trong khi đã đang sẽ này đó từ theo tại về để không "
    "người năm ngày như cũng thì mà hay vào ra lại nhiều nên bị do trên dưới sau trước".split()
)


class Embedder(Protocol):
    name: str

    def embed(self, text: str) -> dict[int, float]: ...


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    return [token for token in _TOKEN.findall(text) if token not in STOPWORDS and len(token) > 1]


def _bucket(feature: str) -> int:
    return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=4).digest(), "big") % DIMS


def _normalize(vector: dict[int, float]) -> dict[int, float]:
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if not norm:
        return {}
    return {key: round(value / norm, 5) for key, value in vector.items()}


class HashEmbedder:
    name = "hash-v1"

    def embed(self, text: str) -> dict[int, float]:
        tokens = _tokens(text)
        vector: dict[int, float] = {}
        for token in tokens:
            key = _bucket(token)
            vector[key] = vector.get(key, 0.0) + 1.0
        # bigrams weigh more: shared phrases ("giá vàng tăng") are stronger evidence than shared words
        for first, second in zip(tokens, tokens[1:]):
            key = _bucket(f"{first} {second}")
            vector[key] = vector.get(key, 0.0) + 2.0
        # dampen very frequent terms so long articles do not dominate
        return _normalize({key: 1.0 + math.log(value) for key, value in vector.items()})


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str = "nomic-embed-text", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.name = f"ollama:{model}"

    def embed(self, text: str) -> dict[int, float]:
        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": text[:4000]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        values = response.json()["embeddings"][0]
        return _normalize({index: float(value) for index, value in enumerate(values)})


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(key, 0.0) for key, value in a.items())


def build_embedder(settings, kind: str = "hash", model: str = "nomic-embed-text") -> Embedder:
    if kind == "ollama":
        return OllamaEmbedder(settings.ollama_base_url, model)
    return HashEmbedder()


class SemanticDeduper:
    def __init__(self, store, embedder: Embedder, threshold: float = 0.45, window_hours: int = 72) -> None:
        self.store = store
        self.embedder = embedder
        self.threshold = threshold
        self.window_hours = window_hours

    def text_for(self, title: str, body: str) -> str:
        # title twice: headlines are the most comparable part across outlets
        return f"{title}. {title}. {body[:1500]}"

    def find_lead(self, vector: dict[int, float], now: datetime | None = None) -> tuple[str | None, float]:
        now = now or datetime.now(timezone.utc)
        since = (now - timedelta(hours=self.window_hours)).replace(microsecond=0).isoformat()
        best_url, best_score = None, 0.0
        for url, other in self.store.embeddings_since(self.embedder.name, since):
            score = cosine(vector, other)
            if score > best_score:
                best_url, best_score = url, score
        if best_score >= self.threshold:
            return best_url, best_score
        return None, best_score
