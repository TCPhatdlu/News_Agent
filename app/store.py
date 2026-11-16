from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Article, DocumentRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,
    country       TEXT NOT NULL,
    language      TEXT NOT NULL,
    summary       TEXT NOT NULL,
    topics        TEXT NOT NULL,
    sentiment     TEXT NOT NULL,
    author        TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    word_count    INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    image_url     TEXT,
    synced_at     TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT NOT NULL UNIQUE,
    article_url   TEXT NOT NULL,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    mime          TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    local_path    TEXT,
    sha256        TEXT,
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    trigger       TEXT NOT NULL,
    sources       TEXT NOT NULL,
    fetched       INTEGER NOT NULL DEFAULT 0,
    inserted      INTEGER NOT NULL DEFAULT 0,
    duplicates    INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    documents     INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS feedback (
    article_url   TEXT PRIMARY KEY,
    value         INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skipped (
    url           TEXT PRIMARY KEY,
    reason        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_documents_article ON documents(article_url);
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")  # CLI and web can run together
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def url_exists(self, url: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone()
        return row is not None

    def url_seen(self, url: str) -> bool:
        """True if the URL was stored or deliberately skipped (duplicate content, empty body)."""
        if self.url_exists(url):
            return True
        row = self._conn.execute("SELECT 1 FROM skipped WHERE url = ?", (url,)).fetchone()
        return row is not None

    def mark_skipped(self, url: str, reason: str, when: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO skipped (url, reason, created_at) VALUES (?, ?, ?)",
                (url, reason, when),
            )

    def add_documents(self, documents: list[DocumentRecord], fetched_at: str) -> None:
        with self._conn:
            for document in documents:
                self._insert_document(document, fetched_at=fetched_at)

    def hash_exists(self, digest: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM articles WHERE content_hash = ?", (digest,)
        ).fetchone()
        return row is not None

    def insert_article(self, article: Article) -> int | None:
        with self._conn:
            try:
                cursor = self._conn.execute(
                    """
                    INSERT INTO articles (
                        url, title, source, country, language, summary, topics, sentiment,
                        author, published_at, fetched_at, word_count, content_hash, image_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article.url,
                        article.title,
                        article.source,
                        article.country,
                        article.language,
                        article.summary,
                        json.dumps(article.topics, ensure_ascii=False),
                        article.sentiment,
                        article.author,
                        article.published_at,
                        article.fetched_at,
                        article.word_count,
                        article.content_hash,
                        article.image_url,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            article_id = int(cursor.lastrowid)
            for document in article.documents:
                self._insert_document(document, fetched_at=article.fetched_at)
            return article_id

    def _insert_document(self, document: DocumentRecord, fetched_at: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO documents (
                url, article_url, title, kind, mime, size_bytes, local_path, sha256, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.url,
                document.article_url,
                document.title,
                document.kind,
                document.mime,
                document.size_bytes,
                document.local_path,
                document.sha256,
                fetched_at,
            ),
        )

    def mark_synced(self, urls: list[str], when: str) -> None:
        with self._conn:
            self._conn.executemany(
                "UPDATE articles SET synced_at = ? WHERE url = ?",
                [(when, url) for url in urls],
            )

    def recent(self, limit: int = 20, unsynced_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM articles"
        if unsynced_only:
            sql += " WHERE synced_at IS NULL"
        sql += " ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?"
        rows = self._conn.execute(sql, (limit,)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def pending_for_lark(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM articles WHERE synced_at IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def set_feedback(self, url: str, value: int, when: str) -> bool:
        """value: +1 like, -1 dislike, 0 clears. Returns False if the article is unknown."""
        if not self.url_exists(url):
            return False
        with self._conn:
            if value == 0:
                self._conn.execute("DELETE FROM feedback WHERE article_url = ?", (url,))
            else:
                self._conn.execute(
                    "INSERT INTO feedback (article_url, value, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(article_url) DO UPDATE SET value = excluded.value, "
                    "created_at = excluded.created_at",
                    (url, 1 if value > 0 else -1, when),
                )
        return True

    def feedback_map(self) -> dict[str, int]:
        return {
            row["article_url"]: row["value"]
            for row in self._conn.execute("SELECT article_url, value FROM feedback")
        }

    def feedback_rows(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT a.topics, a.source, f.value FROM feedback f "
            "JOIN articles a ON a.url = f.article_url"
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["topics"] = json.loads(data.get("topics") or "[]")
        return data

    def stats(self) -> dict:
        total = self._conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        documents = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
        by_source = {
            row["source"]: row["n"]
            for row in self._conn.execute(
                "SELECT source, COUNT(*) AS n FROM articles GROUP BY source ORDER BY n DESC"
            )
        }
        latest = self._conn.execute(
            "SELECT MAX(fetched_at) AS t FROM articles"
        ).fetchone()["t"]
        return {
            "articles": total,
            "documents": documents,
            "by_source": by_source,
            "last_fetched_at": latest,
            "db_path": str(self.path),
        }

    def start_run(self, trigger: str, sources: list[str], started_at: str) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO runs (started_at, trigger, sources) VALUES (?, ?, ?)",
                (started_at, trigger, json.dumps(sources, ensure_ascii=False)),
            )
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: str,
        fetched: int,
        inserted: int,
        duplicates: int,
        failed: int,
        documents: int,
        status: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE runs SET finished_at = ?, fetched = ?, inserted = ?, duplicates = ?,
                                failed = ?, documents = ?, status = ?
                WHERE id = ?
                """,
                (finished_at, fetched, inserted, duplicates, failed, documents, status, run_id),
            )
