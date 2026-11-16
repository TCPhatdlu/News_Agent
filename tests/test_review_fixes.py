from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.lark import LarkSink
from app.llm import heuristic_enrichment, parse_enrichment
from app.net import StaticFetcher
from app.pipeline import Pipeline
from app.web import css_url
from app.config import load_settings
from app.llm import MockLLM
from tests.test_pipeline import ARTICLE, FEED, SOURCES
from tests.test_store import make_article
from app.store import Store


class SyncTests(unittest.TestCase):
    def test_failure_in_later_chunk_keeps_earlier_chunks_marked(self) -> None:
        import app.lark as lark_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "n.db")
            for i in range(3):
                store.insert_article(make_article(f"https://e.vn/{i}", f"h{i}"))

            class Sink(LarkSink):
                CHUNK_SIZE = 2

                def _tenant_token(self) -> str:
                    return "t"

            sink = Sink(app_id="a", app_secret="b", app_token="c", table_id="d")
            calls = {"n": 0}

            class Resp:
                def raise_for_status(self) -> None: ...

                def json(self) -> dict:
                    return {"code": 0}

            def fake_post(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:  # second chunk fails
                    raise RuntimeError("boom")
                return Resp()

            original = lark_mod.httpx.post
            lark_mod.httpx.post = fake_post
            try:
                settings = load_settings({"data_dir": root, "llm_provider": "mock"})
                pipeline = Pipeline(settings, store=store, sink=sink, llm=MockLLM())
                with self.assertRaises(RuntimeError):
                    pipeline.sync_pending()
                # the 2 rows in chunk 1 were accepted by Lark -> must not be re-sent next time
                self.assertEqual(len(store.pending_for_lark()), 1)
            finally:
                lark_mod.httpx.post = original
                store.close()


class SkippedTests(unittest.TestCase):
    def test_short_and_duplicate_pages_are_not_refetched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "s.yaml").write_text(SOURCES, encoding="utf-8")
            settings = load_settings(
                {"data_dir": root / "d", "sources_path": root / "s.yaml", "llm_provider": "mock"}
            )
            same = ARTICLE.format(title="Cung tieu de", doc="https://example.vn/f.pdf")
            pages = {
                "https://example.vn/feed": FEED,
                "https://example.vn/bai-mot": same,
                "https://example.vn/bai-hai": same,  # identical content -> duplicate by hash
            }
            fetcher = StaticFetcher(pages, {"https://example.vn/f.pdf": b"%PDF"})
            pipeline = Pipeline(settings, fetcher=fetcher, llm=MockLLM())
            try:
                first = pipeline.collect(push=False)
                self.assertEqual((first.inserted, first.duplicates), (1, 1))
                fetcher.calls.clear()
                second = pipeline.collect(push=False)
                self.assertEqual(second.inserted, 0)
                # only the feed is re-downloaded, not the two article pages
                self.assertEqual(fetcher.calls, ["https://example.vn/feed"])
            finally:
                pipeline.store.close()


class LLMParsingTests(unittest.TestCase):
    def test_unknown_topics_are_dropped_and_case_fixed(self) -> None:
        result = parse_enrichment(
            '{"summary":"Tom tat.","topics":["công nghệ","Bóng chày","Công nghệ"],"sentiment":"x"}'
        )
        self.assertEqual(result.topics, ["Công nghệ"])
        self.assertEqual(result.sentiment, "neutral")

    def test_all_unknown_falls_back(self) -> None:
        self.assertEqual(
            parse_enrichment('{"summary":"ok","topics":["???"]}').topics, ["Tổng hợp"]
        )

    def test_sentiment_uses_whole_words(self) -> None:
        # "đạt" must not match inside "đạtxyz"; "tăng" appears once as a real word
        result = heuristic_enrichment("Gia tăng", "Cong ty dạtxyz dạtxyz tăng. " * 3)
        self.assertEqual(result.sentiment, "positive")


class CssUrlTests(unittest.TestCase):
    def test_rejects_non_http(self) -> None:
        self.assertEqual(css_url("javascript:alert(1)"), "")
        self.assertEqual(css_url(None), "")

    def test_cannot_break_out_of_url_quotes(self) -> None:
        out = css_url("https://x.vn/a.png');background:red;x:url('")
        for bad in ("'", '"', "(", ")", " "):
            self.assertNotIn(bad, out)


if __name__ == "__main__":
    unittest.main()
