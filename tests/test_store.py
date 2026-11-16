from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models import Article, DocumentRecord
from app.store import Store


def make_article(url: str = "https://example.vn/a", digest: str = "abc123") -> Article:
    return Article(
        url=url,
        title="Tieu de bai bao",
        source="VnExpress",
        country="VN",
        language="vi",
        summary="Tom tat ngan.",
        topics=["Kinh tế", "Công nghệ"],
        sentiment="neutral",
        author="Tac gia",
        published_at="2026-11-10T08:00:00+00:00",
        fetched_at="2026-11-10T09:00:00+00:00",
        word_count=120,
        content_hash=digest,
        image_url=None,
        documents=[
            DocumentRecord(
                url="https://example.vn/file.pdf",
                article_url=url,
                title="file.pdf",
                kind="pdf",
                mime="application/pdf",
                size_bytes=1024,
            )
        ],
    )


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "news.db")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_insert_and_deduplicate(self) -> None:
        article = make_article()
        self.assertIsNotNone(self.store.insert_article(article))
        self.assertIsNone(self.store.insert_article(article))
        self.assertTrue(self.store.url_exists(article.url))
        self.assertTrue(self.store.hash_exists(article.content_hash))
        self.assertEqual(self.store.stats()["articles"], 1)
        self.assertEqual(self.store.stats()["documents"], 1)

    def test_pending_and_mark_synced(self) -> None:
        self.store.insert_article(make_article())
        self.store.insert_article(make_article("https://example.vn/b", "def456"))
        pending = self.store.pending_for_lark()
        self.assertEqual(len(pending), 2)
        self.store.mark_synced([pending[0]["url"]], "2026-11-10T10:00:00+00:00")
        self.assertEqual(len(self.store.pending_for_lark()), 1)

    def test_run_logging(self) -> None:
        run_id = self.store.start_run("test", ["VnExpress"], "2026-11-10T09:00:00+00:00")
        self.store.finish_run(
            run_id,
            finished_at="2026-11-10T09:01:00+00:00",
            fetched=5,
            inserted=2,
            duplicates=3,
            failed=0,
            documents=1,
            status="ok",
        )
        rows = self.store.recent(limit=5)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
