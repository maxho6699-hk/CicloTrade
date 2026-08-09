from datetime import datetime

import pytest

from core.compat import UTC
from src.apps.worker.mystic_editorial import (
    EditorialState,
    SocialPlatform,
    SourcePost,
    build_mystic_draft,
    review_mystic_draft,
)


def post(platform, url, text, author="market_writer"):
    return SourcePost(
        platform=platform,
        public_url=url,
        author_handle=author,
        text=text,
        published_at="2026-08-09T13:00:00+00:00",
        collected_at="2026-08-09T13:05:00+00:00",
        public_content=True,
    )


class Collector:
    def __init__(self, posts):
        self.posts = posts

    def collect_public_posts(self, query, limit):
        assert query and limit == 20
        return self.posts


class Summarizer:
    def summarize(self, topic, posts):
        assert topic == "财报跳空讨论"
        assert len(posts) >= 2
        return "财报跳空成为市场热门讨论", "多个公开市场博主正在讨论大型科技股财报后的跳空行为，观点存在明显分歧，内容仅用于娱乐舆情观察。"


def sources():
    return [
        post(SocialPlatform.X, "https://x.com/market_writer/status/1", "Large-cap earnings gaps are drawing attention."),
        post(SocialPlatform.THREADS, "https://www.threads.net/@trader/post/2", "Investors debate whether gaps will fade."),
    ]


def test_pipeline_deduplicates_and_requires_editor_review_before_approval():
    items = sources()
    draft = build_mystic_draft(
        "财报跳空讨论",
        [Collector([items[0], items[0]]), Collector([items[1]])],
        Summarizer(),
        now=datetime(2026, 8, 9, 14, tzinfo=UTC),
    )

    assert draft.state is EditorialState.REVIEW_REQUIRED
    assert len(draft.sources) == 2
    assert not hasattr(draft, "trading_score")

    approved = review_mystic_draft(draft, editor_id="editor-42", approved=True)
    assert approved.state is EditorialState.APPROVED
    assert approved.editor_id == "editor-42"


def test_private_or_unapproved_domains_are_rejected():
    invalid = post(SocialPlatform.X, "https://example.com/private/1", "Private scraped content")

    with pytest.raises(ValueError, match="public HTTPS"):
        build_mystic_draft(
            "财报跳空讨论", [Collector([invalid, sources()[1]])], Summarizer(),
            now=datetime(2026, 8, 9, 14, tzinfo=UTC),
        )


def test_single_source_cannot_be_dressed_up_as_a_trend():
    with pytest.raises(ValueError, match="two independent"):
        build_mystic_draft(
            "财报跳空讨论", [Collector([sources()[0]])], Summarizer(),
            now=datetime(2026, 8, 9, 14, tzinfo=UTC),
        )


def test_review_is_single_use_and_requires_named_editor():
    draft = build_mystic_draft(
        "财报跳空讨论", [Collector(sources())], Summarizer(),
        now=datetime(2026, 8, 9, 14, tzinfo=UTC),
    )
    rejected = review_mystic_draft(draft, editor_id="editor-42", approved=False)

    with pytest.raises(ValueError, match="review-required"):
        review_mystic_draft(rejected, editor_id="editor-42", approved=True)
    with pytest.raises(ValueError, match="editor"):
        review_mystic_draft(draft, editor_id="", approved=True)
