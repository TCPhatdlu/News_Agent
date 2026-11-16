from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_settings, load_sources
from .pipeline import Pipeline


def _print(message: str) -> None:
    print(message, flush=True)


def _build_pipeline(args) -> Pipeline:
    settings = load_settings({"dry_run": args.dry_run, "llm_provider": args.llm})
    return Pipeline(settings)


def cmd_sources(args) -> int:
    settings = load_settings()
    for source in load_sources(settings.sources_path):
        _print(f"{source.name:<14} {source.kind:<5} {source.country:<4} {source.url}")
    return 0


def cmd_collect(args) -> int:
    pipeline = _build_pipeline(args)
    try:
        if args.agent:
            from .agent import AgentUnavailable, NewsAgent

            try:
                agent = NewsAgent(pipeline.settings, pipeline)
                print(agent.run(max_sources=args.agent_sources, per_source=args.agent_articles))
                return 0
            except AgentUnavailable as exc:
                _print(f"! {exc}\n  Falling back to the deterministic pipeline.")
            except Exception as exc:
                _print(f"! agent run failed: {exc}\n  Falling back to the deterministic pipeline.")
        stats = pipeline.collect(
            only=args.source,
            limit=args.limit,
            with_documents=not args.no_documents,
            push=not args.no_push,
            log=_print if not args.quiet else None,
        )
    finally:
        pipeline.store.close()
        close = getattr(pipeline.fetcher, "close", None)
        if callable(close):
            close()

    _print(
        f"\nfetched={stats.fetched} inserted={stats.inserted} duplicates={stats.duplicates} "
        f"failed={stats.failed} documents={stats.documents} pushed={stats.pushed}"
    )
    if stats.errors:
        _print("errors:")
        for error in stats.errors[:20]:
            _print(f"  - {error}")
    return 0 if not stats.errors else 1


def cmd_stats(args) -> int:
    pipeline = _build_pipeline(args)
    try:
        _print(json.dumps(pipeline.store.stats(), ensure_ascii=False, indent=2))
    finally:
        pipeline.store.close()
    return 0


def cmd_recent(args) -> int:
    pipeline = _build_pipeline(args)
    try:
        rows = pipeline.store.recent(limit=args.limit, unsynced_only=args.unsynced)
    finally:
        pipeline.store.close()
    for row in rows:
        _print(
            f"{row['published_at'] or row['fetched_at']} | {row['source']:<12} | "
            f"{row['title'][:80]}\n    {','.join(row['topics'])} | {row['summary'][:140]}"
        )
    return 0


def cmd_digest(args) -> int:
    from .personalize import learn_weights, load_profile, rank

    pipeline = _build_pipeline(args)
    try:
        store = pipeline.store
        rows = store.recent(limit=args.pool)
        ranked = rank(
            rows,
            load_profile(pipeline.settings.profile_path),
            learn_weights(store.feedback_rows()),
            feedback=store.feedback_map(),
            top=args.top,
        )
    finally:
        pipeline.store.close()
    if not ranked:
        _print("khong co bai phu hop (thu `collect` truoc hoac noi long profile)")
        return 0
    for index, item in enumerate(ranked, 1):
        row = item.row
        _print(
            f"{index}. [{item.score:.2f}] {row['title'][:80]} ({row['source']})\n"
            f"    {', '.join(row['topics'])} | {'; '.join(item.reasons)}\n    {row['url']}"
        )
    return 0


def cmd_feedback(args) -> int:
    from .models import utc_now_iso

    value = 0 if args.clear else (-1 if args.dislike else 1)
    pipeline = _build_pipeline(args)
    try:
        found = pipeline.store.set_feedback(args.url, value, utc_now_iso())
    finally:
        pipeline.store.close()
    if not found:
        _print("khong tim thay bai voi URL nay")
        return 1
    _print({1: "da thich", -1: "da danh dau khong thich", 0: "da xoa phan hoi"}[value])
    return 0


def cmd_publish(args) -> int:
    from .publish import CHANNELS, Publisher

    pipeline = _build_pipeline(args)
    try:
        publisher = Publisher.from_settings(pipeline.store, pipeline.settings)
        if args.action == "queue":
            channels = args.channel or []
            if not channels or any(c not in CHANNELS for c in channels):
                _print(f"can --channel trong: {', '.join(CHANNELS)}")
                return 1
            created = publisher.queue(args.target, channels)
            _print(f"da tao {len(created)} bai: {created}")
        elif args.action == "list":
            for post in pipeline.store.list_posts(args.status):
                mode = "live" if publisher.is_live(post["channel"]) else "dry-run"
                _print(
                    f"#{post['id']:<4} {post['status']:<9} {post['channel']:<9} {mode:<7} "
                    f"{(post.get('article_title') or post['article_url'])[:60]}"
                    + (f"  post_id={post['post_id']}" if post.get("post_id") else "")
                )
        elif args.action == "approve":
            publisher.approve(int(args.target), args.text)
            _print("da duyet")
        elif args.action == "reject":
            publisher.reject(int(args.target))
            _print("da tu choi")
        elif args.action == "send":
            results = (
                publisher.send_approved() if args.target == "all"
                else [publisher.send(int(args.target))]
            )
            for result in results:
                _print(json.dumps(result, ensure_ascii=False))
            return 0 if all(r["ok"] for r in results) else 1
    except ValueError as exc:
        _print(f"loi: {exc}")
        return 1
    finally:
        pipeline.store.close()
    return 0


def cmd_health(args) -> int:
    pipeline = _build_pipeline(args)
    try:
        for row in pipeline.store.source_health():
            state = "OK " if not row["consecutive_failures"] else f"LOI x{row['consecutive_failures']}"
            _print(f"{state:<7} {row['source']:<14} ok={row['last_ok_at'] or '-'}  {row['last_error'] or ''}"[:160])
        _print("\ncanh bao gan day:")
        for alert in pipeline.store.recent_alerts(10):
            _print(f"  {alert['created_at']} [{alert['level']}] {alert['message']} -> {alert['delivered']}")
    finally:
        pipeline.store.close()
    return 0


def cmd_export(args) -> int:
    pipeline = _build_pipeline(args)
    try:
        path = pipeline.export_jsonl(Path(args.out), limit=args.limit)
    finally:
        pipeline.store.close()
    _print(f"exported to {path}")
    return 0


def cmd_url(args) -> int:
    from .config import Source
    from .models import ArticleRef

    pipeline = _build_pipeline(args)
    source = Source(name=args.name, url=args.url, kind="html", country=args.country, language="vi")
    ref = ArticleRef(title="", url=args.url, source=source.name)
    try:
        article = pipeline.process_ref(ref, source, with_documents=not args.no_documents)
    finally:
        pipeline.store.close()
        close = getattr(pipeline.fetcher, "close", None)
        if callable(close):
            close()

    if article is None:
        _print("duplicate or unsupported page (no usable body)")
        return 0
    _print(f"{article.title}\n  topics: {', '.join(article.topics)}")
    _print(f"  summary: {article.summary}")
    _print(f"  words={article.word_count} documents={len(article.documents)}")
    for document in article.documents:
        _print(f"    - {document.kind:<5} {document.size_bytes:>8} B  {document.local_path}")
    return 0


def cmd_push(args) -> int:
    pipeline = _build_pipeline(args)
    try:
        pushed = pipeline.sync_pending(limit=args.limit)
        _print(f"pushed {pushed} record(s) (dry_run={pipeline.sink.dry_run or not pipeline.sink.configured})")
    finally:
        pipeline.store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-agent",
        description="Agent tong hop tin tuc va tai lieu lien quan (Python, smolagents, Lark Base).",
    )
    parser.add_argument("--llm", choices=["ollama", "mock", "commandcode"], default="ollama")
    parser.add_argument("--dry-run", action="store_true", help="khong ghi len Lark Base")
    sub = parser.add_subparsers(dest="command", required=True)

    sub_sources = sub.add_parser("sources", help="liet ke nguon tin")
    sub_sources.set_defaults(func=cmd_sources)

    collect = sub.add_parser("collect", help="thu thap, tom tat va luu bai moi")
    collect.add_argument("--source", action="append", help="chi chay nguon nay (lap lai duoc)")
    collect.add_argument("--limit", type=int, help="so bai toi da moi nguon")
    collect.add_argument("--no-documents", action="store_true", help="bo qua tai lieu dinh kem")
    collect.add_argument("--no-push", action="store_true", help="khong day len Lark Base")
    collect.add_argument("--quiet", action="store_true")
    collect.add_argument("--agent", action="store_true", help="dung smolagents ToolCallingAgent")
    collect.add_argument("--agent-sources", type=int, default=3)
    collect.add_argument("--agent-articles", type=int, default=2)
    collect.set_defaults(func=cmd_collect)

    stats = sub.add_parser("stats", help="thong ke co so du lieu")
    stats.set_defaults(func=cmd_stats)

    recent = sub.add_parser("recent", help="liet ke bai moi nhat")
    recent.add_argument("--limit", type=int, default=10)
    recent.add_argument("--unsynced", action="store_true")
    recent.set_defaults(func=cmd_recent)

    digest = sub.add_parser("digest", help="ban tin ca nhan hoa theo config/profile.yaml + phan hoi")
    digest.add_argument("--top", type=int, default=5)
    digest.add_argument("--pool", type=int, default=300, help="so bai gan nhat de xep hang")
    digest.set_defaults(func=cmd_digest)

    feedback = sub.add_parser("feedback", help="thich/khong thich mot bai de he thong hoc")
    feedback.add_argument("url")
    group = feedback.add_mutually_exclusive_group()
    group.add_argument("--dislike", action="store_true")
    group.add_argument("--clear", action="store_true")
    feedback.set_defaults(func=cmd_feedback)

    publish = sub.add_parser("publish", help="hang dang bai: queue/list/approve/reject/send")
    publish.add_argument("action", choices=["queue", "list", "approve", "reject", "send"])
    publish.add_argument("target", nargs="?", default="", help="URL (queue), id bai dang, hoac 'all' (send)")
    publish.add_argument("--channel", action="append", help="telegram|lark|facebook|twitter (lap lai duoc)")
    publish.add_argument("--status", help="loc khi list: pending|approved|published|failed|rejected")
    publish.add_argument("--text", help="noi dung da sua khi approve")
    publish.set_defaults(func=cmd_publish)

    health = sub.add_parser("health", help="suc khoe nguon tin va canh bao")
    health.set_defaults(func=cmd_health)

    export = sub.add_parser("export", help="xuat JSONL")
    export.add_argument("--out", default="data/articles.jsonl")
    export.add_argument("--limit", type=int, default=1000)
    export.set_defaults(func=cmd_export)

    ingest = sub.add_parser("ingest-url", help="xu ly mot URL bat ky (ke ca trang co tai lieu dinh kem)")
    ingest.add_argument("url")
    ingest.add_argument("--name", default="Manual", help="ten nguon gan cho ban ghi")
    ingest.add_argument("--country", default="VN")
    ingest.add_argument("--no-documents", action="store_true")
    ingest.set_defaults(func=cmd_url)

    push = sub.add_parser("push", help="day cac ban ghi chua dong bo len Lark Base")
    push.add_argument("--limit", type=int, default=100)
    push.set_defaults(func=cmd_push)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _print("interrupted")
        return 130
    except Exception as exc:
        _print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
