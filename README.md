# Agent tổng hợp tin tức và tài liệu liên quan

Ứng dụng Python thu thập tin tức từ nhiều nguồn, dùng **AI Agent (smolagents)** và LLM chạy cục bộ
(Ollama) để tóm tắt – phân loại, tải **tài liệu đính kèm** (PDF/DOCX/XLSX…), lưu vào **SQLite**
và đồng bộ lên **Lark Base**. Toàn bộ pipeline thay thế workflow n8n trước đây.

## Kiến trúc

```
                 ┌──────────────────────────── agent (smolagents) ───────────────────────────┐
                 │  list_sources → fetch_source_titles → process_article → database_stats   │
                 └───────────────────────────────────┬──────────────────────────────────────┘
                                                     │ (có thể chạy không cần agent: pipeline tất định)
   config/sources.yaml                               ▼
   ┌──────────────┐   RSS/HTML    ┌──────────┐   ┌────────────┐   ┌─────────────┐   ┌───────────┐
   │ Source       │──adapter─────▶│  net.py  │──▶│ extract.py │──▶│ llm.py      │──▶│ store.py  │
   │ registry     │               │ (httpx)  │   │ (lxml)     │   │ (Ollama)    │   │ (SQLite)  │
   └──────────────┘               └──────────┘   └─────┬──────┘   └─────────────┘   └─────┬─────┘
                                                       │ documents.py                    │
                                                       ▼                                 ▼
                                              data/documents/*.pdf              lark.py (Lark Base)
```

| Thành phần | File | Vai trò |
| --- | --- | --- |
| Cấu hình | `app/config.py`, `config/sources.yaml` | đọc `.env`, registry nguồn tin |
| Mô hình dữ liệu | `app/models.py` | `Article`, `DocumentRecord`, chuẩn hóa & hash |
| Tải dữ liệu | `app/net.py` | httpx, retry, giới hạn dung lượng |
| Adapter | `app/adapters/` | RSS 2.0 / Atom, HTML + XPath |
| Bóc tách | `app/extract.py` | tiêu đề, thân bài, metadata, link tài liệu |
| Tài liệu | `app/documents.py` | tải, kiểm tra kích thước, SHA-256 |
| Lưu trữ | `app/store.py` | SQLite: articles/documents/runs, khử trùng lặp |
| LLM | `app/llm.py` | Ollama JSON mode + fallback heuristic |
| Agent | `app/agent.py` | smolagents `ToolCallingAgent`, 4 công cụ |
| Điều phối | `app/pipeline.py` | chạy theo nguồn, đếm thống kê, đẩy Lark |
| Lark | `app/lark.py` | `tenant_access_token`, `batch_create`, dry-run JSONL |
| CLI | `app/cli.py` | các lệnh vận hành |

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # rồi điền cấu hình
```

Yêu cầu: Python ≥ 3.11. Ba lựa chọn LLM cho `--llm`:

| Giá trị | Cách hoạt động | Khi nào dùng |
| --- | --- | --- |
| `ollama` | gọi Ollama cục bộ, JSON mode | chạy 24/7, không tốn phí API |
| `commandcode` | gọi Command Code CLI headless (`commandcode -p ... --output-format json`) | cần chất lượng cao hơn, đã `commandcode login` |
| `mock` | heuristic theo từ khóa, không gọi mạng | kiểm thử, môi trường không có model |

Không có Ollama vẫn chạy được ở `--llm mock`, và nếu provider nào lỗi thì pipeline tự lùi về heuristic.

## Sử dụng

```bash
python -m app.cli sources                      # liệt kê nguồn
python -m app.cli collect --limit 3 --dry-run  # thu thập + tóm tắt, chưa đẩy Lark
python -m app.cli collect --source VnExpress   # chỉ một nguồn
python -m app.cli collect --agent              # để agent tự quyết định quy trình
python -m app.cli ingest-url https://...       # xử lý một trang bất kỳ (kèm tài liệu)
python -m app.cli stats                        # thống kê DB
python -m app.cli recent --limit 5             # 5 bài mới nhất
python -m app.cli export --out data/articles.jsonl
python -m app.cli push                         # đẩy bản ghi chưa đồng bộ lên Lark Base
python -m app.web --port 8000                  # web demo tại http://127.0.0.1:8000
python tools/export_dashboard.py --data-dir data/demo --out docs/dashboard.html
```

`--dry-run` ghi bản ghi Lark ra `data/outbox/lark-<timestamp>.jsonl` thay vì gọi API.

## Cá nhân hóa

`config/profile.yaml` khai báo chủ đề/nguồn ưa thích (kèm trọng số), từ khóa theo dõi, từ khóa chặn,
ẩn sắc thái. Điểm bài = trọng số chủ đề + nguồn + từ khóa (khớp nguyên từ) + độ mới (giảm một nửa
sau `half_life_hours`) + phần **học từ 👍/👎** (mỗi lượt ±0,3 cho chủ đề và nguồn của bài, chặn ±2).
Bài bị 👎 hoặc dính từ khóa chặn sẽ không xuất hiện trong danh sách cá nhân hóa.

```bash
python -m app.cli digest --top 5                 # bản tin xếp hạng, hiển thị lý do chấm điểm
python -m app.cli feedback <url>                 # thích (--dislike, --clear)
python -m app.web                                # tab "Dành cho bạn" + nút 👍/👎
```

## Cảnh báo lỗi (Error Workflow)

Mỗi lượt chạy ghi sức khỏe từng nguồn (`source_health`). Hệ thống cảnh báo khi một nguồn lỗi
`ALERT_SOURCE_FAILURES` lượt liên tiếp (mặc định 2, tránh báo động vì lỗi thoáng qua), khi đồng bộ
Lark thất bại, khi hơn một nửa số bài trong lượt bị lỗi, hoặc khi lượt chạy bị dừng giữa chừng (lượt
vẫn được đóng với trạng thái `error`). Cảnh báo gửi qua Telegram và/hoặc webhook Lark; chưa cấu hình
thì ghi ra `data/outbox/alerts-*.jsonl`. Trang **Sức khỏe hệ thống** hiển thị chuỗi ngày chạy ổn
định, tỉ lệ trùng lặp 7 ngày, nguồn đang lỗi, lượt chạy và cảnh báo (`python -m app.cli health`).

## Khử trùng lặp ngữ nghĩa

Ngoài trùng URL và trùng `content_hash`, mỗi bài mới được nhúng vector (mặc định vector băm n-gram,
không cần model; hoặc `SEMANTIC_EMBEDDER=ollama`) và so cosine với các bài trong 72 giờ. Vượt
`SEMANTIC_THRESHOLD` thì bài được gom vào tin trước đó (`articles.cluster_of`): web hiện một thẻ
kèm "+N nguồn khác", và bài phụ không đẩy lên Lark. Ngưỡng 0,45 được chọn trên dữ liệu demo (cặp
khác tin cao nhất 0,37; cặp viết lại cùng tin 0,54) — nên hiệu chỉnh lại khi có thêm dữ liệu.

## Phát tán và duyệt bài

Kênh: Telegram, Lark (IM), Facebook Page, X (Twitter). Luồng: **đưa vào hàng → duyệt → đăng →
kiểm tra post_id**. Facebook và X mặc định cần duyệt (`PUBLISH_REQUIRE_APPROVAL`); bài chỉ được
tính là đã đăng khi nền tảng trả về id. Kênh chưa có khóa API, hoặc khi `DRY_RUN=true`, chạy
dry-run: ghi ra `data/outbox/publish-*.jsonl` với id `dry-…`.

```bash
python -m app.cli publish queue https://... --channel telegram --channel facebook
python -m app.cli publish list --status pending
python -m app.cli publish approve 3 --text "Nội dung đã sửa"
python -m app.cli publish send all            # gửi mọi bài đã duyệt
```

Trên web: nút **Đăng lên…** ở mỗi bài, trang **Duyệt đăng** để sửa nội dung, duyệt, từ chối, đăng.

## Dữ liệu

`data/news.db` (SQLite):

- `articles` — `url` UNIQUE, `content_hash`, `title`, `source`, `country`, `language`, `summary`,
  `topics` (JSON), `sentiment`, `author`, `published_at`, `fetched_at`, `word_count`, `synced_at`
- `documents` — `url` UNIQUE, `article_url`, `kind`, `mime`, `size_bytes`, `local_path`, `sha256`
- `runs` — log mỗi lượt chạy: `fetched`, `inserted`, `duplicates`, `failed`, `documents`, `status`

Khử trùng lặp hai lớp: theo `url` trước khi tải trang, theo `content_hash` sau khi bóc tách
(bắt được cùng một bài đăng lại ở nhiều nguồn).

## Lark Base

Đặt tên cột trong bảng khớp map ở `app/lark.py` (`FIELD_MAP`): **Tiêu đề** (Text, primary),
**URL** (URL), **Nguồn**, **Quốc gia**, **Ngôn ngữ**, **Tóm tắt**, **Chủ đề** (Multi-select),
**Sắc thái** (Single select: Tích cực/Trung tính/Tiêu cực), **Tác giả**, **Ngày đăng**,
**Ngày lấy tin**, **Số từ**, **Ảnh**.

Biến môi trường: `LARK_APP_ID`, `LARK_APP_SECRET`, `LARK_APP_TOKEN`, `LARK_TABLE_ID`.
Thiếu cấu hình ⇒ tự động dry-run, không lỗi.

## Kiểm thử

```bash
python -m unittest discover -s tests -t . -v
```

19 test phủ adapter RSS/Atom/HTML, bóc tách bài và link tài liệu, khử trùng lặp, đẩy Lark dry-run,
JSON parsing và fallback heuristic. Toàn bộ test chạy offline (`StaticFetcher`).

## Ghi chú vận hành

- Tôn trọng bản quyền: chỉ lưu tóm tắt + URL, không sao chép toàn văn ra ngoài.
- `MAX_DOCUMENT_MB` chặn tải tài liệu quá lớn; tài liệu lưu theo SHA-256 nên trùng nội dung chỉ tốn một bản.
- Với máy GPU 4 GB VRAM, dùng `qwen2.5:7b` (hoặc `3b`); 14B cần ≥ 11 GB VRAM.
- Báo cáo: xem `docs/BaoCao_Agent_TongHopTinTuc.docx` (sinh bằng `tools/build_report_docx.py`).
