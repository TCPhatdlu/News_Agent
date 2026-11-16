"""Error workflow: decide when a run deserves an alert, and deliver it.

Delivery targets (all optional, read from env/.env):
  ALERT_TELEGRAM_BOT_TOKEN + ALERT_TELEGRAM_CHAT_ID   -> Telegram bot message
  ALERT_LARK_WEBHOOK                                  -> Lark custom-bot webhook
With no target configured, or DRY_RUN, alerts go to data/outbox/alerts-*.jsonl.
Every alert is also stored in the `alerts` table so the web health page can show it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import get_env
from .models import utc_now_iso


@dataclass
class AlertPolicy:
    # a feed must fail this many runs in a row before we page someone (one blip is noise)
    source_failures: int = 2
    # alert when more than this share of articles in a run failed
    article_failure_ratio: float = 0.5


class Notifier:
    def __init__(
        self,
        *,
        outbox_dir: Path | None,
        dry_run: bool = False,
        telegram_token: str | None = None,
        telegram_chat_id: str | None = None,
        lark_webhook: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.outbox_dir = outbox_dir
        self.dry_run = dry_run
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.lark_webhook = lark_webhook
        self.timeout = timeout

    @classmethod
    def from_env(cls, settings) -> "Notifier":
        return cls(
            outbox_dir=settings.outbox_dir,
            dry_run=settings.dry_run,
            telegram_token=get_env("ALERT_TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=get_env("ALERT_TELEGRAM_CHAT_ID") or None,
            lark_webhook=get_env("ALERT_LARK_WEBHOOK") or None,
        )

    def send(self, level: str, message: str) -> str:
        """Deliver an alert; returns where it went. Never raises: alerting must not break a run."""
        delivered: list[str] = []
        if not self.dry_run:
            if self.telegram_token and self.telegram_chat_id:
                try:
                    response = httpx.post(
                        f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                        json={"chat_id": self.telegram_chat_id, "text": f"[{level.upper()}] {message}"},
                        timeout=self.timeout,
                    )
                    if response.status_code == 200 and response.json().get("ok"):
                        delivered.append("telegram")
                except httpx.HTTPError:
                    pass
            if self.lark_webhook:
                try:
                    response = httpx.post(
                        self.lark_webhook,
                        json={"msg_type": "text", "content": {"text": f"[{level.upper()}] {message}"}},
                        timeout=self.timeout,
                    )
                    if response.status_code == 200 and response.json().get("code", 0) == 0:
                        delivered.append("lark")
                except (httpx.HTTPError, ValueError):
                    pass
        if not delivered:
            delivered.append(self._write_outbox(level, message))
        return ",".join(delivered)

    def _write_outbox(self, level: str, message: str) -> str:
        if self.outbox_dir is None:
            return "none"
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        with (self.outbox_dir / f"alerts-{day}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"at": utc_now_iso(), "level": level, "message": message}, ensure_ascii=False)
                + "\n"
            )
        return "outbox"


def evaluate_run(stats, source_failures: dict[str, int], policy: AlertPolicy) -> list[tuple[str, str]]:
    """Turn one run's outcome into (level, message) alerts."""
    alerts: list[tuple[str, str]] = []
    if stats.crash:
        alerts.append(("error", f"Lượt thu thập bị dừng giữa chừng: {stats.crash}"))
    for source, count in sorted(source_failures.items()):
        if count >= policy.source_failures:
            alerts.append(("warning", f"Nguồn {source} lỗi {count} lượt liên tiếp."))
    lark_errors = [error for error in stats.errors if error.startswith("lark:")]
    if lark_errors:
        alerts.append(("error", f"Đồng bộ Lark thất bại: {lark_errors[0][5:].strip()[:200]}"))
    attempted = stats.inserted + stats.failed_articles
    if attempted >= 4 and stats.failed_articles / attempted > policy.article_failure_ratio:
        alerts.append(
            ("warning", f"{stats.failed_articles}/{attempted} bài xử lý lỗi trong lượt chạy.")
        )
    return alerts
