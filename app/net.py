from __future__ import annotations

import time
from typing import Protocol

import httpx


class FetchError(RuntimeError):
    pass


class _Permanent(Exception):
    """Internal: a failure that retrying cannot fix."""


class Fetcher(Protocol):
    def text(self, url: str) -> str: ...

    def bytes(self, url: str, max_bytes: int) -> bytes: ...


class HttpFetcher:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        user_agent: str = "NewsDigestAgent/1.0",
        retries: int = 2,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.retries = retries
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi,en;q=0.8",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, url: str, stream: bool = False) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                if stream:
                    request = self._client.build_request("GET", url)
                    response = self._client.send(request, stream=True)
                else:
                    response = self._client.get(url)
                if response.status_code >= 400:
                    status = response.status_code
                    response.close()
                    # 4xx (except 408/429) will not change on retry
                    retryable = status >= 500 or status in (408, 429)
                    error = FetchError(f"HTTP {status} for {url}")
                    if not retryable:
                        raise _Permanent(str(error))
                    raise error
                return response
            except _Permanent as exc:
                raise FetchError(str(exc)) from None
            except (httpx.HTTPError, FetchError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
        raise FetchError(str(last_error) if last_error else f"failed to fetch {url}")

    def text(self, url: str) -> str:
        response = self._get(url)
        response.encoding = response.encoding or "utf-8"
        return response.text

    def bytes(self, url: str, max_bytes: int) -> bytes:
        response = self._get(url, stream=True)
        try:
            declared = response.headers.get("content-length")
            if declared and int(declared) > max_bytes:
                raise FetchError(f"document too large ({declared} bytes): {url}")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError(f"document exceeded {max_bytes} bytes: {url}")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()


class StaticFetcher:
    """Offline fetcher used by tests and `--offline` runs."""

    def __init__(self, pages: dict[str, str], binaries: dict[str, bytes] | None = None) -> None:
        self.pages = pages
        self.binaries = binaries or {}
        self.calls: list[str] = []

    def text(self, url: str) -> str:
        self.calls.append(url)
        if url not in self.pages:
            raise FetchError(f"no offline page for {url}")
        return self.pages[url]

    def bytes(self, url: str, max_bytes: int) -> bytes:
        self.calls.append(url)
        if url not in self.binaries:
            raise FetchError(f"no offline binary for {url}")
        data = self.binaries[url]
        if len(data) > max_bytes:
            raise FetchError(f"document exceeded {max_bytes} bytes: {url}")
        return data
