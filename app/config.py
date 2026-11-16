from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    kind: str
    country: str
    language: str
    max_articles: int = 15
    selectors: dict[str, str] | None = None


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    db_path: Path
    documents_dir: Path
    outbox_dir: Path
    sources_path: Path
    profile_path: Path
    llm_provider: str
    ollama_base_url: str
    ollama_model: str
    ollama_timeout: float
    request_timeout: float
    user_agent: str
    max_articles_per_source: int
    max_document_mb: int
    max_documents_per_article: int
    lark_app_id: str | None
    lark_app_secret: str | None
    lark_app_token: str | None
    lark_table_id: str | None
    lark_base_url: str
    dry_run: bool

    @property
    def lark_configured(self) -> bool:
        return all(
            [self.lark_app_id, self.lark_app_secret, self.lark_app_token, self.lark_table_id]
        )


def _env(env: dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or env.get(key) or default


def _as_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings(overrides: dict[str, object] | None = None) -> Settings:
    env = _load_env_file(PROJECT_ROOT / ".env")
    overrides = overrides or {}
    data_dir = Path(str(overrides.get("data_dir") or _env(env, "DATA_DIR", "data")))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    settings = Settings(
        root=PROJECT_ROOT,
        data_dir=data_dir,
        db_path=data_dir / "news.db",
        documents_dir=data_dir / "documents",
        outbox_dir=data_dir / "outbox",
        sources_path=Path(str(overrides.get("sources_path") or PROJECT_ROOT / "config" / "sources.yaml")),
        profile_path=Path(str(overrides.get("profile_path") or PROJECT_ROOT / "config" / "profile.yaml")),
        llm_provider=str(overrides.get("llm_provider") or _env(env, "LLM_PROVIDER", "ollama")),
        ollama_base_url=_env(env, "OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=_env(env, "OLLAMA_MODEL", "qwen2.5:7b"),
        ollama_timeout=float(_env(env, "OLLAMA_TIMEOUT", "120")),
        request_timeout=float(_env(env, "REQUEST_TIMEOUT", "20")),
        user_agent=_env(env, "USER_AGENT", "NewsDigestAgent/1.0 (+https://example.local)"),
        max_articles_per_source=int(_env(env, "MAX_ARTICLES_PER_SOURCE", "15")),
        max_document_mb=int(_env(env, "MAX_DOCUMENT_MB", "10")),
        max_documents_per_article=int(_env(env, "MAX_DOCUMENTS_PER_ARTICLE", "3")),
        lark_app_id=_env(env, "LARK_APP_ID") or None,
        lark_app_secret=_env(env, "LARK_APP_SECRET") or None,
        lark_app_token=_env(env, "LARK_APP_TOKEN") or None,
        lark_table_id=_env(env, "LARK_TABLE_ID") or None,
        lark_base_url=_env(env, "LARK_BASE_URL", "https://open.larksuite.com"),
        dry_run=_as_bool(overrides.get("dry_run"), _as_bool(_env(env, "DRY_RUN", "false"))),
    )
    return settings


def load_sources(path: Path | None = None) -> list[Source]:
    path = path or PROJECT_ROOT / "config" / "sources.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    sources: list[Source] = []
    for item in raw.get("sources") or []:
        sources.append(
            Source(
                name=item["name"],
                url=item["url"],
                kind=item.get("type", "rss"),
                country=item.get("country", "VN"),
                language=item.get("language", "vi"),
                max_articles=int(item.get("max_articles", defaults.get("max_articles", 15))),
                selectors=item.get("selectors"),
            )
        )
    return sources
