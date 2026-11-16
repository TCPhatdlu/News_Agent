"""Publishing: queue -> (approval) -> send -> verify post_id.

Channels and the env keys they need (all optional):
  telegram  PUBLISH_TELEGRAM_BOT_TOKEN, PUBLISH_TELEGRAM_CHAT_ID       (Bot API sendMessage)
  lark      LARK_APP_ID, LARK_APP_SECRET, PUBLISH_LARK_CHAT_ID        (IM v1 messages)
  facebook  PUBLISH_FACEBOOK_PAGE_ID, PUBLISH_FACEBOOK_PAGE_TOKEN     (Graph API /{page}/feed)
  twitter   PUBLISH_X_USER_TOKEN                                      (X API v2 POST /2/tweets)

A channel without credentials, or any channel while DRY_RUN is on, runs in dry-run: the post is
written to data/outbox/publish-*.jsonl and gets a "dry-..." id, nothing leaves the machine.

Approval: channels listed in PUBLISH_REQUIRE_APPROVAL (default facebook,twitter) start as
"pending" and are only sent after a human approves them in the web UI or CLI.
A post counts as published only when the platform returned a post/message id.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import get_env, get_env_list
from .models import utc_now_iso

CHANNELS = ("telegram", "lark", "facebook", "twitter")
CHANNEL_LABELS = {"telegram": "Telegram", "lark": "Lark", "facebook": "Facebook Page", "twitter": "X (Twitter)"}
TWEET_LIMIT = 280
TCO_LENGTH = 23  # X counts every link as 23 characters


class PublishError(RuntimeError):
    pass


def compose(channel: str, article: dict) -> str:
    title = (article.get("title") or "").strip()
    summary = (article.get("summary") or "").strip()
    url = article.get("url") or ""
    if channel == "twitter":
        budget = TWEET_LIMIT - TCO_LENGTH - 1
        text = title if len(title) <= budget else title[: budget - 1].rstrip() + "…"
        return f"{text} {url}"
    if channel == "facebook":
        return f"{title}\n\n{summary}"  # the link travels separately so Facebook renders a preview
    return f"📰 {title}\n\n{summary}\n\n{url}"


def validate(channel: str, text: str) -> str | None:
    if not text.strip():
        return "Nội dung trống."
    if channel == "twitter":
        length = len(text)
        for token in text.split():
            if token.startswith(("http://", "https://")):
                length += TCO_LENGTH - len(token)
        if length > TWEET_LIMIT:
            return f"Quá {TWEET_LIMIT} ký tự ({length})."
    if channel == "telegram" and len(text) > 4096:
        return "Telegram giới hạn 4096 ký tự."
    return None


class Channel:
    name = "base"

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return False

    def send(self, text: str, article: dict) -> str:
        raise NotImplementedError


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str | None, chat_id: str | None, **kw) -> None:
        super().__init__(**kw)
        self.token, self.chat_id = token, chat_id

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, article: dict) -> str:
        response = httpx.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text},
            timeout=self.timeout,
        )
        payload = response.json()
        message_id = (payload.get("result") or {}).get("message_id")
        if not payload.get("ok") or not message_id:
            raise PublishError(f"Telegram: {payload.get('description') or response.status_code}")
        return str(message_id)


class LarkChannel(Channel):
    name = "lark"

    def __init__(self, app_id, app_secret, chat_id, base_url="https://open.larksuite.com", **kw) -> None:
        super().__init__(**kw)
        self.app_id, self.app_secret, self.chat_id = app_id, app_secret, chat_id
        self.base_url = base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret and self.chat_id)

    def send(self, text: str, article: dict) -> str:
        auth = httpx.post(
            f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.timeout,
        ).json()
        if auth.get("code") != 0:
            raise PublishError(f"Lark auth: {auth.get('msg')}")
        payload = httpx.post(
            f"{self.base_url}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {auth['tenant_access_token']}"},
            json={"receive_id": self.chat_id, "msg_type": "text", "content": json.dumps({"text": text})},
            timeout=self.timeout,
        ).json()
        message_id = (payload.get("data") or {}).get("message_id")
        if payload.get("code") != 0 or not message_id:
            raise PublishError(f"Lark: {payload.get('msg')}")
        return str(message_id)


class FacebookChannel(Channel):
    name = "facebook"
    GRAPH = "https://graph.facebook.com/v19.0"

    def __init__(self, page_id: str | None, page_token: str | None, **kw) -> None:
        super().__init__(**kw)
        self.page_id, self.page_token = page_id, page_token

    @property
    def configured(self) -> bool:
        return bool(self.page_id and self.page_token)

    def send(self, text: str, article: dict) -> str:
        payload = httpx.post(
            f"{self.GRAPH}/{self.page_id}/feed",
            data={"message": text, "link": article.get("url", ""), "access_token": self.page_token},
            timeout=self.timeout,
        ).json()
        if not payload.get("id"):
            raise PublishError(f"Facebook: {(payload.get('error') or {}).get('message', 'no post id')}")
        return str(payload["id"])


class TwitterChannel(Channel):
    name = "twitter"

    def __init__(self, user_token: str | None, **kw) -> None:
        super().__init__(**kw)
        self.user_token = user_token

    @property
    def configured(self) -> bool:
        return bool(self.user_token)

    def send(self, text: str, article: dict) -> str:
        payload = httpx.post(
            "https://api.x.com/2/tweets",
            headers={"Authorization": f"Bearer {self.user_token}"},
            json={"text": text},
            timeout=self.timeout,
        ).json()
        tweet_id = (payload.get("data") or {}).get("id")
        if not tweet_id:
            detail = payload.get("detail") or payload.get("title") or "no tweet id"
            raise PublishError(f"X: {detail}")
        return str(tweet_id)


def build_channels(settings) -> dict[str, Channel]:
    return {
        "telegram": TelegramChannel(
            get_env("PUBLISH_TELEGRAM_BOT_TOKEN") or None, get_env("PUBLISH_TELEGRAM_CHAT_ID") or None
        ),
        "lark": LarkChannel(
            settings.lark_app_id, settings.lark_app_secret,
            get_env("PUBLISH_LARK_CHAT_ID") or None, settings.lark_base_url,
        ),
        "facebook": FacebookChannel(
            get_env("PUBLISH_FACEBOOK_PAGE_ID") or None, get_env("PUBLISH_FACEBOOK_PAGE_TOKEN") or None
        ),
        "twitter": TwitterChannel(get_env("PUBLISH_X_USER_TOKEN") or None),
    }


class Publisher:
    def __init__(
        self,
        store,
        channels: dict[str, Channel],
        *,
        outbox_dir: Path | None,
        dry_run: bool,
        require_approval: list[str] | None = None,
    ) -> None:
        self.store = store
        self.channels = channels
        self.outbox_dir = outbox_dir
        self.dry_run = dry_run
        self.require_approval = set(
            require_approval if require_approval is not None else ["facebook", "twitter"]
        )

    @classmethod
    def from_settings(cls, store, settings) -> "Publisher":
        return cls(
            store,
            build_channels(settings),
            outbox_dir=settings.outbox_dir,
            dry_run=settings.dry_run,
            require_approval=get_env_list("PUBLISH_REQUIRE_APPROVAL", "facebook,twitter"),
        )

    def is_live(self, channel: str) -> bool:
        target = self.channels.get(channel)
        return bool(target and target.configured and not self.dry_run)

    def queue(self, url: str, channels: list[str]) -> list[int]:
        article = self.store.get_article(url)
        if article is None:
            raise ValueError("unknown article")
        created = []
        for channel in channels:
            if channel not in CHANNELS:
                raise ValueError(f"unknown channel: {channel}")
            status = "pending" if channel in self.require_approval else "approved"
            post_id = self.store.queue_post(url, channel, compose(channel, article), status, utc_now_iso())
            if post_id is not None:
                created.append(post_id)
        return created

    def approve(self, post_id: int, text: str | None = None) -> None:
        post = self._editable(post_id)
        new_text = text if text is not None else post["text"]
        problem = validate(post["channel"], new_text)
        if problem:
            raise ValueError(problem)
        self.store.update_post(post_id, text=new_text, status="approved", decided_at=utc_now_iso(), error=None)

    def reject(self, post_id: int) -> None:
        self._editable(post_id)
        self.store.update_post(post_id, status="rejected", decided_at=utc_now_iso())

    def edit(self, post_id: int, text: str) -> None:
        post = self._editable(post_id)
        problem = validate(post["channel"], text)
        if problem:
            raise ValueError(problem)
        self.store.update_post(post_id, text=text)

    def _editable(self, post_id: int) -> dict:
        post = self.store.get_post(post_id)
        if post is None:
            raise ValueError("post not found")
        if post["status"] not in {"pending", "approved", "failed"}:
            raise ValueError(f"post is already {post['status']}")
        return post

    def send(self, post_id: int) -> dict:
        post = self.store.get_post(post_id)
        if post is None:
            raise ValueError("post not found")
        # the approval gate: "failed" is only reachable from "approved", so a retry is allowed
        if post["status"] not in {"approved", "failed"}:
            raise ValueError("only approved posts can be sent")
        article = self.store.get_article(post["article_url"]) or {"url": post["article_url"]}
        channel = self.channels.get(post["channel"])
        try:
            if self.is_live(post["channel"]):
                remote_id, dry = channel.send(post["text"], article), 0
            else:
                remote_id, dry = self._write_outbox(post), 1
        except (PublishError, httpx.HTTPError, ValueError) as exc:
            self.store.update_post(post_id, status="failed", error=str(exc)[:300])
            return {"ok": False, "error": str(exc)}
        self.store.update_post(
            post_id, status="published", post_id=remote_id, dry_run=dry,
            published_at=utc_now_iso(), error=None,
        )
        return {"ok": True, "post_id": remote_id, "dry_run": bool(dry)}

    def send_approved(self) -> list[dict]:
        return [self.send(post["id"]) for post in self.store.list_posts("approved")]

    def _write_outbox(self, post: dict) -> str:
        remote_id = f"dry-{uuid.uuid4().hex[:10]}"
        if self.outbox_dir is not None:
            self.outbox_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            with (self.outbox_dir / f"publish-{day}.jsonl").open("a", encoding="utf-8") as handle:
                record = {
                    "post_id": remote_id, "channel": post["channel"], "text": post["text"],
                    "article_url": post["article_url"], "at": utc_now_iso(),
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return remote_id
