from __future__ import annotations

import argparse
import html
import json
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Settings, load_settings
from .models import utc_now_iso
from .personalize import learn_weights, load_profile, rank
from .pipeline import Pipeline
from .store import Store

RUN_STATE: dict[str, object] = {"running": False, "message": "chưa chạy", "started_at": None}
RUN_LOCK = threading.Lock()

CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#14161c;--muted:#6b7280;--line:#e5e7eb;--accent:#1f6feb;--chip:#eef2ff;--chip-ink:#3345b0}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,Segoe UI,Roboto,"Helvetica Neue",Arial,sans-serif}
a{color:inherit;text-decoration:none}
.wrap{max-width:1100px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:22px;flex-wrap:wrap}
h1{font-size:26px;margin:0;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat b{display:block;font-size:22px;letter-spacing:-.02em}
.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:18px}
.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,select{font:inherit;padding:9px 11px;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--ink);min-width:140px}
button{font:inherit;font-weight:600;padding:9px 14px;border:0;border-radius:9px;background:var(--accent);color:#fff;cursor:pointer}
button.ghost{background:#fff;border:1px solid var(--line);color:var(--ink);font-weight:500}
button:disabled{opacity:.55;cursor:not-allowed}
.article{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px;display:grid;grid-template-columns:1fr 140px;gap:16px}
.article:hover{border-color:#cfd8e3;box-shadow:0 1px 2px rgba(16,24,40,.06)}
.article h2{font-size:17px;line-height:1.4;margin:0 0 6px;letter-spacing:-.01em}
.meta{color:var(--muted);font-size:12.5px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.chip{background:var(--chip);color:var(--chip-ink);border-radius:999px;padding:2px 9px;font-size:12px;font-weight:600}
.chip.sent{border-radius:6px;font-weight:600}
.neg{background:#fef2f2;color:#b91c1c}
.pos{background:#ecfdf3;color:#047857}
.neu{background:#f3f4f6;color:#4b5563}
.summary{color:#2b303b;font-size:14px;margin:0}
.thumb{width:140px;height:96px;border-radius:10px;background:#eef1f6 center/cover no-repeat;border:1px solid var(--line)}
.empty{color:var(--muted);text-align:center;padding:40px 0}
.votes{display:flex;gap:6px;align-items:center;margin-top:8px}
.vote,.tab{padding:4px 10px}
.vote.on,.tab.on{background:var(--chip);border-color:var(--accent)}
.status{font-size:13px;color:var(--muted)}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:26px}
@media(max-width:760px){.stats{grid-template-columns:repeat(2,1fr)}.article{grid-template-columns:1fr}.thumb{width:100%;height:150px}}
"""


def fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value[:16].replace("T", " ")
    return stamp.astimezone().strftime("%d/%m/%Y %H:%M")


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def css_url(value: object) -> str:
    """http(s) URL made safe for url('…') inside a style attribute, or '' if unusable."""
    text = str(value or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    # percent-encode everything that could close the url('...') or the attribute
    for char in "'\"()\\<>\n\r\t ":
        text = text.replace(char, f"%{ord(char):02X}")
    return html.escape(text)


def sentiment_class(sentiment: str) -> str:
    return {"positive": "pos", "negative": "neg"}.get(sentiment or "", "neu")


def sentiment_label(sentiment: str) -> str:
    return {"positive": "Tích cực", "negative": "Tiêu cực"}.get(sentiment or "", "Trung tính")


def option(value: str, label: str | None = None, selected: bool = False) -> str:
    mark = " selected" if selected else ""
    return f'<option value="{esc(value)}"{mark}>{esc(label or value)}</option>'


def render_page(
    rows: list[dict],
    stats: dict,
    *,
    sources: list[str],
    topics: list[str],
    filters: dict[str, str],
    interactive: bool = True,
    title: str = "Agent tổng hợp tin tức",
    view: str = "all",
    feedback: dict[str, int] | None = None,
    scores: dict[str, tuple[float, list[str]]] | None = None,
) -> str:
    feedback = feedback or {}
    scores = scores or {}
    cards = []
    for row in rows:
        vote = feedback.get(row.get("url"), 0)
        score_html = ""
        if row.get("url") in scores:
            value, reasons = scores[row["url"]]
            score_html = (
                f'<span class="chip" title="{esc("; ".join(reasons))}">★ {value:.1f}</span>'
            )
        actions = ""
        if interactive:
            actions = (
                f'<div class="votes" data-url="{esc(row.get("url"))}">'
                f'<button type="button" class="ghost vote{" on" if vote > 0 else ""}" data-v="1">👍</button>'
                f'<button type="button" class="ghost vote{" on" if vote < 0 else ""}" data-v="-1">👎</button>'
                f"{score_html}</div>"
            )
        chips = "".join(f'<span class="chip">{esc(t)}</span>' for t in row.get("topics") or [])
        thumb = row.get("image_url")
        safe_thumb = css_url(thumb)
        thumb_style = f' style="background-image:url(\'{safe_thumb}\')"' if safe_thumb else ""
        link_open = f'<a href="{esc(row["url"])}" target="_blank" rel="noreferrer">' if row.get("url") else "<div>"
        link_close = "</a>" if row.get("url") else "</div>"
        cards.append(
            f"""<article class="article">
  <div>
    <div class="meta">
      <strong>{esc(row.get("source"))}</strong>
      <span>·</span><span>{fmt_date(row.get("published_at") or row.get("fetched_at"))}</span>
      <span>·</span><span>{esc(row.get("word_count"))} từ</span>
      <span class="chip sent {sentiment_class(row.get("sentiment"))}">{sentiment_label(row.get("sentiment"))}</span>
    </div>
    {link_open}<h2>{esc(row.get("title"))}</h2>{link_close}
    <div class="chips">{chips}</div>
    <p class="summary">{esc(row.get("summary"))}</p>
    {actions}
  </div>
  <div class="thumb"{thumb_style}></div>
</article>"""
        )

    body = "\n".join(cards) if cards else '<div class="empty">Chưa có bài nào. Bấm “Chạy thu thập” hoặc chạy CLI.</div>'

    source_options = option("", "Tất cả nguồn", not filters.get("source"))
    source_options += "".join(
        option(name, name, filters.get("source") == name) for name in sources
    )
    topic_options = option("", "Tất cả chủ đề", not filters.get("topic"))
    topic_options += "".join(
        option(name, name, filters.get("topic") == name) for name in topics
    )

    controls = ""
    if interactive:
        tab_all = "" if view == "foryou" else " on"
        tab_you = " on" if view == "foryou" else ""
        controls = f"""
<div class="panel">
  <div class="filters" style="margin-bottom:10px">
    <a href="/"><button type="button" class="ghost tab{tab_all}">Tất cả</button></a>
    <a href="/?view=foryou"><button type="button" class="ghost tab{tab_you}">Dành cho bạn</button></a>
  </div>
  <div class="filters">
    <form method="get" action="/" class="filters">
      <input type="hidden" name="view" value="{esc(view)}">
      <select name="source">{source_options}</select>
      <select name="topic">{topic_options}</select>
      <input type="search" name="q" placeholder="Tìm tiêu đề, tóm tắt…" value="{esc(filters.get('q', ''))}">
      <button type="submit">Lọc</button>
      <a href="/"><button type="button" class="ghost">Xoá lọc</button></a>
    </form>
    <button id="run">Chạy thu thập</button>
    <select id="provider">
      <option value="mock">mock (nhanh)</option>
      <option value="commandcode">commandcode</option>
      <option value="ollama">ollama</option>
    </select>
    <span class="status" id="status">{esc(RUN_STATE.get("message"))}</span>
  </div>
</div>"""

    script = """
<script>
const btn = document.getElementById('run'), status = document.getElementById('status');
async function poll(){
  const r = await fetch('/api/run'); const d = await r.json();
  status.textContent = d.message;
  btn.disabled = d.running;
  if(!d.running && d.changed){ d.changed = false; location.reload(); }
}
btn?.addEventListener('click', async () => {
  btn.disabled = true;
  const provider = document.getElementById('provider').value;
  const r = await fetch('/api/collect?provider=' + provider + '&limit=1', {method:'POST'});
  const d = await r.json();
  status.textContent = d.message || d.error;
  poll();
});
document.querySelectorAll('.votes').forEach(box => {
  box.querySelectorAll('.vote').forEach(b => b.addEventListener('click', async () => {
    const next = b.classList.contains('on') ? 0 : Number(b.dataset.v);
    const r = await fetch('/api/feedback?value=' + next + '&url=' + encodeURIComponent(box.dataset.url), {method:'POST'});
    if (r.ok) location.reload();
  }));
});
setInterval(() => { if (btn) poll(); }, 1500);
</script>""" if interactive else ""

    return f"""<!doctype html>
<html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style>
</head><body><div class="wrap">
<header>
  <div>
    <h1>{esc(title)}</h1>
    <div class="sub">Python · smolagents · SQLite · Lark Base</div>
  </div>
  <div class="status">{esc(stats.get("db_path", ""))}</div>
</header>
<div class="stats">
  <div class="stat"><b>{esc(stats.get("articles", 0))}</b><span>Bài báo</span></div>
  <div class="stat"><b>{esc(stats.get("documents", 0))}</b><span>Tài liệu</span></div>
  <div class="stat"><b>{esc(len(stats.get("by_source") or {}))}</b><span>Nguồn</span></div>
  <div class="stat"><b>{esc((stats.get("last_fetched_at") or "—")[:10])}</b><span>Lần lấy gần nhất</span></div>
</div>
{controls}
{body}
<footer>{esc(len(rows))} bản ghi hiển thị · dữ liệu từ SQLite</footer>
</div>{script}</body></html>"""


def _filter_rows(rows: list[dict], filters: dict[str, str]) -> list[dict]:
    query = (filters.get("q") or "").strip().lower()
    result = []
    for row in rows:
        if filters.get("source") and row.get("source") != filters["source"]:
            continue
        if filters.get("topic") and filters["topic"] not in (row.get("topics") or []):
            continue
        if query and query not in f"{row.get('title','')} {row.get('summary','')}".lower():
            continue
        result.append(row)
    return result


def collect_facets(rows: list[dict]) -> tuple[list[str], list[str]]:
    sources = sorted({row.get("source") for row in rows if row.get("source")})
    topics: set[str] = set()
    for row in rows:
        topics.update(row.get("topics") or [])
    return sources, sorted(topics)


def run_collection(settings: Settings, provider: str, limit: int) -> None:
    try:
        overrides = {"dry_run": True, "llm_provider": provider, "data_dir": settings.data_dir}
        run_settings = load_settings(overrides)
        pipeline = Pipeline(run_settings)
        stats = pipeline.collect(limit=limit, with_documents=False, push=True)
        message = (
            f"xong: {stats.inserted} bài mới, {stats.duplicates} trùng, "
            f"{stats.failed} lỗi"
        )
        pipeline.store.close()
        close = getattr(pipeline.fetcher, "close", None)
        if callable(close):
            close()
        RUN_STATE.update({"message": message, "changed": True})
    except Exception as exc:
        RUN_STATE.update({"message": f"lỗi: {exc}", "changed": True})
    finally:
        RUN_STATE["running"] = False
        RUN_STATE["stopped_at"] = datetime.now().isoformat(timespec="seconds")


class Handler(BaseHTTPRequestHandler):
    settings: Settings
    server_version = "NewsAgent/1.0"

    def log_message(self, *args: object) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser clients (curl, tests) send no Origin
        return urlparse(origin).netloc == self.headers.get("Host", "")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            return self._json({"status": "ok"})
        if parsed.path == "/api/run":
            with RUN_LOCK:
                payload = dict(RUN_STATE)
                payload.setdefault("changed", False)
                if not RUN_STATE.get("running"):
                    RUN_STATE["changed"] = False  # one-shot: tell the client once, avoid reload loops
            return self._json(payload)

        with Store(self.settings.db_path) as store:
            stats = store.stats()
            rows = store.recent(limit=300)
            feedback = store.feedback_map()
            learned = learn_weights(store.feedback_rows())

        if parsed.path == "/api/articles":
            return self._json({"articles": rows, "stats": stats})
        if parsed.path == "/api/stats":
            return self._json(stats)
        if parsed.path not in {"/", "/index.html"}:
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

        params = parse_qs(parsed.query)
        filters = {key: (params.get(key) or [""])[0] for key in ("source", "topic", "q")}
        sources, topics = collect_facets(rows)
        filtered = _filter_rows(rows, filters)
        view = (params.get("view") or ["all"])[0]
        scores = None
        if view == "foryou":
            ranked = rank(
                filtered, load_profile(self.settings.profile_path), learned, feedback=feedback
            )
            filtered = [item.row for item in ranked]
            scores = {item.row["url"]: (item.score, item.reasons) for item in ranked}
        page = render_page(
            filtered, stats, sources=sources, topics=topics, filters=filters,
            view=view, feedback=feedback, scores=scores,
        )
        self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._same_origin():
            return self._json({"error": "cross-origin request blocked"}, HTTPStatus.FORBIDDEN)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/feedback":
            try:
                value = int((params.get("value") or ["0"])[0])
            except ValueError:
                return self._json({"error": "invalid value"}, HTTPStatus.BAD_REQUEST)
            url = (params.get("url") or [""])[0]
            if value not in (-1, 0, 1) or not url:
                return self._json({"error": "invalid feedback"}, HTTPStatus.BAD_REQUEST)
            with Store(self.settings.db_path) as store:
                found = store.set_feedback(url, value, utc_now_iso())
            if not found:
                return self._json({"error": "unknown article"}, HTTPStatus.NOT_FOUND)
            return self._json({"ok": True})
        if parsed.path != "/api/collect":
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        provider = (params.get("provider") or ["mock"])[0]
        try:
            limit = int((params.get("limit") or ["1"])[0])
        except ValueError:
            return self._json({"error": "invalid limit"}, HTTPStatus.BAD_REQUEST)
        if not 1 <= limit <= 20:
            return self._json({"error": "limit must be 1-20"}, HTTPStatus.BAD_REQUEST)
        if provider not in {"mock", "commandcode", "ollama"}:
            return self._json({"error": "unknown provider"}, HTTPStatus.BAD_REQUEST)
        with RUN_LOCK:
            if RUN_STATE.get("running"):
                return self._json({"error": "đang chạy", "message": RUN_STATE.get("message")},
                                  HTTPStatus.CONFLICT)
            RUN_STATE.update(
                running=True,
                message=f"đang thu thập ({provider})…",
                changed=False,
                started_at=datetime.now().isoformat(timespec="seconds"),
            )
        threading.Thread(
            target=run_collection,
            args=(self.settings, provider, limit),
            daemon=True,
        ).start()
        return self._json({"started": True, "message": RUN_STATE["message"]})


def create_server(settings: Settings, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"settings": settings})
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.web", description="Web demo cho agent tin tức")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    overrides = {"data_dir": args.data_dir} if args.data_dir else {}
    settings = load_settings(overrides)
    server = create_server(settings, args.host, args.port)
    print(f"Web demo: http://{args.host}:{args.port}  (db: {settings.db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
