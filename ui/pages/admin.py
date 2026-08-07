# -*- coding: utf-8 -*-
"""Button-driven operations console with persisted RBAC and audit logs."""

from __future__ import annotations

from importlib.util import find_spec
import json
import os
from typing import Callable

import pandas as pd
import streamlit as st

from core.admin_service import AdminService, ROLE_LABELS
from core.database import get_database
from core.plans import PLAN_ORDER
from core.strategy_scoring import StrategyScorer
from core.user_profiles import UserProfileService
from notification.email_sender import smtp_configured
from notification.telegram_bot import telegram_configured
from payment.order_service import OrderService
from payment.paddle_client import PaddleClient
from payment.paypal_client import PayPalClient
from trading.tiger_api import TigerAPI
from ui.components import page_heading, section_label


def _run_action(action: Callable[[], object], success: str) -> None:
    try:
        action()
    except (PermissionError, ValueError, RuntimeError) as exc:
        st.error(str(exc), icon=":material/error:")
    except Exception as exc:
        get_database().log_system_event("ERROR", "ADMIN", "后台操作失败", str(exc)[:1000])
        st.error("操作未完成，错误已写入系统事件。", icon=":material/error:")
    else:
        st.session_state.admin_flash = success
        st.rerun()


@st.dialog("确认后台操作")
def _confirm_action(message: str, action: Callable[[], object], success: str, key: str) -> None:
    st.warning(message, icon=":material/warning:")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("取消", icon=":material/close:", key=f"{key}_cancel"):
            st.rerun()
        if st.button("确认执行", type="primary", icon=":material/check:", key=f"{key}_confirm"):
            _run_action(action, success)


def _user_label(row: dict) -> str:
    name = row.get("display_name") or row["email"]
    return f"{name} · {row['email']} · #{row['id']}"


def _render_users(service: AdminService, actor_id: int, role: str) -> None:
    users = service.list_users(actor_id)
    section_label("用户账户", f"{len(users)} 个账户")
    query = st.text_input(
        "搜索用户",
        placeholder="邮箱、显示名称或用户 ID",
        icon=":material/search:",
        key="admin_user_search",
    ).strip()
    frame = pd.DataFrame(users)
    if query and not frame.empty:
        frame = frame[
            frame.astype(str).apply(
                lambda row: row.str.contains(query, case=False, regex=False).any(), axis=1
            )
        ]
    if frame.empty:
        st.info("没有符合条件的用户。", icon=":material/search_off:")
        return
    display = frame[
        [
            "id",
            "email",
            "display_name",
            "plan_type",
            "subscription_expire",
            "is_active",
            "active_sessions",
            "active_ips",
            "admin_role",
            "last_login",
        ]
    ].copy()
    display["admin_role"] = display["admin_role"].map(ROLE_LABELS).fillna("普通用户")
    display.columns = [
        "ID",
        "邮箱",
        "显示名称",
        "方案",
        "订阅到期",
        "账户启用",
        "有效会话",
        "有效 IP",
        "后台角色",
        "上次登录",
    ]
    st.dataframe(display, hide_index=True)

    selected_id = st.selectbox(
        "选择要管理的用户",
        [int(row["id"]) for row in users],
        format_func=lambda value: _user_label(next(row for row in users if row["id"] == value)),
        key="admin_selected_user",
    )
    selected = next(row for row in users if row["id"] == selected_id)
    with st.container(border=True):
        st.markdown(f"**{selected.get('display_name') or selected['email']}**")
        st.caption(
            f"{selected['email']} · {selected['plan_type']} · "
            f"{'启用' if selected['is_active'] else '已停用'} · "
            f"{selected['active_sessions']} 个有效会话"
        )
        with st.container(horizontal=True):
            if selected["is_active"]:
                if st.button(
                    "停用账户",
                    icon=":material/person_off:",
                    disabled=selected_id == actor_id,
                    key="admin_disable_user",
                ):
                    _confirm_action(
                        "停用后，该账户的所有登录会话会立即失效。",
                        lambda: service.set_user_active(actor_id, selected_id, False),
                        "账户已停用，所有会话已失效。",
                        "disable_user",
                    )
            elif st.button("恢复账户", type="primary", icon=":material/person_check:", key="admin_enable_user"):
                _run_action(
                    lambda: service.set_user_active(actor_id, selected_id, True), "账户已恢复。"
                )
            if st.button(
                "重置登录会话",
                icon=":material/logout:",
                disabled=selected_id == actor_id or not selected["active_sessions"],
                key="admin_reset_sessions",
            ):
                _confirm_action(
                    "该用户需要重新登录，所有当前设备会立即退出。",
                    lambda: service.reset_sessions(actor_id, selected_id),
                    "该用户的会话已全部失效。",
                    "reset_sessions",
                )
            if st.button(
                "解除登录锁定",
                icon=":material/lock_open:",
                key="admin_unlock_user",
            ):
                _run_action(lambda: service.unlock_user(actor_id, selected_id), "登录锁定已解除。")

    if service.has_permission(role, "membership_grant") and not service.has_permission(role, "billing"):
        section_label("赠送体验", "客服可赠送体验，但不能直接调整正式会员等级")
        with st.form("support_membership_trial"):
            trial_plan = st.selectbox("体验方案", ["标准版", "高级版", "专业版"], key="support_trial_plan")
            trial_days = st.number_input("体验天数", min_value=1, max_value=90, value=7, step=1, key="support_trial_days")
            trial_reason = st.text_input("赠送原因（必填）", max_chars=240, key="support_trial_reason")
            trial_note = st.text_area("备注（选填）", max_chars=500, key="support_trial_note")
            grant = st.form_submit_button("赠送体验", type="primary", icon=":material/card_giftcard:")
        if grant:
            _run_action(
                lambda: service.grant_trial(actor_id, selected_id, trial_plan, int(trial_days), trial_reason, trial_note),
                "体验权益已赠送并记录日志。",
            )
        logs = service.list_membership_logs(actor_id, 100)
        with st.expander(f"会员变更日志 · {len(logs)} 条", icon=":material/history:"):
            if logs:
                st.dataframe(pd.DataFrame(logs), hide_index=True, width="stretch")
            else:
                st.caption("尚无会员变更记录。")

    section_label("IP 与设备", "每个账户最多 3 个启用中的 IP")
    ips = service.list_ips(actor_id, selected_id)
    if ips:
        ip_frame = pd.DataFrame(ips)[["id", "ip_address", "first_seen", "last_used", "is_active"]]
        ip_frame.columns = ["记录 ID", "IP 地址", "首次使用", "最后使用", "启用"]
        st.dataframe(ip_frame, hide_index=True)
    else:
        st.info("此账户尚无 IP 记录。", icon=":material/info:")
    with st.form(f"admin_add_ip_{selected_id}", border=False):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            new_ip = st.text_input("新增 IP", placeholder="例如 203.0.113.10", autocomplete="off")
            add_ip = st.form_submit_button("添加或恢复 IP", icon=":material/add:")
    if add_ip:
        _run_action(lambda: service.add_ip(actor_id, selected_id, new_ip), "IP 已加入白名单。")
    active_ips = [row for row in ips if row["is_active"]]
    if active_ips:
        remove_id = st.selectbox(
            "选择要停用的 IP",
            [int(row["id"]) for row in active_ips],
            format_func=lambda value: next(row["ip_address"] for row in active_ips if row["id"] == value),
            key="admin_remove_ip_id",
        )
        with st.container(horizontal=True):
            if st.button("停用所选 IP", icon=":material/link_off:", key="admin_remove_ip"):
                _confirm_action(
                    "停用后，该 IP 的现有会话会立即退出，且只能由管理员重新启用。",
                    lambda: service.remove_ip(actor_id, selected_id, remove_id),
                    "所选 IP 已停用。",
                    "remove_ip",
                )
            if st.button("清空有效 IP", icon=":material/delete_sweep:", key="admin_clear_ips"):
                _confirm_action(
                    "此操作会停用全部 IP 并立即撤销该账户的所有会话。",
                    lambda: service.clear_ips(actor_id, selected_id),
                    "有效 IP 已清空。",
                    "clear_ips",
                )
    sessions = service.list_sessions(actor_id, selected_id)
    with st.expander(f"最近登录设备 · {len(sessions)} 条"):
        if sessions:
            session_frame = pd.DataFrame(sessions)
            session_frame.columns = ["IP 地址", "设备", "登录时间", "最后活动", "有效"]
            st.dataframe(session_frame, hide_index=True)
        else:
            st.caption("没有登录会话。")

    if service.has_permission(role, "roles"):
        section_label("后台角色", "最小权限分工；仅超级管理员可调整")
        current_role = selected.get("admin_role")
        choices: list[str | None] = [None, *ROLE_LABELS]
        with st.form("admin_role_form"):
            new_role = st.selectbox(
                "分配角色",
                choices,
                index=choices.index(current_role) if current_role in choices else 0,
                format_func=lambda value: "非管理员" if value is None else ROLE_LABELS[value],
            )
            role_submit = st.form_submit_button("保存后台角色", type="primary", icon=":material/admin_panel_settings:")
        if role_submit:
            _run_action(
                lambda: service.set_role(actor_id, selected_id, new_role), "后台角色已更新。"
            )
        admins = service.list_admins(actor_id)
        if admins:
            admin_frame = pd.DataFrame(admins)
            admin_frame["role"] = admin_frame["role"].map(ROLE_LABELS)
            admin_frame.columns = ["ID", "邮箱", "显示名称", "账户启用", "角色", "更新时间"]
            st.dataframe(admin_frame, hide_index=True)


def _render_billing(service: AdminService, actor_id: int) -> None:
    annual_bonus = service.control_enabled("annual_bonus_enabled", True)
    with st.container(border=True):
        st.metric("年付限时权益", "15 个月" if annual_bonus else "12 个月")
        st.caption("只影响开关变更后新建的年付订单；已创建订单的权益天数不会改变。")
        if st.toggle("启用年付赠送 3 个月", value=annual_bonus, key="annual_bonus_toggle") != annual_bonus:
            _run_action(
                lambda: service.set_annual_bonus_enabled(actor_id, not annual_bonus),
                "年付限时权益设置已更新。",
            )

    summary = service.payment_summary(actor_id)
    with st.container(horizontal=True):
        st.metric("全部订单", int(summary.get("total") or 0), border=True)
        st.metric("待处理", int(summary.get("pending") or 0), border=True)
        st.metric("已支付", int(summary.get("paid") or 0), border=True)
        st.metric("已退款", int(summary.get("refunded") or 0), border=True)
        st.metric("累计实收", f"HK${float(summary.get('paid_amount') or 0):,.2f}", border=True)

    section_label("人工调整订阅", "直接写入账户权益并保留审计记录")
    users = service.list_subscription_users(actor_id)
    selected_id = st.selectbox(
        "选择用户",
        [int(row["id"]) for row in users],
        format_func=lambda value: _user_label(next(row for row in users if row["id"] == value)),
        key="billing_user",
    )
    selected = next(row for row in users if row["id"] == selected_id)
    with st.form("admin_subscription_adjust"):
        plan = st.selectbox(
            "订阅方案",
            PLAN_ORDER,
            index=PLAN_ORDER.index(selected["plan_type"]) if selected["plan_type"] in PLAN_ORDER else 0,
        )
        days = st.number_input(
            "增加有效期（天）",
            min_value=1,
            max_value=3650,
            value=30,
            step=1,
            help="从当前到期日继续累加；选择免费版时会清除到期日。",
        )
        reason = st.text_input("调整原因（必填）", max_chars=240)
        note = st.text_area("备注（选填）", max_chars=500)
        adjust = st.form_submit_button("更新订阅权益", type="primary", icon=":material/event_available:")
    if adjust:
        _run_action(
            lambda: service.adjust_subscription(actor_id, selected_id, plan, int(days), reason, note),
            "订阅权益已更新。",
        )

    if service.has_permission(service.role_for(actor_id), "membership_grant"):
        section_label("赠送体验", "客服/运营可赠送 1–90 天；不会自动给新用户试用")
        with st.form("admin_membership_trial"):
            trial_plan = st.selectbox("体验方案", ["标准版", "高级版", "专业版"], key="trial_plan")
            trial_days = st.number_input("体验天数", min_value=1, max_value=90, value=7, step=1, key="trial_days")
            trial_reason = st.text_input("赠送原因（必填）", max_chars=240, key="trial_reason")
            trial_note = st.text_area("体验备注（选填）", max_chars=500, key="trial_note")
            grant = st.form_submit_button("赠送体验", type="primary", icon=":material/card_giftcard:")
        if grant:
            _run_action(
                lambda: service.grant_trial(actor_id, selected_id, trial_plan, int(trial_days), trial_reason, trial_note),
                "体验权益已赠送并记录日志。",
            )

    logs = service.list_membership_logs(actor_id, 100)
    with st.expander(f"会员变更日志 · {len(logs)} 条", icon=":material/history:"):
        if logs:
            log_frame = pd.DataFrame(logs)[["created_at", "admin_email", "user_email", "operation_type", "before_plan", "after_plan", "expire_at", "reason", "note"]]
            log_frame.columns = ["时间", "操作人", "用户", "类型", "变更前", "变更后", "到期", "原因", "备注"]
            st.dataframe(log_frame, hide_index=True, width="stretch")
        else:
            st.caption("尚无会员变更记录。")

    section_label("订单与对账", "订单、回调和账户权益均来自 SQLite")
    left, right = st.columns(2)
    with left:
        status = st.selectbox(
            "订单状态", ["全部", "pending", "paid", "failed", "cancelled", "refunded"], key="order_status"
        )
    with right:
        method = st.selectbox("付款方式", ["全部", "paddle", "paypal", "fps"], key="order_method")
    orders = service.list_orders(actor_id, status, method)
    if orders:
        order_frame = pd.DataFrame(orders)
        order_frame.columns = [
            "订单号",
            "用户邮箱",
            "方案",
            "周期",
            "金额",
            "币种",
            "付款方式",
            "外部订单号",
            "状态",
            "创建时间",
            "支付时间",
            "退款时间",
        ]
        st.dataframe(
            order_frame,
            hide_index=True,
            column_config={"金额": st.column_config.NumberColumn(format="%.2f")},
        )
    else:
        st.info("没有符合条件的订单。", icon=":material/inbox:")

    pending_fps = service.list_orders(actor_id, "pending", "fps")
    if pending_fps:
        with st.form("confirm_fps"):
            fps_no = st.selectbox("等待确认的 FPS 订单", [row["order_no"] for row in pending_fps])
            fps_confirmed = st.checkbox("我已在银行记录中核对订单号、币种和金额")
            confirm_fps = st.form_submit_button(
                "确认 FPS 已入账", type="primary", icon=":material/paid:", disabled=not fps_confirmed
            )
        if confirm_fps:
            _run_action(lambda: service.confirm_fps(actor_id, fps_no), "FPS 订单已入账并更新订阅。")
    else:
        st.success("没有等待人工确认的 FPS 订单。", icon=":material/check_circle:")

    paid_orders = service.list_orders(actor_id, "paid", "全部")
    if paid_orders:
        section_label("退款登记", "先在原支付通道完成退款，再在此同步账户权益")
        refund_no = st.selectbox("选择已支付订单", [row["order_no"] for row in paid_orders], key="refund_order")
        allowed, reason = OrderService(service.db).refund_eligibility(refund_no)
        (st.success if allowed else st.warning)(reason)
        st.warning(
            "此按钮不会向 Paddle 或 PayPal 发起资金退款。必须先在原支付通道完成退款或 FPS 冲正。",
            icon=":material/warning:",
        )
        refund_confirmed = st.checkbox(
            "我已在原支付通道完成退款，并核对订单号与金额",
            key="refund_confirmed",
        )
        if st.button(
            "登记退款并降级账户",
            icon=":material/replay:",
            disabled=not (allowed and refund_confirmed),
            key="admin_refund",
        ):
            _run_action(
                lambda: service.record_external_refund(actor_id, refund_no),
                "外部退款结果已登记，账户已降级。",
            )

    callbacks = service.reconciliation_rows(actor_id)
    with st.expander(f"支付回调对账 · {len(callbacks)} 条"):
        if callbacks:
            callback_frame = pd.DataFrame(callbacks)
            callback_frame.columns = ["事件 ID", "订单号", "已处理", "接收时间", "订单匹配", "订单状态", "外部订单号"]
            st.dataframe(callback_frame, hide_index=True)
        else:
            st.caption("尚无支付回调。")

    st.caption("Paddle 尚未配置时不会创建真实交易；PayPal 与 FPS 继续按各自实际状态处理。")


def _render_research(service: AdminService, actor_id: int) -> None:
    published = service.control_enabled("recommendations_published", True)
    with st.container(border=True):
        st.metric("推荐发布状态", "已发布" if published else "已停止")
        st.caption("这是全站推荐输出的发布门禁，所有变更会写入审计日志。")
        with st.container(horizontal=True):
            if st.button(
                "发布推荐",
                type="primary",
                icon=":material/publish:",
                disabled=published,
                key="publish_recommendations",
            ):
                _run_action(
                    lambda: service.set_recommendations_published(actor_id, True), "推荐发布已恢复。"
                )
            if st.button(
                "停止发布",
                icon=":material/pause_circle:",
                disabled=not published,
                key="stop_recommendations",
            ):
                _run_action(
                    lambda: service.set_recommendations_published(actor_id, False), "推荐发布已停止。"
                )
    section_label("研究功能使用", "最近 200 条回测、策略详情和复制信号操作")
    rows = service.recommendation_activity(actor_id)
    if rows:
        frame = pd.DataFrame(rows)
        frame.columns = ["用户 ID", "邮箱", "策略", "操作", "结果", "时间"]
        st.dataframe(frame, hide_index=True)
    else:
        st.info("尚无研究功能使用记录。", icon=":material/query_stats:")

    section_label("用戶畫像", "每週從平台內部回測行為聚合，不對外共享")
    profile_service = UserProfileService(service.db)
    profile_stats = profile_service.statistics()
    if profile_stats:
        st.dataframe(pd.DataFrame(profile_stats), hide_index=True, width="stretch")
    else:
        st.info("尚無可聚合的用戶畫像。", icon=":material/group:")
    if st.button("立即更新用戶畫像", icon=":material/refresh:", key="admin_refresh_profiles"):
        _run_action(profile_service.aggregate_all, "用戶畫像已更新。")

    section_label("策略與模板管理", "YAML 核心目錄 + 資料庫擴展 · 所有寫操作保留審計")
    definitions = service.list_strategy_definitions(actor_id)
    if definitions:
        strategy_frame = pd.DataFrame(
            [
                {
                    "Key": item["key"], "名稱": item["name"], "類型": item["family"],
                    "風險": item["risk"], "核心": item["core"], "啟用": item["active"],
                }
                for item in definitions
            ]
        )
        st.dataframe(strategy_frame, hide_index=True, width="stretch")
        selected_key = st.selectbox(
            "選擇策略", [item["key"] for item in definitions],
            format_func=lambda value: next(item["name"] for item in definitions if item["key"] == value),
            key="admin_strategy_key",
        )
        selected_definition = next(item for item in definitions if item["key"] == selected_key)
        if st.button(
            "停用策略" if selected_definition["active"] else "重新啟用策略",
            icon=":material/toggle_off:" if selected_definition["active"] else ":material/toggle_on:",
            key="admin_toggle_strategy",
        ):
            target = not selected_definition["active"]
            _run_action(
                lambda: service.set_strategy_active(actor_id, selected_key, target),
                "策略狀態已更新。",
            )
    with st.expander("新增或更新擴展策略", icon=":material/add_circle:"):
        with st.form("admin_strategy_upsert"):
            first = st.columns(3, gap="small")
            strategy_key = first[0].text_input("策略 Key", max_chars=64, placeholder="custom_trend_v1")
            strategy_name = first[1].text_input("策略名稱", max_chars=120)
            family = first[2].selectbox("類型", ["equity", "option"])
            second = st.columns(2, gap="small")
            engine = second[0].text_input("引擎 Key", value="rules", max_chars=80)
            risk = second[1].selectbox("風險等級", ["low", "medium", "high"], index=1)
            scenario = st.text_input("適用場景", max_chars=1000)
            description = st.text_area("策略說明", max_chars=4000)
            parameters_text = st.text_area("參數 JSON", value="{}", max_chars=8000)
            rules_text = st.text_area("規則 JSON", value='{"entry": [], "exit": []}', max_chars=16000)
            save_strategy = st.form_submit_button("保存擴展策略", type="primary", icon=":material/save:")
        if save_strategy:
            try:
                definition = {
                    "key": strategy_key, "name": strategy_name, "family": family,
                    "engine": engine, "scenario": scenario, "description": description,
                    "risk": risk, "parameters": json.loads(parameters_text),
                    "rules": json.loads(rules_text), "example_metrics": {},
                    "core": False, "active": True,
                }
            except json.JSONDecodeError as exc:
                st.error(f"參數或規則 JSON 無效：{exc}", icon=":material/error:")
            else:
                _run_action(
                    lambda: service.save_strategy_definition(actor_id, definition),
                    "擴展策略已保存。",
                )
    latest_scores = StrategyScorer(service.db).latest()
    pending = [item for item in latest_scores if item["lifecycle_status"] in {"watch", "retire_pending"}]
    if pending:
        st.warning("觀察池與待淘汰只做標記；停用仍需由管理員在上方確認。", icon=":material/warning:")
        st.dataframe(pd.DataFrame(pending), hide_index=True, width="stretch")

    section_label("功能路线图", "发布后用户端路线图立即更新")
    roadmap = service.list_roadmap(actor_id)
    with st.form("admin_roadmap_create"):
        cols = st.columns([.8, 1.2, 1, .6], gap="small")
        with cols[0]:
            quarter = st.text_input("季度", placeholder="2026 Q4", max_chars=30)
        with cols[1]:
            roadmap_name = st.text_input("功能名称", max_chars=120)
        with cols[2]:
            roadmap_status = st.selectbox("状态", ["live", "in_progress", "planning", "evaluating"], format_func=lambda value: {"live": "已上线", "in_progress": "开发中", "planning": "规划中", "evaluating": "评估中"}[value])
        with cols[3]:
            sort_order = st.number_input("排序", min_value=0, max_value=999, value=0)
        roadmap_description = st.text_area("说明", max_chars=1000)
        add_roadmap = st.form_submit_button("新增路线图项目", type="primary", icon=":material/add:")
    if add_roadmap:
        _run_action(lambda: service.save_roadmap_item(actor_id, quarter, roadmap_name, roadmap_status, roadmap_description, int(sort_order)), "路线图项目已发布。")
    if roadmap:
        st.dataframe(pd.DataFrame(roadmap), hide_index=True, width="stretch")
        delete_id = st.selectbox("删除路线图项目", [int(row["id"]) for row in roadmap], format_func=lambda value: next(row["name"] for row in roadmap if row["id"] == value))
        if st.button("删除所选路线图项目", icon=":material/delete:"):
            _confirm_action("删除后用户端将立即移除此项目。", lambda: service.delete_roadmap_item(actor_id, delete_id), "路线图项目已删除。", "delete_roadmap")


def _service_status_rows() -> list[dict[str, str]]:
    paddle = PaddleClient()
    paypal = PayPalClient()
    tiger = TigerAPI()
    price_keys = [
        f"PADDLE_PRICE_{plan}_{cycle}"
        for plan in ("STANDARD", "ADVANCED", "PROFESSIONAL")
        for cycle in ("MONTHLY", "QUARTERLY", "YEARLY")
    ]
    paddle_prices = sum(bool(os.getenv(key)) for key in price_keys)
    paddle_webhook = bool(os.getenv("PADDLE_WEBHOOK_SECRET"))
    paypal_webhook = bool(os.getenv("PAYPAL_WEBHOOK_ID"))
    tiger_sdk = find_spec("tigeropen") is not None
    app_url = os.getenv("APP_BASE_URL", "http://localhost:8501")
    data_source = os.getenv("DATA_SOURCE", "yfinance").lower()
    polygon_ready = bool(os.getenv("POLYGON_API_KEY"))

    return [
        {
            "服务": "市场数据",
            "状态": "可用" if data_source == "yfinance" or polygon_ready else "未配置",
            "环境": data_source,
            "说明": "Yahoo 无需密钥" if data_source == "yfinance" else "Polygon API Key 已配置" if polygon_ready else "缺少 Polygon API Key",
        },
        {
            "服务": "Paddle Billing",
            "状态": "配置齐全，待联调" if paddle.configured and paddle_webhook and paddle_prices == len(price_keys) else "未配置完整",
            "环境": paddle.environment,
            "说明": f"API {'有' if paddle.configured else '缺'} · Webhook {'有' if paddle_webhook else '缺'} · Price ID {paddle_prices}/{len(price_keys)}",
        },
        {
            "服务": "PayPal",
            "状态": "配置齐全，待联调" if paypal.configured and paypal_webhook else "未配置完整",
            "环境": paypal.environment,
            "说明": f"客户端密钥 {'有' if paypal.configured else '缺'} · Webhook ID {'有' if paypal_webhook else '缺'}",
        },
        {
            "服务": "FPS",
            "状态": "已配置，待核对流程验收" if os.getenv("FPS_PAYMENT_INSTRUCTIONS", "").strip() else "未配置",
            "环境": "人工核对",
            "说明": "只有配置收款说明且后台确认银行入账后才开通订阅",
        },
        {
            "服务": "Tiger OpenAPI",
            "状态": "配置齐全，待联调" if tiger.configured and tiger_sdk else "未配置完整",
            "环境": tiger.environment,
            "说明": f"凭证 {'有' if tiger.configured else '缺'} · SDK {'已安装' if tiger_sdk else '未安装'} · 实盘总开关 {'开启' if os.getenv('TIGER_REAL_TRADING_ENABLED', 'false').lower() == 'true' else '关闭'}",
        },
        {
            "服务": "Telegram",
            "状态": "已配置，待验证" if telegram_configured() else "未配置",
            "环境": "外部通知",
            "说明": "仅配置状态，不代表消息发送已验证",
        },
        {
            "服务": "SMTP",
            "状态": "已配置，待验证" if smtp_configured() else "未配置",
            "环境": "邮件",
            "说明": "用于密码重设邮件",
        },
        {
            "服务": "正式域名",
            "状态": "待配置" if "localhost" in app_url or "your-domain" in app_url else "已配置，待验证",
            "环境": app_url,
            "说明": "上线前还需 HTTPS、DNS 与回调地址验证",
        },
    ]


def _render_system(service: AdminService, actor_id: int) -> None:
    paused = service.control_enabled("opening_paused", False)
    with st.container(border=True):
        st.metric("全局开仓状态", "已暂停" if paused else "允许研究/模拟开仓")
        st.caption("该控制会同步到所有现有用户，并自动应用到之后注册的账户。")
        if paused:
            if st.button("恢复所有新开仓", type="primary", icon=":material/play_circle:"):
                _run_action(
                    lambda: service.set_global_opening_paused(actor_id, False), "全局新开仓已恢复。"
                )
        else:
            confirmed = st.checkbox("我确认立即暂停所有用户的新开仓", key="confirm_global_pause")
            if st.button(
                "暂停所有新开仓",
                icon=":material/emergency:",
                disabled=not confirmed,
                key="global_pause",
            ):
                _run_action(
                    lambda: service.set_global_opening_paused(actor_id, True), "全局新开仓已暂停。"
                )

    section_label("服务与凭证", "仅显示配置状态，不读取或展示任何密钥")
    st.dataframe(pd.DataFrame(_service_status_rows()), hide_index=True)
    st.info(
        "“已配置，待验证”表示环境变量齐全，但尚未完成真实 API 请求、Webhook 回放或资金对账。",
        icon=":material/info:",
    )

    db = service.db
    risk = db.get_risk_logs(100)
    events = db.get_system_events(100)
    risk_tab, event_tab = st.tabs([":material/shield: 风控事件", ":material/terminal: 系统事件"])
    with risk_tab:
        if risk:
            st.dataframe(pd.DataFrame(risk), hide_index=True)
        else:
            st.caption("尚无风控事件。")
    with event_tab:
        if events:
            st.dataframe(pd.DataFrame(events), hide_index=True)
        else:
            st.caption("尚无系统事件。")


def _render_audit(service: AdminService, actor_id: int) -> None:
    section_label("后台审计", "所有管理员写操作，最近 500 条")
    query = st.text_input(
        "搜索审计记录",
        placeholder="管理员邮箱、操作类型、用户 ID 或订单号",
        icon=":material/search:",
        key="admin_audit_search",
    ).strip()
    rows = service.list_audit(actor_id)
    frame = pd.DataFrame(rows)
    if query and not frame.empty:
        frame = frame[
            frame.astype(str).apply(
                lambda row: row.str.contains(query, case=False, regex=False).any(), axis=1
            )
        ]
    if frame.empty:
        st.info("没有符合条件的后台操作。", icon=":material/search_off:")
    else:
        frame.columns = ["记录 ID", "时间", "操作员", "操作类型", "详情"]
        st.dataframe(frame, hide_index=True)


def render() -> None:
    admin = st.session_state.user
    if not admin.get("is_admin"):
        st.error("此页面仅限后台人员。", icon=":material/lock:")
        return
    service = AdminService()
    try:
        role = service.role_for(int(admin["id"]))
    except PermissionError as exc:
        st.error(str(exc), icon=":material/lock:")
        return

    page_heading(
        "OPERATIONS / CONTROL",
        "运营管理后台",
        "用户、订阅、推荐和系统控制统一落库；每次写操作都保留审计记录。",
        ROLE_LABELS[role],
    )
    if flash := st.session_state.pop("admin_flash", None):
        st.success(flash, icon=":material/check_circle:")

    metrics = service.dashboard_metrics(int(admin["id"]))
    metric_labels = {
        "users": "全部用户",
        "active_users": "启用账户",
        "subscribers": "有效订阅",
        "pending_orders": "待处理订单",
        "critical_risk": "24h 严重风控",
    }
    visible_metrics = [(metric_labels[key], metrics[key]) for key in metric_labels if key in metrics]
    if visible_metrics:
        with st.container(horizontal=True):
            for label, value in visible_metrics:
                st.metric(label, value, border=True)

    sections: list[tuple[str, str]] = []
    for label, permission in (
        ("用户与权限", "users"),
        ("订单与订阅", "billing"),
        ("推荐发布", "research"),
        ("系统与风控", "system"),
        ("审计记录", "audit"),
    ):
        if service.has_permission(role, permission):
            sections.append((label, permission))
    labels = [label for label, _ in sections]
    if st.session_state.get("admin_section") not in labels:
        st.session_state.admin_section = labels[0]
    section = st.segmented_control("后台区域", labels, key="admin_section", width="stretch")
    actor_id = int(admin["id"])
    if section == "用户与权限":
        _render_users(service, actor_id, role)
    elif section == "订单与订阅":
        _render_billing(service, actor_id)
    elif section == "推荐发布":
        _render_research(service, actor_id)
    elif section == "系统与风控":
        _render_system(service, actor_id)
    else:
        _render_audit(service, actor_id)
