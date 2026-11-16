from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import load_settings
from app.llm import MockLLM
from app.net import StaticFetcher
from app.pipeline import Pipeline

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Bai mot</title><link>https://example.vn/bai-mot</link><pubDate>Mon, 10 Nov 2026 08:00:00 +0700</pubDate></item>
  <item><title>Bai hai</title><link>https://example.vn/bai-hai</link><pubDate>Mon, 10 Nov 2026 09:00:00 +0700</pubDate></item>
</channel></rss>"""

ARTICLE = """<html lang="vi"><head>
<meta property="og:title" content="{title}" />
<meta property="article:published_time" content="2026-11-10T08:30:00+07:00" />
</head><body><article>
<p>Doanh nghiep det may trong nuoc dang tang cuong dau tu vao cong nghe de nang cao nang luc canh tranh tren thi truong quoc te.</p>
<p>Nhieu nha may da hoan tat giai doan tu dong hoa dau tien va ghi nhan muc giam chi phi dang ke trong nam nay.</p>
<p>Du bao trong ba nam toi, ty le tu dong hoa se tiep tuc tang manh o cac cong doan cat va may.</p>
<a href="{doc}">Tai lieu dinh kem</a>
</article></body></html>"""

SOURCES = """defaults:
  max_articles: 5
sources:
  - name: Test Source
    type: rss
    url: https://example.vn/feed
    country: VN
    language: vi
"""


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sources_path = root / "sources.yaml"
        self.sources_path.write_text(SOURCES, encoding="utf-8")
        self.settings = load_settings(
            {
                "data_dir": root / "data",
                "sources_path": self.sources_path,
                "llm_provider": "mock",
                "dry_run": True,
            }
        )
        pages = {
            "https://example.vn/feed": FEED,
            "https://example.vn/bai-mot": ARTICLE.format(
                title="Bai mot ve det may", doc="https://example.vn/files/bao-cao.pdf"
            ),
            "https://example.vn/bai-hai": ARTICLE.format(
                title="Bai hai ve det may", doc="https://example.vn/files/phu-luc.docx"
            ),
        }
        binaries = {
            "https://example.vn/files/bao-cao.pdf": b"%PDF-1.4 fake",
            "https://example.vn/files/phu-luc.docx": b"PK fake docx",
        }
        self.fetcher = StaticFetcher(pages, binaries)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _pipeline(self) -> Pipeline:
        # both fixture pages share one body, so semantic dedup would (rightly) group them
        return Pipeline(self.settings, fetcher=self.fetcher, llm=MockLLM(), deduper=False)

    def test_collect_inserts_articles_and_documents(self) -> None:
        pipeline = self._pipeline()
        try:
            stats = pipeline.collect(push=False)
        finally:
            pipeline.store.close()

        self.assertEqual(stats.fetched, 2)
        self.assertEqual(stats.inserted, 2)
        self.assertEqual(stats.duplicates, 0)
        self.assertEqual(stats.failed, 0)
        self.assertEqual(stats.documents, 2)

        pipeline = self._pipeline()
        try:
            rows = pipeline.store.recent(limit=10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["source"], "Test Source")
            self.assertTrue(rows[0]["summary"])
            self.assertEqual(pipeline.store.stats()["documents"], 2)
        finally:
            pipeline.store.close()

        stored = list((self.settings.documents_dir).glob("*"))
        self.assertEqual(len(stored), 2)

    def test_second_run_detects_duplicates(self) -> None:
        pipeline = self._pipeline()
        try:
            pipeline.collect(push=False)
            stats = pipeline.collect(push=False)
        finally:
            pipeline.store.close()
        self.assertEqual(stats.inserted, 0)
        self.assertEqual(stats.duplicates, 2)

    def test_dry_run_writes_lark_outbox(self) -> None:
        pipeline = self._pipeline()
        try:
            stats = pipeline.collect(push=True)
        finally:
            pipeline.store.close()
        self.assertEqual(stats.pushed, 2)
        outbox = list(self.settings.outbox_dir.glob("lark-*.jsonl"))
        self.assertEqual(len(outbox), 1)
        records = [json.loads(line) for line in outbox[0].read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(records), 2)
        self.assertIn("Tiêu đề", records[0]["fields"])
        self.assertEqual(records[0]["fields"]["Nguồn"], "Test Source")

    def test_source_filter_rejects_unknown(self) -> None:
        pipeline = self._pipeline()
        try:
            with self.assertRaises(ValueError):
                pipeline.collect(only=["Khong Ton Tai"], push=False)
        finally:
            pipeline.store.close()

    def test_export_jsonl(self) -> None:
        pipeline = self._pipeline()
        try:
            pipeline.collect(push=False)
            path = pipeline.export_jsonl(self.settings.data_dir / "exports" / "articles.jsonl")
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(lines), 2)
            self.assertIn("topics", json.loads(lines[0]))
        finally:
            pipeline.store.close()


if __name__ == "__main__":
    unittest.main()
