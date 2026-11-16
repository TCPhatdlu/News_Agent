from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.personalize import Profile, learn_weights, load_profile, rank, score_article
from app.store import Store
from tests.test_store import make_article

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def row(url="u1", topics=("Công nghệ",), source="VnExpress", title="Tin", summary="", hours=1,
        sentiment="neutral"):
    return {
        "url": url, "title": title, "summary": summary, "topics": list(topics), "source": source,
        "sentiment": sentiment, "published_at": (NOW - timedelta(hours=hours)).isoformat(),
        "fetched_at": None,
    }


class ScoringTests(unittest.TestCase):
    def test_topic_weight_orders_results(self) -> None:
        profile = Profile(topics={"Công nghệ": 1.0, "Thể thao": 0.1}, recency_weight=0)
        items = rank([row("a", ["Thể thao"]), row("b", ["Công nghệ"])], profile, now=NOW)
        self.assertEqual([i.row["url"] for i in items], ["b", "a"])

    def test_keyword_is_whole_word(self) -> None:
        profile = Profile(keywords=("ai",), recency_weight=0)
        self.assertEqual(score_article(row(title="hai người tài"), profile, now=NOW).score, 0)
        self.assertGreater(score_article(row(title="AI mới ra mắt"), profile, now=NOW).score, 0)

    def test_blocked_and_hidden_sentiment_are_filtered(self) -> None:
        profile = Profile(blocked=("showbiz",), hide_sentiment=("negative",))
        self.assertIsNone(score_article(row(title="Tin showbiz"), profile, now=NOW))
        self.assertIsNone(score_article(row(sentiment="negative"), profile, now=NOW))

    def test_recency_decays(self) -> None:
        profile = Profile(half_life_hours=24)
        fresh = score_article(row(hours=0), profile, now=NOW).score
        old = score_article(row(hours=48), profile, now=NOW).score
        self.assertAlmostEqual(old, fresh / 4, places=2)

    def test_disliked_article_is_dropped(self) -> None:
        items = rank([row("a"), row("b")], Profile(), feedback={"a": -1}, now=NOW)
        self.assertEqual([i.row["url"] for i in items], ["b"])

    def test_learned_weights_shift_ranking(self) -> None:
        learned = learn_weights([{"topics": ["Thể thao"], "source": "X", "value": 1}] * 3)
        profile = Profile(topics={"Công nghệ": 0.5}, recency_weight=0)
        items = rank([row("a", ["Công nghệ"]), row("b", ["Thể thao"])], profile, learned, now=NOW)
        self.assertEqual(items[0].row["url"], "b")

    def test_load_profile_missing_file_gives_empty_profile(self) -> None:
        self.assertEqual(load_profile(Path("nope.yaml")), Profile())


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "news.db")
        self.store.insert_article(make_article())

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_set_overwrite_and_clear(self) -> None:
        url = "https://example.vn/a"
        self.assertTrue(self.store.set_feedback(url, 1, "t"))
        self.assertTrue(self.store.set_feedback(url, -1, "t"))
        self.assertEqual(self.store.feedback_map(), {url: -1})
        rows = self.store.feedback_rows()
        self.assertEqual(rows[0]["topics"], ["Kinh tế", "Công nghệ"])
        self.store.set_feedback(url, 0, "t")
        self.assertEqual(self.store.feedback_map(), {})

    def test_unknown_article_rejected(self) -> None:
        self.assertFalse(self.store.set_feedback("https://nope", 1, "t"))


if __name__ == "__main__":
    unittest.main()
