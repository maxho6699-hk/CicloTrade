# -*- coding: utf-8 -*-
"""Beginner-first recommendations backed by the immutable quant journal."""

from __future__ import annotations

from datetime import datetime
import math
import os
import re
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from core.plans import can, effective_plan
from core.quant_journal import QuantJournal
from core.strategy_registry import StrategyRegistry
from core.database import get_database
from core.user_settings import load_user_settings
from notification.telegram_bot import telegram_configured, verified_user_target
from ui.components import page_heading, section_label
from ui.quant_format import contract_label, strategy_version_label
from ui.recommendations import load_recommendations


LOCAL_ZONE = ZoneInfo("Asia/Taipei")
MARKET_CODES = {"美股": "US", "A股": "CN"}
MARKET_CURRENCIES = {"美股": "USD", "A股": "CNY"}


def _system_ledger_key() -> str:
    return os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")


def _local_time(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("Asia/Taipei")


def _number(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(number):
        return "--"
    text = f"{number:,.{digits}f}"
    return text.rstrip("0").rstrip(".") if digits else text


def _money(value: Any, currency: str) -> str:
    number = _number(value, 2)
    return "--" if number == "--" else f"{currency} {number}"


def position_size(account_budget: float, entry: float, stop: float) -> tuple[int, float]:
    """Use 1% fixed risk with a 10% single-position cap; always round down."""
    values = (float(account_budget), float(entry), float(stop))
    if not all(math.isfinite(value) and value > 0 for value in values):
        return 0, 0.0
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return 0, 0.0
    by_risk = math.floor(account_budget * 0.01 / risk_per_share)
    by_concentration = math.floor(account_budget * 0.10 / entry)
    shares = max(0, min(by_risk, by_concentration))
    return shares, shares * risk_per_share


def _queue_page(page: str, payload_key: str | None = None, payload: dict[str, Any] | None = None) -> None:
    if payload_key:
        st.session_state[payload_key] = payload or {}
    st.session_state["pending_page"] = page


def _contract_label(leg: dict[str, Any]) -> str:
    return contract_label(leg)


def _event_status(event: dict[str, Any]) -> str:
    if event.get("event_type") == "reversal":
        return "撤销事件"
    if not event.get("active"):
        return "已被后续事件替代"
    if event.get("event_type") == "correction":
        return "有效更正"
    return "有效"


def _action_label(event: dict[str, Any], leg: dict[str, Any]) -> str:
    delta = float(leg.get("quantity_delta") or 0)
    target = float(leg.get("target_quantity") or 0)
    if delta > 0:
        action = "买入 / 增持"
    elif target < 0:
        action = "卖出 / 建立空头"
    elif delta < 0:
        action = "卖出 / 减持"
    else:
        action = "仓位不变"
    if event.get("event_type") == "correction":
        return f"更正 · {action}"
    if event.get("event_type") == "reversal":
        return f"撤销 · {action}"
    return action


def _execution_records(
    journal: QuantJournal,
    events: list[dict[str, Any]],
    market: str,
) -> list[dict[str, Any]]:
    """Resolve real position deltas, including corrections and reversals."""
    records: list[dict[str, Any]] = []
    market_code = MARKET_CODES[market]
    for event in events:
        try:
            legs = journal.execution_legs(int(event["id"]))
        except (KeyError, TypeError, ValueError, RuntimeError):
            legs = event.get("legs") or []
        for leg in legs:
            if leg.get("market") == market_code:
                records.append({"event": event, "leg": leg})
    return records


def _timeline_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in reversed(records):
        event = record["event"]
        leg = record["leg"]
        rows.append(
            {
                "时间": _local_time(event["occurred_at"]),
                "状态": _event_status(event),
                "标的 / 合约": _contract_label(leg),
                "动作": _action_label(event, leg),
                "数量变化": float(leg.get("quantity_delta") or 0),
                "目标仓位": float(leg.get("target_quantity") or 0),
                "记录价": leg.get("price"),
                "币种": leg.get("currency") or "--",
                "策略": event.get("strategy_name") or "--",
                "版本": strategy_version_label(event.get("strategy_version")),
                "事件 ID": event.get("id"),
                "来源": "策略服务",
            }
        )
    return pd.DataFrame(rows)


def _position_frame(replay: dict[str, Any], market: str) -> pd.DataFrame:
    rows = []
    market_code = MARKET_CODES[market]
    for position in replay.get("positions", {}).values():
        if position.get("market") != market_code:
            continue
        rows.append(
            {
                "标的 / 合约": _contract_label(position),
                "数量": float(position.get("quantity") or 0),
                "平均成本": position.get("average_cost"),
                "账本标记价": position.get("mark_price"),
                "持仓价值": position.get("market_value"),
                "浮动盈亏": position.get("unrealized_pnl"),
                "币种": position.get("currency") or "--",
            }
        )
    return pd.DataFrame(rows)


def _safe_reason(event: dict[str, Any]) -> str | None:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("reason") or metadata.get("rationale")
    return str(value)[:500] if value else None


def _normalize_symbol(value: str, market: str) -> str | None:
    symbol = value.strip().upper()
    if market == "A股":
        symbol = re.sub(r"\.(SS|SZ)$", "", symbol)
        return symbol if re.fullmatch(r"\d{6}", symbol) else None
    return symbol if re.fullmatch(r"[A-Z][A-Z0-9.=-]{0,14}", symbol) else None


def _render_symbol_control(market: str) -> tuple[str, ...]:
    key = f"recommendations_extra_symbols_{market}"
    extras = tuple(st.session_state.get(key, ()))
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.caption(
            f"当前已追加 {len(extras)} 个自选标的" if extras else "可加入基础股票池以外的标的"
        )
        with st.popover("添加股票", icon=":material/add_chart:"):
            with st.form(f"recommendations_add_symbol_{market}", border=False):
                value = st.text_input(
                    "股票代码",
                    placeholder="例如 PLTR…" if market == "美股" else "例如 688981…",
                    max_chars=16,
                    autocomplete="off",
                )
                submitted = st.form_submit_button(
                    "加入本页",
                    type="primary",
                    icon=":material/add:",
                    width="stretch",
                )
            if submitted:
                symbol = _normalize_symbol(value, market)
                if not symbol:
                    st.error("请输入符合当前市场格式的股票代码。", icon=":material/error:")
                elif symbol in extras:
                    st.info("该股票已经在本页。", icon=":material/info:")
                elif len(extras) >= 12:
                    st.warning("单次最多追加 12 个股票，以免行情请求过慢。", icon=":material/speed:")
                else:
                    st.session_state[key] = (*extras, symbol)
                    st.rerun()
            if extras and st.button(
                "清除追加股票",
                icon=":material/delete_sweep:",
                width="stretch",
                key=f"recommendations_clear_{market}",
            ):
                st.session_state[key] = ()
                st.session_state.pop(f"recommendations_symbol_{market}", None)
                st.rerun()
    return extras


def _telegram_status(user: dict[str, Any], event: str, capability: str, instrument_type: str) -> tuple[str, str]:
    if not can(effective_plan(user), capability):
        return "套餐未包含", "gray"
    settings = load_user_settings(int(user["id"]))
    target = verified_user_target(settings)
    if not telegram_configured("1"):
        return "通知服务维护中", "orange"
    if not target:
        return "平台可用，待绑定", "blue"
    events = settings.get("tg_events") if isinstance(settings.get("tg_events"), dict) else {}
    if events.get(event) is not True:
        return "已绑定，事件未开启", "gray"
    latest = get_database().fetch_one(
        """SELECT status,sent_at,updated_at FROM quant_event_deliveries
           WHERE user_id=? AND instrument_type=? ORDER BY id DESC LIMIT 1""",
        (int(user["id"]), instrument_type),
    )
    if latest and latest["status"] == "failed":
        return "推送异常，系统重试中", "orange"
    if latest and latest["status"] == "sent":
        sent = _local_time(latest["sent_at"]).strftime("%m-%d %H:%M")
        return f"推送中 · 最近 {sent}", "green"
    return "推送中 · 等待新事件", "green"


def _render_access_status(user: dict[str, Any], plan: str) -> None:
    stock_status, stock_color = _telegram_status(user, "stock_signal", "tg_stock_signal", "stock")
    option_status, option_color = _telegram_status(user, "option_signal", "tg_option_signal", "option")
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.badge(plan, icon=":material/workspace_premium:", color="primary")
        st.badge(
            "网页正式操作已开启" if can(plan, "signal_web") else "网页正式操作未开启",
            icon=":material/check_circle:" if can(plan, "signal_web") else ":material/lock:",
            color="green" if can(plan, "signal_web") else "gray",
        )
        st.badge(
            f"正股 Telegram · {stock_status}",
            icon=":material/notifications_active:" if stock_color == "green" else ":material/notifications_off:",
            color=stock_color,
        )
        st.badge(
            f"期权 Telegram · {option_status}",
            icon=":material/notifications_active:" if option_color == "green" else ":material/notifications_off:",
            color=option_color,
        )


def _render_latest_action(records: list[dict[str, Any]], plan: str, market: str) -> None:
    section_label("正式操作", "不可变账本 · 更正与撤销不会覆盖历史")
    if not can(plan, "signal_web"):
        st.info(
            "正式量化操作与完整时间线从标准版开放。当前仍可查看下方量价研究候选。",
            icon=":material/lock:",
        )
        return
    if not records:
        with st.container(border=True):
            with st.container(horizontal=True, gap="small", vertical_alignment="center"):
                st.badge("当前动作", icon=":material/pause_circle:", color="blue")
                st.badge("等待", color="gray")
            st.subheader(f"{market} · 暂不新增仓位", anchor=False)
            st.write("当前没有通过策略交叉验证和风险闸门的新动作。等待本身也是系统决策，不需要用户配置任何任务。")
            st.caption("系统会在收盘后自动复核，并在盘中数据或已验证事件触发时更新；下方研究候选仅用于观察，不冒充正式买卖。")
            st.button(
                "查看市场行情",
                icon=":material/candlestick_chart:",
                width="stretch",
                on_click=_queue_page,
                args=("terminal", "market_prefill", {"market": market}),
            )
        return

    latest = records[-1]
    event = latest["event"]
    leg = latest["leg"]
    occurred = _local_time(event["occurred_at"])
    is_today = occurred.date() == datetime.now(LOCAL_ZONE).date()
    if is_today:
        st.success("今天已有经过账本验证的正式操作。", icon=":material/verified:")
    else:
        st.info(
            f"今天暂无新的正式操作；下方保留最近一笔记录（{occurred.strftime('%m-%d %H:%M')}）。",
            icon=":material/history:",
        )

    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.badge(_event_status(event), color="green" if event.get("active") else "orange")
        st.badge("正股" if leg.get("instrument_type") == "stock" else "期权", color="blue")
        st.badge(str(event.get("strategy_name") or "未命名策略"), color="gray")
    st.subheader(f"{_contract_label(leg)} · {_action_label(event, leg)}", anchor=False)
    st.caption(
        f"策略 {event.get('strategy_name', '--')} / {strategy_version_label(event.get('strategy_version'))} · "
        f"{occurred.strftime('%Y-%m-%d %H:%M:%S')}（台北）"
    )
    columns = st.columns(4, gap="small")
    columns[0].metric("账本记录价", _money(leg.get("price"), leg.get("currency") or MARKET_CURRENCIES[market]), border=True)
    columns[1].metric("数量变化", _number(leg.get("quantity_delta"), 4), border=True)
    columns[2].metric("目标仓位", _number(leg.get("target_quantity"), 4), border=True)
    columns[3].metric("事件编号", f"#{event.get('id', '--')}", border=True)
    st.caption("记录价来自策略事件，不代表当前可成交价；执行前必须核对实时行情、账户资金和风险限制。")

    quantity_delta = float(leg.get("quantity_delta") or 0)
    price = float(leg.get("price") or 0)
    if leg.get("instrument_type") == "stock" and quantity_delta and price > 0:
        st.button(
            "用模拟盘验证这笔操作",
            type="primary",
            icon=":material/order_approve:",
            width="stretch",
            on_click=_queue_page,
            args=(
                "trading",
                "trade_prefill",
                {
                    "market": market,
                    "symbol": leg.get("symbol"),
                    "side": "BUY" if quantity_delta > 0 else "SELL",
                    "quantity": max(1, int(abs(quantity_delta))),
                    "price": price,
                    "strategy": event.get("strategy_name") or "量化事件",
                },
            ),
        )
    elif leg.get("instrument_type") == "option":
        st.caption("期权事件已记录，但个人期权订单通道尚未开放；请先复核真实合约、权利金与流动性。")

    reason = _safe_reason(event)
    with st.expander("为什么出现这笔操作", icon=":material/account_tree:"):
        st.markdown(reason or "策略事件没有附带可公开的决策说明。")
        st.caption(
            f"事件来源：策略服务 · 外部编号 {event.get('external_event_id', '--')} · "
            f"事件类型 {event.get('event_type', '--')}"
        )


def _render_research(frame: pd.DataFrame, market: str) -> pd.Series | None:
    section_label("量价研究候选", "6 个月日线量价 · 最长 5 分钟缓存 · 非正式操作")
    if frame.empty:
        st.info(
            "当前没有满足数据完整度要求的研究候选。可稍后重试，或通过“添加股票”加入其他标的。",
            icon=":material/search_off:",
        )
        return None

    options = frame["标的"].astype(str).tolist()
    selected_symbol = st.pills(
        "选择股票",
        options,
        default=options[0],
        format_func=lambda value: (
            f"{value} · {int(frame.loc[frame['标的'].astype(str) == value, '评分'].iloc[0]):+d}"
        ),
        key=f"recommendations_symbol_{market}",
        width="stretch",
    )
    if selected_symbol is None:
        selected_symbol = options[0]
    selected = frame.loc[frame["标的"].astype(str) == selected_symbol].iloc[0]
    score = int(selected["评分"])
    tone = "green" if score >= 30 else "red" if score <= -30 else "gray"
    currency = str(selected.get("货币") or MARKET_CURRENCIES[market])

    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.badge("研究候选", icon=":material/science:", color="blue")
        st.badge(str(selected["观点"]), color=tone)
        st.badge(f"量价评分 {score:+d}", color=tone)
    st.subheader(str(selected["标的"]), anchor=False)
    columns = st.columns(4, gap="small")
    columns[0].metric("最新观察价", _money(selected["最新价"], currency), border=True)
    columns[1].metric("正式入场价", "未生成", "等待账本事件", border=True)
    columns[2].metric("止损参考", _money(selected["止损参考"], currency), "研究阈值", border=True)
    columns[3].metric("目标参考", _money(selected["目标参考"], currency), "不是收益保证", border=True)
    score = int(selected["评分"])
    action = "观察买入" if score >= 30 else "回避 / 减仓" if score <= -30 else "等待"
    st.markdown(f"**现在怎么做：** {action}。{selected['正股建议']}")
    if score >= 30:
        budget = st.number_input(
            f"本次模拟预算（{currency}）",
            min_value=1_000.0,
            max_value=10_000_000.0,
            value=10_000.0,
            step=1_000.0,
            key=f"recommendations_budget_{market}",
            help="这里只用于模拟仓位计算，不读取或改变你的券商资金。",
        )
        shares, risk_amount = position_size(
            float(budget), float(selected["最新价"]), float(selected["止损参考"])
        )
        st.markdown(
            f"**模拟数量：** {shares:,} 股 · 止损触发预计风险约 {currency} {risk_amount:,.2f}。"
            "按预算 1% 风险并限制单一标的不超过预算 10%，取更小值。"
        )
        if shares > 0:
            st.button(
                "用模拟盘验证",
                type="primary",
                icon=":material/order_approve:",
                width="stretch",
                on_click=_queue_page,
                args=(
                    "trading",
                    "trade_prefill",
                    {
                        "market": market,
                        "symbol": str(selected["标的"]),
                        "side": "BUY",
                        "quantity": shares,
                        "price": float(selected["最新价"]),
                        "strategy": "量价研究候选",
                    },
                ),
            )
    elif score <= -30:
        st.button(
            "查看目前仓位",
            type="primary",
            icon=":material/account_balance_wallet:",
            width="stretch",
            on_click=_queue_page,
            args=("dashboard",),
        )
    else:
        st.button(
            "查看市场行情",
            type="primary",
            icon=":material/candlestick_chart:",
            width="stretch",
            on_click=_queue_page,
            args=("terminal", "market_prefill", {"market": market, "symbol": str(selected["标的"])}),
        )

    with st.expander("查看评分证据与全部候选", icon=":material/analytics:"):
        st.markdown(f"**当前证据：** {selected['依据']}")
        st.caption("评分用于筛选观察顺序；它没有真实账户状态、订单回报或当前买卖盘，不能独立触发交易。")
        columns_to_show = [
            "标的", "最新价", "货币", "评分", "观点", "正股建议", "止损参考", "目标参考", "依据",
        ]
        st.dataframe(
            frame[[column for column in columns_to_show if column in frame]],
            hide_index=True,
            width="stretch",
            column_config={
                "最新价": st.column_config.NumberColumn(format="%.2f"),
                "止损参考": st.column_config.NumberColumn(format="%.2f"),
                "目标参考": st.column_config.NumberColumn(format="%.2f"),
                "评分": st.column_config.ProgressColumn(min_value=-100, max_value=100, format="%d"),
            },
        )
    return selected


def _render_option_candidate(
    selected: pd.Series | None,
    plan: str,
    market: str,
    active_option_names: set[str],
) -> None:
    section_label("期权结构", "先判断结构，再用真实期权链核对合约、报价与流动性")
    if selected is None:
        st.info("选择到有效股票候选后，系统才会生成期权结构研究。", icon=":material/hourglass_empty:")
        return
    if not can(plan, "option_chain"):
        st.info(
            "期权结构、真实期权链、报价 K 线、Greeks、IV 与组合研究仅对专业版开放。",
            icon=":material/lock:",
        )
        return

    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.badge("结构研究候选", icon=":material/schema:", color="violet")
        st.badge("不是可执行合约", icon=":material/warning:", color="orange")
    st.subheader(str(selected["期权策略"]), anchor=False)
    option_strategy = str(selected["期权策略"])
    if market == "A股" and option_strategy == "暂不买期权":
        st.warning(str(selected["期权建议"]), icon=":material/block:")
    elif option_strategy not in active_option_names:
        st.warning(
            f"研究结果指向“{option_strategy}”，但该策略目前未启用；系统不会展示或执行已停用结构。",
            icon=":material/lock:",
        )
    else:
        st.markdown(f"**结构草案：** {selected['期权建议']}")
        columns = st.columns(3, gap="small")
        columns[0].metric("到期范围", f"{int(selected['DTE'])}–{int(selected['DTE']) + 15} DTE", border=True)
        columns[1].metric("行权价偏移", f"{int(selected['行权价偏移']):+d}%", border=True)
        columns[2].metric("真实期权链", "待复核", "报价、价差、成交量与 OI", border=True)
        st.warning(
            "这是结构研究候选，执行前必须复核真实合约代码、到期日、买卖价差、成交量、未平仓量和即时权利金。",
            icon=":material/fact_check:",
        )
        st.info(
            "止盈与止损不是固定等到价格触发。系统会按最新数据持续复核，可能提前建议止盈或退出；"
            "请密切关注本站策略时间线及与你会员等级对应的 Telegram 推送。",
            icon=":material/notifications_active:",
        )
    st.caption(
        "当前页面不会把估算行权价伪装成可成交订单；只有量化账本写入经过验证的期权事件后，才会显示正式数量和记录价。"
    )


def _render_timeline(
    records: list[dict[str, Any]],
    plan: str,
    market: str,
    ledger_error: Exception | None = None,
) -> None:
    section_label("量化交易日志", "严格按时间追加 · 原记录、更正与撤销全部保留")
    if not can(plan, "signal_web"):
        st.info("量化交易日志从标准版开放。", icon=":material/lock:")
        return
    if ledger_error:
        st.error(
            "量化账本当前无法读取，因此不能确认交易日志是否为空。请先恢复账本连接后再查看。",
            icon=":material/database_off:",
        )
        return
    timeline = _timeline_frame(records)
    if timeline.empty:
        st.info(
            f"{market}目前暂无已执行操作。系统会持续监测，出现买卖后自动记录并推送；无需手动设置。",
            icon=":material/history:",
        )
        return
    st.dataframe(
        timeline,
        hide_index=True,
        width="stretch",
        column_order=(
            "时间", "状态", "标的 / 合约", "动作", "数量变化", "目标仓位", "记录价", "币种", "策略", "版本",
        ),
        column_config={
            "时间": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
            "数量变化": st.column_config.NumberColumn(format="%.4f"),
            "目标仓位": st.column_config.NumberColumn(format="%.4f"),
            "记录价": st.column_config.NumberColumn(format="%.4f"),
        },
    )
    with st.expander("审计字段", icon=":material/receipt_long:"):
        st.dataframe(
            timeline[["时间", "事件 ID", "状态", "来源", "策略", "版本", "标的 / 合约"]],
            hide_index=True,
            width="stretch",
            column_config={"时间": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss")},
        )
        st.caption("事件 ID 与来源用于追踪策略服务器写入记录；已失效事件仍保留，避免历史被重写。")


def render() -> None:
    user = st.session_state.get("user") or {}
    plan = effective_plan(user)
    registry = StrategyRegistry()
    registry.sync_catalog()
    active_option_names = {
        item["name"] for item in registry.list_for_plan(plan, family="option")
    }
    page_heading(
        "DECISION / VERIFIED ACTIONS",
        "今日行动",
        "先回答现在做什么、买多少、何时退出；正式事件与研究候选始终明确区分。",
        "ONE NEXT STEP · AUDITABLE HISTORY",
    )
    market = st.segmented_control(
        "市场",
        ["美股", "A股"],
        default="美股",
        key="recommendations_market",
        width="stretch",
        required=True,
    ) or "美股"
    _render_access_status(user, plan)
    extras = _render_symbol_control(market)

    journal: QuantJournal | None = None
    records: list[dict[str, Any]] = []
    ledger_error: Exception | None = None
    if can(plan, "signal_web"):
        try:
            journal = QuantJournal()
            events = journal.list_events(_system_ledger_key())
            records = _execution_records(journal, events, market)
        except Exception as exc:
            ledger_error = exc

    if ledger_error:
        section_label("正式操作", "不可变账本")
        st.error(
            "量化账本暂时无法读取。研究候选仍可继续查看，但不会被标记为正式操作；系统会自动重试。",
            icon=":material/database_off:",
        )
    else:
        _render_latest_action(records, plan, market)

    try:
        with st.spinner("正在读取量价数据并更新研究候选…", show_time=True):
            frame = load_recommendations(market, extras)
    except Exception as exc:
        section_label("量价研究候选", "真实数据读取失败")
        st.error(
            f"研究候选暂时不可用：{str(exc).rstrip('。.!！')}。系统没有使用演示数据填补结果。",
            icon=":material/cloud_off:",
        )
        selected = None
    else:
        selected = _render_research(frame, market)

    _render_option_candidate(selected, plan, market, active_option_names)
    _render_timeline(records, plan, market, ledger_error)
    st.caption(
        "风险披露：所有内容均为量化研究与系统记录，不构成持牌个别投资建议。期权可能损失全部权利金；行情、税费与滑点会影响结果。"
    )
