# -*- coding: utf-8 -*-
"""SQLite 审计记录查询。"""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from core.admin_service import AdminService
from core.database import get_database
from core.plans import can, effective_plan
from core.quant_journal import QuantJournal
from ui.components import page_heading, section_label


def _search(frame: pd.DataFrame, query: str) -> pd.DataFrame:
    if not query or frame.empty:
        return frame
    return frame[frame.astype(str).apply(lambda row: row.str.contains(query, case=False, regex=False).any(), axis=1)]


def _show(frame: pd.DataFrame, query: str, empty: str) -> None:
    frame = _search(frame, query)
    if frame.empty:
        st.info(empty, icon=":material/search_off:")
    else:
        st.dataframe(frame, hide_index=True, width="stretch")


def _quant_rows(
    events: list[dict],
    journal: QuantJournal | None = None,
    *,
    include_options: bool = True,
) -> pd.DataFrame:
    ledger = journal or QuantJournal()
    rows = []
    for event in reversed(events):
        try:
            execution = ledger.execution_legs(int(event["id"]))
        except (KeyError, TypeError, ValueError, RuntimeError, StopIteration):
            execution = []
        hidden_options = [
            leg for leg in execution
            if leg.get("instrument_type") == "option" and not include_options
        ]
        legs = [
            leg for leg in execution
            if include_options or leg.get("instrument_type") != "option"
        ]
        if not legs:
            legs = [None]
        for leg in legs:
            if hidden_options and leg is None:
                action = "期权事件（升级后查看）"
                contract = "期权事件（升级后查看）"
            else:
                delta = float(leg["quantity_delta"]) if leg else 0.0
                action = "买入 / 增持" if delta > 0 else "卖出 / 减持" if delta < 0 else "撤销"
                if event["event_type"] == "reversal":
                    action = f"撤销 · {action}"
                elif event["event_type"] == "correction":
                    action = f"更正 · {action}"
                contract = leg["instrument_key"] if leg else "--"
            if event["event_type"] == "reversal":
                status = "撤销事件"
            elif event["event_type"] == "correction":
                status = "更正事件"
            else:
                status = "有效" if event["active"] else "已更正 / 撤销"
            occurred = pd.Timestamp(event["occurred_at"])
            if occurred.tzinfo is None:
                occurred = occurred.tz_localize("UTC")
            # Metadata can carry contract terms even on a stock correction or reversal.
            metadata = event["metadata"] if include_options else {}
            rows.append(
                {
                    "事件 ID": event["id"],
                    "发生时间": occurred.tz_convert("Asia/Hong_Kong"),
                    "状态": status,
                    "策略": event["strategy_name"],
                    "版本": event["strategy_version"],
                    "标的 / 合约": contract,
                    "动作": action,
                    "数量变化": leg["quantity_delta"] if leg else None,
                    "目标仓位": leg["target_quantity"] if leg else None,
                    "记录价": leg["price"] if leg else None,
                    "币种": leg["currency"] if leg else "--",
                    "来源": event["source"],
                    "外部编号": event["external_event_id"],
                    "更正事件": event["corrects_event_id"],
                    "说明": json.dumps(metadata, ensure_ascii=False, sort_keys=True) if metadata else "--",
                }
            )
    return pd.DataFrame(rows)


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    db = get_database()
    page_heading(
        "AUDIT / ACTIVITY",
        "审计日志",
        "订单、风控、策略操作、登录与系统事件统一来自 SQLite，不显示固定演示记录。",
        "PERSISTED · WAL",
    )
    with st.form("audit_filter", border=False):
        query = st.text_input(
            "搜索关键词", placeholder="输入标的、事件、订单号或说明…", autocomplete="off", icon=":material/search:",
        ).strip()
        submitted = st.form_submit_button("查询记录", icon=":material/search:")
    del submitted
    orders = db.fetch_all(
        "SELECT order_id,symbol,side,quantity,price,status,strategy_name,account_mode,created_at FROM orders WHERE reason=? ORDER BY created_at DESC LIMIT 500",
        (f"user={user['id']}",),
    )
    actions = db.fetch_all(
        "SELECT action_type,details,created_at FROM user_action_logs WHERE user_id=? ORDER BY created_at DESC LIMIT 500",
        (user["id"],),
    )
    strategy_actions = db.fetch_all(
        "SELECT strategy_name,action,params,result,created_at FROM strategy_action_logs WHERE user_id=? ORDER BY created_at DESC LIMIT 500",
        (user["id"],),
    )
    risk = db.get_risk_logs(500, user["id"])
    can_view_system = False
    if user.get("is_admin"):
        try:
            role = AdminService(db).role_for(int(user["id"]))
            can_view_system = AdminService.has_permission(role, "system")
        except PermissionError:
            pass
    labels = [
        ":material/history: 量化时间线",
        ":material/order_approve: 订单",
        ":material/query_stats: 策略",
        ":material/security: 用户",
        ":material/shield: 风控",
    ]
    if can_view_system:
        labels.append(":material/terminal: 系统")
    tabs = st.tabs(labels)
    quant_tab, order_tab, strategy_tab, security_tab, risk_tab = tabs[:5]
    with quant_tab:
        if not can(plan, "signal_web"):
            st.info("量化操作时间线从标准版开放。", icon=":material/lock:")
        else:
            journal = QuantJournal(db)
            events = journal.list_events(os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system"))
            section_label("统一量化时间线", f"{len(events)} 个不可变事件 · 修正与撤销均追加记录")
            _show(
                _quant_rows(events, journal, include_options=can(plan, "option_chain")),
                query,
                "尚未接收到经过验证的量化操作记录。",
            )
    with order_tab:
        section_label("订单记录", f"{len(orders)} 条")
        _show(pd.DataFrame(orders), query, "没有符合条件的订单记录。")
    with strategy_tab:
        section_label("策略操作", f"{len(strategy_actions)} 条")
        _show(pd.DataFrame(strategy_actions), query, "没有符合条件的策略操作。")
    with security_tab:
        section_label("用户与安全", f"{len(actions)} 条")
        _show(pd.DataFrame(actions), query, "没有符合条件的用户操作。")
    with risk_tab:
        section_label("风控判断", f"{len(risk)} 条")
        _show(pd.DataFrame(risk), query, "没有符合条件的风控事件。")
    if can_view_system:
        system = db.get_system_events(500)
        with tabs[5]:
            section_label("系统事件", f"{len(system)} 条")
            _show(pd.DataFrame(system), query, "尚无系统事件。")
