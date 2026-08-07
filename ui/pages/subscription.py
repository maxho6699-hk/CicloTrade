# -*- coding: utf-8 -*-
"""五档订阅、支付订单与退款入口。"""

from __future__ import annotations

import html
import os

import pandas as pd
import streamlit as st

from core.plans import PLAN_ORDER, PLANS, effective_plan
from payment.order_service import OrderService
from payment.paddle_client import PaddleClient
from payment.paypal_client import PayPalClient
from ui.components import page_heading, section_label


PLAN_ENV = {"标准版": "STANDARD", "高级版": "ADVANCED", "专业版": "PROFESSIONAL", "定制版": "CUSTOM"}
CYCLE_LABELS = {"monthly": "月付", "quarterly": "季付", "yearly": "年付（15 个月）", "project": "项目"}


def _plan_cards(current: str) -> str:
    cards = []
    for name in PLAN_ORDER:
        plan = PLANS[name]
        monthly = plan["prices"].get("monthly")
        price = "HKD 0" if monthly == 0 else "HKD 30,000 起" if monthly is None else f"HKD {monthly:,} / 月"
        active = " active" if name == current else ""
        features = "".join(f"<li>{html.escape(feature)}</li>" for feature in plan["features"])
        cards.append(
            f'<article class="plan-card{active}"><header><h3>{html.escape(name)}</h3>'
            f'<b>{"当前方案" if active else "可选方案"}</b></header><strong>{price}</strong>'
            f'<p>{html.escape(plan["summary"])}</p><ul>{features}</ul></article>'
        )
    return '<section class="plan-grid" aria-label="订阅方案">' + "".join(cards) + "</section>"


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    service = OrderService()
    annual_bonus = service.annual_bonus_enabled()
    payment_result = str(st.query_params.get("payment", ""))
    if payment_result == "success":
        st.success("PayPal 付款已完成，订阅权益已经更新。", icon=":material/check_circle:")
    elif payment_result == "cancelled":
        st.warning("PayPal 付款已取消，订单仍保持待付款状态。", icon=":material/info:")
    elif payment_result:
        st.error("PayPal 付款未能完成，请返回订单记录重试或联系支持。", icon=":material/error:")
    page_heading(
        "BILLING / MEMBERSHIP",
        "订阅与账单",
        "按研究深度选择方案。付款状态、使用记录与退款资格全部留痕。",
        f"CURRENT · {plan}",
    )
    st.html(_plan_cards(plan))

    section_label(
        "购买订阅",
        "限时年付含 15 个月使用期" if annual_bonus else "年付为 12 个月使用期",
    )
    st.warning(
        "退款政策：购买后 24 小时内，且未使用回测、预警、策略详情、复制信号或券商连接，方可申请退款。",
        icon=":material/policy:",
    )
    with st.form("purchase_plan"):
        columns = st.columns(3, gap="small")
        with columns[0]:
            selected_plan = st.selectbox("订阅方案", [name for name in PLAN_ORDER if name != "免费版"])
        with columns[1]:
            available_cycles = list(PLANS[selected_plan]["prices"])
            cycle = st.selectbox(
                "付款周期",
                available_cycles,
                format_func=lambda value: (
                    "年付（限时 15 个月）"
                    if value == "yearly" and annual_bonus
                    else "年付（12 个月）"
                    if value == "yearly"
                    else CYCLE_LABELS[value]
                ),
            )
        with columns[2]:
            method = st.selectbox("支付方式", ["paddle", "paypal", "fps"], format_func=lambda value: {"paddle": "Paddle", "paypal": "PayPal", "fps": "FPS 转数快"}[value])
        amount = float(PLANS[selected_plan]["prices"][cycle])
        entitlement = (
            "15 个月（12 个月 + 限时赠送 3 个月）"
            if cycle == "yearly" and annual_bonus
            else "12 个月"
            if cycle == "yearly"
            else "3 个月"
            if cycle == "quarterly"
            else "1 个月"
            if cycle == "monthly"
            else "按项目合同"
        )
        with st.container(horizontal=True):
            st.metric("应付总额", f"HKD {amount:,.0f}", border=True)
            st.metric("使用权益", entitlement, border=True)
            st.metric("付款通道", {"paddle": "Paddle", "paypal": "PayPal", "fps": "FPS 转数快"}[method], border=True)
        st.caption(f"订单摘要：{selected_plan} · {CYCLE_LABELS[cycle].split('（')[0]} · {entitlement} · 一次支付 HKD {amount:,.0f}")
        acknowledged = st.checkbox("我已阅读并同意退款政策、用户协议、隐私政策与风险披露")
        submitted = st.form_submit_button(
            f"建立 HKD {amount:,.0f} 付款订单",
            type="primary",
            icon=":material/receipt_long:",
        )
    if submitted:
        if not acknowledged:
            st.error("请先确认政策与协议。", icon=":material/error:")
        else:
            order = None
            try:
                paddle_client = None
                paypal_client = None
                price_id = ""
                if method == "paddle":
                    paddle_client = PaddleClient()
                    price_id = os.getenv(f"PADDLE_PRICE_{PLAN_ENV[selected_plan]}_{cycle.upper()}", "")
                    if not paddle_client.configured or not price_id:
                        raise RuntimeError("Paddle 尚未配置完成，请改用 PayPal 或 FPS。")
                elif method == "paypal":
                    paypal_client = PayPalClient()
                    if not paypal_client.configured:
                        raise RuntimeError("PayPal 尚未配置完成，请改用 FPS。")
                elif not os.getenv("FPS_PAYMENT_INSTRUCTIONS", "").strip():
                    raise RuntimeError("FPS 收款资料尚未配置，当前不能建立人工付款订单。")
                order = service.create_order(user["id"], selected_plan, cycle, method)
                if method == "fps":
                    st.success(f"FPS 待付款订单已建立。付款备注必须填写 {order['order_no']}。", icon=":material/check_circle:")
                    st.info(os.environ["FPS_PAYMENT_INSTRUCTIONS"], icon=":material/account_balance:")
                    st.caption("付款后由财务后台按订单号、币种和金额核对入账。")
                elif method == "paddle":
                    transaction = paddle_client.create_transaction(order["order_no"], price_id)
                    service.attach_external_id(order["order_no"], transaction["id"], price_id)
                    checkout_url = (transaction.get("checkout") or {}).get("url")
                    if checkout_url:
                        st.link_button("前往 Paddle 安全付款", checkout_url, icon=":material/open_in_new:")
                    else:
                        st.error("Paddle 未返回付款网址。订单已保留，请联系支持并提供订单号。", icon=":material/error:")
                else:
                    paypal_order = paypal_client.create_order(order["order_no"], float(order["amount"]))
                    service.attach_external_id(order["order_no"], paypal_order["id"])
                    approve_url = next((link["href"] for link in paypal_order.get("links", []) if link.get("rel") == "approve"), None)
                    if approve_url:
                        st.link_button("前往 PayPal 安全付款", approve_url, icon=":material/open_in_new:")
                    else:
                        st.error("PayPal 未返回审批网址。订单已保留，请联系支持并提供订单号。", icon=":material/error:")
            except (ValueError, RuntimeError) as exc:
                st.error(f"订单未能进入付款：{exc}", icon=":material/error:")
            except Exception as exc:
                service.db.log_system_event("ERROR", "PAYMENT", "建立付款交易失败", str(exc)[:1000])
                suffix = f" 请联系支持并提供订单号 {order['order_no']}。" if order else " 请稍后重试。"
                st.error(f"付款服务暂时不可用。{suffix}", icon=":material/error:")

    section_label("订单记录", "支付回调采用 event_id 幂等处理")
    orders = service.list_orders(user["id"])
    if not orders:
        st.info("尚无订阅订单。选择方案后即可建立付款订单。", icon=":material/receipt:")
        return
    frame = pd.DataFrame(orders)[["order_no", "plan_type", "billing_cycle", "amount", "currency", "pay_method", "status", "created_at"]]
    frame.columns = ["订单号", "方案", "周期", "金额", "币种", "方式", "状态", "建立时间"]
    st.dataframe(frame, hide_index=True, width="stretch")
    paid = [order for order in orders if order["status"] == "paid"]
    if paid:
        selected = st.selectbox("检查退款资格", [order["order_no"] for order in paid])
        allowed, reason = service.refund_eligibility(selected)
        (st.success if allowed else st.warning)(reason, icon=":material/check_circle:" if allowed else ":material/info:")
        if allowed and st.button("提交退款申请", icon=":material/replay:"):
            service.log_action(user["id"], "REFUND_REQUEST", {"order_no": selected})
            st.success("退款申请已记录，请等待管理员核对支付通道。")
