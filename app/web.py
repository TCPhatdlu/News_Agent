from __future__ import annotations

import argparse
import html
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

from .config import Settings, load_settings
from .models import utc_now_iso
from .personalize import learn_weights, load_profile, rank
from .pipeline import Pipeline
from .publish import CHANNEL_LABELS, CHANNELS, Publisher
from .store import Store

RUN_STATE: dict[str, object] = {"running": False, "message": "chưa chạy", "started_at": None}
RUN_LOCK = threading.Lock()

POST_STATUS_LABELS = {
    "pending": "Chờ duyệt",
    "approved": "Đã duyệt",
    "published": "Đã đăng",
    "failed": "Lỗi",
    "rejected": "Từ chối",
}

# Layout borrows from tools people already know: Feedly/Inoreader (left rail with views, topics,
# sources), Google News (one story, "+N nguồn" cluster), Buffer (approval queue by status with
# editable copy), and a status page for run health.
CSS = """
:root{--bg:#f5f6f8;--panel:#fff;--ink:#16181d;--muted:#6b7280;--line:#e6e8ec;--accent:#2563eb;
--accent-ink:#fff;--soft:#eef2ff;--soft-ink:#3046b8;--ok:#0f9d58;--warn:#d97706;--bad:#dc2626;
--pos-bg:#ecfdf3;--pos:#047857;--neg-bg:#fef2f2;--neg:#b91c1c;--neu-bg:#f3f4f6;--neu:#4b5563}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1115;--panel:#171a21;--ink:#e7e9ee;
--muted:#9aa1ad;--line:#262b35;--soft:#1d2540;--soft-ink:#a9b8ff;--pos-bg:#0f2a1f;--pos:#5dd39e;
--neg-bg:#2c1416;--neg:#ff8a8a;--neu-bg:#22262e;--neu:#b7bdc8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14.5px/1.55 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}
a{color:inherit;text-decoration:none}
.app{display:grid;grid-template-columns:248px 1fr;min-height:100vh}
.rail{background:var(--panel);border-right:1px solid var(--line);padding:18px 12px;position:sticky;top:0;height:100vh;overflow:auto}
.brand{font-weight:700;font-size:16px;padding:4px 10px 14px;letter-spacing:-.01em}
.nav a,.facet a{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 10px;border-radius:8px;color:var(--ink)}
.nav a:hover,.facet a:hover{background:var(--bg)}
.nav a.on,.facet a.on{background:var(--soft);color:var(--soft-ink);font-weight:600}
.badge{font-size:11.5px;font-weight:700;background:var(--accent);color:var(--accent-ink);border-radius:999px;padding:0 7px;min-width:20px;text-align:center}
.badge.bad{background:var(--bad)}
.count{color:var(--muted);font-size:12px}
.rail h4{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:18px 10px 6px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:8px;background:var(--line)}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}
.main{padding:20px 28px 60px;max-width:1040px;width:100%}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.top h1{font-size:22px;margin:0 auto 0 0;letter-spacing:-.02em}
input,select,textarea{font:inherit;padding:8px 11px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink)}
input[type=search]{min-width:240px}
button{font:inherit;font-weight:600;padding:8px 14px;border:0;border-radius:9px;background:var(--accent);color:var(--accent-ink);cursor:pointer}
button.ghost{background:var(--panel);border:1px solid var(--line);color:var(--ink);font-weight:500}
button.small{padding:4px 10px;font-size:13px}
button.danger{background:var(--bad)}
button:disabled{opacity:.55;cursor:not-allowed}
.status{font-size:12.5px;color:var(--muted)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px}
.article{display:grid;grid-template-columns:1fr 132px;gap:16px}
.article h2{font-size:16.5px;line-height:1.4;margin:0 0 6px}
.article h2 a:hover{color:var(--accent)}
.meta{color:var(--muted);font-size:12.5px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}
.chip{background:var(--soft);color:var(--soft-ink);border-radius:999px;padding:1px 9px;font-size:12px;font-weight:600}
.chip.pos{background:var(--pos-bg);color:var(--pos)}.chip.neg{background:var(--neg-bg);color:var(--neg)}.chip.neu{background:var(--neu-bg);color:var(--neu)}
.chip.ok{background:var(--pos-bg);color:var(--pos)}.chip.bad{background:var(--neg-bg);color:var(--neg)}.chip.warn{background:#fff7ed;color:var(--warn)}
.summary{margin:0;color:var(--ink);opacity:.88}
.thumb{width:132px;height:92px;border-radius:10px;background:var(--bg) center/cover no-repeat;border:1px solid var(--line)}
.actions{display:flex;gap:6px;align-items:center;margin-top:10px;flex-wrap:wrap}
.vote.on{background:var(--soft);border-color:var(--accent)}
details.cluster{margin-top:8px;font-size:13px}
details.cluster summary{cursor:pointer;color:var(--accent);font-weight:600}
details.cluster li{margin:3px 0;color:var(--muted)}
.share{display:none;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px;padding:10px;border:1px dashed var(--line);border-radius:10px}
.share.open{display:flex}
.share label{display:flex;gap:5px;align-items:center;font-size:13px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.tabs a{padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--panel);font-size:13px}
.tabs a.on{background:var(--soft);color:var(--soft-ink);border-color:transparent;font-weight:600}
.post textarea{width:100%;min-height:96px;resize:vertical;margin:8px 0}
.post .hint{font-size:12px;color:var(--muted)}
.post .err{color:var(--bad);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi b{display:block;font-size:22px;letter-spacing:-.02em}
.kpi span{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.section-title{font-size:15px;margin:22px 0 10px}
.empty{color:var(--muted);text-align:center;padding:40px 0}
.err-text{color:var(--muted);font-size:12.5px;max-width:420px;word-break:break-word}
@media(max-width:860px){.app{grid-template-columns:1fr}.rail{position:static;height:auto}
.kpis{grid-template-columns:repeat(2,1fr)}.article{grid-template-columns:1fr}.thumb{width:100%;height:150px}
.main{padding:16px}input[type=search]{min-width:0;width:100%}}
"""

SCRIPT = """
<script>
const $ = (s, el=document) => el.querySelector(s);
async function api(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(body || {})});
  let d = {}; try { d = await r.json(); } catch(e) {}
  if(!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}
document.querySelectorAll('.vote').forEach(b => b.addEventListener('click', async () => {
  const box = b.closest('[data-url]');
  const next = b.classList.contains('on') ? 0 : Number(b.dataset.v);
  try { await api('/api/feedback', {url: box.dataset.url, value: next}); location.reload(); }
  catch(e){ alert(e.message); }
}));
document.querySelectorAll('.share-toggle').forEach(b => b.addEventListener('click', () => {
  b.closest('[data-url]').querySelector('.share').classList.toggle('open');
}));
document.querySelectorAll('.share-send').forEach(b => b.addEventListener('click', async () => {
  const box = b.closest('[data-url]');
  const channels = [...box.querySelectorAll('.share input:checked')].map(i => i.value);
  if(!channels.length) return alert('Chọn ít nhất một kênh.');
  try { const d = await api('/api/posts/queue', {url: box.dataset.url, channels});
        b.textContent = d.created ? 'Đã đưa ' + d.created + ' bài vào hàng duyệt' : 'Đã có trong hàng duyệt';
        b.disabled = true; }
  catch(e){ alert(e.message); }
}));
document.querySelectorAll('.post [data-act]').forEach(b => b.addEventListener('click', async () => {
  const card = b.closest('.post'); const id = card.dataset.id; const act = b.dataset.act;
  const text = card.querySelector('textarea') ? card.querySelector('textarea').value : null;
  if(act === 'reject' && !confirm('Từ chối bài này?')) return;
  if(act === 'send' && !confirm('Đăng bài này lên ' + card.dataset.channel + '?')) return;
  b.disabled = true;
  try { await api('/api/posts/' + id + '/' + act, {text}); location.reload(); }
  catch(e){ alert(e.message); b.disabled = false; }
}));
document.querySelectorAll('.post textarea').forEach(t => {
  const out = t.closest('.post').querySelector('.len');
  const upd = () => { if(out) out.textContent = t.value.length + ' ký tự'; };
  t.addEventListener('input', upd); upd();
});
const run = $('#run'), status = $('#status');
async function poll(){
  if(!run) return;
  const d = await (await fetch('/api/run')).json();
  status.textContent = d.message; run.disabled = d.running;
  if(!d.running && d.changed) location.reload();
}
run?.addEventListener('click', async () => {
  run.disabled = true;
  try { const d = await api('/api/collect?provider=' + $('#provider').value + '&limit=2');
        status.textContent = d.message; } catch(e){ status.textContent = e.message; }
  poll();
});
if(run) setInterval(poll, 2000);
</script>"""


# ------------------------------------------------------------------ helpers
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


def link(params: dict[str, str], base: str = "/") -> str:
    clean = {key: value for key, value in params.items() if value}
    return base + ("?" + urlencode(clean) if clean else "")


def health_class(row: dict | None) -> str:
    if not row:
        return ""
    failures = int(row.get("consecutive_failures") or 0)
    return "ok" if failures == 0 else ("warn" if failures == 1 else "bad")


# ------------------------------------------------------------------ layout
def layout(
    *,
    title: str,
    active: str,
    main: str,
    rail: dict | None = None,
    interactive: bool = True,
) -> str:
    rail = rail or {}
    filters = rail.get("filters") or {}
    view = rail.get("view", "latest")

    def nav(key: str, href: str, label: str, badge: str = "") -> str:
        on = " on" if active == key else ""
        return f'<a class="{on.strip()}" href="{href}"><span>{label}</span>{badge}</a>'

    pending = rail.get("pending_posts", 0)
    failing = rail.get("failing_sources", 0)
    nav_html = "".join(
        [
            nav("foryou", "/?view=foryou", "Dành cho bạn"),
            nav("latest", "/", "Mới nhất"),
            nav("posts", "/posts", "Duyệt đăng", f'<span class="badge">{pending}</span>' if pending else ""),
            nav("health", "/health", "Sức khỏe hệ thống",
                f'<span class="badge bad">{failing}</span>' if failing else ""),
        ]
    ) if interactive else ""

    facets = ""
    if rail.get("topics"):
        items = "".join(
            f'<a class="{"on" if filters.get("topic") == name else ""}" '
            f'href="{esc(link({"view": view if view == "foryou" else "", "topic": name}))}">'
            f'<span>{esc(name)}</span><span class="count">{count}</span></a>'
            for name, count in rail["topics"]
        )
        facets += f'<h4>Chủ đề</h4><div class="facet">{items}</div>'
    if rail.get("sources"):
        health = rail.get("health") or {}
        items = "".join(
            f'<a class="{"on" if filters.get("source") == name else ""}" '
            f'href="{esc(link({"view": view if view == "foryou" else "", "source": name}))}">'
            f'<span><i class="dot {health_class(health.get(name))}"></i>{esc(name)}</span>'
            f'<span class="count">{count}</span></a>'
            for name, count in rail["sources"]
        )
        facets += f'<h4>Nguồn</h4><div class="facet">{items}</div>'

    return f"""<!doctype html>
<html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style>
</head><body><div class="app">
<aside class="rail">
  <div class="brand">Agent tổng hợp tin tức</div>
  <nav class="nav">{nav_html}</nav>
  {facets}
</aside>
<main class="main">{main}</main>
</div>{SCRIPT if interactive else ""}</body></html>"""


# ------------------------------------------------------------------ feed
def article_card(
    row: dict,
    *,
    interactive: bool,
    vote: int = 0,
    score: tuple[float, list[str]] | None = None,
    members: list[dict] | None = None,
) -> str:
    chips = "".join(f'<span class="chip">{esc(t)}</span>' for t in row.get("topics") or [])
    thumb = css_url(row.get("image_url"))
    thumb_html = f'<div class="thumb" style="background-image:url(\'{thumb}\')"></div>' if thumb else '<div class="thumb"></div>'
    url = row.get("url") or ""

    cluster = ""
    if members:
        items = "".join(
            f'<li><a href="{esc(m["url"])}" target="_blank" rel="noreferrer">{esc(m["source"])}: {esc(m["title"])}</a></li>'
            for m in members
        )
        cluster = (
            f'<details class="cluster"><summary>Cùng tin này: +{len(members)} nguồn khác</summary>'
            f"<ul>{items}</ul></details>"
        )

    actions = ""
    if interactive:
        score_html = ""
        if score:
            value, reasons = score
            score_html = f'<span class="chip" title="{esc("; ".join(reasons))}">★ {value:.1f}</span>'
        boxes = "".join(
            f'<label><input type="checkbox" value="{c}"> {esc(CHANNEL_LABELS[c])}</label>' for c in CHANNELS
        )
        actions = f"""
    <div class="actions">
      <button type="button" class="ghost small vote{' on' if vote > 0 else ''}" data-v="1" title="Thích">👍</button>
      <button type="button" class="ghost small vote{' on' if vote < 0 else ''}" data-v="-1" title="Không thích">👎</button>
      <button type="button" class="ghost small share-toggle">Đăng lên…</button>
      {score_html}
    </div>
    <div class="share">{boxes}<button type="button" class="small share-send">Đưa vào hàng duyệt</button></div>"""

    return f"""<article class="card article" data-url="{esc(url)}">
  <div>
    <div class="meta">
      <strong>{esc(row.get("source"))}</strong><span>·</span>
      <span>{fmt_date(row.get("published_at") or row.get("fetched_at"))}</span><span>·</span>
      <span>{esc(row.get("word_count"))} từ</span>
      <span class="chip {sentiment_class(row.get("sentiment"))}">{sentiment_label(row.get("sentiment"))}</span>
    </div>
    <h2><a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(row.get("title"))}</a></h2>
    <div class="chips">{chips}</div>
    <p class="summary">{esc(row.get("summary"))}</p>
    {cluster}{actions}
  </div>
  {thumb_html}
</article>"""


def render_page(
    rows: list[dict],
    stats: dict,
    *,
    sources: list[str],
    topics: list[str],
    filters: dict[str, str],
    interactive: bool = True,
    title: str = "Agent tổng hợp tin tức",
    view: str = "latest",
    feedback: dict[str, int] | None = None,
    scores: dict[str, tuple[float, list[str]]] | None = None,
    clusters: dict[str, list[dict]] | None = None,
    rail: dict | None = None,
) -> str:
    feedback = feedback or {}
    scores = scores or {}
    clusters = clusters or {}
    cards = [
        article_card(
            row,
            interactive=interactive,
            vote=feedback.get(row.get("url"), 0),
            score=scores.get(row.get("url")),
            members=clusters.get(row.get("url")),
        )
        for row in rows
    ]
    empty = (
        "Chưa có bài phù hợp. Thử bỏ bớt bộ lọc, hoặc bấm “Chạy thu thập”."
        if interactive else "Chưa có bài nào."
    )
    body = "\n".join(cards) if cards else f'<div class="empty">{empty}</div>'

    heading = "Dành cho bạn" if view == "foryou" else "Mới nhất"
    if filters.get("topic"):
        heading += f" · {filters['topic']}"
    if filters.get("source"):
        heading += f" · {filters['source']}"

    top = f'<div class="top"><h1>{esc(heading if interactive else title)}</h1>'
    if interactive:
        hidden = "".join(
            f'<input type="hidden" name="{key}" value="{esc(filters.get(key) or (view if key == "view" else ""))}">'
            for key in ("view", "topic", "source")
            if filters.get(key) or (key == "view" and view == "foryou")
        )
        top += f"""
  <form method="get" action="/">{hidden}
    <input type="search" name="q" placeholder="Tìm tiêu đề, tóm tắt…" value="{esc(filters.get('q', ''))}">
  </form>
  <select id="provider" title="Mô hình tóm tắt">
    <option value="mock">mock (nhanh)</option><option value="commandcode">commandcode</option>
    <option value="ollama">ollama</option>
  </select>
  <button id="run">Chạy thu thập</button>"""
    top += "</div>"
    if interactive:
        top += f'<div class="status" id="status" style="margin:-8px 0 14px">{esc(RUN_STATE.get("message"))}</div>'
    else:
        top += (
            f'<div class="status" style="margin:-8px 0 14px">{esc(stats.get("articles", 0))} bài · '
            f'{esc(len(stats.get("by_source") or {}))} nguồn · cập nhật {esc((stats.get("last_fetched_at") or "—")[:10])}</div>'
        )

    if rail is None:
        rail = {
            "topics": [(name, "") for name in topics],
            "sources": [(name, "") for name in sources],
            "filters": filters,
            "view": view,
        }
    return layout(title=title, active=view, main=top + body, rail=rail, interactive=interactive)


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


def facet_counts(rows: list[dict]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    topics: dict[str, int] = {}
    sources: dict[str, int] = {}
    for row in rows:
        sources[row["source"]] = sources.get(row["source"], 0) + 1
        for topic in row.get("topics") or []:
            topics[topic] = topics.get(topic, 0) + 1
    order = lambda d: sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
    return order(topics), order(sources)


# ------------------------------------------------------------------ posts
def render_posts(posts: list[dict], counts: dict[str, int], status: str, live: dict[str, bool], rail: dict) -> str:
    tabs = "".join(
        f'<a class="{"on" if status == key else ""}" href="/posts?status={key}">{label} '
        f'<span class="count">{counts.get(key, 0)}</span></a>'
        for key, label in POST_STATUS_LABELS.items()
    )
    cards = []
    for post in posts:
        channel = post["channel"]
        editable = post["status"] in {"pending", "approved", "failed"}
        mode = (
            '<span class="chip ok">gửi thật</span>' if live.get(channel)
            else '<span class="chip warn" title="Chưa cấu hình khóa API hoặc đang DRY_RUN: chỉ ghi ra data/outbox">dry-run</span>'
        )
        text_html = (
            f'<textarea>{esc(post["text"])}</textarea><div class="hint"><span class="len"></span></div>'
            if editable else f'<p class="summary" style="white-space:pre-wrap">{esc(post["text"])}</p>'
        )
        buttons = ""
        if post["status"] == "pending":
            buttons = ('<button class="small" data-act="approve">Duyệt</button>'
                       '<button class="small ghost" data-act="save">Lưu nội dung</button>'
                       '<button class="small danger" data-act="reject">Từ chối</button>')
        elif post["status"] in {"approved", "failed"}:
            buttons = ('<button class="small" data-act="send">Đăng ngay</button>'
                       '<button class="small ghost" data-act="save">Lưu nội dung</button>'
                       '<button class="small danger" data-act="reject">Từ chối</button>')
        result = ""
        if post.get("post_id"):
            dry = " (dry-run)" if post.get("dry_run") else ""
            result = f'<div class="hint">post_id: <code>{esc(post["post_id"])}</code>{dry} · {fmt_date(post.get("published_at"))}</div>'
        if post.get("error"):
            result += f'<div class="err">{esc(post["error"])}</div>'
        cards.append(f"""<div class="card post" data-id="{post['id']}" data-channel="{esc(CHANNEL_LABELS.get(channel, channel))}">
  <div class="meta"><strong>{esc(CHANNEL_LABELS.get(channel, channel))}</strong>{mode}
    <span>·</span><span>{esc(POST_STATUS_LABELS.get(post['status'], post['status']))}</span>
    <span>·</span><span>tạo {fmt_date(post.get('created_at'))}</span></div>
  <div><a href="{esc(post['article_url'])}" target="_blank" rel="noreferrer"><b>{esc(post.get('article_title') or post['article_url'])}</b></a>
    <span class="count">· {esc(post.get('article_source') or '')}</span></div>
  {text_html}{result}
  <div class="actions">{buttons}</div>
</div>""")
    body = "\n".join(cards) or '<div class="empty">Không có bài nào ở trạng thái này. Dùng nút “Đăng lên…” ở danh sách tin để đưa bài vào hàng duyệt.</div>'
    main = f"""<div class="top"><h1>Duyệt đăng</h1></div>
<p class="status" style="margin-top:-8px">Facebook và X cần duyệt trước khi đăng; Telegram và Lark được duyệt sẵn
(đổi bằng <code>PUBLISH_REQUIRE_APPROVAL</code>). Một bài chỉ tính là đã đăng khi nền tảng trả về post_id.</p>
<div class="tabs">{tabs}</div>{body}"""
    return layout(title="Duyệt đăng", active="posts", main=main, rail=rail)


# ------------------------------------------------------------------ health
def render_health(*, runs, health, alerts, streak, dup, total, rail) -> str:
    last = runs[0] if runs else None
    failing = [row for row in health if int(row.get("consecutive_failures") or 0) > 0]
    ratio = f"{dup / total * 100:.1f}%" if total else "—"
    streak_text = "—" if streak is None else (f"{streak:.1f} ngày" if streak >= 1 else f"{streak * 24:.0f} giờ")
    status_chip = {"ok": "ok", "partial": "warn", "error": "bad"}.get((last or {}).get("status"), "")
    kpis = f"""<div class="kpis">
  <div class="kpi"><b>{streak_text}</b><span>Chạy ổn định liên tục</span></div>
  <div class="kpi"><b><span class="chip {status_chip}">{esc((last or {}).get('status', '—'))}</span></b><span>Lượt chạy gần nhất · {fmt_date((last or {}).get('started_at'))}</span></div>
  <div class="kpi"><b>{ratio}</b><span>Tỉ lệ trùng lặp (7 ngày, {dup}/{total})</span></div>
  <div class="kpi"><b>{len(failing)}/{len(health)}</b><span>Nguồn đang lỗi</span></div>
</div>"""

    source_rows = "".join(
        f"<tr><td><i class='dot {health_class(row)}'></i>{esc(row['source'])}</td>"
        f"<td>{fmt_date(row.get('last_ok_at'))}</td><td>{esc(row.get('last_count'))}</td>"
        f"<td>{esc(row.get('consecutive_failures'))}</td>"
        f"<td class='err-text'>{esc(row.get('last_error') or '') if row.get('consecutive_failures') else ''}</td></tr>"
        for row in health
    ) or "<tr><td colspan=5 class='status'>Chưa có dữ liệu — chạy thu thập một lần.</td></tr>"

    run_rows = "".join(
        f"<tr><td>{fmt_date(run.get('started_at'))}</td>"
        f"<td><span class='chip {({'ok': 'ok', 'partial': 'warn', 'error': 'bad'}).get(run.get('status'), '')}'>{esc(run.get('status'))}</span></td>"
        f"<td>{esc(run.get('inserted'))}</td><td>{esc(run.get('similar'))}</td><td>{esc(run.get('duplicates'))}</td>"
        f"<td>{esc(run.get('failed'))}</td><td class='err-text'>{esc(run.get('error') or '')}</td></tr>"
        for run in runs
    ) or "<tr><td colspan=7 class='status'>Chưa có lượt chạy.</td></tr>"

    alert_rows = "".join(
        f"<tr><td>{fmt_date(a.get('created_at'))}</td>"
        f"<td><span class='chip {'bad' if a.get('level') == 'error' else 'warn'}'>{esc(a.get('level'))}</span></td>"
        f"<td>{esc(a.get('message'))}</td><td class='status'>{esc(a.get('delivered'))}</td></tr>"
        for a in alerts
    ) or "<tr><td colspan=4 class='status'>Không có cảnh báo.</td></tr>"

    main = f"""<div class="top"><h1>Sức khỏe hệ thống</h1>
  <select id="provider"><option value="mock">mock (nhanh)</option><option value="commandcode">commandcode</option><option value="ollama">ollama</option></select>
  <button id="run">Chạy thu thập</button></div>
<div class="status" id="status" style="margin:-8px 0 14px">{esc(RUN_STATE.get("message"))}</div>
{kpis}
<h3 class="section-title">Nguồn tin</h3>
<div class="card" style="padding:4px 6px"><table><tr><th>Nguồn</th><th>Lần OK gần nhất</th><th>Số bài feed</th><th>Lỗi liên tiếp</th><th>Lỗi gần nhất</th></tr>{source_rows}</table></div>
<h3 class="section-title">Cảnh báo</h3>
<div class="card" style="padding:4px 6px"><table><tr><th>Lúc</th><th>Mức</th><th>Nội dung</th><th>Gửi tới</th></tr>{alert_rows}</table></div>
<h3 class="section-title">Lượt chạy</h3>
<div class="card" style="padding:4px 6px"><table><tr><th>Bắt đầu</th><th>Trạng thái</th><th>Mới</th><th>Cùng tin</th><th>Trùng</th><th>Lỗi</th><th>Ghi chú</th></tr>{run_rows}</table></div>"""
    return layout(title="Sức khỏe hệ thống", active="health", main=main, rail=rail)


# ------------------------------------------------------------------ background run
def run_collection(settings: Settings, provider: str, limit: int) -> None:
    try:
        overrides = {"dry_run": True, "llm_provider": provider, "data_dir": settings.data_dir}
        pipeline = Pipeline(load_settings(overrides))
        try:
            stats = pipeline.collect(limit=limit, with_documents=False, push=True)
        finally:
            pipeline.store.close()
            close = getattr(pipeline.fetcher, "close", None)
            if callable(close):
                close()
        message = (
            f"xong: {stats.inserted} bài mới ({stats.similar} cùng tin), "
            f"{stats.duplicates} trùng, {stats.failed} lỗi"
            + (f", {len(stats.alerts)} cảnh báo" if stats.alerts else "")
        )
        RUN_STATE.update({"message": message, "changed": True})
    except Exception as exc:
        RUN_STATE.update({"message": f"lỗi: {exc}", "changed": True})
    finally:
        RUN_STATE["running"] = False
        RUN_STATE["stopped_at"] = datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ HTTP
POST_ACTION = re.compile(r"^/api/posts/(\d+)/(approve|reject|send|save)$")


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

    def _html(self, page: str) -> None:
        self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser clients (curl, tests) send no Origin
        return urlparse(origin).netloc == self.headers.get("Host", "")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > 64 * 1024:
            raise ValueError("body too large")
        data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        return data if isinstance(data, dict) else {}

    def _rail(self, store: Store, rows: list[dict], filters: dict, view: str) -> dict:
        topics, sources = facet_counts(rows)
        health = {row["source"]: row for row in store.source_health()}
        return {
            "topics": topics,
            "sources": sources,
            "health": health,
            "filters": filters,
            "view": view,
            "pending_posts": store.post_counts().get("pending", 0),
            "failing_sources": sum(1 for row in health.values() if int(row["consecutive_failures"] or 0) >= 2),
        }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
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
            all_rows = store.recent(limit=400)
            leads = [row for row in all_rows if not row.get("cluster_of")]

            if parsed.path == "/api/articles":
                return self._json({"articles": all_rows, "stats": store.stats()})
            if parsed.path == "/api/stats":
                return self._json(store.stats())
            if parsed.path == "/api/posts":
                return self._json({"posts": store.list_posts()})

            if parsed.path == "/posts":
                status = (params.get("status") or ["pending"])[0]
                if status not in POST_STATUS_LABELS:
                    status = "pending"
                publisher = Publisher.from_settings(store, self.settings)
                live = {channel: publisher.is_live(channel) for channel in CHANNELS}
                page = render_posts(
                    store.list_posts(status), store.post_counts(), status, live,
                    self._rail(store, leads, {}, "posts"),
                )
                return self._html(page)

            if parsed.path == "/health":
                now = utc_now_iso()
                since = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
                dup, total = store.duplicate_ratio(since)
                page = render_health(
                    runs=store.recent_runs(15), health=store.source_health(),
                    alerts=store.recent_alerts(15), streak=store.stable_streak_days(now),
                    dup=dup, total=total, rail=self._rail(store, leads, {}, "health"),
                )
                return self._html(page)

            if parsed.path not in {"/", "/index.html"}:
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

            filters = {key: (params.get(key) or [""])[0] for key in ("source", "topic", "q")}
            view = "foryou" if (params.get("view") or [""])[0] == "foryou" else "latest"
            feedback = store.feedback_map()
            learned = learn_weights(store.feedback_rows())
            clusters = store.cluster_members()
            stats = store.stats()
            rail = self._rail(store, leads, filters, view)

        filtered = _filter_rows(leads, filters)
        scores = None
        if view == "foryou":
            ranked = rank(filtered, load_profile(self.settings.profile_path), learned, feedback=feedback)
            filtered = [item.row for item in ranked]
            scores = {item.row["url"]: (item.score, item.reasons) for item in ranked}
        page = render_page(
            filtered, stats, sources=[], topics=[], filters=filters, view=view,
            feedback=feedback, scores=scores, clusters=clusters, rail=rail,
        )
        self._html(page)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._same_origin():
            return self._json({"error": "cross-origin request blocked"}, HTTPStatus.FORBIDDEN)
        params = parse_qs(parsed.query)
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError):
            return self._json({"error": "invalid JSON body"}, HTTPStatus.BAD_REQUEST)

        if parsed.path == "/api/feedback":
            value = body.get("value", (params.get("value") or ["0"])[0])
            url = body.get("url") or (params.get("url") or [""])[0]
            try:
                value = int(value)
            except (TypeError, ValueError):
                return self._json({"error": "invalid value"}, HTTPStatus.BAD_REQUEST)
            if value not in (-1, 0, 1) or not url:
                return self._json({"error": "invalid feedback"}, HTTPStatus.BAD_REQUEST)
            with Store(self.settings.db_path) as store:
                found = store.set_feedback(url, value, utc_now_iso())
            if not found:
                return self._json({"error": "unknown article"}, HTTPStatus.NOT_FOUND)
            return self._json({"ok": True})

        if parsed.path == "/api/posts/queue":
            channels = body.get("channels") or []
            if not isinstance(channels, list) or not channels:
                return self._json({"error": "chọn ít nhất một kênh"}, HTTPStatus.BAD_REQUEST)
            with Store(self.settings.db_path) as store:
                try:
                    created = Publisher.from_settings(store, self.settings).queue(str(body.get("url", "")), channels)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return self._json({"ok": True, "created": len(created)})

        match = POST_ACTION.match(parsed.path)
        if match:
            post_id, action = int(match.group(1)), match.group(2)
            text = body.get("text")
            with Store(self.settings.db_path) as store:
                publisher = Publisher.from_settings(store, self.settings)
                try:
                    if action == "approve":
                        publisher.approve(post_id, text)
                    elif action == "reject":
                        publisher.reject(post_id)
                    elif action == "save":
                        publisher.edit(post_id, text or "")
                    else:
                        if text is not None:
                            publisher.edit(post_id, text)
                        result = publisher.send(post_id)
                        if not result["ok"]:
                            return self._json({"error": result["error"]}, HTTPStatus.BAD_GATEWAY)
                        return self._json(result)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
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
        threading.Thread(target=run_collection, args=(self.settings, provider, limit), daemon=True).start()
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
