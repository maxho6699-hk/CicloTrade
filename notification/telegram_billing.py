# -*- coding: utf-8 -*-
"""Secure Telegram subscription checkout and manual payment review."""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
from html import escape
import os
from typing import Any

from core.admin_service import AdminService
from core.plans import PLANS, plan_display_name
from notification.telegram_models import TelegramDeskResponse, TelegramOutbound
from notification.telegram_outbox import enqueue_telegram_outbound
from notification.templates import telegram_membership
from payment.order_service import OrderService


_PLAN_SLUGS = {
    "standard": "标准版",
    "advanced": "高级版",
    "professional": "专业版",
    "custom": "定制版",
}
_PLAN_ENV = {
    "标准版": "STANDARD",
    "高级版": "ADVANCED",
    "专业版": "PROFESSIONAL",
    "定制版": "CUSTOM",
}
_CYCLE_LABELS = {
    "monthly": "月付",
    "quarterly": "季付",
    "yearly": "年付",
    "project": "项目",
}


def _home_keyboard():
    return [[{"text": "⬅️ 主选单", "callback_data": "desk:home"}]]


def _binding_required() -> TelegramDeskResponse:
    return TelegramDeskResponse(
        "🔒 <b>需要先绑定账户</b>\n\n为了保护会员和付款记录，请先在网站登录后完成一次 Telegram 验证。",
        [
            [{"text": "🔗 安全绑定", "url": "https://ciclotrade.com/account"}],
            *_home_keyboard(),
        ],
    )


def _admin_targets(database) -> list[dict[str, Any]]:
    AdminService(database)
    rows = database.fetch_all(
        """SELECT u.id,t.chat_id,r.role
           FROM telegram_accounts t
           JOIN users u ON u.id=t.user_id
           JOIN admin_roles r ON r.user_id=u.id
           WHERE u.is_active=1 AND u.is_admin=1 AND t.is_active=1 AND t.revoked_at IS NULL"""
    )
    return [row for row in rows if AdminService.has_permission(str(row["role"]), "billing")]


def _user_chat(database, user_id: int) -> str | None:
    row = database.fetch_one(
        """SELECT chat_id FROM telegram_accounts
           WHERE user_id=? AND is_active=1 AND revoked_at IS NULL""",
        (int(user_id),),
    )
    return str(row["chat_id"]) if row else None


def _valid_order_options(slug: str, cycle: str, method: str) -> tuple[str, float]:
    if slug not in _PLAN_SLUGS or method not in {"fps", "paypal", "paddle"}:
        raise ValueError("订单选项无效。")
    plan = _PLAN_SLUGS[slug]
    if cycle not in PLANS[plan]["prices"]:
        raise ValueError("此方案不支持所选付款周期。")
    return plan, float(PLANS[plan]["prices"][cycle])


def _order_idempotency(user_id: int, slug: str, cycle: str, method: str) -> str:
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    return f"telegram:{int(user_id)}:{slug}:{cycle}:{method}:{hour}"


def _create_order(
    database,
    account: dict[str, Any] | None,
    slug: str,
    cycle: str,
    method: str,
) -> TelegramDeskResponse:
    if not account:
        return _binding_required()
    plan, amount = _valid_order_options(slug, cycle, method)
    fps_instructions = os.getenv("FPS_PAYMENT_INSTRUCTIONS", "").strip()
    if method == "fps" and not fps_instructions:
        raise ValueError("FPS 收款资料尚未配置，请联系客户服务。")
    service = OrderService(database)
    order = service.create_order(
        int(account["id"]),
        plan,
        cycle,
        method,
        terms_accepted=True,
        source="telegram",
        idempotency_key=_order_idempotency(int(account["id"]), slug, cycle, method),
    )
    order_no = str(order["order_no"])
    if method == "fps":
        message = (
            "🏦 <b>CicloTrade · FPS 待付款</b>\n\n"
            f"<blockquote>订单：<code>{escape(order_no)}</code>\n"
            f"方案：{escape(plan_display_name(plan))} · {_CYCLE_LABELS[cycle]}\n"
            f"金额：HKD {amount:,.0f}</blockquote>\n"
            f"{escape(fps_instructions)}\n\n"
            f"转账备注必须填写 <code>{escape(order_no)}</code>。付款后可直接点击“已付款”，"
            "也可上传付款截图；系统只会提交人工审核，不会自动开通会员。"
        )
        return TelegramDeskResponse(
            message,
            [
                [{"text": "✅ 我已付款", "callback_data": f"pay:claimed:{order_no}"}],
                [{"text": "🧾 我的订单", "callback_data": "desk:orders"}],
                *_home_keyboard(),
            ],
        )

    checkout_url = None
    if method == "paypal":
        from payment.paypal_client import PayPalClient

        client = PayPalClient()
        if not client.configured:
            raise ValueError("PayPal 付款暂未开放。")
        external = client.create_order(order_no, amount)
        service.attach_external_id(order_no, str(external["id"]))
        checkout_url = next(
            (str(link["href"]) for link in external.get("links", []) if link.get("rel") == "approve"),
            None,
        )
    else:
        from payment.paddle_client import PaddleClient

        client = PaddleClient()
        price_id = os.getenv(f"PADDLE_PRICE_{_PLAN_ENV[plan]}_{cycle.upper()}", "").strip()
        if not client.configured or not price_id:
            raise ValueError("Paddle 付款暂未开放。")
        external = client.create_transaction(order_no, price_id)
        service.attach_external_id(order_no, str(external["id"]), price_id)
        checkout_url = str((external.get("checkout") or {}).get("url") or "") or None
    if not checkout_url:
        raise RuntimeError("付款平台未返回安全付款网址。")
    return TelegramDeskResponse(
        "🔐 <b>安全付款订单已建立</b>\n\n"
        f"<blockquote><code>{escape(order_no)}</code>\n{escape(plan_display_name(plan))} · HKD {amount:,.0f}</blockquote>\n"
        "付款完成后，支付平台会自动回传并开通会员。",
        [
            [{"text": "前往安全付款", "url": checkout_url}],
            [{"text": "🧾 我的订单", "callback_data": "desk:orders"}],
            *_home_keyboard(),
        ],
    )


def _claim_followups(
    database,
    account: dict[str, Any],
    order: dict[str, Any],
    claim: dict[str, Any],
    *,
    evidence_chat_id: str | None = None,
    evidence_message_id: int | None = None,
) -> tuple[TelegramOutbound, ...]:
    message = (
        "💳 <b>CicloTrade · 待审核付款</b>\n\n"
        f"<blockquote>申报 #{int(claim['id'])}\n"
        f"订单：<code>{escape(str(order['order_no']))}</code>\n"
        f"账户：{escape(str(account['email']))}\n"
        f"{escape(plan_display_name(str(order['plan_type'])))} · {escape(str(order['currency']))} {float(order['amount']):,.0f}</blockquote>\n"
        "请先核对银行到账、金额与订单备注，再批准。"
    )
    buttons = [
        [{"text": "✅ 开始核对到账", "callback_data": f"admin:approve:{claim['id']}:{claim['attempt']}"}],
        [{"text": "❌ 未到账 / 驳回", "callback_data": f"admin:reject:{claim['id']}:{claim['attempt']}"}],
    ]
    return tuple(
        TelegramOutbound(
            str(admin["chat_id"]),
            message,
            buttons,
            copy_from_chat_id=evidence_chat_id,
            copy_message_id=evidence_message_id,
        )
        for admin in _admin_targets(database)
    )


def _submit_claim(
    database,
    chat_id: str,
    account: dict[str, Any] | None,
    order_no: str,
    *,
    message_id: int | None,
    update_id: str | int | None,
    photo: dict[str, str] | None = None,
) -> TelegramDeskResponse:
    if not account:
        return _binding_required()
    service = OrderService(database)
    order = service.get_order_for_user(int(account["id"]), order_no)
    if str(order["pay_method"]) != "fps":
        raise ValueError("此订单由支付平台自动确认，无需人工申报。")
    existing = database.fetch_one(
        "SELECT * FROM manual_payment_claims WHERE order_no=? AND status='submitted'",
        (order_no,),
    )
    claim = service.submit_manual_payment_claim(
        int(account["id"]),
        order_no,
        evidence_file_id=photo.get("file_id") if photo else None,
        evidence_file_unique_id=photo.get("file_unique_id") if photo else None,
        evidence_message_id=message_id,
        source_update_id=update_id,
    )
    if not existing:
        for outbound in _claim_followups(
            database,
            account,
            order,
            claim,
            evidence_chat_id=chat_id if photo else None,
            evidence_message_id=message_id if photo else None,
        ):
            enqueue_telegram_outbound(
                database,
                outbound,
                f"manual-claim:{claim['id']}:admin:{outbound.chat_id}",
            )
    return TelegramDeskResponse(
        "⏳ <b>付款申报已提交</b>\n\n"
        f"<blockquote>订单：<code>{escape(order_no)}</code>\n状态：等待财务人工核对</blockquote>\n"
        "审核通过后，Bot 会自动通知你并更新会员；请勿重复提交或连续点击。",
        [[{"text": "🧾 查看订单", "callback_data": "desk:orders"}], *_home_keyboard()],
    )


def _photo_claim(
    database,
    chat_id: str,
    account: dict[str, Any] | None,
    photo: dict[str, str],
    message_id: int,
    update_id: str | int | None,
    command: str,
) -> TelegramDeskResponse:
    if not account:
        return _binding_required()
    pending = [
        order for order in OrderService(database).list_pending_orders(int(account["id"]), source="telegram")
        if str(order.get("pay_method")) == "fps"
    ]
    if not pending:
        return TelegramDeskResponse(
            "🧾 <b>没有可关联的 FPS 待付款订单</b>\n\n请先选择会员方案并建立订单，再上传付款截图。",
            [[{"text": "💎 选择方案", "callback_data": "desk:plans"}], *_home_keyboard()],
        )
    order = pending[0] if len(pending) == 1 else next(
        (item for item in pending if str(item["order_no"]).upper() in command.upper()),
        None,
    )
    if order is None:
        order_lines = "\n".join(f"• <code>{escape(str(item['order_no']))}</code>" for item in pending[:5])
        return TelegramDeskResponse(
            "🧾 <b>请选择截图对应的订单</b>\n\n"
            f"<blockquote>{order_lines}</blockquote>\n"
            "你有多张待付款订单。请重新上传截图，并在图片说明中填写完整订单号，系统不会自动猜测。",
            [[{"text": "🧾 查看订单", "callback_data": "desk:orders"}], *_home_keyboard()],
        )
    return _submit_claim(
        database,
        chat_id,
        account,
        str(order["order_no"]),
        message_id=message_id,
        update_id=update_id,
        photo=photo,
    )


def queue_manual_payment_review_notice(database, reviewed: dict[str, Any], approved: bool) -> bool:
    """Persist the user's review result so transient Telegram errors can retry."""
    user_chat = _user_chat(database, int(reviewed["user_id"]))
    if not user_chat:
        return False
    order_no = str(reviewed["order_no"])
    if approved:
        user = database.fetch_one(
            "SELECT plan_type,subscription_expire FROM users WHERE id=?",
            (int(reviewed["user_id"]),),
        ) or {}
        notice = telegram_membership(
            str(user.get("plan_type") or "--"),
            str(user.get("subscription_expire") or "--"),
            "付款已由财务核对通过",
        )
    else:
        notice = (
            "❌ <b>CicloTrade · 付款申报未通过</b>\n\n"
            f"订单 <code>{escape(order_no)}</code> 暂未核对到账。请检查金额与转账备注后重新提交。"
        )
    return enqueue_telegram_outbound(
        database,
        TelegramOutbound(user_chat, notice),
        f"manual-claim:{reviewed['id']}:user:{'approved' if approved else 'rejected'}",
    )


def _review_claim(
    database,
    account: dict[str, Any] | None,
    claim_id: int,
    attempt: int,
    approved: bool,
    *,
    settlement_reference: str | None = None,
) -> TelegramDeskResponse:
    if not account or not account.get("is_admin"):
        raise PermissionError("此操作仅限已验证的财务管理员。")
    claim = database.fetch_one(
        "SELECT * FROM manual_payment_claims WHERE id=? AND attempt=?",
        (int(claim_id), int(attempt)),
    )
    if not claim:
        raise ValueError("付款申报不存在。")
    order_no = str(claim["order_no"])
    service = AdminService(database)
    reviewed = service.review_manual_payment_claim(
        int(account["id"]),
        int(claim["id"]),
        approved,
        settlement_reference=settlement_reference if approved else None,
        rejection_reason=None if approved else "管理员未能核对到账，请检查金额与订单备注后重新提交。",
    )
    queue_manual_payment_review_notice(database, reviewed, approved)
    state = "已核对到账并开通会员" if approved else "已驳回，未开通会员"
    return TelegramDeskResponse(
        f"{'✅' if approved else '❌'} <b>{state}</b>\n\n"
        f"<blockquote>订单：<code>{escape(order_no)}</code>\n申报 #{int(reviewed['id'])}</blockquote>",
        [[{"text": "🧾 我的订单", "callback_data": "desk:orders"}], *_home_keyboard()],
    )


def _approval_prompt(
    database,
    account: dict[str, Any] | None,
    claim_id: int,
    attempt: int,
) -> TelegramDeskResponse:
    if not account or not account.get("is_admin"):
        raise PermissionError("此操作仅限已验证的财务管理员。")
    service = AdminService(database)
    if not service.has_permission(service.role_for(int(account["id"])), "billing"):
        raise PermissionError("当前后台角色无权执行此操作。")
    claim = database.fetch_one(
        "SELECT id,attempt,order_no,status FROM manual_payment_claims WHERE id=? AND attempt=?",
        (int(claim_id), int(attempt)),
    )
    if not claim:
        raise ValueError("付款申报不存在。")
    if claim["status"] != "submitted":
        raise ValueError("付款申报已经处理，请勿重复审核。")
    order_no = str(claim["order_no"])
    return TelegramDeskResponse(
        "🔐 <b>CicloTrade · 财务复核</b>\n\n"
        f"<blockquote>订单：<code>{escape(order_no)}</code>\n"
        "请在银行或 FPS 后台确认收款金额和备注。</blockquote>\n"
        "确认后发送：\n"
        f"<code>/approve {int(claim_id)} {int(attempt)} 银行流水号</code>\n\n"
        "流水号只用于防止同一笔入账重复开通，不会展示给用户。",
        [[{"text": "❌ 未到账 / 驳回", "callback_data": f"admin:reject:{int(claim_id)}:{int(attempt)}"}], *_home_keyboard()],
    )


def handle_billing_action(
    database,
    chat_id: str,
    account: dict[str, Any] | None,
    command: str,
    *,
    message_id: int | None = None,
    update_id: str | int | None = None,
    photo: dict[str, str] | None = None,
) -> TelegramDeskResponse | None:
    if photo is not None:
        if message_id is None:
            raise ValueError("Telegram 图片消息无效。")
        return _photo_claim(database, chat_id, account, photo, message_id, update_id, command)
    if command.startswith("buy:create:"):
        _, _, slug, cycle, method = command.split(":")
        return _create_order(database, account, slug, cycle, method)
    if command.startswith("pay:claimed:"):
        if message_id is None:
            raise ValueError("Telegram 付款确认消息无效。")
        return _submit_claim(
            database,
            chat_id,
            account,
            command.split(":", 2)[2],
            message_id=message_id,
            update_id=update_id,
        )
    if command.startswith("admin:"):
        _, decision, claim_id, attempt = command.split(":")
        if decision == "approve":
            return _approval_prompt(database, account, int(claim_id), int(attempt))
        return _review_claim(database, account, int(claim_id), int(attempt), False)
    if command.lower().split(maxsplit=1)[0].split("@", 1)[0] == "/approve":
        parts = command.split(maxsplit=3)
        if len(parts) != 4:
            raise ValueError("请使用 /approve 申报号 次数 银行流水号 完成审核。")
        return _review_claim(
            database,
            account,
            int(parts[1]),
            int(parts[2]),
            True,
            settlement_reference=parts[3].strip(),
        )
    return None
