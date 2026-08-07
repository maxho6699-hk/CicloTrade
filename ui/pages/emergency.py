# -*- coding: utf-8 -*-
"""持久化紧急暂停与定制版模拟盘全平。"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from core.database import get_database
from core.plans import can, effective_plan
from trading.order_manager import OrderManager
from ui.components import page_heading, section_label


def _set_paused(paused: bool) -> None:
    user_id = st.session_state.user["id"]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db = get_database()
    global_control = db.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )
    if not paused and global_control and str(global_control["control_value"]).lower() in {"1", "true", "yes", "on"}:
        raise ValueError("平台正在执行全局暂停，只有风控后台可以恢复新开仓。")
    db.execute(
        """INSERT INTO user_controls (user_id,opening_paused,updated_at) VALUES (?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET opening_paused=excluded.opening_paused,updated_at=excluded.updated_at""",
        (user_id, int(paused), now),
    )
    db.execute(
        "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
        (user_id, "PAUSE_OPENING" if paused else "RESUME_OPENING", f"opening_paused={paused}", now),
    )
    st.session_state.paused = paused


@st.dialog("确认暂停新开仓")
def _confirm_pause() -> None:
    st.warning("确认后，所有新的开仓请求都会被拒绝。现有持仓不会自动平仓。", icon=":material/warning:")
    st.caption("Tier 0 强制平仓、行情监控和审计记录继续运行。")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("取消", icon=":material/close:"):
            st.rerun()
        if st.button("确认暂停", type="primary", icon=":material/pause_circle:", key="confirm_pause"):
            _set_paused(True)
            st.rerun()


@st.dialog("确认一键全平模拟持仓")
def _confirm_flatten() -> None:
    st.error("此操作会按最新可用真实价格，反向成交当前账户的全部模拟净持仓。", icon=":material/crisis_alert:")
    confirmation = st.text_input("输入 FLATTEN 确认", autocomplete="off")
    if st.button("执行一键全平", type="primary", disabled=confirmation != "FLATTEN", key="confirm_flatten"):
        try:
            orders = OrderManager().liquidate_paper(st.session_state.user["id"])
            get_database().execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (st.session_state.user["id"], "LIQUIDATE_ALL_PAPER", f"orders={len(orders)}", datetime.now(UTC).isoformat(timespec="seconds")),
            )
            st.success(f"已完成 {len(orders)} 笔模拟平仓。")
            st.rerun()
        except Exception as exc:
            get_database().log_system_event("ERROR", "TRADING", "模拟账户全平失败", str(exc)[:1000])
            st.error("模拟持仓未能全部平仓，请检查行情通道后重试。")


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    paused = st.session_state.paused
    global_control = get_database().fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )
    globally_paused = bool(
        global_control and str(global_control["control_value"]).lower() in {"1", "true", "yes", "on"}
    )
    page_heading(
        "EMERGENCY / CONTROL",
        "紧急控制",
        "高风险操作需要明确确认并写入审计；暂停只限制新开仓，Tier 0 保护持续运行。",
        "TIER 0 ACTIVE",
    )
    state = "新开仓已暂停" if paused else "新开仓风控正常"
    st.html(
        f'<section class="emergency"><div><span class="chip {"danger" if paused else "success"}">{state}</span>'
        f'<h2>{"系统正在拒绝所有新开仓" if paused else "紧急暂停尚未启用"}</h2>'
        '<p>Tier 0 强制平仓、行情监控和审计日志始终保持运行。</p></div></section>'
    )
    if paused:
        if globally_paused:
            st.warning("平台级暂停正在生效，只有风控后台可以恢复新开仓。", icon=":material/admin_panel_settings:")
        elif st.button("恢复新开仓", type="primary", icon=":material/play_circle:", width="stretch", key="resume_opening"):
            _set_paused(False)
            st.rerun()
    elif st.button("暂停所有新开仓", type="primary", icon=":material/pause_circle:", width="stretch", key="pause_opening"):
        _confirm_pause()
    section_label("持续运行", "暂停新开仓不会关闭以下保护")
    st.success("行情监控 · 风控过滤器 · Tier 0 强制平仓 · 审计日志", icon=":material/shield:")
    section_label("一键全平", "仅限定制版；此处只操作模拟盘")
    if can(plan, "liquidate_all"):
        if st.button("一键全平模拟持仓", icon=":material/crisis_alert:", width="stretch"):
            _confirm_flatten()
    else:
        st.warning("一键全平只对定制版开放。", icon=":material/lock:")
