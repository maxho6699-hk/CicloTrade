# -*- coding: utf-8 -*-
"""Telegram Bot API 推送。"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from core.plans import can, effective_plan


class TelegramDeliveryUncertain(RuntimeError):
    """The request may have reached Telegram, so automatic retry is unsafe."""


def telegram_configured(chat_id: str | None = None) -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and (chat_id or os.getenv("TELEGRAM_CHAT_ID")))


def verified_user_target(settings: dict[str, Any], event: str | None = None) -> str | None:
    """Return a user-owned destination only after explicit consent and verification."""
    channel = settings.get("telegram")
    events = settings.get("tg_events")
    if (
        not isinstance(channel, dict)
        or channel.get("consent") is not True
        or channel.get("verified") is not True
        or (event is not None and (not isinstance(events, dict) or events.get(event) is not True))
    ):
        return None
    chat_id = channel.get("chat_id")
    if isinstance(chat_id, bool) or not isinstance(chat_id, (int, str)):
        return None
    target = str(chat_id).strip()
    return target if target.isdigit() and 1 <= len(target) <= 20 else None


def entitled_user_target(user: dict[str, Any], settings: dict[str, Any], event: str | None = None) -> str | None:
    """Apply plan entitlement before consent/verification checks."""
    event = event or "stock_signal"
    option_event = event in {"option_signal", "option_order", "option_alert"}
    system_event = event in {"system_exception", "membership_update"}
    capability = "tg_option_signal" if option_event else "tg_system" if system_event else "tg_stock_signal"
    required_event = None if event == "membership_update" else event
    return verified_user_target(settings, required_event) if can(effective_plan(user), capability) else None


def issue_verification_token(database, user_id: int, chat_id: str, consent: bool, ttl_minutes: int = 15) -> str:
    """Create a short-lived one-time Telegram binding challenge."""
    chat_id = str(chat_id).strip()
    if consent is not True:
        raise ValueError("请先同意 Telegram 通知。")
    if not chat_id.isdigit() or not 1 <= len(chat_id) <= 20:
        raise ValueError("Telegram Chat ID 必须是数字。")
    if not 1 <= int(ttl_minutes) <= 60:
        raise ValueError("验证码有效期必须在 1 至 60 分钟之间。")
    token = secrets.token_urlsafe(18)
    now = datetime.now(UTC)
    with database.transaction() as conn:
        user = conn.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (int(user_id),)).fetchone()
        if not user:
            raise ValueError("用户不存在或已停用。")
        conn.execute(
            "UPDATE telegram_verifications SET verified_at=? WHERE user_id=? AND verified_at IS NULL",
            (now.isoformat(timespec="seconds"), int(user_id)),
        )
        conn.execute(
            "INSERT INTO telegram_verifications (user_id,chat_id,token_hash,expires_at,consent,created_at) VALUES (?,?,?,?,1,?)",
            (int(user_id), chat_id, hashlib.sha256(token.encode()).hexdigest(), (now + timedelta(minutes=int(ttl_minutes))).isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )
    return token


def confirm_verification(database, user_id: int, token: str) -> str:
    digest = hashlib.sha256(str(token).strip().encode()).hexdigest()
    with database.transaction() as conn:
        row = conn.execute(
            """SELECT * FROM telegram_verifications
               WHERE user_id=? AND token_hash=? AND verified_at IS NULL AND consent=1
               ORDER BY id DESC LIMIT 1""",
            (int(user_id), digest),
        ).fetchone()
        if not row:
            raise ValueError("验证码无效或已使用。")
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("验证码已失效，请重新申请。") from exc
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise ValueError("验证码已过期，请重新申请。")
        claimed = conn.execute(
            "UPDATE telegram_verifications SET verified_at=? WHERE id=? AND verified_at IS NULL",
            (datetime.now(UTC).isoformat(timespec="seconds"), row["id"]),
        )
        if claimed.rowcount != 1:
            raise ValueError("验证码无效或已使用。")
        return str(row["chat_id"])


def send_telegram(message: str, chat_id: str | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    target = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not target:
        raise RuntimeError("Telegram Bot 尚未配置。")
    data = json.dumps({"chat_id": target, "text": message, "disable_web_page_preview": True}).encode()
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"Telegram HTTP {response.status}")
    except HTTPError as exc:
        raise RuntimeError(f"Telegram HTTP {exc.code}") from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise TelegramDeliveryUncertain(f"Telegram delivery uncertain: {type(exc).__name__}") from exc
