from __future__ import annotations

import json

from .adapters import get_adapter
from .config import Settings, load_sources
from .pipeline import Pipeline

try:
    from smolagents import OpenAIServerModel, ToolCallingAgent, tool

    SMOLAGENTS_AVAILABLE = True
except Exception:
    SMOLAGENTS_AVAILABLE = False


AGENT_TASK = """Ban la tro ly tong hop tin tuc. Hay thuc hien:
1. Goi list_sources de biet cac nguon.
2. Voi moi nguon (toi da {max_sources} nguon), goi fetch_source_titles de lay danh sach bai moi.
3. Chon toi da {per_source} bai moi nhat moi nguon va goi process_article cho tung bai.
4. Ket thuc bang database_stats va tom tat ngan gon nhung gi da luu.
Khong bia dat bai bao. Chi xu ly URL do cong cu tra ve.
"""


class AgentUnavailable(RuntimeError):
    pass


class NewsAgent:
    def __init__(self, settings: Settings, pipeline: Pipeline | None = None) -> None:
        if not SMOLAGENTS_AVAILABLE:
            raise AgentUnavailable(
                "smolagents is not installed. Run: pip install smolagents"
            )
        self.settings = settings
        self.pipeline = pipeline or Pipeline(settings)
        self._process_log: list[str] = []

    def _sources_payload(self) -> str:
        sources = load_sources(self.settings.sources_path)
        return json.dumps(
            [
                {
                    "name": s.name,
                    "type": s.kind,
                    "country": s.country,
                    "url": s.url,
                }
                for s in sources
            ],
            ensure_ascii=False,
        )

    def _source_by_name(self, name: str):
        for source in load_sources(self.settings.sources_path):
            if source.name.lower() == name.lower():
                return source
        raise ValueError(f"unknown source: {name}. Call list_sources first.")

    def _fetch_titles(self, source_name: str, limit: int = 10) -> str:
        source = self._source_by_name(source_name)
        refs = get_adapter(source.kind).list_articles(source, self.pipeline.fetcher, limit=limit)
        return json.dumps(
            [{"title": r.title, "url": r.url, "published_at": r.published_at} for r in refs],
            ensure_ascii=False,
        )

    def _process_article(self, url: str, source_name: str) -> str:
        source = self._source_by_name(source_name)
        from .models import ArticleRef

        ref = ArticleRef(title="", url=url, source=source.name)
        article = self.pipeline.process_ref(ref, source, with_documents=True)
        if article is None:
            self._process_log.append(f"duplicate: {url}")
            return json.dumps({"status": "duplicate", "url": url}, ensure_ascii=False)
        self._process_log.append(f"saved: {article.title}")
        return json.dumps(
            {
                "status": "saved",
                "title": article.title,
                "topics": article.topics,
                "summary": article.summary,
                "documents": len(article.documents),
            },
            ensure_ascii=False,
        )

    def _stats(self) -> str:
        return json.dumps(self.pipeline.store.stats(), ensure_ascii=False)

    def _build_tools(self):
        agent = self

        def list_sources() -> str:
            """Tra ve danh sach nguon tin da cau hinh (ten, loai, quoc gia, url)."""
            return agent._sources_payload()

        def fetch_source_titles(source_name: str, limit: int = 10) -> str:
            """Lay danh sach bai moi cua mot nguon.

            Args:
                source_name: Ten nguon, phai khop voi list_sources.
                limit: So bai toi da can lay.
            """
            return agent._fetch_titles(source_name, limit)

        def process_article(url: str, source_name: str) -> str:
            """Tai, tom tat va luu mot bai bao vao co so du lieu.

            Args:
                url: URL bai bao lay tu fetch_source_titles.
                source_name: Ten nguon tuong ung.
            """
            return agent._process_article(url, source_name)

        def database_stats() -> str:
            """Thong ke so bai, so tai lieu va phan bo theo nguon."""
            return agent._stats()

        return [
            tool(list_sources),
            tool(fetch_source_titles),
            tool(process_article),
            tool(database_stats),
        ]

    def run(self, *, max_sources: int = 3, per_source: int = 2) -> str:
        model = OpenAIServerModel(
            model_id=self.settings.ollama_model,
            api_base=f"{self.settings.ollama_base_url}/v1",
            api_key="ollama",
        )
        agent = ToolCallingAgent(
            tools=self._build_tools(),
            model=model,
            max_steps=max(6, max_sources * per_source * 2),
            verbosity_level=1,
        )
        task = AGENT_TASK.format(max_sources=max_sources, per_source=per_source)
        result = agent.run(task)
        text = str(result)
        if self._process_log:
            text += "\n\nChi tiet: " + "; ".join(self._process_log)
        return text
