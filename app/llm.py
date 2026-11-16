from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

import httpx

from .models import Enrichment, normalize_text

SYSTEM_PROMPT = (
    "Ban la bien tap vien tin tuc. Chi tra ve mot doi tuong JSON hop le, khong them chu nao khac."
)

USER_TEMPLATE = """Phan tich bai bao sau va tra ve JSON dung 3 truong:
- "summary": tom tat 2-3 cau bang tieng Viet, trung thanh, khong them thong tin ngoai bai.
- "topics": mang 1-3 nhan, MOI nhan phai lay dung trong danh sach: Kinh tế, Công nghệ, Thể thao,
  Giáo dục, Sức khỏe, Thời trang, Chính trị, Môi trường, Giải trí, Khoa học, Đời sống.
- "sentiment": mot trong "positive", "neutral", "negative".

Tieu de: {title}
Nguon: {source}
Noi dung:
{body}
"""

TOPIC_KEYWORDS = {
    "Kinh tế": ("kinh tế", "chứng khoán", "lạm phát", "gdp", "đầu tư", "ngân hàng", "giá vàng", "xuất khẩu"),
    "Công nghệ": ("công nghệ", "ai", "trí tuệ nhân tạo", "chip", "smartphone", "phần mềm", "dữ liệu"),
    "Thể thao": ("bóng đá", "thể thao", "vận động viên", "olympic", "tennis", "sea games"),
    "Giáo dục": ("giáo dục", "tuyển sinh", "sinh viên", "trường học", "đại học", "học sinh"),
    "Sức khỏe": ("sức khỏe", "bệnh", "y tế", "vaccine", "dịch", "bệnh viện"),
    "Thời trang": ("thời trang", "xu hướng", "nhà thiết kế", "bộ sưu tập", "fashion"),
    "Chính trị": ("chính phủ", "quốc hội", "bộ trưởng", "ngoại giao", "chính sách"),
    "Môi trường": ("môi trường", "biến đổi khí hậu", "ô nhiễm", "thiên tai", "bão"),
    "Giải trí": ("phim", "ca sĩ", "giải trí", "âm nhạc", "sao", "showbiz"),
}

ALLOWED_TOPICS = tuple(TOPIC_KEYWORDS) + ("Đời sống", "Khoa học", "Tổng hợp")
_TOPIC_LOOKUP = {topic.lower(): topic for topic in ALLOWED_TOPICS}

POSITIVE = ("tăng", "thành công", "khởi sắc", "tích cực", "đạt", "phát triển", "hỗ trợ")
NEGATIVE = ("giảm", "thiệt hại", "sai phạm", "khủng hoảng", "tử vong", "cảnh báo", "sụt")


def _mentions(haystack: str, keyword: str) -> bool:
    if len(keyword) <= 3:
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", haystack) is not None
    return keyword in haystack


def heuristic_enrichment(title: str, text: str, source: str = "") -> Enrichment:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalize_text(text)) if s.strip()]
    summary = " ".join(sentences[:2])[:360].strip()
    if not summary:
        summary = normalize_text(title)[:200]

    haystack = f"{title} {text}".lower()
    topics = [
        topic
        for topic, words in TOPIC_KEYWORDS.items()
        if any(_mentions(haystack, word) for word in words)
    ]
    if not topics:
        topics = ["Tổng hợp"]

    positive = _count_words(haystack, POSITIVE)
    negative = _count_words(haystack, NEGATIVE)
    if positive > negative:
        sentiment = "positive"
    elif negative > positive:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    return Enrichment(summary=summary, topics=topics[:4], sentiment=sentiment)


def _count_words(haystack: str, words: tuple[str, ...]) -> int:
    return sum(
        len(re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", haystack)) for word in words
    )


def extract_json_object(payload: str) -> dict:
    start = payload.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(payload)):
            char = payload[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = payload[start : index + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = payload.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


def parse_enrichment(content: str) -> Enrichment:
    """Shared by every LLM backend: JSON text -> validated Enrichment."""
    data = extract_json_object(content)
    summary = normalize_text(str(data.get("summary", "")))[:500]
    if not summary:
        raise ValueError("model returned an empty summary")
    raw_topics = data.get("topics") or []
    if isinstance(raw_topics, str):
        raw_topics = [raw_topics]
    topics: list[str] = []
    for raw in raw_topics:
        topic = _TOPIC_LOOKUP.get(normalize_text(str(raw)).lower())  # drop labels outside the list
        if topic and topic not in topics:
            topics.append(topic)
    sentiment = str(data.get("sentiment", "neutral")).lower()
    if sentiment not in {"positive", "neutral", "negative"}:
        sentiment = "neutral"
    return Enrichment(summary=summary, topics=topics[:4] or ["Tổng hợp"], sentiment=sentiment)


class LLMClient(Protocol):
    name: str

    def enrich(self, title: str, text: str, source: str = "") -> Enrichment: ...


class MockLLM:
    name = "mock"

    def enrich(self, title: str, text: str, source: str = "") -> Enrichment:
        return heuristic_enrichment(title, text, source)


class OllamaLLM:
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def health(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def enrich(self, title: str, text: str, source: str = "") -> Enrichment:
        prompt = USER_TEMPLATE.format(title=title, source=source, body=text[:8000])
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        response = httpx.post(
            f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        return parse_enrichment(content)


class CommandCodeLLM:
    """Uses the Command Code CLI in headless mode as the summarizing model."""

    name = "commandcode"

    def __init__(self, executable: str | None = None, timeout: float = 180.0) -> None:
        self.executable = executable or self._resolve_executable()
        self.timeout = timeout

    @staticmethod
    def _resolve_executable() -> str:
        for candidate in ("commandcode.cmd", "commandcode", "cmd"):
            path = shutil.which(candidate)
            if path and Path(path).name.lower() != "cmd.exe":
                return path
        if shutil.which("cmd") and Path(shutil.which("cmd")).name.lower() != "cmd.exe":
            return shutil.which("cmd")  # type: ignore[return-value]
        raise RuntimeError(
            "Command Code CLI not found. Install it with: npm i -g command-code"
        )

    @staticmethod
    def _final_text(stdout: str) -> str:
        """Headless JSON streams differ by version; accept every known shape."""
        text = ""
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame.get("finalText"), str):
                text = frame["finalText"]
            event = frame.get("event") or {}
            if event.get("type") == "run_end":
                result = event.get("result") or {}
                if isinstance(result.get("finalText"), str):
                    text = result["finalText"]
            if event.get("type") == "message_end":
                for block in event.get("content") or []:
                    if block.get("type") == "text" and block.get("text"):
                        text = block["text"]
        return text

    def enrich(self, title: str, text: str, source: str = "") -> Enrichment:
        prompt = (
            USER_TEMPLATE.format(title=title, source=source, body=text[:8000])
            + "\nChi tra ve JSON, khong kem loi dan, khong dung cong cu."
        )
        process = subprocess.run(
            [self.executable, "-p", "--output-format", "json", "--skip-onboarding"],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"commandcode exited {process.returncode}: {process.stderr.strip()[:200]}"
            )

        final_text = self._final_text(process.stdout)
        if not final_text:
            final_text = process.stdout

        return parse_enrichment(final_text)


def build_llm(settings) -> LLMClient:
    provider = (settings.llm_provider or "ollama").lower()
    if provider == "mock":
        return MockLLM()
    if provider in {"commandcode", "cmd"}:
        return CommandCodeLLM(timeout=settings.ollama_timeout)
    return OllamaLLM(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout,
    )
