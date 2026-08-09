"""Audited X/Threads editorial pipeline isolated from trading decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
from typing import Protocol, Sequence
from urllib.parse import urlparse

from core.compat import UTC
from src.apps.worker._compat import StrEnum


class SocialPlatform(StrEnum):
    X = "x"
    THREADS = "threads"


class EditorialState(StrEnum):
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SourcePost:
    platform: SocialPlatform
    public_url: str
    author_handle: str
    text: str
    published_at: str
    collected_at: str
    public_content: bool


@dataclass(frozen=True)
class MysticDraft:
    draft_id: str
    topic: str
    headline: str
    summary: str
    sources: tuple[SourcePost, ...]
    source_hash: str
    state: EditorialState
    created_at: str
    editor_id: str | None = None
    reviewed_at: str | None = None


class SocialCollector(Protocol):
    def collect_public_posts(self, query: str, limit: int) -> Sequence[SourcePost]: ...


class EditorialSummarizer(Protocol):
    def summarize(self, topic: str, posts: Sequence[SourcePost]) -> tuple[str, str]: ...


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _validate_source(post: SourcePost, now: datetime) -> None:
    host = (urlparse(post.public_url).hostname or "").lower()
    allowed = {
        SocialPlatform.X: {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
        SocialPlatform.THREADS: {"threads.net", "www.threads.net"},
    }[post.platform]
    if not post.public_content or host not in allowed or urlparse(post.public_url).scheme != "https":
        raise ValueError("mystic sources must be public HTTPS X or Threads posts")
    if not 1 <= len(post.author_handle.strip()) <= 100:
        raise ValueError("source author is invalid")
    if not 1 <= len(post.text.strip()) <= 10_000:
        raise ValueError("source text is empty or too long")
    published, collected = _time(post.published_at), _time(post.collected_at)
    if published > collected or collected > now:
        raise ValueError("source timestamps are invalid")


def build_mystic_draft(
    topic: str,
    collectors: Sequence[SocialCollector],
    summarizer: EditorialSummarizer,
    *,
    limit_per_source: int = 20,
    now: datetime | None = None,
) -> MysticDraft:
    topic = topic.strip()
    if not 2 <= len(topic) <= 120:
        raise ValueError("mystic topic must be between 2 and 120 characters")
    if not collectors or not 1 <= limit_per_source <= 100:
        raise ValueError("at least one bounded collector is required")
    current = now or datetime.now(UTC)
    posts: list[SourcePost] = []
    seen_urls: set[str] = set()
    seen_content: set[str] = set()
    for collector in collectors:
        for post in collector.collect_public_posts(topic, limit_per_source):
            _validate_source(post, current)
            content_hash = hashlib.sha256(post.text.strip().encode()).hexdigest()
            if post.public_url in seen_urls or content_hash in seen_content:
                continue
            seen_urls.add(post.public_url)
            seen_content.add(content_hash)
            posts.append(post)
    if len(posts) < 2:
        raise ValueError("mystic drafts require at least two independent public sources")
    headline, summary = summarizer.summarize(topic, posts)
    headline, summary = headline.strip(), summary.strip()
    if not 5 <= len(headline) <= 120 or not 20 <= len(summary) <= 1_500:
        raise ValueError("editorial summary length is invalid")
    canonical = "\n".join(sorted(f"{post.public_url}|{post.published_at}" for post in posts))
    source_hash = hashlib.sha256(canonical.encode()).hexdigest()
    created_at = current.isoformat(timespec="seconds")
    draft_id = hashlib.sha256(f"{topic}|{source_hash}|{created_at}".encode()).hexdigest()[:24]
    return MysticDraft(
        draft_id=draft_id,
        topic=topic,
        headline=headline,
        summary=summary,
        sources=tuple(posts),
        source_hash=source_hash,
        state=EditorialState.REVIEW_REQUIRED,
        created_at=created_at,
    )


def review_mystic_draft(
    draft: MysticDraft,
    *,
    editor_id: str,
    approved: bool,
    now: datetime | None = None,
) -> MysticDraft:
    if draft.state is not EditorialState.REVIEW_REQUIRED:
        raise ValueError("only review-required drafts may be reviewed")
    if not 3 <= len(editor_id.strip()) <= 100:
        raise ValueError("editor identity is required")
    return replace(
        draft,
        state=EditorialState.APPROVED if approved else EditorialState.REJECTED,
        editor_id=editor_id,
        reviewed_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
    )
