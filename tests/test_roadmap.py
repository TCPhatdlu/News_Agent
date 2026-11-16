"""Tests for the short/medium-term roadmap: alerts, semantic dedup, publishing + approval, web."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.alerts import Notifier
from app.config import load_settings
from app.llm import MockLLM
from app.net import StaticFetcher
from app.pipeline import Pipeline
from app.publish import Channel, PublishError, Publisher, compose, validate
from app.semantic import HashEmbedder, SemanticDeduper, cosine
from app.store import Store
from tests.test_pipeline import ARTICLE, FEED, SOURCES
from tests.test_store import make_article

GOLD_A = ("Giá vàng hôm nay tăng mạnh, vượt mốc 90 triệu đồng mỗi lượng",
          "Giá vàng miếng SJC sáng nay tăng 1,5 triệu đồng, vượt mốc 90 triệu đồng mỗi lượng do giá vàng "
          "thế giới lập đỉnh mới và nhu cầu trú ẩn tăng.")
GOLD_B = ("Vàng SJC vượt 90 triệu đồng/lượng, tăng mạnh theo thế giới",
          "Sáng nay giá vàng SJC được các doanh nghiệp điều chỉnh tăng mạnh, vượt 90 triệu đồng một lượng "
          "khi giá vàng thế giới lên đỉnh mới.")
FOOTBALL = ("Đội tuyển Việt Nam thắng Kyrgyzstan 2-1 ở vòng loại",
            "Đình Bắc và Văn Tùng ghi bàn giúp tuyển Việt Nam thắng Kyrgyzstan 2-1 trên sân nhà.")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "s.yaml").write_text(SOURCES, encoding="utf-8")
        self.settings = load_settings(
            {"data_dir": self.root / "d", "sources_path": self.root / "s.yaml",
             "llm_provider": "mock", "dry_run": True}
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()


class SemanticTests(Base):
    def test_rewrite_scores_above_threshold_other_story_below(self) -> None:
        emb = HashEmbedder()
        dedup = SemanticDeduper(None, emb)
        a, b, c = (emb.embed(dedup.text_for(t, body)) for t, body in (GOLD_A, GOLD_B, FOOTBALL))
        self.assertGreaterEqual(cosine(a, b), dedup.threshold)
        self.assertLess(cosine(a, c), 0.2)

    def test_pipeline_groups_same_story_and_keeps_it_out_of_lark(self) -> None:
        same = ARTICLE.format(title="Det may tang dau tu", doc="https://example.vn/f.pdf")
        pages = {"https://example.vn/feed": FEED,
                 "https://example.vn/bai-mot": same,
                 "https://example.vn/bai-hai": same.replace("Det may tang dau tu", "Nganh det may dau tu manh")}
        pipeline = Pipeline(self.settings, fetcher=StaticFetcher(pages, {"https://example.vn/f.pdf": b"%PDF"}),
                            llm=MockLLM())
        try:
            stats = pipeline.collect(push=False)
            self.assertEqual((stats.inserted, stats.similar), (2, 1))
            clusters = pipeline.store.cluster_members()
            self.assertEqual(list(clusters), ["https://example.vn/bai-mot"])
            self.assertEqual(len(pipeline.store.pending_for_lark()), 1)
            self.assertEqual(pipeline.store.duplicate_ratio("2000-01-01"), (1, 2))
        finally:
            pipeline.store.close()


class AlertTests(Base):
    def test_feed_failing_twice_alerts_on_second_run_only(self) -> None:
        pipeline = Pipeline(self.settings, fetcher=StaticFetcher({}), llm=MockLLM(), deduper=False)
        try:
            first = pipeline.collect(push=False)
            self.assertEqual(first.alerts, [])
            second = pipeline.collect(push=False)
            self.assertEqual(len(second.alerts), 1)
            self.assertIn("Test Source", second.alerts[0])
            self.assertEqual(pipeline.store.recent_alerts()[0]["delivered"], "outbox")
            self.assertTrue(list(self.settings.outbox_dir.glob("alerts-*.jsonl")))
            # a good run resets the counter
            pipeline.fetcher = StaticFetcher({"https://example.vn/feed": FEED,
                                              "https://example.vn/bai-mot": ARTICLE.format(title="A", doc="x"),
                                              "https://example.vn/bai-hai": ARTICLE.format(title="B", doc="y")})
            pipeline.collect(push=False)
            self.assertEqual(pipeline.store.source_health()[0]["consecutive_failures"], 0)
        finally:
            pipeline.store.close()

    def test_crash_closes_run_as_error_and_alerts(self) -> None:
        pipeline = Pipeline(self.settings, fetcher=StaticFetcher({}), llm=MockLLM(), deduper=False)

        def boom(*args, **kwargs):
            raise RuntimeError("disk full")

        pipeline._collect_source = boom
        try:
            stats = pipeline.collect(push=False)
            self.assertIn("disk full", stats.crash)
            run = pipeline.store.recent_runs(1)[0]
            self.assertEqual(run["status"], "error")
            self.assertEqual(pipeline.store.recent_alerts()[0]["level"], "error")
        finally:
            pipeline.store.close()

    def test_notifier_never_raises_without_targets(self) -> None:
        notifier = Notifier(outbox_dir=None)
        self.assertEqual(notifier.send("error", "x"), "none")


class FakeChannel(Channel):
    name = "telegram"

    def __init__(self, result: str | None) -> None:
        super().__init__()
        self.result = result
        self.sent: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    def send(self, text: str, article: dict) -> str:
        self.sent.append(text)
        if self.result is None:
            raise PublishError("no message id returned")
        return self.result


class PublishTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.store = Store(self.root / "p.db")
        self.store.insert_article(make_article())
        self.url = "https://example.vn/a"

    def tearDown(self) -> None:
        self.store.close()
        super().tearDown()

    def publisher(self, channels=None, dry_run=True) -> Publisher:
        return Publisher(self.store, channels or {}, outbox_dir=self.root / "out", dry_run=dry_run)

    def test_approval_gate(self) -> None:
        pub = self.publisher()
        ids = pub.queue(self.url, ["telegram", "facebook"])
        statuses = {p["channel"]: p["status"] for p in self.store.list_posts()}
        self.assertEqual(statuses, {"telegram": "approved", "facebook": "pending"})
        facebook = [p for p in self.store.list_posts() if p["channel"] == "facebook"][0]
        with self.assertRaises(ValueError):
            pub.send(facebook["id"])  # cannot skip approval
        pub.approve(facebook["id"], "Nội dung đã sửa")
        result = pub.send(facebook["id"])
        self.assertTrue(result["ok"] and result["dry_run"])
        self.assertTrue(result["post_id"].startswith("dry-"))
        self.assertEqual(self.store.get_post(facebook["id"])["text"], "Nội dung đã sửa")
        self.assertEqual(len(ids), 2)
        self.assertEqual(pub.queue(self.url, ["telegram"]), [])  # no double queueing

    def test_live_send_requires_post_id(self) -> None:
        ok = self.publisher({"telegram": FakeChannel("12345")}, dry_run=False)
        post = ok.queue(self.url, ["telegram"])[0]
        self.assertEqual(ok.send(post)["post_id"], "12345")
        self.assertEqual(self.store.get_post(post)["status"], "published")

        self.store.insert_article(make_article("https://example.vn/b", "zz"))
        bad = self.publisher({"telegram": FakeChannel(None)}, dry_run=False)
        post = bad.queue("https://example.vn/b", ["telegram"])[0]
        self.assertFalse(bad.send(post)["ok"])
        row = self.store.get_post(post)
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["post_id"])

    def test_tweet_fits_280(self) -> None:
        article = {"title": "T" * 400, "summary": "s", "url": "https://example.vn/" + "x" * 200}
        text = compose("twitter", article)
        self.assertIsNone(validate("twitter", text))
        self.assertIsNotNone(validate("twitter", "x" * 300))


class MigrationTests(unittest.TestCase):
    def test_old_database_gets_new_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, source TEXT, "
                        "country TEXT, language TEXT, summary TEXT, topics TEXT, sentiment TEXT, author TEXT, "
                        "published_at TEXT, fetched_at TEXT, word_count INTEGER, content_hash TEXT, "
                        "image_url TEXT, synced_at TEXT)")
            con.commit()
            con.close()
            store = Store(path)
            try:
                self.assertIsNotNone(store.insert_article(make_article()))
                self.assertEqual(store.cluster_members(), {})
            finally:
                store.close()


class WebTests(Base):
    def test_pages_and_queue_flow(self) -> None:
        from app.web import create_server

        store = Store(self.settings.db_path)
        store.insert_article(make_article())
        store.close()
        server = create_server(self.settings, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path, body, origin=None):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            req = urllib.request.Request(base + path, json.dumps(body).encode(), headers, method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        try:
            for path in ("/", "/?view=foryou", "/posts", "/health"):
                with urllib.request.urlopen(base + path) as resp:
                    self.assertEqual(resp.status, 200, path)
            self.assertEqual(post("/api/posts/queue", {"url": "https://example.vn/a", "channels": ["twitter"]},
                                  origin="http://evil.example")[0], 403)
            status, data = post("/api/posts/queue", {"url": "https://example.vn/a", "channels": ["twitter"]})
            self.assertEqual((status, data["created"]), (200, 1))
            post_id = json.loads(urllib.request.urlopen(base + "/api/posts").read())["posts"][0]["id"]
            self.assertEqual(post(f"/api/posts/{post_id}/send", {})[0], 400)  # still pending
            self.assertEqual(post(f"/api/posts/{post_id}/approve", {"text": "x" * 300})[0], 400)  # too long
            self.assertEqual(post(f"/api/posts/{post_id}/approve", {"text": "Tin ngắn"})[0], 200)
            status, data = post(f"/api/posts/{post_id}/send", {})
            self.assertEqual(status, 200)
            self.assertTrue(data["post_id"].startswith("dry-"))
            page = urllib.request.urlopen(base + "/posts?status=published").read().decode()
            self.assertIn(data["post_id"], page)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
