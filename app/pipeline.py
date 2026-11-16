from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adapters import get_adapter
from .config import Settings, Source, load_sources
from .documents import download_documents
from .extract import extract_article
from .lark import LarkSink
from .llm import LLMClient, heuristic_enrichment
from .models import Article, ArticleRef, content_hash, utc_now_iso
from .net import Fetcher, HttpFetcher
from .store import Store

Logger = Callable[[str], None]


@dataclass
class RunStats:
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    failed: int = 0
    documents: int = 0
    pushed: int = 0
    errors: list[str] = field(default_factory=list)
    inserted_urls: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "failed": self.failed,
            "documents": self.documents,
            "pushed": self.pushed,
            "errors": self.errors,
        }


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        store: Store | None = None,
        llm: LLMClient | None = None,
        fetcher: Fetcher | None = None,
        sink: LarkSink | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or Store(settings.db_path)
        from .llm import build_llm

        self.llm = llm or build_llm(settings)
        self.fetcher = fetcher or HttpFetcher(
            timeout=settings.request_timeout, user_agent=settings.user_agent
        )
        self.sink = sink or LarkSink(
            app_id=settings.lark_app_id,
            app_secret=settings.lark_app_secret,
            app_token=settings.lark_app_token,
            table_id=settings.lark_table_id,
            base_url=settings.lark_base_url,
            outbox_dir=settings.outbox_dir,
            dry_run=settings.dry_run,
        )

    def sources(self, only: list[str] | None = None) -> list[Source]:
        sources = load_sources(self.settings.sources_path)
        if only:
            wanted = {name.lower() for name in only}
            sources = [s for s in sources if s.name.lower() in wanted]
            if not sources:
                raise ValueError(f"no source matched {only}")
        return sources

    def collect(
        self,
        *,
        only: list[str] | None = None,
        limit: int | None = None,
        with_documents: bool = True,
        push: bool = True,
        log: Logger | None = None,
    ) -> RunStats:
        log = log or (lambda message: None)
        sources = self.sources(only)
        stats = RunStats()
        started = utc_now_iso()
        run_id = self.store.start_run("cli", [s.name for s in sources], started)

        for source in sources:
            effective_limit = limit or source.max_articles or self.settings.max_articles_per_source
            try:
                refs = get_adapter(source.kind).list_articles(
                    source, self.fetcher, limit=effective_limit
                )
            except Exception as exc:
                stats.failed += 1
                stats.errors.append(f"{source.name}: feed error: {exc}")
                log(f"[{source.name}] feed error: {exc}")
                continue

            log(f"[{source.name}] {len(refs)} article(s) in feed")
            for ref in refs:
                stats.fetched += 1
                try:
                    outcome = self.process_ref(ref, source, with_documents=with_documents)
                except Exception as exc:
                    stats.failed += 1
                    stats.errors.append(f"{ref.url}: {exc}")
                    log(f"  ! {ref.url}: {exc}")
                    continue
                if outcome is None:
                    stats.duplicates += 1
                    log(f"  = duplicate: {ref.title[:60]}")
                else:
                    stats.inserted += 1
                    stats.documents += len(outcome.documents)
                    stats.inserted_urls.append(outcome.url)
                    log(
                        f"  + {outcome.title[:60]} "
                        f"[{', '.join(outcome.topics)}] {outcome.word_count} words"
                    )

        if push and stats.inserted_urls:
            try:
                stats.pushed = self.sync_pending(limit=500)
            except Exception as exc:
                stats.failed += 1
                stats.errors.append(f"lark: {exc}")
                log(f"! Lark push failed: {exc}")

        self.store.finish_run(
            run_id,
            finished_at=utc_now_iso(),
            fetched=stats.fetched,
            inserted=stats.inserted,
            duplicates=stats.duplicates,
            failed=stats.failed,
            documents=stats.documents,
            status="ok" if not stats.errors else "partial",
        )
        return stats

    def sync_pending(self, limit: int = 100) -> int:
        """Push unsynced rows to Lark; only rows Lark actually accepted get marked as synced."""
        pending = self.store.pending_for_lark(limit=limit)
        real = not self.sink.dry_run and self.sink.configured
        try:
            pushed = self.sink.push(pending)
        finally:
            if real and self.sink.pushed_urls:
                self.store.mark_synced(self.sink.pushed_urls, utc_now_iso())
        return pushed

    def process_ref(self, ref: ArticleRef, source: Source, *, with_documents: bool) -> Article | None:
        """Fetch, enrich and store one article. Returns None for duplicates."""
        if self.store.url_seen(ref.url):
            return None

        page = self.fetcher.text(ref.url)
        extracted = extract_article(
            page, ref.url, max_documents=self.settings.max_documents_per_article
        )
        title = extracted.title or ref.title
        if not extracted.text or len(extracted.text) < 80:
            # remember it so later runs do not re-download a page that has no usable body
            self.store.mark_skipped(ref.url, "body-too-short", utc_now_iso())
            raise ValueError("article body too short to summarize")

        digest = content_hash(title, extracted.text)
        if self.store.hash_exists(digest):
            self.store.mark_skipped(ref.url, "duplicate-content", utc_now_iso())
            return None

        try:
            enrichment = self.llm.enrich(title, extracted.text, source.name)
        except Exception:
            enrichment = heuristic_enrichment(title, extracted.text, source.name)

        article = Article(
            url=ref.url,
            title=title,
            source=source.name,
            country=source.country,
            language=extracted.language or source.language,
            summary=enrichment.summary,
            topics=enrichment.topics,
            sentiment=enrichment.sentiment,
            author=extracted.author,
            published_at=extracted.published_at or ref.published_at,
            fetched_at=utc_now_iso(),
            word_count=len(extracted.text.split()),
            content_hash=digest,
            image_url=extracted.image_url,
            documents=[],
        )
        # insert first, download after: a lost insert race no longer leaves orphan files
        if self.store.insert_article(article) is None:
            return None

        if with_documents and extracted.document_links:
            article.documents = download_documents(
                extracted.document_links,
                article_url=ref.url,
                fetcher=self.fetcher,
                dest_dir=self.settings.documents_dir,
                max_bytes=self.settings.max_document_mb * 1024 * 1024,
                limit=self.settings.max_documents_per_article,
            )
            self.store.add_documents(article.documents, article.fetched_at)
        return article

    _process_ref = process_ref  # backwards-compatible alias

    def export_jsonl(self, path: Path | None = None, limit: int = 1000) -> Path:
        path = path or (self.settings.data_dir / "articles.jsonl")
        rows = self.store.recent(limit=limit)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path


def offline_pipeline(settings: Settings, pages: dict[str, str], *, llm: LLMClient | None = None) -> Pipeline:
    from .net import StaticFetcher

    return Pipeline(settings, fetcher=StaticFetcher(pages), llm=llm)
