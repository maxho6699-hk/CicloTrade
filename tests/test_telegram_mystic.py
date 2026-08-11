from datetime import datetime
from core.compat import UTC

import pytest

from notification.telegram_mystic import authorized_mystic_source_ids, normalize_mystic_update


def _update(*, chat_id=-100123, message_id=42, edited=False):
    key = "edited_channel_post" if edited else "channel_post"
    posted = int(datetime(2026, 8, 11, 1, 18, tzinfo=UTC).timestamp())
    message = {
        "message_id": message_id,
        "date": posted,
        "chat": {"id": chat_id, "type": "channel", "title": "CicloTrade Mystic"},
        "text": "开盘前的市场气氛\n仅作娱乐记录。",
        "media_group_id": "album-7",
        "photo": [
            {"file_id": "small-photo", "file_unique_id": "photo-1", "file_size": 10},
            {"file_id": "large-photo", "file_unique_id": "photo-1", "file_size": 100},
        ],
    }
    if edited:
        message["edit_date"] = posted + 60
    return {"update_id": 99, key: message}


def test_mystic_normalizer_preserves_original_order_text_and_media_references():
    post = normalize_mystic_update(_update(), {"-100123"})

    assert post.source_chat_id == "-100123"
    assert post.source_message_id == 42 and post.source_order == 42
    assert post.post_date == "2026-08-11"
    assert post.body.startswith("开盘前")
    assert post.media[0].file_id == "large-photo"
    assert post.media_group_id == "album-7"
    payload = post.as_storage_payload()
    assert payload["dedupe_key"] == "-100123:42"
    assert "liked" not in payload and "likes" not in payload


def test_mystic_normalizer_rejects_unauthorized_or_empty_sources():
    with pytest.raises(PermissionError, match="未获管理员授权"):
        normalize_mystic_update(_update(), {"-100999"})
    empty = _update()
    empty["channel_post"].pop("text")
    empty["channel_post"].pop("photo")
    with pytest.raises(ValueError, match="没有正文或媒体"):
        normalize_mystic_update(empty, {"-100123"})


def test_mystic_edits_keep_same_dedupe_key_but_change_payload_hash():
    original = normalize_mystic_update(_update(), {"-100123"})
    edited_update = _update(edited=True)
    edited_update["edited_channel_post"]["text"] += "\n编辑补充。"
    edited = normalize_mystic_update(edited_update, {"-100123"})

    assert edited.dedupe_key == original.dedupe_key
    assert edited.edited_at and edited.payload_hash != original.payload_hash


def test_authorized_source_configuration_accepts_only_exact_numeric_chat_ids():
    assert authorized_mystic_source_ids("-100123,456") == frozenset({"-100123", "456"})
    with pytest.raises(ValueError):
        authorized_mystic_source_ids("@channel")
