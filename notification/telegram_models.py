# -*- coding: utf-8 -*-
"""Typed Telegram service-desk responses."""

from __future__ import annotations

from dataclasses import dataclass, field

from notification.telegram_bot import TelegramKeyboard


@dataclass(frozen=True)
class TelegramOutbound:
    chat_id: str
    message: str
    buttons: TelegramKeyboard | None = None
    copy_from_chat_id: str | None = None
    copy_message_id: int | None = None


@dataclass(frozen=True)
class TelegramDeskResponse:
    message: str
    keyboard: TelegramKeyboard
    followups: tuple[TelegramOutbound, ...] = field(default_factory=tuple)
