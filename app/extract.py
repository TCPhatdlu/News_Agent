from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlparse

from lxml import html as lxml_html

from .models import ExtractedArticle, normalize_text

DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".csv",
    ".rtf",
}

_DROP_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg")
_BOILERPLATE = re.compile(
    r"(quang cao|quảng cáo|advertisement|subscribe|dang ky nhan tin|đăng ký nhận tin|"
    r"copyright|ban quyen|bản quyền|theo dõi chúng tôi|follow us)",
    re.IGNORECASE,
)


def _meta(tree, *names: str) -> str | None:
    for name in names:
        values = tree.xpath(
            f"//meta[@property='{name}']/@content | //meta[@name='{name}']/@content"
        )
        if values:
            value = normalize_text(values[0])
            if value:
                return value
    return None


def _is_document_link(href: str) -> bool:
    parsed = urlparse(href)
    path = unquote(parsed.path).lower()
    for extension in DOCUMENT_EXTENSIONS:
        if path.endswith(extension):
            return True
    return False


def extract_document_links(page: str, base_url: str, limit: int = 5) -> list[str]:
    tree = lxml_html.fromstring(page)
    found: list[str] = []
    seen: set[str] = set()
    for anchor in tree.xpath("//a[@href]"):
        href = anchor.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        if not _is_document_link(absolute) or absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= limit:
            break
    return found


def _best_content_node(tree):
    candidates = tree.xpath("//article") or tree.xpath("//main") or []
    candidates += tree.xpath(
        "//div[contains(@class,'content') or contains(@class,'body') or contains(@id,'content')]"
    )
    if not candidates:
        body = tree.find(".//body")
        return [body] if body is not None else [tree]

    def score(node) -> int:
        text = normalize_text(node.text_content())
        return len(text)

    candidates.sort(key=score, reverse=True)
    return candidates


def extract_article(page: str, url: str, max_documents: int = 5) -> ExtractedArticle:
    tree = lxml_html.fromstring(page)
    for tag in _DROP_TAGS:
        for node in tree.xpath(f"//{tag}"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    title = _meta(tree, "og:title", "twitter:title")
    if not title:
        headings = tree.xpath("//h1//text()")
        title = normalize_text(headings[0]) if headings else ""

    node = _best_content_node(tree)[0]
    paragraphs = [normalize_text(p.text_content()) for p in node.xpath(".//p")]
    paragraphs = [p for p in paragraphs if len(p) >= 40 and not _BOILERPLATE.search(p)]
    if paragraphs:
        body = "\n\n".join(paragraphs)
    else:
        body = normalize_text(node.text_content())

    author = _meta(tree, "author", "article:author", "og:article:author")
    published = _meta(tree, "article:published_time", "og:published_time", "datePublished")
    image = _meta(tree, "og:image", "twitter:image")
    langs = tree.xpath("//html/@lang")
    language = normalize_text(langs[0]).split("-")[0] if langs else None

    return ExtractedArticle(
        title=title,
        text=body,
        author=author,
        published_at=published,
        language=language,
        image_url=image,
        document_links=extract_document_links(page, url, limit=max_documents),
    )
