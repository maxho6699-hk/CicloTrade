# -*- coding: utf-8 -*-
"""Beginner-first market actions with a persisted quant history."""

from __future__ import annotations

from datetime import datetime
import math
import os
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from core.plans import can, effective_plan
from core.quant_journal import QuantJournal
from ui.components import page_heading, section_label
from ui.recommendations import load_recommendations, render_recommendations


def _system_ledger_key() -> str:
    return os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")


_ACTION_COLUMNS = (
    "市场", "标的", "最新价", "货币", "评分", "观点", "正股建议",
    "期权建议", "止损参考", "目标参考", "依据",
)
_PROFESSIONAL_COLUMNS = (
    *_ACTION_COLUMNS[:7], "期权策略", "期权建议", "止损参考", "目标参考", "依据",
)


def _safe_action_frame(frame: pd.DataFrame, plan: str) -> pd.DataFrame:
    """Keep only fields needed by the beginner action card."""
    safe = frame.loc[:, [column for column in _ACTION_COLUMNS if column in frame]].copy()
    strategy = frame.get("期权策略", pd.Series("", index=frame.index)).astype(str)
    if can(plan, "option_chain"):
        safe["期权建议"] = frame.get("期权建议", "暂无期权结构建议")
    elif can(plan, "strategy_all"):
        safe["期权建议"] = strategy.map(lambda value: f"{value} · 升级后查看合约参数")
    else:
        safe["期权建议"] = "当前方案不展示期权结构"
    return safe


def _professional_frame(frame: pd.DataFrame, plan: str) -> pd.DataFrame:
    """Return the allow-listed professional payload, never hidden columns."""
    columns = [column for column in _PROFESSIONAL_COLUMNS if column in frame]
    if not can(plan, "option_chain"):
        columns = [column for column in columns if column not in {"期权策略", "期权建议"}]
    return frame.loc[:, columns].copy()


def _position_reference(row: pd.Series, account_value: float = 100_000) -> tuple[int, str]:
    """Size a stock reference at 1% risk and 20% maximum notional."""
    score = int(row["评分"])
    if score < 30:
        return 0, "当前不新增正股仓位"
    price = float(row["最新价"])
    stop = float(row["止损参考"])
    risk_per_share = abs(price - stop)
    if not math.isfinite(risk_per_share) or risk_per_share <= 0 or price <= 0:
        return 0, "风险距离无效，暂不计算数量"
    by_risk = int(account_value * 0.01 // risk_per_share)
    by_notional = int(account_value * 0.20 // price)
    quantity = max(0, min(by_risk, by_notional))
    return quantity, "按账户 1% 风险、单标的不超过 20% 计算"


def _history_rows(
    events: list[dict],
    journal: QuantJournal | None = None,
    *,
    include_options: bool = True,
) -> pd.DataFrame:
    ledger = journal or QuantJournal()
    rows: list[dict] = []
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
            delta = float(leg.get("quantity_delta", 0)) if leg else 0.0
            if hidden_options and leg is None:
                action = "期权事件（升级后查看）"
                contract = "期权事件（升级后查看）"
            else:
                action = "买入 / 增持" if delta > 0 else "卖出 / 减持" if delta < 0 else "撤销 / 更正"
                if event.get("event_type") == "reversal":
                    action = f"撤销 · {action}"
                elif event.get("event_type") == "correction":
                    action = f"更正 · {action}"
                contract = leg["instrument_key"] if leg else "--"
            occurred = pd.Timestamp(event["occurred_at"])
            if occurred.tzinfo is None:
                occurred = occurred.tz_localize("UTC")
            rows.append(
                {
                    "时间": occurred.tz_convert("Asia/Taipei"),
                    "状态": (
                        "撤销事件" if event.get("event_type") == "reversal"
                        else "更正事件" if event.get("event_type") == "correction"
                        else "有效" if event.get("active") else "已更正 / 撤销"
                    ),
                    "策略": event["strategy_name"],
                    "标的 / 合约": contract,
                    "动作": action,
                    "数量变化": delta if leg else None,
                    "目标仓位": leg.get("target_quantity") if leg else None,
                    "记录价": leg.get("price") if leg else None,
                    "币种": leg.get("currency", "--") if leg else "--",
                }
            )
    return pd.DataFrame(rows)


def _render_actions(frame: pd.DataFrame, market: str, plan: str = "专业版") -> None:
    frame = _safe_action_frame(frame, plan)
    if frame.empty:
        st.info("当前没有满足数据完整度要求的研究候选。", icon=":material/hourglass_empty:")
        return

    strongest = frame.iloc[0]
    positive_count = int((frame["评分"] >= 30).sum())
    avoid_count = int((frame["评分"] <= -30).sum())
    summary = st.columns(4, gap="small")
    summary[0].metric("优先查看", str(strongest["标的"]), f"评分 {int(strongest['评分']):+d}", border=True)
    summary[1].metric("偏多候选", positive_count, "评分至少 +30", border=True)
    summary[2].metric("回避 / 减仓", avoid_count, "评分不高于 -30", border=True)
    summary[3].metric("覆盖标的", len(frame), market, border=True)

    symbol = st.selectbox(
        "选择要查看的股票",
        frame["标的"].tolist(),
        format_func=lambda value: (
            f"{value} · {frame.loc[frame['标的'] == value, '观点'].iloc[0]} · "
            f"{int(frame.loc[frame['标的'] == value, '评分'].iloc[0]):+d} 分"
        ),
        key=f"action_symbol_{market}",
    )
    selected = frame.loc[frame["标的"] == symbol].iloc[0]
    render_recommendations(frame.loc[frame["标的"] == symbol], limit=1)

    quantity, quantity_note = _position_reference(selected)
    score = int(selected["评分"])
    model_action = "等待回调确认" if score >= 30 else "回避 / 减仓" if score <= -30 else "暂不操作"
    currency = str(selected.get("货币", "USD"))
    with st.container(border=True):
        st.subheader("一张行动单", anchor=False)
        st.caption("模型研究候选 · 不会自动下单 · 下单前仍需核对实时成交价与个人风险")
        details = st.columns(4, gap="small")
        details[0].metric("模型动作", model_action, str(selected["观点"]), border=True)
        details[1].metric("价格参考", f"{currency} {float(selected['最新价']):,.2f}", "页面最后一笔行情", border=True)
        details[2].metric("止损参考", f"{currency} {float(selected['止损参考']):,.2f}", "触及后重新评估", border=True)
        details[3].metric("目标参考", f"{currency} {float(selected['目标参考']):,.2f}", "不是收益保证", border=True)
        st.markdown(f"**正股怎么做：** {selected['正股建议']}")
        st.markdown(
            f"**数量参考：** {quantity:,} 股。{quantity_note}。"
            if quantity
            else f"**数量参考：** 0 股。{quantity_note}。"
        )
        st.markdown(f"**期权怎么做：** {selected['期权建议']}")
        st.caption(
            "期权权利金、买卖价差、真实合约代码与可买数量必须等待授权期权链返回；"
            "在数据缺失时系统不会伪造可成交报价。"
        )
        st.markdown(f"**依据：** {selected['依据']}")


def _render_professional(frame: pd.DataFrame, plan: str = "免费版") -> None:
    if not can(plan, "reports"):
        st.info("专业研究明细从专业版开放。", icon=":material/lock:")
        return
    frame = _professional_frame(frame, plan)
    section_label("专业研究明细", "仅发送衍生结论到浏览器，不发送 API 密钥、原始策略权重或供应商完整载荷")
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "最新价": st.column_config.NumberColumn(format="%.2f"),
            "止损参考": st.column_config.NumberColumn(format="%.2f"),
            "目标参考": st.column_config.NumberColumn(format="%.2f"),
            "评分": st.column_config.ProgressColumn(min_value=-100, max_value=100, format="%d"),
        },
    )
    with st.expander("数据与模型保护边界", icon=":material/encrypted:"):
        st.markdown(
            "- 浏览器只接收当前会员可见的行情摘要、衍生评分和行动结果。\n"
            "- Polygon / Yahoo 等供应商原始响应、API 密钥、策略参数与组合权重只留在服务器。\n"
            "- 已显示的数据无法阻止用户阅读或截图，因此敏感数据必须在发送到前端前删除，而不是只靠隐藏列。\n"
            "- 正式上线前需按数据商合同确认展示、缓存、历史留存、期权链和再分发权限。"
        )


def _render_history(plan: str) -> None:
    if not can(plan, "signal_web"):
        st.info("量化交易时间线从标准版开放；免费版仍可查看当前研究候选。", icon=":material/lock:")
        return
    journal = QuantJournal()
    events = journal.list_events(_system_ledger_key())
    section_label("量化交易历史", f"{len(events)} 个不可变事件 · 更正与撤销也会保留")
    frame = _history_rows(events, journal, include_options=can(plan, "option_chain"))
    if frame.empty:
        st.info(
            "尚未接收到经过验证的量化操作。策略服务器推送第一笔正式事件后，记录会按时间线永久追加。",
            icon=":material/history:",
        )
        return
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "时间": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
            "记录价": st.column_config.NumberColumn(format="%.4f"),
            "数量变化": st.column_config.NumberColumn(format="%.4f"),
            "目标仓位": st.column_config.NumberColumn(format="%.4f"),
        },
    )


def render() -> None:
    plan = effective_plan(st.session_state.user)
    page_heading(
        "DECISION / ACTION CENTER",
        "行动建议",
        "先回答现在看什么、怎么做、风险在哪里；需要时再展开专业数据与完整量化历史。",
        "DERIVED SIGNALS · SERVER-SIDE MODEL",
    )
    market = st.segmented_control(
        "市场",
        ["美股", "A股"],
        default="美股",
        key="actions_market",
        width="stretch",
    )
    view = st.segmented_control(
        "查看内容",
        ["现在怎么做", "专业数据", "交易历史"],
        default="现在怎么做",
        key="actions_view",
        width="stretch",
    )
    st.caption(
        f"页面计算时间 {datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d %H:%M:%S')}（台北） · "
        "信号是量化研究输出，不是持牌个别投资建议。"
    )

    if view == "交易历史":
        _render_history(plan)
        return
    try:
        with st.spinner("正在读取行情并生成研究候选…", show_time=True):
            frame = load_recommendations(market)
    except Exception as exc:
        st.error(f"行动建议暂时不可用：{exc}", icon=":material/cloud_off:")
        return
    if view == "专业数据":
        _render_professional(frame, plan)
    else:
        _render_actions(frame, market, plan)
