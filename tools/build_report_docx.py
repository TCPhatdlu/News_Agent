"""Generate the project report as a formatted .docx file.

Run: python tools/build_report_docx.py
Output: docs/BaoCao_Agent_TongHopTinTuc.docx
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "BaoCao_Agent_TongHopTinTuc.docx"

BODY_FONT = "Times New Roman"
CODE_FONT = "Consolas"


def set_font(run, name: str = BODY_FONT, size: int = 13, bold: bool = False, color: str | None = None):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def body(doc, text: str, *, indent: bool = True, align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after: int = 6):
    paragraph = doc.add_paragraph()
    paragraph.alignment = align
    paragraph.paragraph_format.space_after = Pt(space_after)
    paragraph.paragraph_format.line_spacing = 1.4
    if indent:
        paragraph.paragraph_format.first_line_indent = Cm(1.0)
    run = paragraph.add_run(text)
    set_font(run)
    return paragraph


def bullet(doc, text: str):
    paragraph = doc.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.line_spacing = 1.35
    paragraph.paragraph_format.space_after = Pt(3)
    run = paragraph.add_run(text)
    set_font(run)


def heading(doc, text: str, level: int = 1):
    paragraph = doc.add_heading(level=level)
    paragraph.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    paragraph.paragraph_format.space_after = Pt(6)
    run = paragraph.add_run(text)
    set_font(run, size={1: 16, 2: 14, 3: 13}.get(level, 13), bold=True, color="1F3864")
    return paragraph


def shade(cell, hex_color: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def table(doc, headers: list[str], rows: list[list[str]], widths: list[float] | None = None):
    tbl = doc.add_table(rows=1, cols=len(headers))
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    header_cells = tbl.rows[0].cells
    for index, text in enumerate(headers):
        header_cells[index].text = ""
        paragraph = header_cells[index].paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(text)
        set_font(run, size=11, bold=True)
        shade(header_cells[index], "D9E2F3")

    for row in rows:
        cells = tbl.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = ""
            paragraph = cells[index].paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(2)
            run = paragraph.add_run(value)
            set_font(run, size=11)

    if widths:
        for index, width in enumerate(widths):
            for row in tbl.rows:
                row.cells[index].width = Cm(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return tbl


def code_block(doc, code: str, caption: str | None = None):
    if caption:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(caption)
        set_font(run, size=10, bold=True, color="7F7F7F")
        paragraph.paragraph_format.space_after = Pt(2)

    for line in code.rstrip("\n").split("\n"):
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.left_indent = Cm(0.6)
        paragraph.paragraph_format.line_spacing = 1.0
        run = paragraph.add_run(line if line else " ")
        set_font(run, name=CODE_FONT, size=9)
        p_pr = paragraph._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "F2F2F2")
        p_pr.append(shd)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def add_field(paragraph, instruction: str, placeholder: str = ""):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(separate)
    run._r.append(text)
    run._r.append(end)


def cover(doc: Document):
    for _ in range(2):
        doc.add_paragraph()

    def centered(text: str, size: int, bold: bool = False, space: int = 4):
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(space)
        run = paragraph.add_run(text)
        set_font(run, size=size, bold=bold)

    centered("TRƯỜNG ĐẠI HỌC ĐÀ LẠT", 15, True)
    centered("KHOA CÔNG NGHỆ THÔNG TIN", 14, True, 18)
    centered("───────────", 13)
    for _ in range(2):
        doc.add_paragraph()
    centered("ĐỒ ÁN CHUYÊN NGÀNH", 18, True)
    centered("NGÀNH CÔNG NGHỆ THÔNG TIN", 13)
    for _ in range(2):
        doc.add_paragraph()
    centered("XÂY DỰNG AI AGENT TỔNG HỢP TIN TỨC", 20, True, 6)
    centered("VÀ TÀI LIỆU LIÊN QUAN TỰ ĐỘNG", 20, True, 6)
    centered("TÍCH HỢP LARK BASE", 20, True)
    for _ in range(4):
        doc.add_paragraph()

    info = [
        ("Sinh viên thực hiện", "Hiếu – Tài"),
        ("Mã số sinh viên", "2212437"),
        ("Giảng viên hướng dẫn", "………………………………"),
        ("Lớp", "………………………………"),
    ]
    for label, value in info:
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.left_indent = Cm(4.5)
        paragraph.paragraph_format.space_after = Pt(6)
        run = paragraph.add_run(f"{label}: ")
        set_font(run, size=13, bold=True)
        run = paragraph.add_run(value)
        set_font(run, size=13)

    for _ in range(5):
        doc.add_paragraph()
    centered("Lâm Đồng, tháng 11 năm 2026", 13, True)
    doc.add_page_break()


def toc(doc: Document):
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("MỤC LỤC")
    set_font(run, size=16, bold=True, color="1F3864")
    doc.add_paragraph()

    entry = doc.add_paragraph()
    entry.paragraph_format.space_after = Pt(6)
    add_field(entry, 'TOC \\o "1-3" \\h \\z \\u', "Nhấn chuột phải → Update Field (F9) để dựng mục lục.")
    doc.add_page_break()


def footer_page_numbers(section):
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    set_font(run, size=11)
    add_field(paragraph, "PAGE", "1")


def build() -> Path:
    doc = Document()

    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(2.0)
    footer_page_numbers(section)

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(13)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)

    cover(doc)
    toc(doc)

    # ---------------------------------------------------------------- Chương I
    heading(doc, "CHƯƠNG I: TỔNG QUAN VỀ ĐỀ TÀI", 1)
    heading(doc, "1.1. Lý do chọn đề tài", 2)
    body(
        doc,
        "Khối lượng tin tức trên Internet tăng nhanh hơn khả năng đọc của con người. Mỗi ngày một "
        "biên tập viên phải theo dõi hàng chục trang báo, lọc ra bài liên quan, đọc toàn văn, tóm tắt "
        "và nhập lại vào một bảng dữ liệu dùng chung. Công việc này lặp lại, tốn thời gian và dễ sai "
        "sót khi phải xử lý hàng trăm bài mỗi tuần.",
    )
    body(
        doc,
        "Sự phát triển của các mô hình ngôn ngữ lớn (LLM) chạy cục bộ cho phép tự động hóa phần đọc "
        "hiểu này mà không phải gửi dữ liệu ra nhà cung cấp bên ngoài. Kết hợp với một tác nhân AI "
        "(AI Agent) có khả năng gọi công cụ, hệ thống có thể tự quyết định thứ tự công việc: lấy danh "
        "sách bài, tải nội dung, trích xuất tài liệu đính kèm, tóm tắt và ghi vào cơ sở dữ liệu.",
    )
    body(
        doc,
        "Phiên bản trước của đề tài dựng luồng xử lý trên nền tảng kéo–thả n8n. Cách làm đó nhanh "
        "nhưng bộc lộ ba hạn chế: khó kiểm thử tự động, khó tái sử dụng ngoài giao diện n8n, và "
        "không kiểm soát được chi tiết thuật toán bóc tách nội dung. Vì vậy đề tài được làm lại "
        "thành một ứng dụng Python độc lập, có mã nguồn rõ ràng, có kiểm thử, và vẫn giữ khả năng "
        "đồng bộ lên Lark Base như cũ.",
    )

    heading(doc, "1.2. Mục tiêu của đề tài", 2)
    for item in [
        "Xây dựng ứng dụng tự động thu thập tin tức từ nhiều nguồn (RSS và HTML) theo cấu hình.",
        "Bóc tách nội dung bài báo, loại bỏ menu/quảng cáo, và phát hiện tài liệu đính kèm (PDF, DOCX, XLSX…).",
        "Dùng AI Agent (smolagents) và LLM cục bộ để tóm tắt, phân loại chủ đề và đánh giá sắc thái.",
        "Lưu trữ có khử trùng lặp hai lớp và đồng bộ kết quả lên Lark Base.",
        "Cá nhân hóa danh sách tin theo hồ sơ người dùng và phản hồi thích/không thích.",
        "Cung cấp giao diện dòng lệnh, giao diện web demo, bộ kiểm thử offline và tài liệu kỹ thuật đầy đủ.",
    ]:
        bullet(doc, item)

    heading(doc, "1.3. Đối tượng và phạm vi nghiên cứu", 2)
    body(
        doc,
        "Đối tượng nghiên cứu là quy trình thu thập – xử lý – lưu trữ tin tức, cùng các kỹ thuật bóc "
        "tách nội dung web và kiến trúc tác nhân AI gọi công cụ. Phạm vi thực nghiệm giới hạn ở bảy "
        "nguồn báo tiếng Việt có RSS hoặc cấu trúc HTML ổn định; phần suy luận dùng mô hình Qwen2.5 "
        "chạy trên Ollama. Hệ thống không đăng bài, không vượt tường phí và không lưu toàn văn ra "
        "ngoài phạm vi tóm tắt phục vụ học tập.",
    )

    heading(doc, "1.4. Phương pháp thực hiện", 2)
    body(
        doc,
        "Đề tài dùng phương pháp thực nghiệm có đối chứng: mỗi thành phần được thiết kế theo giao "
        "diện riêng, kiểm thử bằng dữ liệu mẫu offline, sau đó chạy thật trên các nguồn tin để đo "
        "tỷ lệ bóc tách thành công. Các quyết định kỹ thuật đều được ghi lại kèm lý do, để có thể "
        "truy vết khi kết quả không như mong đợi.",
    )

    heading(doc, "1.5. Bố cục báo cáo", 2)
    body(
        doc,
        "Báo cáo gồm bốn chương. Chương I trình bày lý do, mục tiêu và phạm vi. Chương II phân tích "
        "yêu cầu và thiết kế kiến trúc, mô hình dữ liệu, tác nhân AI. Chương III mô tả triển khai và "
        "kết quả thực nghiệm. Chương IV kết luận và nêu hướng phát triển. Phần phụ lục chứa cấu trúc "
        "mã nguồn và các lệnh vận hành.",
    )

    doc.add_page_break()

    # --------------------------------------------------------------- Chương II
    heading(doc, "CHƯƠNG II: PHÂN TÍCH VÀ THIẾT KẾ HỆ THỐNG", 1)

    heading(doc, "2.1. Yêu cầu chức năng", 2)
    table(
        doc,
        ["Mã", "Chức năng", "Mô tả", "Thành phần"],
        [
            ["FR-01", "Quản lý nguồn tin", "Khai báo nguồn trong YAML: tên, URL, loại, quốc gia, ngôn ngữ", "config/sources.yaml"],
            ["FR-02", "Thu thập danh sách bài", "Đọc RSS 2.0/Atom hoặc HTML theo XPath", "app/adapters"],
            ["FR-03", "Bóc tách nội dung", "Lấy tiêu đề, thân bài, tác giả, ngày đăng, ảnh đại diện", "app/extract.py"],
            ["FR-04", "Phát hiện tài liệu", "Tìm liên kết PDF/DOCX/XLSX trong bài", "app/extract.py"],
            ["FR-05", "Tải tài liệu", "Giới hạn dung lượng, lưu theo SHA-256", "app/documents.py"],
            ["FR-06", "Tóm tắt bằng LLM", "Sinh tóm tắt 2–3 câu, chủ đề, sắc thái", "app/llm.py"],
            ["FR-07", "Khử trùng lặp", "Theo URL và theo content_hash", "app/store.py"],
            ["FR-08", "Đồng bộ Lark Base", "batch_create, có chế độ dry-run", "app/lark.py"],
            ["FR-09", "Tác nhân AI", "Agent tự chọn công cụ theo nhiệm vụ", "app/agent.py"],
            ["FR-10", "Vận hành CLI", "collect, stats, recent, export, push, ingest-url, digest, feedback", "app/cli.py"],
            ["FR-11", "Cá nhân hóa", "Chấm điểm bài theo hồ sơ, học từ phản hồi thích/không thích", "app/personalize.py"],
            ["FR-12", "Giao diện web", "Duyệt, lọc, tab Dành cho bạn, nút thích/không thích", "app/web.py"],
        ],
        widths=[1.6, 3.5, 7.2, 3.7],
    )

    heading(doc, "2.2. Yêu cầu phi chức năng", 2)
    table(
        doc,
        ["Mã", "Yêu cầu", "Tiêu chí"],
        [
            ["NFR-01", "Hiệu năng", "Một bài báo xử lý xong dưới 5 giây khi không tính thời gian LLM"],
            ["NFR-02", "Độ tin cậy", "Một nguồn lỗi không làm dừng toàn bộ lượt chạy"],
            ["NFR-03", "Khả năng kiểm thử", "Toàn bộ tầng xử lý chạy được offline bằng dữ liệu mẫu"],
            ["NFR-04", "Bảo mật", "Không gửi dữ liệu ra ngoài; khóa API chỉ đọc từ biến môi trường"],
            ["NFR-05", "Bảo trì", "Mỗi module một trách nhiệm, phụ thuộc một chiều"],
            ["NFR-06", "Khả chuyển", "Chạy trên Windows/Linux, Python ≥ 3.11"],
        ],
        widths=[1.8, 3.6, 10.6],
    )

    heading(doc, "2.3. Kiến trúc tổng thể", 2)
    body(
        doc,
        "Hệ thống chia thành năm tầng trách nhiệm. Tầng cấu hình đọc biến môi trường và registry nguồn. "
        "Tầng thu thập chịu trách nhiệm giao thức mạng. Tầng bóc tách chuyển HTML thành dữ liệu có cấu "
        "trúc. Tầng làm giàu gọi LLM và sinh tóm tắt. Tầng lưu trữ ghi SQLite và đẩy Lark Base. Chiều "
        "phụ thuộc chỉ đi từ trên xuống, không có vòng lặp, nhờ đó từng tầng có thể kiểm thử độc lập.",
    )
    code_block(
        doc,
        """cli.py  ->  pipeline.py  ->  adapters/  ->  net.py
                    |                 |
                    |                 +-> extract.py -> documents.py
                    +-> llm.py        |
                    +-> store.py  <---+
                    +-> lark.py
agent.py -> (dùng lại pipeline, bọc thành 4 công cụ cho ToolCallingAgent)
web.py, cli.py -> personalize.py -> store.py (đọc bài + phản hồi)""",
        "Hình 2.1. Luồng phụ thuộc giữa các module",
    )

    heading(doc, "2.4. Thiết kế tác nhân AI", 2)
    body(
        doc,
        "Tác nhân dùng ToolCallingAgent của smolagents. Thay vì để mô hình tự sinh và thực thi mã "
        "(CodeAgent), hệ thống chỉ cho phép gọi bốn công cụ đã định nghĩa trước. Cách này giới hạn "
        "bề mặt tấn công: mô hình không thể chạy lệnh hệ thống hay đọc tệp tuỳ ý.",
    )
    table(
        doc,
        ["Công cụ", "Vai trò", "Rủi ro kiểm soát"],
        [
            ["list_sources", "Liệt kê nguồn đã cấu hình", "Chỉ đọc cấu hình"],
            ["fetch_source_titles", "Lấy tiêu đề + URL mới của một nguồn", "Chỉ đọc feed, giới hạn số lượng"],
            ["process_article", "Tải, tóm tắt, lưu một bài", "URL phải do công cụ trước trả về"],
            ["database_stats", "Thống kê cơ sở dữ liệu", "Chỉ đọc"],
        ],
        widths=[4.4, 5.4, 6.2],
    )
    body(
        doc,
        "Mô hình được cấu hình qua giao diện tương thích OpenAI mà Ollama cung cấp tại địa chỉ "
        "http://localhost:11434/v1. Nếu Ollama không sẵn sàng, agent ném lỗi và ứng dụng tự động "
        "chuyển sang pipeline tất định, nhờ đó việc vận hành không bị gián đoạn.",
    )

    heading(doc, "2.5. Thiết kế bóc tách nội dung", 2)
    body(
        doc,
        "Trang báo hiện đại có hai dạng: nội dung nằm trong thẻ đoạn văn, hoặc được nhúng trong một "
        "khối dữ liệu có cấu trúc. Bộ bóc tách xử lý theo thứ tự ưu tiên: loại bỏ các khối điều hướng "
        "và quảng cáo; chọn vùng nội dung lớn nhất trong các ứng viên article, main hoặc div có tên "
        "chứa content/body; lấy các đoạn văn đủ dài; cuối cùng ghép lại và chuẩn hóa khoảng trắng.",
    )
    body(
        doc,
        "Độ dài tối thiểu của một đoạn là 40 ký tự nhằm loại chú thích ảnh và dòng trạng thái. Nếu "
        "không tìm được đoạn văn nào, hệ thống dùng toàn bộ văn bản của vùng nội dung, sau đó mới "
        "đánh giá bài có đủ dài để tóm tắt hay không. Ngưỡng chấp nhận là 80 ký tự; bài ngắn hơn bị "
        "coi là video hoặc trang lỗi và bị bỏ qua thay vì đưa vào cơ sở dữ liệu.",
    )
    code_block(
        doc,
        """def extract_article(page: str, url: str) -> ExtractedArticle:
    tree = lxml_html.fromstring(page)
    drop_tags(tree)                      # script, style, nav, footer, aside...
    title = og_title(tree) or h1(tree)
    node = best_content_node(tree)       # article | main | div[class*=content]
    paragraphs = [p for p in node.xpath(".//p") if len(p) >= 40]
    body = "\\n\\n".join(paragraphs) or node.text_content()
    return ExtractedArticle(title, body, author=..., published_at=..., document_links=...)""",
        "Đoạn mã 2.1. Quy trình bóc tách rút gọn",
    )

    heading(doc, "2.6. Thiết kế cơ sở dữ liệu", 2)
    body(
        doc,
        "SQLite được chọn vì không cần máy chủ riêng, giao dịch ACID đầy đủ và dễ sao lưu; chế độ WAL "
        "cho phép CLI và web đọc/ghi đồng thời. Năm bảng phản ánh các thực thể của bài toán: bài báo, "
        "tài liệu đính kèm, lượt chạy, phản hồi người dùng và các URL đã bỏ qua.",
    )
    table(
        doc,
        ["Bảng", "Khóa chính", "Cột chính", "Ràng buộc"],
        [
            ["articles", "id", "url, title, source, country, language, summary, topics, sentiment, author, published_at, fetched_at, word_count, content_hash, synced_at", "url UNIQUE, content_hash index"],
            ["documents", "id", "url, article_url, kind, mime, size_bytes, local_path, sha256", "url UNIQUE"],
            ["runs", "id", "started_at, finished_at, fetched, inserted, duplicates, failed, documents, status", "log vận hành"],
            ["feedback", "article_url", "value (+1 / -1), created_at", "mỗi bài tối đa một phản hồi"],
            ["skipped", "url", "reason, created_at", "bài trùng nội dung hoặc không có thân bài"],
        ],
        widths=[2.6, 2.0, 8.6, 4.2],
    )
    body(
        doc,
        "Khử trùng lặp tiến hành hai lớp. Lớp thứ nhất so URL trước khi tải trang, tiết kiệm băng thông. "
        "Lớp thứ hai so content_hash — băm tiêu đề chuẩn hóa cùng 20.000 ký tự đầu của thân bài — để "
        "phát hiện cùng một sự việc được đăng lại ở nhiều nguồn với URL khác nhau. Chỉ số hash được "
        "đánh index nên phép so sánh vẫn nhanh khi dữ liệu lớn. Các URL bị loại ở lớp thứ hai hoặc vì "
        "không có thân bài được ghi vào bảng skipped, nhờ đó lượt chạy sau không tải lại những trang này.",
    )

    heading(doc, "2.7. Thiết kế tích hợp Lark Base", 2)
    body(
        doc,
        "Lark Base đóng vai trò giao diện biên tập: người dùng lọc, duyệt và theo dõi trạng thái trên "
        "bảng, còn hệ thống ghi dữ liệu qua API. Việc ghi dùng endpoint batch_create, chia lô 500 bản "
        "ghi mỗi lần gọi để tránh giới hạn kích thước yêu cầu. Hệ thống chỉ đánh dấu đã đồng bộ những "
        "lô mà Lark xác nhận thành công; nếu một lô sau bị lỗi thì các lô trước không bị gửi lại, tránh "
        "tạo bản ghi trùng.",
    )
    table(
        doc,
        ["Trường Lark Base", "Kiểu", "Nguồn dữ liệu"],
        [
            ["Tiêu đề", "Text (primary)", "extract.title"],
            ["URL", "URL", "ArticleRef.url"],
            ["Nguồn", "Single select", "Source.name"],
            ["Quốc gia / Ngôn ngữ", "Single select", "Source.country, language"],
            ["Tóm tắt", "Text dài", "LLM summary"],
            ["Chủ đề", "Multi select", "LLM topics"],
            ["Sắc thái", "Single select", "positive / neutral / negative"],
            ["Ngày đăng / Ngày lấy tin", "Date", "metadata và thời điểm chạy"],
            ["Số từ / Ảnh / Tác giả", "Number / URL / Text", "kết quả bóc tách"],
        ],
        widths=[4.6, 5.0, 6.4],
    )

    heading(doc, "2.8. Thiết kế cá nhân hóa", 2)
    body(
        doc,
        "Cá nhân hóa được làm bằng hàm chấm điểm thuần túy trên các cột đã có (chủ đề, nguồn, tiêu đề, "
        "tóm tắt, sắc thái, ngày đăng), không gọi thêm LLM nên kết quả giải thích được và kiểm thử "
        "được. Hồ sơ tĩnh khai báo trong config/profile.yaml; phần học tự động lấy từ phản hồi "
        "thích/không thích của người dùng.",
    )
    table(
        doc,
        ["Thành phần điểm", "Cách tính"],
        [
            ["Chủ đề", "Trọng số chủ đề trong hồ sơ + độ lệch học được từ phản hồi"],
            ["Nguồn", "Trọng số nguồn trong hồ sơ + độ lệch học được"],
            ["Từ khóa", "Mỗi từ khóa theo dõi xuất hiện (khớp nguyên từ) cộng keyword_weight"],
            ["Độ mới", "recency_weight × 2^(−tuổi bài / half_life_hours), mặc định giảm một nửa sau 48 giờ"],
            ["Loại bỏ", "Bài chứa từ khóa chặn, sắc thái bị ẩn hoặc đã bị đánh dấu không thích"],
        ],
        widths=[4.0, 12.0],
    )
    body(
        doc,
        "Mỗi lượt thích hoặc không thích cộng/trừ 0,3 điểm cho từng chủ đề và nguồn của bài, giới hạn "
        "trong khoảng ±2 để một vài phản hồi không làm lệch hẳn hồ sơ. Kết quả xuất hiện ở tab "
        "Dành cho bạn của web (kèm điểm và lý do khi rê chuột) và ở lệnh digest.",
    )

    doc.add_page_break()

    # -------------------------------------------------------------- Chương III
    heading(doc, "CHƯƠNG III: TRIỂN KHAI VÀ ĐÁNH GIÁ", 1)

    heading(doc, "3.1. Môi trường triển khai", 2)
    table(
        doc,
        ["Thành phần", "Cấu hình sử dụng"],
        [
            ["Hệ điều hành", "Windows 11, shell PowerShell 5.1"],
            ["Python", "3.14.7 (tương thích ≥ 3.11)"],
            ["Thư viện", "httpx, lxml, PyYAML, smolagents, openai (client), python-docx"],
            ["LLM", "Ollama + qwen2.5:7b (4-bit), hoặc chế độ mock khi không có GPU"],
            ["Lưu trữ", "SQLite (data/news.db), tài liệu tại data/documents/"],
            ["Giao diện", "CLI và web demo (http.server, không cần framework)"],
            ["Kích thước mã nguồn", "13 module Python chính, 35 kiểm thử tự động"],
        ],
        widths=[5.0, 11.0],
    )

    heading(doc, "3.2. Cấu hình nguồn tin và kết quả thực nghiệm", 2)
    body(
        doc,
        "Bảy nguồn tin được khai báo trong tệp cấu hình. Sáu nguồn dùng RSS, một nguồn dùng adapter "
        "HTML với XPath. Trong quá trình chạy thật, hai cấu hình ban đầu thất bại và đã được sửa: "
        "VietnamNet trả về 404 với đường dẫn feed tin mới, và Bao Đầu Tư không có thẻ h3 nên bộ chọn "
        "tiêu đề không khớp. Sau khi đổi sang feed thời sự và cho adapter chọn liên kết đầu tiên có "
        "văn bản, cả bảy nguồn đều hoạt động.",
    )
    table(
        doc,
        ["Nguồn", "Loại", "Trạng thái", "Số từ bóc tách (mẫu)"],
        [
            ["VnExpress", "RSS", "OK", "1.111 – 1.597"],
            ["Tuổi Trẻ", "RSS", "OK", "545 – 1.538"],
            ["Thanh Niên", "RSS", "OK", "165 – 439"],
            ["Dân Trí", "RSS", "OK", "384 – 1.117"],
            ["VietnamNet", "RSS", "OK (sau khi sửa feed)", "đã kiểm tra 1.000 mục feed"],
            ["Nhân Dân", "RSS", "OK", "321 – 1.586"],
            ["Báo Đầu Tư", "HTML + XPath", "OK (sau khi sửa selector)", "1.082 – 1.716"],
        ],
        widths=[3.5, 3.2, 4.4, 4.9],
    )
    body(
        doc,
        "Kết quả cho thấy độ dài thân bài bóc tách được phụ thuộc bản chất bài viết: bài phân tích "
        "đạt trên 1.000 từ, trong khi bản tin ngắn hoặc bài dạng video chỉ vài trăm từ. Bài video "
        "không có thân văn bản bị loại đúng theo ngưỡng tối thiểu, tránh ghi dữ liệu rỗng vào bảng.",
    )

    heading(doc, "3.3. Triển khai mô hình ngôn ngữ", 2)
    body(
        doc,
        "Mô hình được gọi qua endpoint /api/chat của Ollama với tham số format bằng json, buộc đầu ra "
        "đúng cấu trúc. Prompt quy định rõ ba trường summary, topics và sentiment, kèm chỉ dẫn không "
        "thêm thông tin ngoài bài. Nếu mô hình trả về văn bản có lẫn lời dẫn, bộ phân tích vẫn trích "
        "được đối tượng JSON bằng cách đếm độ sâu ngoặc, nên không phụ thuộc định dạng tuyệt đối.",
    )
    body(
        doc,
        "Khi Ollama không khả dụng hoặc trả lời không hợp lệ, hệ thống dùng hàm làm giàu theo luật: "
        "tóm tắt lấy hai câu đầu, chủ đề đối chiếu bộ từ khóa, sắc thái đếm từ tích cực và tiêu cực. "
        "Cơ chế dự phòng này giữ cho pipeline luôn hoàn tất, đồng thời là nền tảng cho bộ kiểm thử "
        "offline chạy trong môi trường không có mô hình.",
    )
    code_block(
        doc,
        """POST http://localhost:11434/api/chat
{
  "model": "qwen2.5:7b",
  "stream": false,
  "format": "json",
  "options": { "temperature": 0.2, "num_ctx": 8192 },
  "messages": [
    { "role": "system", "content": "Chi tra ve JSON hop le." },
    { "role": "user", "content": "Tieu de: ...\\nNoi dung: ..." }
  ]
}""",
        "Đoạn mã 3.1. Yêu cầu gửi tới Ollama",
    )

    heading(doc, "3.4. Kiểm thử tự động", 2)
    body(
        doc,
        "Bộ kiểm thử dùng unittest của thư viện chuẩn, không phụ thuộc mạng. Một fetcher tĩnh thay thế "
        "tầng HTTP, cho phép mô phỏng feed, trang bài và tệp đính kèm. Kết quả chạy: 35/35 test đạt.",
    )
    table(
        doc,
        ["Nhóm kiểm thử", "Nội dung", "Số test"],
        [
            ["test_adapters", "RSS, Atom, giới hạn số lượng, HTML + XPath", "4"],
            ["test_extract", "Tiêu đề, thân bài, metadata, liên kết tài liệu, ngưỡng tối thiểu", "3"],
            ["test_llm", "Trích JSON nhiễu, từ chối đầu ra rỗng, heuristic chủ đề/sắc thái", "4"],
            ["test_store", "Chèn và khử trùng lặp, đồng bộ, log lượt chạy", "3"],
            ["test_pipeline", "Chạy đầy đủ, phát hiện trùng, outbox Lark, xuất JSONL", "5"],
            ["test_personalize", "Chấm điểm, từ khóa nguyên từ, lọc, độ mới, học từ phản hồi, lưu phản hồi", "9"],
            ["test_review_fixes", "Đồng bộ Lark theo lô, không tải lại bài đã bỏ qua, chuẩn hóa đầu ra LLM, an toàn URL ảnh", "7"],
        ],
        widths=[4.2, 9.8, 2.0],
    )

    heading(doc, "3.5. Đánh giá theo ISO/IEC 25010", 2)
    table(
        doc,
        ["Đặc tính", "Đánh giá", "Bằng chứng"],
        [
            ["Hiệu năng", "Đạt", "Xử lý tuần tự từng bài; lxml bóc tách dưới 0,1 giây/trang; nút thắt là LLM"],
            ["Độ tin cậy", "Đạt", "Lỗi một nguồn được ghi nhận và bỏ qua; có fallback heuristic"],
            ["Khả năng bảo trì", "Đạt", "13 module một trách nhiệm; 35 test; kiểu dữ liệu tĩnh"],
            ["Bảo mật", "Đạt", "Không rời dữ liệu khỏi máy; agent chỉ gọi 4 công cụ; giới hạn dung lượng tải; web chặn POST khác nguồn gốc"],
            ["Tương thích", "Đạt", "Windows/Linux; Python ≥ 3.11; Lark Base qua HTTPS"],
            ["Khả năng sử dụng", "Khá", "CLI rõ ràng; web demo có lọc, tìm kiếm và danh sách cá nhân hóa"],
        ],
        widths=[3.6, 2.4, 10.0],
    )

    heading(doc, "3.6. Hạn chế còn tồn tại", 2)
    for item in [
        "Bài dạng video hoặc nội dung tải bằng JavaScript chưa bóc tách được thân văn bản.",
        "Web chỉ là bản demo cho một người dùng, chưa có đăng nhập và chưa cho sửa tóm tắt.",
        "Cá nhân hóa dựa trên chủ đề, nguồn và từ khóa; chưa hiểu ngữ nghĩa nội dung bài.",
        "Trình tải trang chưa chặn địa chỉ nội bộ khi theo chuyển hướng và chưa đọc robots.txt.",
        "Chất lượng tóm tắt phụ thuộc mô hình; 7B tiết kiệm tài nguyên nhưng kém 14B ở bài dài.",
        "Lịch chạy tự động chưa tích hợp sẵn, cần dùng Task Scheduler hoặc cron.",
    ]:
        bullet(doc, item)

    doc.add_page_break()

    # --------------------------------------------------------------- Chương IV
    heading(doc, "CHƯƠNG IV: KẾT LUẬN VÀ HƯỚNG PHÁT TRIỂN", 1)

    heading(doc, "4.1. Kết quả đạt được", 2)
    body(
        doc,
        "Đề tài đã xây dựng hoàn chỉnh một ứng dụng Python thay thế luồng n8n trước đây. Hệ thống thu "
        "thập được tin từ bảy nguồn báo, bóc tách thân bài và metadata, tải tài liệu đính kèm, tóm tắt "
        "và phân loại bằng LLM cục bộ, lưu vào SQLite có khử trùng lặp hai lớp và đồng bộ lên Lark Base "
        "theo lô. Ngoài ra hệ thống có giao diện web demo và cơ chế cá nhân hóa dựa trên hồ sơ và phản "
        "hồi thích/không thích.",
    )
    body(
        doc,
        "Về mặt kỹ thuật, ba kết quả đáng chú ý. Thứ nhất, kiến trúc phân tầng cho phép chạy toàn bộ "
        "quá trình xử lý mà không cần mạng, nhờ đó 35 kiểm thử tự động chạy trong dưới 0,5 giây. Thứ hai, "
        "tác nhân AI được giới hạn trong bốn công cụ thay vì sinh mã tự do, cân bằng giữa tính linh "
        "hoạt và an toàn. Thứ ba, cơ chế dự phòng theo luật đảm bảo pipeline không bao giờ dừng vì lỗi "
        "mô hình.",
    )
    body(
        doc,
        "So với bản n8n, mã nguồn mới kiểm thử được, tái sử dụng được ngoài giao diện kéo–thả, và "
        "kiểm soát được từng bước xử lý văn bản — điều mà một node đóng gói sẵn không làm được.",
    )

    heading(doc, "4.2. Hướng phát triển", 2)
    body(
        doc,
        "Các hướng phát triển được chia theo hai trục: bổ sung năng lực xử lý dữ liệu, và hoàn thiện "
        "khả năng vận hành thực tế. Mỗi hạng mục đều gắn với một điều kiện hoàn thành đo được, để "
        "tránh việc đánh giá tiến độ bằng cảm tính.",
    )

    heading(doc, "4.2.1. Nhóm chức năng ưu tiên", 3)
    table(
        doc,
        ["Hướng phát triển", "Nội dung", "Giá trị"],
        [
            ["Giao diện web", "Nâng cấp bản demo: đăng nhập, nhiều hồ sơ, cho phép sửa tóm tắt", "Tăng khả năng sử dụng"],
            ["Cá nhân hóa ngữ nghĩa", "Embedding để so độ tương đồng với các bài đã thích", "Gợi ý chính xác hơn"],
            ["Khử trùng lặp ngữ nghĩa", "Embedding + cosine similarity để gộp bài cùng sự việc", "Giảm tin lặp"],
            ["Tìm kiếm ngữ nghĩa", "Vector store cho phép hỏi đáp trên kho bài đã lưu", "Khai thác dữ liệu"],
            ["Lịch chạy tự động", "Task Scheduler/cron gọi CLI mỗi sáng", "Vận hành thực tế"],
            ["Cảnh báo lỗi", "Gửi thông báo Lark/Telegram khi lượt chạy thất bại", "Giám sát"],
            ["Đa ngôn ngữ", "Dịch song ngữ tiêu đề và tóm tắt", "Mở rộng độc giả"],
        ],
        widths=[4.4, 7.6, 4.0],
    )

    heading(doc, "4.2.2. Kiến trúc mục tiêu", 3)
    body(
        doc,
        "Kiến trúc hiện tại chạy theo lịch (polling): mỗi lượt chạy quét toàn bộ nguồn dù phần lớn bài "
        "đã có trong cơ sở dữ liệu. Kiến trúc mục tiêu chuyển sang hướng sự kiện (event-driven): nguồn "
        "tin hoặc bộ lập lịch phát một sự kiện, hệ thống chỉ xử lý phần chênh lệch. Về dài hạn, ba "
        "thành phần có thể tách thành dịch vụ độc lập — bộ thu thập, bộ suy luận và bộ phát tán — "
        "giao tiếp qua hàng đợi, nhờ đó có thể mở rộng từng phần theo tải thực tế.",
    )

    heading(doc, "4.2.3. Lộ trình đề xuất", 3)
    body(
        doc,
        "Lộ trình chia ba giai đoạn. Các ngưỡng trong cột điều kiện hoàn thành là mục tiêu đề xuất, "
        "cần hiệu chỉnh lại sau khi có số liệu vận hành thực tế.",
    )
    table(
        doc,
        ["Giai đoạn", "Nội dung", "Điều kiện hoàn thành"],
        [
            [
                "Ngắn hạn",
                "Ổn định luồng thu thập; bổ sung cảnh báo lỗi tự động qua Lark/Telegram",
                "Chạy liên tục 30 ngày không có lỗi nghiêm trọng",
            ],
            [
                "Ngắn hạn",
                "Bổ sung bước khử trùng lặp ngữ nghĩa bằng embedding",
                "Tỉ lệ trùng lặp sau lọc dưới 5%",
            ],
            [
                "Trung hạn",
                "Gửi bản tin cá nhân hóa (digest) định kỳ qua Lark",
                "Bản tin gửi đúng lịch, có điểm phù hợp kèm theo",
            ],
            [
                "Dài hạn",
                "Chuyển sang kiến trúc event-driven hoàn toàn",
                "Không còn lượt chạy rỗng",
            ],
            [
                "Dài hạn",
                "Mở rộng nguồn RSS và đa dạng hóa loại dữ liệu",
                "Hỗ trợ ít nhất 3 loại nguồn: RSS, HTML và API",
            ],
        ],
        widths=[2.6, 8.4, 5.0],
    )
    body(
        doc,
        "Ba hạng mục ngắn hạn có thể bắt đầu ngay trên mã nguồn hiện tại: cảnh báo lỗi chỉ cần một "
        "hàm gửi thông báo trong tầng điều phối; khử trùng lặp ngữ nghĩa tận dụng endpoint embedding "
        "sẵn có của Ollama; còn ổn định thu thập đã có nền tảng nhờ cơ chế cô lập lỗi theo từng nguồn.",
    )

    heading(doc, "KẾT LUẬN", 1)
    body(
        doc,
        "Đề tài đã chứng minh rằng một tác nhân AI chạy cục bộ, kết hợp với kỹ thuật bóc tách nội dung "
        "cẩn thận, có thể thay thế phần lớn công việc đọc và phân loại tin tức thủ công. Giá trị lớn "
        "nhất không nằm ở mô hình ngôn ngữ, mà ở cách tổ chức hệ thống: tách bạch tầng, giới hạn quyền "
        "của tác nhân, và luôn có đường lui khi mô hình sai. Đây cũng là bài học khi chuyển từ một công "
        "cụ trực quan như n8n sang mã nguồn: chi phí ban đầu cao hơn, nhưng đổi lại là khả năng kiểm "
        "chứng và mở rộng lâu dài.",
    )

    doc.add_page_break()

    heading(doc, "TÀI LIỆU THAM KHẢO", 1)
    for item in [
        "Hugging Face. smolagents documentation — ToolCallingAgent và tool decorator.",
        "Ollama. API reference — /api/chat, chế độ JSON.",
        "Lark Open Platform. Bitable API — authentication và batch_create records.",
        "Python Software Foundation. sqlite3 — DB-API 2.0 interface.",
        "ISO/IEC 25010:2011. Systems and software Quality Requirements and Evaluation (SQuaRE).",
    ]:
        bullet(doc, item)

    heading(doc, "PHỤ LỤC A: CẤU TRÚC MÃ NGUỒN", 1)
    code_block(
        doc,
        """MTK/
├── app/
│   ├── config.py        cấu hình + registry nguồn
│   ├── models.py        dataclass và hàm băm nội dung
│   ├── net.py           HTTP client, fetcher tĩnh cho test
│   ├── extract.py       bóc tách bài và liên kết tài liệu
│   ├── documents.py     tải tài liệu, SHA-256
│   ├── store.py         SQLite: articles/documents/runs
│   ├── llm.py           Ollama + heuristic
│   ├── lark.py          Lark Base sink
│   ├── agent.py         smolagents ToolCallingAgent
│   ├── pipeline.py      điều phối
│   ├── personalize.py   chấm điểm cá nhân hóa, học từ phản hồi
│   ├── web.py           web demo
│   └── cli.py           giao diện dòng lệnh
├── app/adapters/        rss.py, html.py
├── config/sources.yaml  7 nguồn tin
├── config/profile.yaml  hồ sơ cá nhân hóa
├── tests/               35 kiểm thử
└── tools/build_report_docx.py""",
        "Cấu trúc thư mục dự án",
    )

    heading(doc, "PHỤ LỤC B: LỆNH VẬN HÀNH", 1)
    code_block(
        doc,
        """python -m app.cli sources
python -m app.cli collect --limit 3 --dry-run
python -m app.cli collect --source VnExpress
python -m app.cli collect --agent
python -m app.cli ingest-url https://example.vn/bai-viet
python -m app.cli stats
python -m app.cli recent --limit 5
python -m app.cli export --out data/articles.jsonl
python -m app.cli --dry-run push
python -m app.cli digest --top 5
python -m app.cli feedback https://example.vn/bai-viet --dislike
python -m app.web --port 8000
python -m unittest discover -s tests -t . -v""",
        "Các lệnh thường dùng",
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes)")
