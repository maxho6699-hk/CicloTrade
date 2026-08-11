# -*- coding: utf-8 -*-
"""Normalize administrator-authorized Telegram mystic posts for persistence.

This module deliberately has no database or website dependency. The Telegram
adapter can hand its storage payload to the shared API once the approved
tables exist. Likes are website-user data and are never synthesized here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from core.compat import UTC
import hashlib
import json
import os
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


_MAX_POST_TEXT = 20_000
_MEDIA_FIELDS = ("animation", "audio", "document", "video", "voice", "video_note", "sticker")


def _chat_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Telegram 玄学来源 Chat ID 无效。")
    cleaned = str(value).strip()
    if not cleaned or cleaned == "-" or not cleaned.lstrip("-").isdigit() or len(cleaned) > 24:
        raise ValueError("Telegram 玄学来源 Chat ID 无效。")
    return cleaned


def authorized_mystic_source_ids(value: str | None = None) -> frozenset[str]:
    """Read an exact allow-list; wildcard and usernames are intentionally unsupported."""
    raw = os.getenv("TELEGRAM_MYSTIC_SOURCE_CHAT_IDS", "") if value is None else value
    sources = set()
    for item in str(raw or "").split(","):
        if item.strip():
            sources.add(_chat_id(item))
    return frozenset(sources)


@dataclass(frozen=True)
class TelegramMediaReference:
    media_type: str
    file_id: str
    file_unique_id: str | None
    file_name: str | None
    mime_type: str | None


@dataclass(frozen=True)
class TelegramMysticPost:
    source_chat_id: str
    source_chat_title: str | None
    source_message_id: int
    source_order: int
    media_group_id: str | None
    posted_at: str
    edited_at: str | None
    post_date: str
    body: str
    media: tuple[TelegramMediaReference, ...]
    payload_hash: str

    @property
    def dedupe_key(self) -> str:
        return f"{self.source_chat_id}:{self.source_message_id}"

    def as_storage_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["media"] = [asdict(item) for item in self.media]
        payload["dedupe_key"] = self.dedupe_key
        return payload


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, bool):
        raise ValueError(f"Telegram {field} 无效。")
    try:
        timestamp = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Telegram {field} 无效。") from exc
    if timestamp <= 0:
        raise ValueError(f"Telegram {field} 无效。")
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"Telegram {field} 无效。") from exc


def _media_reference(media_type: str, value: object) -> TelegramMediaReference | None:
    if not isinstance(value, Mapping):
        return None
    file_id = str(value.get("file_id") or "").strip()
    if not file_id or len(file_id) > 512:
        return None
    unique = str(value.get("file_unique_id") or "").strip() or None
    file_name = str(value.get("file_name") or "").strip() or None
    mime_type = str(value.get("mime_type") or "").strip() or None
    return TelegramMediaReference(media_type, file_id, unique, file_name, mime_type)


def _media(message: Mapping[str, Any]) -> tuple[TelegramMediaReference, ...]:
    values: list[TelegramMediaReference] = []
    photos = message.get("photo")
    if isinstance(photos, list):
        candidates = [item for item in photos if isinstance(item, Mapping)]
        if candidates:
            selected = max(
                candidates,
                key=lambda item: int(item.get("file_size") or 0) if str(item.get("file_size") or "0").isdigit() else 0,
            )
            if reference := _media_reference("photo", selected):
                values.append(reference)
    for field in _MEDIA_FIELDS:
        if reference := _media_reference(field, message.get(field)):
            values.append(reference)
    return tuple(values)


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_mystic_update(
    update: Mapping[str, Any],
    authorized_chat_ids: Iterable[str | int],
) -> TelegramMysticPost:
    """Validate one Telegram update and preserve its source ordering and media IDs."""
    if not isinstance(update, Mapping):
        raise ValueError("Telegram update 必须是对象。")
    message = next(
        (
            value
            for key in ("channel_post", "edited_channel_post", "message", "edited_message")
            if isinstance((value := update.get(key)), Mapping)
        ),
        None,
    )
    if message is None:
        raise ValueError("Telegram update 没有可采集贴文。")
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        raise ValueError("Telegram 贴文缺少来源。")
    source_chat_id = _chat_id(chat.get("id"))
    allowed = {_chat_id(value) for value in authorized_chat_ids}
    if source_chat_id not in allowed:
        raise PermissionError("Telegram 玄学来源未获管理员授权。")

    message_id = message.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        raise ValueError("Telegram 贴文消息编号无效。")
    posted = _timestamp(message.get("date"), "原帖时间")
    edited = _timestamp(message.get("edit_date"), "编辑时间") if message.get("edit_date") is not None else None
    body = str(message.get("text") or message.get("caption") or "").strip()
    if len(body) > _MAX_POST_TEXT:
        raise ValueError("Telegram 玄学贴文正文过长。")
    media = _media(message)
    if not body and not media:
        raise ValueError("Telegram 玄学贴文没有正文或媒体。")
    title = str(chat.get("title") or chat.get("username") or "").strip() or None
    media_group_id = str(message.get("media_group_id") or "").strip() or None
    hash_input = {
        "source_chat_id": source_chat_id,
        "source_message_id": message_id,
        "media_group_id": media_group_id,
        "posted_at": posted.isoformat(timespec="seconds"),
        "edited_at": edited.isoformat(timespec="seconds") if edited else None,
        "body": body,
        "media": [asdict(item) for item in media],
    }
    return TelegramMysticPost(
        source_chat_id=source_chat_id,
        source_chat_title=title,
        source_message_id=message_id,
        source_order=message_id,
        media_group_id=media_group_id,
        posted_at=posted.isoformat(timespec="seconds"),
        edited_at=edited.isoformat(timespec="seconds") if edited else None,
        post_date=posted.astimezone(ZoneInfo("Asia/Hong_Kong")).date().isoformat(),
        body=body,
        media=media,
        payload_hash=_payload_hash(hash_input),
    )
