# -*- coding: utf-8 -*-
"""Telegram Bot API 推送。"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from core.compat import UTC
import hashlib
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from core.config_loader import get_config
from core.plans import can, effective_plan
from core.user_settings import load_user_settings, merge_user_settings


class TelegramDeliveryUncertain(RuntimeError):
    """The request may have reached Telegram, so automatic retry is unsafe."""


def _telegram_setting(key: str, default=None):
    return get_config().get(f"telegram.{key}", default)


def telegram_token() -> str:
    return os.getenv(str(_telegram_setting("token_env", "TELEGRAM_BOT_TOKEN")), "").strip()


def telegram_group(group: str) -> str | None:
    value = _telegram_setting(f"groups.{group}")
    target = str(value).strip() if value is not None else ""
    return target if target.startswith("-") and target[1:].isdigit() else None


def telegram_community_url() -> str | None:
    value = str(_telegram_setting("community_url", "")).strip()
    return value or None


def telegram_configured(chat_id: str | None = None) -> bool:
    enabled_env = str(_telegram_setting("enabled_env", "EXTERNAL_ALERTS_ENABLED"))
    return bool(
        os.getenv(enabled_env, "false").strip().lower() == "true"
        and telegram_token()
        and (chat_id or os.getenv("TELEGRAM_CHAT_ID"))
    )


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


_NOTIFY_COMMANDS = {
    "stock": (("stock_signal",), "stock_signal_telegram", "正股量化操作"),
    "option": (("option_signal",), "option_signal_telegram", "期权量化操作"),
    "price": (("price_alert",), "tg_stock_signal", "价格预警"),
    "orders": (("order_submitted", "order_filled", "risk_rejected", "force_liquidation"), "tg_stock_signal", "个人订单与风控"),
    "system": (("system_exception",), "tg_system", "系统异常"),
}


def update_notification_preference(database, chat_id: str, command: str) -> str:
    """Apply a private /notify command to the same settings used by the website."""
    target = str(chat_id).strip()
    if not target.isdigit():
        raise ValueError("请在与 CicloTrade Bot 的私人对话中使用通知设置。")
    parts = str(command or "").strip().lower().split()
    if parts and parts[0].startswith(("/id@", "/start@")):
        parts[0] = parts[0].split("@", 1)[0]
    if parts in (["/id"], ["/start"]):
        return (
            "🔗 CicloTrade · Telegram 绑定\n"
            "━━━━━━━━━━━━━━\n"
            f"你的 Chat ID：{target}\n\n"
            "1. 复制上方数字\n"
            "2. 回到网站「账户与安全 → Telegram」\n"
            "3. 粘贴 Chat ID 并申请验证码\n"
            "4. 把 Bot 发来的验证码粘贴回网站完成绑定"
        )
    row = database.fetch_one(
        """SELECT u.id,u.plan_type,u.subscription_expire,s.settings_json
           FROM users u JOIN user_settings s ON s.user_id=u.id
           WHERE u.is_active=1
             AND CAST(json_extract(s.settings_json,'$.telegram.chat_id') AS TEXT)=?""",
        (target,),
    )
    if not row:
        raise ValueError("此 Telegram 尚未绑定 CicloTrade 账户，请先在网站设置页完成验证。")
    settings = load_user_settings(int(row["id"]), database)
    if verified_user_target(settings) != target:
        raise ValueError("Telegram 绑定尚未完成同意与验证。")
    if parts and parts[0].startswith("/notify@"):
        parts[0] = "/notify"

    def status() -> str:
        events = settings.get("tg_events") if isinstance(settings.get("tg_events"), dict) else {}
        lines = ["⚙️ CicloTrade · 私人通知设置", "━━━━━━━━━━━━━━"]
        for name, (keys, capability, label) in _NOTIFY_COMMANDS.items():
            included = can(effective_plan(row), capability)
            enabled = included and all(events.get(key) is True for key in keys)
            lines.append(f"{label}　{'开启' if enabled else '关闭' if included else '当前会员未开放'}　/notify {name} on|off")
        lines.extend(("━━━━━━━━━━━━━━", "网站设置页与此处实时共用同一份偏好；会员降级后会自动停止越级推送。"))
        return "\n".join(lines)

    if parts in ([], ["/notify"], ["/settings"]):
        return status()
    if len(parts) != 3 or parts[0] != "/notify" or parts[1] not in _NOTIFY_COMMANDS or parts[2] not in {"on", "off"}:
        return "格式：/notify stock|option|price|orders|system on|off\n发送 /notify 查看当前设置。"
    keys, capability, label = _NOTIFY_COMMANDS[parts[1]]
    if not can(effective_plan(row), capability):
        return f"{label}不在当前会员等级内，设置未改变。发送 /notify 查看可用项目。"
    events = dict(settings.get("tg_events") or {})
    for key in keys:
        events[key] = parts[2] == "on"
    settings = merge_user_settings(int(row["id"]), {"tg_events": events}, database)
    state = "已开启" if parts[2] == "on" else "已关闭"
    return f"✅ {label}{state}。\n\n" + status()


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
    enabled_env = str(_telegram_setting("enabled_env", "EXTERNAL_ALERTS_ENABLED"))
    if os.getenv(enabled_env, "false").strip().lower() != "true":
        raise RuntimeError("Telegram 外部通知已由平台停用。")
    token = telegram_token()
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


def remove_group_member(chat_id: str, user_id: str) -> None:
    """Remove an expired member while allowing a later paid rejoin."""
    if not telegram_configured(chat_id):
        raise RuntimeError("Telegram 外部通知已由平台停用。")
    token = telegram_token()
    target_user = str(user_id).strip()
    if not target_user.isdigit():
        raise RuntimeError("Telegram 用户 ID 无效。")
    for method, payload in (
        ("banChatMember", {"chat_id": chat_id, "user_id": target_user}),
        ("unbanChatMember", {"chat_id": chat_id, "user_id": target_user, "only_if_banned": True}),
    ):
        request = Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=json.dumps(payload).encode(),
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
            raise TelegramDeliveryUncertain(
                f"Telegram membership update uncertain: {type(exc).__name__}"
            ) from exc
