from __future__ import annotations

import unittest

from app.adapters import parse_feed
from app.adapters.html import HtmlAdapter
from app.config import Source

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>VnExpress</title>
    <item>
      <title>Bai thu nhat</title>
      <link>https://example.vn/bai-1</link>
      <pubDate>Mon, 10 Nov 2026 08:30:00 +0700</pubDate>
    </item>
    <item>
      <title>Bai thu hai</title>
      <link>https://example.vn/bai-2</link>
      <pubDate>Mon, 10 Nov 2026 09:00:00 +0700</pubDate>
    </item>
    <item>
      <title>Bai thu nhat</title>
      <link>https://example.vn/bai-1</link>
    </item>
  </channel>
</rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Feed</title>
  <entry>
    <title>Atom entry</title>
    <link href="https://example.vn/atom-1"/>
    <updated>2026-11-10T08:30:00Z</updated>
  </entry>
</feed>"""

HTML_PAGE = """<html><body>
  <article><h3><a href="/tin-a">Tin A</a></h3><time datetime="2026-11-09T10:00:00+07:00"></time></article>
  <article><h3><a href="/tin-b">Tin B</a></h3><time datetime="2026-11-09T11:00:00+07:00"></time></article>
</body></html>"""


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = Source(
            name="Test",
            url="https://example.vn/feed",
            kind="rss",
            country="VN",
            language="vi",
        )

    def test_rss_parses_and_deduplicates(self) -> None:
        refs = parse_feed(RSS, self.source)
        self.assertEqual([r.url for r in refs], ["https://example.vn/bai-1", "https://example.vn/bai-2"])
        self.assertTrue(refs[0].published_at.startswith("2026-11-10T01:30:00"))

    def test_rss_respects_limit(self) -> None:
        refs = parse_feed(RSS, self.source, limit=1)
        self.assertEqual(len(refs), 1)

    def test_atom_feed(self) -> None:
        refs = parse_feed(ATOM, self.source)
        self.assertEqual(refs[0].url, "https://example.vn/atom-1")
        self.assertTrue(refs[0].published_at.startswith("2026-11-10T08:30:00"))

    def test_html_adapter_with_xpath_selectors(self) -> None:
        source = Source(
            name="HTML",
            url="https://example.vn/",
            kind="html",
            country="VN",
            language="vi",
            selectors={
                "item": "//article",
                "title": ".//h3//a",
                "link": ".//h3//a/@href",
                "date": ".//time/@datetime",
            },
        )
        refs = HtmlAdapter().parse(HTML_PAGE, source)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].url, "https://example.vn/tin-a")
        self.assertEqual(refs[0].title, "Tin A")


if __name__ == "__main__":
    unittest.main()
