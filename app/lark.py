from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

FIELD_MAP = {
    "title": "Tiêu đề",
    "url": "URL",
    "source": "Nguồn",
    "country": "Quốc gia",
    "language": "Ngôn ngữ",
    "summary": "Tóm tắt",
    "topics": "Chủ đề",
    "sentiment": "Sắc thái",
    "author": "Tác giả",
    "published_at": "Ngày đăng",
    "fetched_at": "Ngày lấy tin",
    "word_count": "Số từ",
    "image_url": "Ảnh",
}

SENTIMENT_LABELS = {
    "positive": "Tích cực",
    "neutral": "Trung tính",
    "negative": "Tiêu cực",
}


def build_fields(row: dict) -> dict:
    return {
        FIELD_MAP["title"]: row.get("title", ""),
        FIELD_MAP["url"]: row.get("url", ""),
        FIELD_MAP["source"]: row.get("source", ""),
        FIELD_MAP["country"]: row.get("country", ""),
        FIELD_MAP["language"]: row.get("language", ""),
        FIELD_MAP["summary"]: row.get("summary", ""),
        FIELD_MAP["topics"]: row.get("topics", []),
        FIELD_MAP["sentiment"]: SENTIMENT_LABELS.get(row.get("sentiment", "neutral"), "Trung tính"),
        FIELD_MAP["author"]: row.get("author") or "",
        FIELD_MAP["published_at"]: row.get("published_at") or "",
        FIELD_MAP["fetched_at"]: row.get("fetched_at") or "",
        FIELD_MAP["word_count"]: int(row.get("word_count") or 0),
        FIELD_MAP["image_url"]: row.get("image_url") or "",
    }


class LarkSink:
    CHUNK_SIZE = 500
    def __init__(
        self,
        *,
        app_id: str | None,
        app_secret: str | None,
        app_token: str | None,
        table_id: str | None,
        base_url: str = "https://open.larksuite.com",
        outbox_dir: Path | None = None,
        dry_run: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self.base_url = base_url.rstrip("/")
        self.outbox_dir = outbox_dir
        self.dry_run = dry_run
        self.timeout = timeout
        self._token: str | None = None
        self._token_expiry: float = 0.0
        # URLs confirmed by Lark so far; survives a failure in a later chunk so callers
        # can mark exactly these as synced instead of re-sending (and duplicating) them.
        self.pushed_urls: list[str] = []

    @property
    def configured(self) -> bool:
        return all([self.app_id, self.app_secret, self.app_token, self.table_id])

    def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token
        response = httpx.post(
            f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Lark auth failed: {data.get('msg')}")
        self._token = data["tenant_access_token"]
        self._token_expiry = time.time() + int(data.get("expire", 7200)) - 60
        return self._token

    def build_records(self, rows: list[dict]) -> list[dict]:
        return [{"fields": build_fields(row)} for row in rows]

    def push(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        self.pushed_urls = []
        records = self.build_records(rows)
        if self.dry_run or not self.configured:
            return self._write_outbox(records)

        token = self._tenant_token()
        url = (
            f"{self.base_url}/open-apis/bitable/v1/apps/{self.app_token}"
            f"/tables/{self.table_id}/records/batch_create"
        )
        pushed = 0
        for start in range(0, len(records), self.CHUNK_SIZE):
            chunk = records[start : start + self.CHUNK_SIZE]
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"records": chunk},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise RuntimeError(f"Lark batch_create failed: {payload.get('msg')}")
            pushed += len(chunk)
            self.pushed_urls.extend(row["url"] for row in rows[start : start + self.CHUNK_SIZE])
        return pushed

    def _write_outbox(self, records: list[dict]) -> int:
        if self.outbox_dir is None:
            return 0
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.outbox_dir / f"lark-{stamp}.jsonl"
        with target.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return len(records)
