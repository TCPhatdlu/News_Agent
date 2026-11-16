from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from .models import DocumentRecord
from .net import Fetcher

_SAFE = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")


def _safe_name(url: str) -> str:
    parsed = urlparse(url)
    stem = Path(unquote(parsed.path)).name or "document"
    cleaned = "".join(ch if ch.lower() in _SAFE else "_" for ch in stem)
    return cleaned[:120] or "document"


def download_documents(
    urls: list[str],
    *,
    article_url: str,
    fetcher: Fetcher,
    dest_dir: Path,
    max_bytes: int,
    limit: int = 3,
) -> list[DocumentRecord]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    records: list[DocumentRecord] = []
    seen: set[str] = set()

    for url in urls:
        if len(records) >= limit or url in seen:
            continue
        seen.add(url)
        try:
            payload = fetcher.bytes(url, max_bytes=max_bytes)
        except Exception:
            continue

        digest = hashlib.sha256(payload).hexdigest()
        suffix = Path(_safe_name(url)).suffix or ".bin"
        target = dest_dir / f"{digest[:16]}{suffix}"
        if not target.exists():
            target.write_bytes(payload)

        mime = mimetypes.guess_type(url)[0] or "application/octet-stream"
        records.append(
            DocumentRecord(
                url=url,
                article_url=article_url,
                title=_safe_name(url),
                kind=suffix.lstrip(".").lower(),
                mime=mime,
                size_bytes=len(payload),
                local_path=str(target),
                sha256=digest,
            )
        )
    return records
