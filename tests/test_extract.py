from __future__ import annotations

import unittest

from app.extract import extract_article, extract_document_links

PAGE = """<!doctype html>
<html lang="vi">
<head>
  <title>Trang chu</title>
  <meta property="og:title" content="Chuyen doi so nganh det may" />
  <meta property="article:published_time" content="2026-11-10T08:30:00+07:00" />
  <meta name="author" content="Nguyen Van A" />
  <meta property="og:image" content="https://example.vn/anh.jpg" />
</head>
<body>
  <nav><p>Menu quang cao</p></nav>
  <article>
    <h1>Chuyen doi so nganh det may</h1>
    <p>Nganh det may Viet Nam dang day manh chuyen doi so de nang cao nang suat va giam chi phi san xuat trong dai han.</p>
    <p>Nhieu doanh nghiep da dua he thong quan ly vao van hanh, giup theo doi don hang theo thoi gian thuc va giam sai sot.</p>
    <p>Du bao trong ba nam toi, ty le tu dong hoa se tang manh o cac cong doan cat va may.</p>
    <a href="/files/bao-cao-det-may.pdf">Tai bao cao</a>
    <a href="/files/phu-luc.docx">Phu luc</a>
    <a href="/lien-he">Lien he</a>
  </article>
  <footer><p>Ban quyen thuoc ve toa soan</p></footer>
</body>
</html>"""


class ExtractTests(unittest.TestCase):
    def test_extracts_title_body_and_metadata(self) -> None:
        article = extract_article(PAGE, "https://example.vn/bai-viet")
        self.assertEqual(article.title, "Chuyen doi so nganh det may")
        self.assertIn("chuyen doi so", article.text.lower())
        self.assertNotIn("quang cao", article.text.lower())
        self.assertEqual(article.author, "Nguyen Van A")
        self.assertEqual(article.language, "vi")
        self.assertEqual(article.image_url, "https://example.vn/anh.jpg")

    def test_collects_only_document_links(self) -> None:
        links = extract_document_links(PAGE, "https://example.vn/bai-viet")
        self.assertEqual(
            links,
            [
                "https://example.vn/files/bao-cao-det-may.pdf",
                "https://example.vn/files/phu-luc.docx",
            ],
        )

    def test_rejects_short_body(self) -> None:
        page = "<html><body><article><p>Qua ngan</p></article></body></html>"
        article = extract_article(page, "https://example.vn/x")
        self.assertLess(len(article.text), 80)


if __name__ == "__main__":
    unittest.main()
