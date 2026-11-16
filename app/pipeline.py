from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adapters import get_adapter
from .alerts import AlertPolicy, Notifier, evaluate_run
from .config import Settings, Source, get_env, load_sources
from .semantic import SemanticDeduper, build_embedder
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
    similar: int = 0          # inserted but grouped under an earlier story (semantic duplicate)
    failed_articles: int = 0
    crash: str | None = None
    alerts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    inserted_urls: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "similar": self.similar,
            "failed": self.failed,
            "documents": self.documents,
            "pushed": self.pushed,
            "alerts": self.alerts,
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
        notifier: Notifier | None = None,
        deduper: SemanticDeduper | None | bool = None,
    ) -> None:
        self.settings = settings
        self.store = store or Store(settings.db_path)
        self.notifier = notifier or Notifier.from_env(settings)
        self.alert_policy = AlertPolicy(
            source_failures=int(get_env("ALERT_SOURCE_FAILURES", "2")),
        )
        if deduper is None and get_env("SEMANTIC_DEDUP", "true").lower() != "false":
            deduper = SemanticDeduper(
                self.store,
                build_embedder(settings, get_env("SEMANTIC_EMBEDDER", "hash"),
                               get_env("SEMANTIC_MODEL", "nomic-embed-text")),
                threshold=float(get_env("SEMANTIC_THRESHOLD", "0.45")),
                window_hours=int(get_env("SEMANTIC_WINDOW_HOURS", "72")),
            )
        self.deduper = deduper or None  # pass deduper=False to switch semantic dedup off
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
        source_failures: dict[str, int] = {}

        try:
            for source in sources:
                self._collect_source(source, limit, with_documents, stats, source_failures, log)
        except Exception as exc:  # anything unexpected: still close the run and raise an alert
            stats.crash = f"{type(exc).__name__}: {exc}"
            stats.errors.append(f"crash: {stats.crash}")
            log(f"! run crashed: {stats.crash}")

        if push and stats.inserted_urls:
            try:
                stats.pushed = self.sync_pending(limit=500)
            except Exception as exc:
                stats.failed += 1
                stats.errors.append(f"lark: {exc}")
                log(f"! Lark push failed: {exc}")

        for level, message in evaluate_run(stats, source_failures, self.alert_policy):
            delivered = self.notifier.send(level, message)
            self.store.add_alert(level, message, delivered, utc_now_iso())
            stats.alerts.append(message)
            log(f"! alert ({delivered}): {message}")

        self.store.finish_run(
            run_id,
            finished_at=utc_now_iso(),
            fetched=stats.fetched,
            inserted=stats.inserted,
            duplicates=stats.duplicates,
            failed=stats.failed,
            documents=stats.documents,
            similar=stats.similar,
            status="error" if stats.crash else ("ok" if not stats.errors else "partial"),
            error=stats.crash or (stats.errors[0][:300] if stats.errors else None),
        )
        return stats

    def _collect_source(self, source, limit, with_documents, stats, source_failures, log) -> None:
        effective_limit = limit or source.max_articles or self.settings.max_articles_per_source
        try:
            refs = get_adapter(source.kind).list_articles(source, self.fetcher, limit=effective_limit)
        except Exception as exc:
            stats.failed += 1
            stats.errors.append(f"{source.name}: feed error: {exc}")
            log(f"[{source.name}] feed error: {exc}")
            source_failures[source.name] = self.store.record_source_error(
                source.name, f"feed: {exc}", utc_now_iso()
            )
            return

        log(f"[{source.name}] {len(refs)} article(s) in feed")
        ok = failed = 0
        last_error = ""
        for ref in refs:
            stats.fetched += 1
            try:
                outcome = self.process_ref(ref, source, with_documents=with_documents)
            except Exception as exc:
                stats.failed += 1
                stats.failed_articles += 1
                failed += 1
                last_error = str(exc)
                stats.errors.append(f"{ref.url}: {exc}")
                log(f"  ! {ref.url}: {exc}")
                continue
            ok += 1
            if outcome is None:
                stats.duplicates += 1
                log(f"  = duplicate: {ref.title[:60]}")
                continue
            stats.inserted += 1
            stats.documents += len(outcome.documents)
            stats.inserted_urls.append(outcome.url)
            if outcome.cluster_of:
                stats.similar += 1
                log(f"  ~ same story as {outcome.cluster_of}: {outcome.title[:60]}")
            else:
                log(f"  + {outcome.title[:60]} [{', '.join(outcome.topics)}] {outcome.word_count} words")

        # a feed whose every article failed is broken even though the feed itself loaded
        if refs and ok == 0 and failed:
            source_failures[source.name] = self.store.record_source_error(
                source.name, f"all {failed} articles failed: {last_error}", utc_now_iso()
            )
        else:
            self.store.record_source_ok(source.name, len(refs), utc_now_iso())

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

        cluster_of = None
        vector = None
        if self.deduper is not None:
            try:
                vector = self.deduper.embedder.embed(self.deduper.text_for(title, extracted.text))
                cluster_of, _score = self.deduper.find_lead(vector)
            except Exception:
                vector = None  # embedding is an optimisation; never lose the article over it

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
            cluster_of=cluster_of,
        )
        # insert first, download after: a lost insert race no longer leaves orphan files
        if self.store.insert_article(article) is None:
            return None
        if vector:
            self.store.save_embedding(article.url, self.deduper.embedder.name, vector)

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
