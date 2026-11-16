from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

LEARN_STEP = 0.3
LEARN_CLAMP = 2.0


@dataclass(frozen=True)
class Profile:
    """Static preferences, optionally loaded from config/profile.yaml."""

    topics: dict[str, float] = field(default_factory=dict)
    sources: dict[str, float] = field(default_factory=dict)
    keywords: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    hide_sentiment: tuple[str, ...] = ()
    keyword_weight: float = 0.8
    recency_weight: float = 1.0
    half_life_hours: float = 48.0


@dataclass
class Scored:
    row: dict
    score: float
    reasons: list[str]


def load_profile(path: Path | str | None) -> Profile:
    if path is None or not Path(path).exists():
        return Profile()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Profile(
        topics={str(k): float(v) for k, v in (raw.get("topics") or {}).items()},
        sources={str(k): float(v) for k, v in (raw.get("sources") or {}).items()},
        keywords=tuple(str(k).lower() for k in raw.get("keywords") or []),
        blocked=tuple(str(k).lower() for k in raw.get("blocked") or []),
        hide_sentiment=tuple(str(k).lower() for k in raw.get("hide_sentiment") or []),
        keyword_weight=float(raw.get("keyword_weight", 0.8)),
        recency_weight=float(raw.get("recency_weight", 1.0)),
        half_life_hours=max(1.0, float(raw.get("half_life_hours", 48))),
    )


def learn_weights(feedback_rows: list[dict]) -> dict[str, dict[str, float]]:
    """Turn 👍/👎 history (rows carry topics, source, value=+1/-1) into weight offsets."""
    topics: dict[str, float] = {}
    sources: dict[str, float] = {}
    for row in feedback_rows:
        value = 1 if int(row.get("value", 0)) > 0 else -1
        for topic in row.get("topics") or []:
            topics[topic] = topics.get(topic, 0.0) + LEARN_STEP * value
        if row.get("source"):
            sources[row["source"]] = sources.get(row["source"], 0.0) + LEARN_STEP * value
    clamp = lambda d: {k: max(-LEARN_CLAMP, min(LEARN_CLAMP, v)) for k, v in d.items()}
    return {"topics": clamp(topics), "sources": clamp(sources)}


def _has_word(text: str, word: str) -> bool:
    # whole-word match so a short keyword like "ai" does not hit "hai" or "tài"
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def _parse_time(row: dict) -> datetime | None:
    for key in ("published_at", "fetched_at"):
        value = row.get(key)
        if not value:
            continue
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    return None


def score_article(
    row: dict,
    profile: Profile,
    learned: dict[str, dict[str, float]] | None = None,
    now: datetime | None = None,
) -> Scored | None:
    """Return None when the article is filtered out (blocked word / hidden sentiment)."""
    learned = learned or {"topics": {}, "sources": {}}
    text = f"{row.get('title', '')} {row.get('summary', '')}".lower()

    if any(_has_word(text, word) for word in profile.blocked):
        return None
    if (row.get("sentiment") or "") in profile.hide_sentiment:
        return None

    score = 0.0
    reasons: list[str] = []

    for topic in row.get("topics") or []:
        weight = profile.topics.get(topic, 0.0) + learned["topics"].get(topic, 0.0)
        if weight:
            score += weight
            reasons.append(f"{topic} {weight:+.1f}")

    source = row.get("source") or ""
    weight = profile.sources.get(source, 0.0) + learned["sources"].get(source, 0.0)
    if weight:
        score += weight
        reasons.append(f"{source} {weight:+.1f}")

    hits = [word for word in profile.keywords if _has_word(text, word)]
    if hits:
        score += profile.keyword_weight * len(hits)
        reasons.append("từ khoá: " + ", ".join(hits))

    stamp = _parse_time(row)
    if stamp is not None and profile.recency_weight:
        now = now or datetime.now(timezone.utc)
        age_hours = max(0.0, (now - stamp).total_seconds() / 3600)
        fresh = profile.recency_weight * math.pow(2, -age_hours / profile.half_life_hours)
        score += fresh
        reasons.append(f"mới {fresh:+.1f}")

    return Scored(row=row, score=round(score, 3), reasons=reasons)


def rank(
    rows: list[dict],
    profile: Profile,
    learned: dict[str, dict[str, float]] | None = None,
    *,
    feedback: dict[str, int] | None = None,
    top: int | None = None,
    now: datetime | None = None,
) -> list[Scored]:
    """Rank rows for the user; articles they 👎'd are dropped."""
    feedback = feedback or {}
    scored: list[Scored] = []
    for row in rows:
        if feedback.get(row.get("url"), 0) < 0:
            continue
        item = score_article(row, profile, learned, now)
        if item is not None:
            scored.append(item)
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:top] if top else scored
