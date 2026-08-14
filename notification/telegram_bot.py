# -*- coding: utf-8 -*-
"""Telegram Bot API 推送。"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from core.compat import UTC
import hashlib
from html import escape
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from core.config_loader import get_config
from core.membership import authoritative_membership_user
from core.plans import can, effective_plan
from notification.entitlement_adapter import policy_allows
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
    # Delivery call sites do not own a database handle yet; interactive Bot
    # capability gates below use the published-policy adapter.
    return verified_user_target(settings, required_event) if can(effective_plan(user), capability) else None


_NOTIFY_COMMANDS = {
    "stock": (("stock_signal",), "stock_signal_telegram", "正股建議"),
    "option": (("option_signal",), "option_signal_telegram", "期權建議"),
    "price": (("price_alert",), "tg_stock_signal", "價格預警"),
    "orders": (("order_submitted", "order_filled", "risk_rejected", "force_liquidation"), "tg_stock_signal", "個人訂單與風控"),
    "system": (("system_exception",), "tg_system", "系統異常"),
}

TelegramKeyboard = list[list[dict[str, str]]]
_MAX_TELEGRAM_MESSAGE_LENGTH = 4096
_MAX_TELEGRAM_BUTTONS = 100
_MAX_TELEGRAM_BUTTON_ROWS = 20
_MAX_TELEGRAM_BUTTON_TEXT_LENGTH = 64
_MAX_TELEGRAM_URL_LENGTH = 512
_MAX_TELEGRAM_CALLBACK_LENGTH = 64
_CALLBACK_DATA = {"menu:home", "menu:settings"} | {
    f"notify:{name}:toggle" for name in _NOTIFY_COMMANDS
}
_CALLBACK_PATTERNS = (
    re.compile(
        r"^desk:(?:home|queries|pnl|membership|timeline|actions|portfolio|market|plans|orders|settings|help|account|pause_opening|receiving)$"
    ),
    re.compile(r"^timeline:(?:choose|custom):(?:stock|option)$"),
    re.compile(r"^timeline:show:(?:stock|option):[1-9][0-9]{0,2}:(?:0|[1-9][0-9]{0,2})$"),
    re.compile(r"^timeline:pnl:(?:today|yesterday|7d):(?:0|[1-9][0-9]{0,2})$"),
    re.compile(r"^buy:plan:(?:standard|advanced)$"),
    re.compile(
        r"^buy:cycle:(?:standard|advanced):(?:monthly|quarterly|yearly)$"
    ),
    re.compile(
        r"^buy:(?:method|create):(?:standard|advanced):"
        r"(?:monthly|quarterly|yearly):(?:fps|alipay|wechat)$"
    ),
    re.compile(r"^pay:claimed:TA[0-9A-F]{12,40}$"),
    re.compile(r"^admin:(?:approve|reject):[1-9][0-9]{0,9}:[1-9][0-9]{0,3}$"),
    re.compile(r"^paycfg:(?:home|cancel)$"),
    re.compile(r"^paycfg:(?:show|settext|setqr|cleartext|clearqr):(?:fps|alipay|wechat)$"),
)


def _telegram_chat_id(value: object, *, private: bool = False) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Telegram Chat ID 无效。")
    target = str(value).strip()
    valid = target.isdigit() if private else (target.isdigit() or (target.startswith("-") and target[1:].isdigit()))
    numeric = target.lstrip("-")
    if not valid or not 1 <= len(numeric) <= 20 or int(numeric) <= 0:
        raise ValueError("Telegram Chat ID 无效。")
    return target


def _telegram_text(value: object, *, limit: int = _MAX_TELEGRAM_MESSAGE_LENGTH) -> str:
    if not isinstance(value, str):
        raise ValueError("Telegram 訊息必須是文字。")
    telegram_units = len(value.encode("utf-16-le")) // 2
    if not value.strip() or telegram_units > limit:
        raise ValueError("Telegram 訊息長度無效。")
    return value


def _telegram_https_url(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_TELEGRAM_URL_LENGTH
        or "\\" in value
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise ValueError("Telegram 按钮 URL 无效。")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Telegram 按钮 URL 无效。") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or port == 0:
        raise ValueError("Telegram 按钮必须使用有效的 HTTPS URL。")
    return value


def _telegram_callback_data(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not 1 <= len(value) <= _MAX_TELEGRAM_CALLBACK_LENGTH
        or (
            value not in _CALLBACK_DATA
            and not any(pattern.fullmatch(value) for pattern in _CALLBACK_PATTERNS)
        )
    ):
        raise ValueError("Telegram callback_data 无效。")
    return value


def telegram_callback_allowed(value: object) -> bool:
    """Accept only the Bot's fixed actions and tightly validated identifiers."""
    try:
        _telegram_callback_data(value)
    except ValueError:
        return False
    return True


def _telegram_keyboard(buttons: TelegramKeyboard | None) -> TelegramKeyboard | None:
    if buttons is None:
        return None
    if not isinstance(buttons, list) or not buttons or len(buttons) > _MAX_TELEGRAM_BUTTON_ROWS:
        raise ValueError("Telegram 按钮列数无效。")
    normalized: TelegramKeyboard = []
    count = 0
    for row in buttons:
        if not isinstance(row, list) or not row or len(row) > 8:
            raise ValueError("Telegram 按钮行无效。")
        normalized_row: list[dict[str, str]] = []
        for item in row:
            if not isinstance(item, dict):
                raise ValueError("Telegram 按钮无效。")
            label = item.get("text")
            if not isinstance(label, str) or not label.strip() or len(label) > _MAX_TELEGRAM_BUTTON_TEXT_LENGTH:
                raise ValueError("Telegram 按钮文字无效。")
            url, callback_data = item.get("url"), item.get("callback_data")
            if (url is None) == (callback_data is None):
                raise ValueError("Telegram 按钮必须指定一个操作。")
            normalized_row.append(
                {"text": label, "url": _telegram_https_url(url)}
                if url is not None
                else {"text": label, "callback_data": _telegram_callback_data(callback_data)}
            )
            count += 1
        normalized.append(normalized_row)
    if count > _MAX_TELEGRAM_BUTTONS:
        raise ValueError("Telegram 按钮数量过多。")
    return normalized


def _app_url(path: str) -> str:
    base = os.getenv("APP_BASE_URL", "https://ciclotrade.com").strip().rstrip("/")
    if not base.startswith("https://"):
        base = "https://ciclotrade.com"
    return f"{base}/{path.strip('/')}"


def telegram_main_keyboard() -> TelegramKeyboard:
    return [
        [{"text": "🔎 查詢中心", "callback_data": "desk:queries"}],
        [{"text": "🔔 我的通知", "callback_data": "desk:settings"}],
        [{"text": "💎 會員與訂單", "callback_data": "desk:membership"}],
        [{"text": "🔗 帳戶", "callback_data": "desk:account"}],
        [{"text": "❓ 幫助與客服", "callback_data": "desk:help"}],
    ]


def verified_account_for_chat(database, chat_id: str) -> dict[str, Any] | None:
    """Resolve an authoritative binding and lazily migrate one legacy JSON binding."""
    target = str(chat_id).strip()
    row = database.fetch_one(
        """SELECT u.id,u.email,u.display_name,u.plan_type,u.subscription_expire,u.is_admin
           FROM telegram_accounts t
           JOIN users u ON u.id=t.user_id
           WHERE u.is_active=1 AND t.is_active=1 AND t.revoked_at IS NULL
             AND t.chat_id=?""",
        (target,),
    )
    if row:
        return authoritative_membership_user(database, row)
    legacy = database.fetch_all(
        """SELECT u.id,u.email,u.display_name,u.plan_type,u.subscription_expire,u.is_admin
           FROM users u JOIN user_settings s ON s.user_id=u.id
           WHERE u.is_active=1
             AND json_extract(s.settings_json,'$.telegram.verified')=1
             AND json_extract(s.settings_json,'$.telegram.consent')=1
             AND CAST(json_extract(s.settings_json,'$.telegram.chat_id') AS TEXT)=?""",
        (target,),
    )
    if len(legacy) != 1:
        return None
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        database.execute(
            """INSERT OR IGNORE INTO telegram_accounts
               (user_id,chat_id,is_active,revoked_at,created_at,updated_at)
               VALUES (?,?,1,NULL,?,?)""",
            (int(legacy[0]["id"]), target, now, now),
        )
    except Exception:
        pass
    row = database.fetch_one(
        """SELECT u.id,u.email,u.display_name,u.plan_type,u.subscription_expire,u.is_admin
           FROM telegram_accounts t
           JOIN users u ON u.id=t.user_id
           WHERE u.is_active=1 AND t.is_active=1 AND t.revoked_at IS NULL
             AND t.chat_id=? AND t.user_id=?""",
        (target, int(legacy[0]["id"])),
    )
    return authoritative_membership_user(database, row) if row else None


def _notification_account(database, target: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    row = verified_account_for_chat(database, target)
    if not row:
        return None, {}
    settings = load_user_settings(int(row["id"]), database)
    return (row, settings) if verified_user_target(settings) == target else (None, {})


def telegram_notification_keyboard(database, chat_id: str) -> TelegramKeyboard:
    target = str(chat_id).strip()
    row, settings = _notification_account(database, target)
    if not row:
        return telegram_main_keyboard()
    events = settings.get("tg_events") if isinstance(settings.get("tg_events"), dict) else {}
    buttons: list[dict[str, str]] = []
    for name, (keys, capability, label) in _NOTIFY_COMMANDS.items():
        if policy_allows(database, row, capability):
            enabled = all(events.get(key) is True for key in keys)
            buttons.append(
                {
                    "text": f"{'✅' if enabled else '◻️'} {label}",
                    "callback_data": f"notify:{name}:toggle",
                }
            )
        else:
            buttons.append({"text": f"🔒 {label}", "url": _app_url("membership")})
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append(
        [
            {"text": "⬅️ 主選單", "callback_data": "menu:home"},
            {"text": "網站通知設定", "url": _app_url("notifications")},
        ]
    )
    return rows


def update_notification_preference(database, chat_id: str, command: str) -> str:
    """Apply a private /notify command to the same settings used by the website."""
    target = str(chat_id).strip()
    if not target.isdigit():
        raise ValueError("请在与 CicloTrade Bot 的私人对话中使用通知设置。")
    parts = str(command or "").strip().lower().split()
    if parts and parts[0].startswith("/"):
        parts[0] = parts[0].split("@", 1)[0]
    if parts and parts[0] == "/start":
        return (
            "🤖 <b>CicloTrade Bot</b>\n\n"
            "<blockquote>量化建議、持倉、風控與會員通知助手。\n"
            f"你的 Chat ID：<code>{target}</code></blockquote>\n\n"
            "點按下方按鈕即可使用；綁定後可直接在 Bot 內管理私人通知。"
        )
    if parts and parts[0] == "/id":
        return (
            "🔗 <b>綁定 CicloTrade</b>\n\n"
            f"<blockquote>你的 Chat ID：<code>{target}</code></blockquote>\n\n"
            "回到網站「账户与安全 → Telegram」，貼上 Chat ID 並申請驗證碼；Bot 收到驗證碼後，回網站完成確認。"
        )
    if parts and parts[0] == "/help":
        return (
            "❓ <b>CicloTrade Bot 幫助</b>\n\n"
            "點按下方按鈕查看今日建議、目前持倉、市場行情或管理通知。\n"
            "Bot 不會要求你提供券商密碼、API Secret 或付款密碼。"
        )
    if parts and parts[0] not in {"/notify", "/settings"}:
        return "🤖 <b>CicloTrade Bot</b>\n\n請使用下方功能按鈕，無需輸入指令。"
    row, settings = _notification_account(database, target)
    if not row:
        raise ValueError("此 Telegram 尚未绑定 CicloTrade 账户，请先在网站设置页完成验证。")

    def status() -> str:
        events = settings.get("tg_events") if isinstance(settings.get("tg_events"), dict) else {}
        lines = ["⚙️ <b>CicloTrade · 私人通知設定</b>", "", "<blockquote>"]
        for keys, capability, label in _NOTIFY_COMMANDS.values():
            included = policy_allows(database, row, capability)
            enabled = included and all(events.get(key) is True for key in keys)
            state = "已開啟" if enabled else "已關閉" if included else "目前會員未開放"
            lines.append(f"{'✅' if enabled else '◻️' if included else '🔒'} <b>{label}</b> · {state}")
        lines.extend(("</blockquote>", "點按按鈕切換；網站與 Bot 共用同一份設定，會員降級後會自動停止越級推送。"))
        return "\n".join(lines)

    if parts in ([], ["/notify"], ["/settings"]):
        return status()
    if len(parts) != 3 or parts[0] != "/notify" or parts[1] not in _NOTIFY_COMMANDS or parts[2] not in {"on", "off", "toggle"}:
        return "⚠️ 無法識別此操作，請使用下方通知按鈕。"
    keys, capability, label = _NOTIFY_COMMANDS[parts[1]]
    if not policy_allows(database, row, capability):
        return f"🔒 <b>{label}</b>不在目前會員等級內，設定未改變。\n\n" + status()
    events = dict(settings.get("tg_events") or {})
    enabled = not all(events.get(key) is True for key in keys) if parts[2] == "toggle" else parts[2] == "on"
    for key in keys:
        events[key] = enabled
    settings = merge_user_settings(int(row["id"]), {"tg_events": events}, database)
    state = "已開啟" if enabled else "已關閉"
    return f"✅ <b>{label}{state}</b>\n\n" + status()


def telegram_bot_response(
    database,
    chat_id: str,
    value: str,
    *,
    callback: bool = False,
) -> tuple[str, TelegramKeyboard]:
    command = str(value or "").strip()
    if callback:
        if command == "menu:home":
            command = "/start"
        elif command == "menu:settings":
            command = "/settings"
        else:
            parts = command.split(":")
            command = (
                f"/notify {parts[1]} toggle"
                if len(parts) == 3 and parts[0] == "notify" and parts[1] in _NOTIFY_COMMANDS and parts[2] == "toggle"
                else "/help"
            )
    try:
        message = update_notification_preference(database, chat_id, command)
    except ValueError as exc:
        message = f"⚠️ <b>尚未完成帳戶綁定</b>\n\n{escape(str(exc))}"
    head = command.lower().split(maxsplit=1)[0].split("@", 1)[0] if command else "/settings"
    keyboard = (
        telegram_notification_keyboard(database, chat_id)
        if head in {"/notify", "/settings"}
        else telegram_main_keyboard()
    )
    return message, keyboard


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
        claimed = conn.execute(
            """SELECT user_id FROM telegram_accounts
               WHERE chat_id=? AND is_active=1 AND revoked_at IS NULL AND user_id<>?""",
            (chat_id, int(user_id)),
        ).fetchone()
        if claimed:
            raise ValueError("此 Telegram 已绑定其他 CicloTrade 账户。")
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
        now = datetime.now(UTC).isoformat(timespec="seconds")
        occupied = conn.execute(
            """SELECT user_id FROM telegram_accounts
               WHERE chat_id=? AND is_active=1 AND revoked_at IS NULL AND user_id<>?""",
            (str(row["chat_id"]), int(user_id)),
        ).fetchone()
        if occupied:
            raise ValueError("此 Telegram 已绑定其他 CicloTrade 账户。")
        conn.execute(
            "DELETE FROM telegram_accounts WHERE chat_id=? AND user_id<>? AND (is_active=0 OR revoked_at IS NOT NULL)",
            (str(row["chat_id"]), int(user_id)),
        )
        try:
            conn.execute(
                """INSERT INTO telegram_accounts
                   (user_id,chat_id,is_active,revoked_at,created_at,updated_at)
                   VALUES (?,?,1,NULL,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   chat_id=excluded.chat_id,is_active=1,revoked_at=NULL,updated_at=excluded.updated_at""",
                (int(user_id), str(row["chat_id"]), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("此 Telegram 已绑定其他 CicloTrade 账户。") from exc
        return str(row["chat_id"])


def revoke_telegram_account(database, user_id: int) -> None:
    """Revoke the authoritative Telegram binding when a user unlinks it."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    database.execute(
        """UPDATE telegram_accounts SET is_active=0,revoked_at=?,updated_at=?
           WHERE user_id=? AND is_active=1""",
        (now, now, int(user_id)),
    )


def send_telegram(
    message: str,
    chat_id: str | None = None,
    *,
    parse_mode: str | None = None,
    button: tuple[str, str] | None = None,
    buttons: TelegramKeyboard | None = None,
    protect_content: bool = False,
) -> None:
    enabled_env = str(_telegram_setting("enabled_env", "EXTERNAL_ALERTS_ENABLED"))
    if os.getenv(enabled_env, "false").strip().lower() != "true":
        raise RuntimeError("Telegram 外部通知已由平台停用。")
    token = telegram_token()
    target = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not target:
        raise RuntimeError("Telegram Bot 尚未配置。")
    try:
        target = _telegram_chat_id(target)
        text = _telegram_text(message)
        if button and buttons is not None:
            raise ValueError("Telegram 不能同时使用旧按钮和多行按钮。")
        keyboard = _telegram_keyboard(buttons)
        if button:
            if not isinstance(button, tuple) or len(button) != 2:
                raise ValueError("Telegram 按钮无效。")
            keyboard = _telegram_keyboard([[{"text": button[0], "url": button[1]}]])
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    payload: dict[str, Any] = {"chat_id": target, "text": text, "disable_web_page_preview": True}
    if protect_content:
        payload["protect_content"] = True
    if parse_mode in {"HTML", "MarkdownV2"}:
        payload["parse_mode"] = parse_mode
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    _telegram_request("sendMessage", payload, token=token)


def send_telegram_photo(
    message: str,
    photo_file_id: str,
    chat_id: str,
    *,
    buttons: TelegramKeyboard | None = None,
    protect_content: bool = True,
) -> None:
    """Send a previously validated same-bot Telegram photo without exposing its file ID."""
    enabled_env = str(_telegram_setting("enabled_env", "EXTERNAL_ALERTS_ENABLED"))
    if os.getenv(enabled_env, "false").strip().lower() != "true":
        raise RuntimeError("Telegram 外部通知已由平台停用。")
    file_id = str(photo_file_id or "").strip()
    if not 1 <= len(file_id) <= 256 or any(ord(char) < 33 or ord(char) > 126 for char in file_id):
        raise RuntimeError("Telegram 图片标识无效。")
    try:
        payload: dict[str, Any] = {
            "chat_id": _telegram_chat_id(chat_id, private=True),
            "photo": file_id,
            "caption": _telegram_text(message, limit=1024),
        }
        keyboard = _telegram_keyboard(buttons)
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        if protect_content:
            payload["protect_content"] = True
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    _telegram_request("sendPhoto", payload)


def _telegram_request(method: str, payload: dict[str, Any], *, token: str | None = None) -> None:
    bot_token = token or telegram_token()
    if not bot_token:
        raise RuntimeError("Telegram Bot 尚未配置。")
    if method not in {
        "sendMessage",
        "sendPhoto",
        "copyMessage",
        "editMessageText",
        "answerCallbackQuery",
        "setMyCommands",
        "setChatMenuButton",
        "setWebhook",
    }:
        raise RuntimeError("Telegram API 方法无效。")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
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


def download_telegram_file(file_id: str, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    """Download one Telegram photo with a hard response-size limit."""
    value = str(file_id or "").strip()
    if not 1 <= len(value) <= 256 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("Telegram 文件标识无效。")
    if not 1 <= int(max_bytes) <= 4 * 1024 * 1024:
        raise ValueError("Telegram 文件大小限制无效。")
    token = telegram_token()
    if not token:
        raise RuntimeError("Telegram Bot 尚未配置。")
    request = Request(
        f"https://api.telegram.org/bot{token}/getFile",
        data=json.dumps({"file_id": value}, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"Telegram HTTP {response.status}")
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Telegram HTTP {exc.code}") from exc
    except (RuntimeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise
    except Exception as exc:
        raise TelegramDeliveryUncertain(f"Telegram 文件读取不确定：{type(exc).__name__}") from exc
    result = payload.get("result") if isinstance(payload, dict) and payload.get("ok") is True else None
    file_path = result.get("file_path") if isinstance(result, dict) else None
    file_size = result.get("file_size") if isinstance(result, dict) else None
    if (
        not isinstance(file_path, str)
        or not 1 <= len(file_path) <= 256
        or ".." in file_path
        or any(ord(char) < 33 or ord(char) > 126 for char in file_path)
    ):
        raise ValueError("Telegram 文件路径无效。")
    if file_size is not None and (isinstance(file_size, bool) or int(file_size) > max_bytes):
        raise ValueError("Telegram 付款凭证图片必须小于 4 MB。")
    download_request = Request(
        f"https://api.telegram.org/file/bot{token}/{quote(file_path, safe='/')}",
        method="GET",
    )
    try:
        with urlopen(download_request, timeout=15) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("Telegram 付款凭证图片必须小于 4 MB。")
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise RuntimeError(f"Telegram HTTP {exc.code}") from exc
    except (RuntimeError, ValueError):
        raise
    except Exception as exc:
        raise TelegramDeliveryUncertain(f"Telegram 文件下载不确定：{type(exc).__name__}") from exc
    if not 1 <= len(data) <= max_bytes:
        raise ValueError("Telegram 付款凭证图片必须小于 4 MB。")
    return data


def edit_telegram_message(
    chat_id: str,
    message_id: int,
    message: str,
    *,
    buttons: TelegramKeyboard | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """Replace a Bot menu message after a validated private callback."""
    try:
        target = _telegram_chat_id(chat_id, private=True)
        if isinstance(message_id, bool) or not isinstance(message_id, int) or not 1 <= message_id <= 2_147_483_647:
            raise ValueError("Telegram message ID 无效。")
        payload: dict[str, Any] = {
            "chat_id": target,
            "message_id": message_id,
            "text": _telegram_text(message),
            "disable_web_page_preview": True,
        }
        keyboard = _telegram_keyboard(buttons)
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        if parse_mode in {"HTML", "MarkdownV2"}:
            payload["parse_mode"] = parse_mode
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    _telegram_request("editMessageText", payload)


def copy_telegram_message(
    chat_id: str,
    from_chat_id: str,
    message_id: int,
) -> None:
    """Copy payment evidence inside Telegram without downloading the file."""
    try:
        target = _telegram_chat_id(chat_id, private=True)
        source = _telegram_chat_id(from_chat_id, private=True)
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or not 1 <= message_id <= 2_147_483_647
        ):
            raise ValueError("Telegram message ID 无效。")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    _telegram_request(
        "copyMessage",
        {"chat_id": target, "from_chat_id": source, "message_id": message_id},
    )


def answer_telegram_callback(callback_query_id: str, text: str | None = None) -> None:
    """Acknowledge an inline callback promptly, without exposing account data."""
    if not isinstance(callback_query_id, str) or not callback_query_id or len(callback_query_id) > 128:
        raise RuntimeError("Telegram callback ID 无效。")
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text is not None:
        try:
            payload["text"] = _telegram_text(text, limit=200)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
    _telegram_request("answerCallbackQuery", payload)


def configure_telegram_bot() -> None:
    """Install commands, the native menu button, and the authenticated webhook."""
    commands = [
        {"command": "start", "description": "主選單與 Chat ID"},
        {"command": "timeline", "description": "交易時間線查詢"},
        {"command": "plans", "description": "會員方案與開通"},
        {"command": "orders", "description": "我的訂閱訂單"},
        {"command": "id", "description": "顯示綁定 Chat ID"},
        {"command": "settings", "description": "私人通知設定"},
        {"command": "payconfig", "description": "財務管理員收款資料"},
        {"command": "help", "description": "使用說明"},
    ]
    _telegram_request("setMyCommands", {"commands": commands})
    _telegram_request("setChatMenuButton", {"menu_button": {"type": "commands"}})
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if webhook_secret:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", webhook_secret):
            raise RuntimeError("Telegram Webhook Secret 格式無效。")
        _telegram_request(
            "setWebhook",
            {
                "url": _app_url("webhooks/telegram"),
                "secret_token": webhook_secret,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": False,
            },
        )


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
