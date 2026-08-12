# -*- coding: utf-8 -*-
"""Private, deduplicated Telegram notices for manual referral payouts."""

from __future__ import annotations

from html import escape
from typing import Any

from core.admin_service import AdminService
from notification.telegram_models import TelegramOutbound
from notification.telegram_outbox import enqueue_telegram_outbound


def queue_withdrawal_review_notice(database, withdrawal: dict[str, Any]) -> int:
    """Notify bound billing admins without exposing payout credentials."""
    AdminService(database)
    targets = database.fetch_all(
        """SELECT t.chat_id,r.role FROM telegram_accounts t
           JOIN users u ON u.id=t.user_id
           JOIN admin_roles r ON r.user_id=u.id
           WHERE u.is_active=1 AND u.is_admin=1 AND t.is_active=1
             AND t.revoked_at IS NULL"""
    )
    queued = 0
    public_id = str(withdrawal["public_id"])
    message = (
        "💰 <b>CicloTrade · 推广提款待审核</b>\n\n"
        f"申请编号：<code>{escape(public_id)}</code>\n"
        f"金额：HKD {int(withdrawal['amount_minor']) / 100:,.2f}\n\n"
        "请在受控管理台核验、批准或拒绝。批准不代表已付款；付款后须由另一名财务人员确认。"
    )
    for row in targets:
        if not AdminService.has_permission(str(row["role"]), "billing"):
            continue
        queued += int(enqueue_telegram_outbound(
            database,
            TelegramOutbound(str(row["chat_id"]), message),
            f"referral-withdrawal:{public_id}:submitted:{row['chat_id']}",
        ))
    return queued


def queue_withdrawal_user_notice(database, withdrawal: dict[str, Any]) -> bool:
    row = database.fetch_one(
        """SELECT chat_id FROM telegram_accounts WHERE user_id=? AND is_active=1
           AND revoked_at IS NULL""",
        (int(withdrawal["user_id"]),),
    )
    if not row:
        return False
    public_id = str(withdrawal["public_id"])
    status = str(withdrawal["status"])
    label = {
        "approved": "已批准，等待人工付款",
        "rejected": "已拒绝，金额已退回可提款余额",
        "paid": "已确认人工付款",
        "system_cancelled": "因退款或拒付已取消",
    }.get(status, status)
    return enqueue_telegram_outbound(
        database,
        TelegramOutbound(
            str(row["chat_id"]),
            f"💰 <b>CicloTrade · 推广提款更新</b>\n\n"
            f"申请编号：<code>{escape(public_id)}</code>\n状态：{escape(label)}",
        ),
        f"referral-withdrawal:{public_id}:user:{status}",
    )
