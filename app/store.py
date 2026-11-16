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

CREATE TABLE IF NOT EXISTS source_health (
    source               TEXT PRIMARY KEY,
    last_ok_at           TEXT,
    last_error_at        TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_count           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    level       TEXT NOT NULL,
    message     TEXT NOT NULL,
    delivered   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    article_url TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    vector      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_url  TEXT NOT NULL,
    channel      TEXT NOT NULL,
    text         TEXT NOT NULL,
    status       TEXT NOT NULL,          -- pending | approved | rejected | published | failed
    post_id      TEXT,
    dry_run      INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TEXT NOT NULL,
    decided_at   TEXT,
    published_at TEXT,
    UNIQUE (article_url, channel)
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
        # columns added after the first release: migrate old databases in place
        self._ensure_column("articles", "cluster_of", "TEXT")
        self._ensure_column("runs", "similar", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("runs", "error", "TEXT")
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        names = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in names:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

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
                        author, published_at, fetched_at, word_count, content_hash, image_url,
                        cluster_of
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        article.cluster_of,
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
            # semantic duplicates stay out of Lark: the lead article already represents the story
            "SELECT * FROM articles WHERE synced_at IS NULL AND cluster_of IS NULL ORDER BY id LIMIT ?",
            (limit,),
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
        similar: int = 0,
        error: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE runs SET finished_at = ?, fetched = ?, inserted = ?, duplicates = ?,
                                failed = ?, documents = ?, status = ?, similar = ?, error = ?
                WHERE id = ?
                """,
                (finished_at, fetched, inserted, duplicates, failed, documents, status,
                 similar, error, run_id),
            )

    def recent_runs(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["sources"] = json.loads(data.get("sources") or "[]")
            result.append(data)
        return result

    def stable_streak_days(self, now_iso: str) -> float | None:
        """Days since the last run that was not fully ok (None when there are no runs)."""
        from datetime import datetime

        first = self._conn.execute("SELECT MIN(started_at) AS t FROM runs").fetchone()["t"]
        if not first:
            return None
        bad = self._conn.execute(
            "SELECT MAX(started_at) AS t FROM runs WHERE status NOT IN ('ok', 'running')"
        ).fetchone()["t"]
        since = datetime.fromisoformat(bad or first)
        return max(0.0, (datetime.fromisoformat(now_iso) - since).total_seconds() / 86400)

    # ---------------------------------------------------------------- source health
    def record_source_ok(self, source: str, count: int, when: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO source_health (source, last_ok_at, consecutive_failures, last_count) "
                "VALUES (?, ?, 0, ?) ON CONFLICT(source) DO UPDATE SET last_ok_at = excluded.last_ok_at, "
                "consecutive_failures = 0, last_count = excluded.last_count",
                (source, when, count),
            )

    def record_source_error(self, source: str, error: str, when: str) -> int:
        """Returns how many runs in a row this source has now failed."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO source_health (source, last_error_at, last_error, consecutive_failures) "
                "VALUES (?, ?, ?, 1) ON CONFLICT(source) DO UPDATE SET last_error_at = excluded.last_error_at, "
                "last_error = excluded.last_error, consecutive_failures = consecutive_failures + 1",
                (source, when, error[:500]),
            )
        row = self._conn.execute(
            "SELECT consecutive_failures AS n FROM source_health WHERE source = ?", (source,)
        ).fetchone()
        return int(row["n"])

    def source_health(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM source_health ORDER BY consecutive_failures DESC, source"
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- alerts
    def add_alert(self, level: str, message: str, delivered: str, when: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO alerts (level, message, delivered, created_at) VALUES (?, ?, ?, ?)",
                (level, message, delivered, when),
            )

    def recent_alerts(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- semantic dedup
    def save_embedding(self, url: str, model: str, vector: dict[int, float]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO embeddings (article_url, model, vector) VALUES (?, ?, ?)",
                (url, model, json.dumps(vector)),
            )

    def embeddings_since(self, model: str, since_iso: str) -> list[tuple[str, dict[int, float]]]:
        """Vectors of lead articles (not themselves duplicates) fetched after `since_iso`."""
        rows = self._conn.execute(
            "SELECT e.article_url, e.vector FROM embeddings e JOIN articles a ON a.url = e.article_url "
            "WHERE e.model = ? AND a.fetched_at >= ? AND a.cluster_of IS NULL",
            (model, since_iso),
        ).fetchall()
        return [
            (row["article_url"], {int(k): v for k, v in json.loads(row["vector"]).items()})
            for row in rows
        ]

    def cluster_members(self) -> dict[str, list[dict]]:
        """lead url -> other articles about the same story."""
        groups: dict[str, list[dict]] = {}
        for row in self._conn.execute(
            "SELECT url, title, source, cluster_of FROM articles WHERE cluster_of IS NOT NULL"
        ):
            groups.setdefault(row["cluster_of"], []).append(dict(row))
        return groups

    def duplicate_ratio(self, since_iso: str) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, SUM(cluster_of IS NOT NULL) AS dup FROM articles "
            "WHERE fetched_at >= ?",
            (since_iso,),
        ).fetchone()
        return int(row["dup"] or 0), int(row["total"] or 0)

    # ---------------------------------------------------------------- publishing
    def get_article(self, url: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM articles WHERE url = ?", (url,)).fetchone()
        return self._row_to_dict(row) if row else None

    def queue_post(self, url: str, channel: str, text: str, status: str, when: str) -> int | None:
        """Returns the post id, or None if this article is already queued for that channel."""
        with self._conn:
            try:
                cursor = self._conn.execute(
                    "INSERT INTO posts (article_url, channel, text, status, created_at, decided_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (url, channel, text, status, when, when if status == "approved" else None),
                )
            except sqlite3.IntegrityError:
                return None
        return int(cursor.lastrowid)

    def get_post(self, post_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT p.*, a.title AS article_title, a.source AS article_source FROM posts p "
            "LEFT JOIN articles a ON a.url = p.article_url WHERE p.id = ?",
            (post_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_posts(self, status: str | None = None, limit: int = 200) -> list[dict]:
        sql = (
            "SELECT p.*, a.title AS article_title, a.source AS article_source FROM posts p "
            "LEFT JOIN articles a ON a.url = p.article_url"
        )
        params: list = []
        if status:
            sql += " WHERE p.status = ?"
            params.append(status)
        sql += " ORDER BY p.id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def post_counts(self) -> dict[str, int]:
        return {
            row["status"]: row["n"]
            for row in self._conn.execute("SELECT status, COUNT(*) AS n FROM posts GROUP BY status")
        }

    def update_post(self, row_id: int, /, **fields) -> None:
        # positional-only: `post_id` is also a column (the platform's id) passed in **fields
        allowed = {"text", "status", "post_id", "dry_run", "error", "decided_at", "published_at"}
        keys = [key for key in fields if key in allowed]
        if not keys:
            return
        with self._conn:
            self._conn.execute(
                f"UPDATE posts SET {', '.join(f'{key} = ?' for key in keys)} WHERE id = ?",
                [fields[key] for key in keys] + [row_id],
            )
