"""Telegram-only administration for FPS, Alipay, and WeChat receiving details."""

from __future__ import annotations

from html import escape
from typing import Any

from notification.telegram_bot import download_telegram_file
from notification.telegram_models import TelegramDeskResponse
from payment.receiver_storage import delete_receiver_qr, store_receiver_qr
from payment.receiving_profile import METHOD_LABELS, METHODS, ReceivingProfileService


def _home_keyboard():
    return [[{"text": "⬅️ 主选单", "callback_data": "desk:home"}]]


def _require(database, account: dict[str, Any] | None) -> tuple[int, ReceivingProfileService]:
    if not account:
        raise PermissionError("请先绑定管理员账户。")
    actor_id = int(account["id"])
    service = ReceivingProfileService(database)
    service.require_billing_admin(actor_id)
    return actor_id, service


def is_billing_admin(database, account: dict[str, Any] | None) -> bool:
    try:
        _require(database, account)
        return True
    except (KeyError, TypeError, ValueError, PermissionError):
        return False


def _summary(database) -> TelegramDeskResponse:
    service = ReceivingProfileService(database)
    lines = ["🏦 <b>CicloTrade · 收款资料管理</b>", "", "这里只允许财务管理员操作。网站没有管理上传入口。", ""]
    buttons = []
    for method in ("fps", "alipay", "wechat"):
        profile = service.current(method)
        state = "已启用" if profile["available"] else "未启用"
        fields = f"ID {'✓' if profile.get('receiver_text') else '—'} · QR {'✓' if profile.get('qr_storage_key') else '—'}"
        lines.append(f"<b>{escape(METHOD_LABELS[method])}</b> · {state}\n{fields}")
        buttons.append([{"text": f"设置 {METHOD_LABELS[method]}", "callback_data": f"paycfg:show:{method}"}])
    buttons.append(_home_keyboard()[0])
    return TelegramDeskResponse("\n".join(lines), buttons)


def _method_card(database, method: str) -> TelegramDeskResponse:
    if method not in METHODS:
        raise ValueError("人工付款方式无效。")
    profile = ReceivingProfileService(database).current(method)
    receiver_text = str(profile.get("receiver_text") or "")
    detail = escape(receiver_text) if receiver_text else "尚未设置"
    message = (
        f"🏦 <b>{escape(METHOD_LABELS[method])} · 收款资料</b>\n\n"
        f"<blockquote>ID / 说明：{detail}\n"
        f"二维码：{'已设置' if profile.get('qr_storage_key') else '尚未设置'}\n"
        f"状态：{'新订单可使用' if profile['available'] else '未启用'}\n"
        f"版本：{int(profile.get('version') or 0)}</blockquote>\n"
        "ID 与二维码可以只设置其中一个，也可以同时保留。修改只影响新订单。"
    )
    buttons = [
        [{"text": "✏️ 设置 ID / 说明", "callback_data": f"paycfg:settext:{method}"}],
        [{"text": "🖼 上传 / 替换二维码", "callback_data": f"paycfg:setqr:{method}"}],
    ]
    if receiver_text:
        buttons.append([{"text": "清除 ID / 说明", "callback_data": f"paycfg:cleartext:{method}"}])
    if profile.get("qr_storage_key"):
        buttons.append([{"text": "清除二维码", "callback_data": f"paycfg:clearqr:{method}"}])
    buttons.extend([[{"text": "⬅️ 返回收款方式", "callback_data": "paycfg:home"}], _home_keyboard()[0]])
    return TelegramDeskResponse(message, buttons)


def _session_prompt(method: str, action: str) -> TelegramDeskResponse:
    if action == "receiver_text":
        instruction = "请直接发送收款 ID 或说明文字，不需要输入命令。最多 500 个字符。"
    else:
        instruction = "请直接发送清晰的二维码图片。支持 Telegram 普通图片，最大 4 MB。"
    return TelegramDeskResponse(
        f"⏳ <b>{escape(METHOD_LABELS[method])} · 等待输入</b>\n\n{instruction}\n\n本次操作 10 分钟后自动失效。",
        [[{"text": "取消本次操作", "callback_data": "paycfg:cancel"}], *_home_keyboard()],
    )


def _consume_session(
    database,
    chat_id: str,
    account: dict[str, Any] | None,
    command: str,
    photo: dict[str, str] | None,
) -> TelegramDeskResponse | None:
    raw = database.fetch_one(
        "SELECT * FROM telegram_payment_receiver_sessions WHERE chat_id=?", (str(chat_id),)
    )
    if not raw:
        return None
    if not account or int(raw["user_id"]) != int(account.get("id") or 0):
        database.execute("DELETE FROM telegram_payment_receiver_sessions WHERE chat_id=?", (str(chat_id),))
        return TelegramDeskResponse("⚠️ <b>收款资料操作已停止</b>\n\n管理员身份已变更。", _home_keyboard())
    actor_id = int(account["id"])
    service = ReceivingProfileService(database)
    try:
        session = service.session(actor_id, chat_id)
    except PermissionError:
        database.execute("DELETE FROM telegram_payment_receiver_sessions WHERE chat_id=?", (str(chat_id),))
        return TelegramDeskResponse("⚠️ <b>收款资料操作已停止</b>\n\n当前账户已没有财务权限。", _home_keyboard())
    if session is None:
        return TelegramDeskResponse("⌛ <b>本次收款资料操作已过期</b>\n\n请重新进入收款资料管理。", _home_keyboard())
    if command == "paycfg:cancel":
        service.cancel_session(actor_id, chat_id)
        return _summary(database)
    if command.startswith("paycfg:") or command.startswith("desk:"):
        service.cancel_session(actor_id, chat_id)
        return None
    method, action = str(session["method"]), str(session["action"])
    if action == "receiver_text":
        if photo is not None or not command or command.startswith("/"):
            return _session_prompt(method, action)
        service.set_receiver_text(actor_id, method, command)
        service.cancel_session(actor_id, chat_id)
        return _method_card(database, method)
    if photo is None:
        return _session_prompt(method, action)
    service.require_billing_admin(actor_id)
    stored = None
    try:
        raw_image = download_telegram_file(str(photo.get("file_id") or ""))
        stored = store_receiver_qr(raw_image, "image/jpeg")
        service.set_receiver_qr(
            actor_id,
            method,
            stored,
            str(photo.get("file_id") or ""),
            str(photo.get("file_unique_id") or ""),
        )
    except Exception:
        if stored is not None:
            delete_receiver_qr(stored.storage_key)
        raise
    service.cancel_session(actor_id, chat_id)
    return _method_card(database, method)


def handle_payment_receiver_action(
    database,
    chat_id: str,
    account: dict[str, Any] | None,
    command: str,
    *,
    photo: dict[str, str] | None = None,
) -> TelegramDeskResponse | None:
    head = str(command or "").lower().split(maxsplit=1)[0].split("@", 1)[0]
    recognized = (
        command in {"desk:receiving", "paycfg:home", "paycfg:cancel"}
        or head in {"/payconfig", "/receiving"}
        or command.startswith("paycfg:")
    )
    session_response = _consume_session(database, chat_id, account, command, photo)
    if session_response is not None:
        return session_response
    if not recognized:
        return None
    actor_id, service = _require(database, account)
    if command == "paycfg:cancel":
        service.cancel_session(actor_id, chat_id)
        return _summary(database)
    if command in {"desk:receiving", "paycfg:home"} or head in {"/payconfig", "/receiving"}:
        return _summary(database)
    parts = command.split(":")
    if len(parts) != 3 or parts[0] != "paycfg" or parts[2] not in METHODS:
        raise ValueError("收款资料操作无效。")
    action, method = parts[1], parts[2]
    if action == "show":
        return _method_card(database, method)
    if action in {"settext", "setqr"}:
        session_action = "receiver_text" if action == "settext" else "qr"
        service.begin_session(actor_id, chat_id, method, session_action)
        return _session_prompt(method, session_action)
    if action in {"cleartext", "clearqr"}:
        service.clear_field(actor_id, method, "receiver_text" if action == "cleartext" else "qr")
        return _method_card(database, method)
    raise ValueError("收款资料操作无效。")
