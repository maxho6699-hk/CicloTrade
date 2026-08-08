# -*- coding: utf-8 -*-
"""Eight-strategy research workspace."""

from __future__ import annotations

import html
from datetime import datetime
from core.compat import UTC
import os

import plotly.graph_objects as go
import pandas as pd
import streamlit as st

from core.plans import can, effective_plan
from core.quant_journal import QuantJournal
from core.database import get_database
from core.strategy_registry import StrategyRegistry
from core.strategy_scoring import StrategyScorer
from data.datasource import get_resilient_data_source
from payment.order_service import OrderService
from strategies.signal_generator import generate_signal
from ui.components import metric_grid, page_heading, section_label
from ui.data import MYSTIC_REFERENCES, STRATEGIES, breakeven_points, strategy_curve


def _render_system_performance() -> None:
    section_label("系统组合表现", "独立策略账本 · 不是客户资金或收益保证")
    currency = st.segmented_control(
        "组合币种", ["USD", "CNY"], default="USD", key="strategy_performance_currency", required=True
    ) or "USD"
    try:
        performance = QuantJournal().performance_windows(
            os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system"), currency
        )
    except (RuntimeError, ValueError):
        st.error("系统策略表现暂时无法读取。", icon=":material/database_off:")
        return
    if not performance:
        st.info(
            "策略服务完成第一笔收盘净值快照后，这里会自动显示连续业绩；系统不会补造历史收益。",
            icon=":material/monitoring:",
        )
        return
    current = performance["current"]
    pnl = float(current["total_pnl"])
    metric_grid(
        (
            ("系统总权益", f"{currency} {float(current['total_equity']):,.2f}", f"基准 {currency} {float(current['initial_cash']):,.2f}", ""),
            ("累计盈亏", f"{currency} {pnl:+,.2f}", "已实现 + 浮动", "positive" if pnl >= 0 else "negative"),
            ("已实现盈亏", f"{currency} {float(current['realized_pnl']):+,.2f}", "已平仓结果", ""),
            ("浮动盈亏", f"{currency} {float(current['unrealized_pnl']):+,.2f}", "当前未平仓", ""),
        )
    )
    rows = []
    for label, window in performance["windows"].items():
        rows.append(
            {
                "周期": label,
                "期间盈亏": float(window["pnl"]) if window["available"] else None,
                "期间收益率": window.get("return") if window["available"] else None,
                "数据状态": "完整" if window["available"] else "历史不足",
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "期间盈亏": st.column_config.NumberColumn(format=f"{currency} %+.2f"),
            "期间收益率": st.column_config.NumberColumn(format="percent"),
        },
    )
    captured = pd.Timestamp(current["captured_at"])
    if captured.tzinfo is None:
        captured = captured.tz_localize("UTC")
    st.caption(
        f"最后快照 {captured.tz_convert('Asia/Taipei').strftime('%Y-%m-%d %H:%M')}（台北） · "
        f"{performance['snapshot_count']} 个不可变净值快照。"
    )


def _cards(items: list[dict], selected: str) -> str:
    cards = []
    for item in items:
        active = " selected" if item["name"] == selected else ""
        stars = "★" * item["difficulty"] + "☆" * (3 - item["difficulty"])
        cards.append(
            f'<article class="strategy-card accent-{item["accent"]}{active}">'
            f'<header><span class="strategy-code">{html.escape(item["category"])} / {html.escape(item["legs"])}</span>'
            f'<span class="stars" aria-label="难度 {item["difficulty"]} 星">{stars}</span></header>'
            f'<h3>{html.escape(item["name"])}</h3><p>{html.escape(item["scenario"])}</p><dl>'
            f'<div><dt>最大亏损</dt><dd>{html.escape(item["max_loss"])}</dd></div>'
            f'<div><dt>最大盈利</dt><dd>{html.escape(item["max_profit"])}</dd></div>'
            f'</dl></article>'
        )
    return '<section class="strategy-grid" aria-label="策略目录">' + "".join(cards) + "</section>"


def render(config: dict | None = None) -> None:
    del config
    page_heading(
        "OPTIONS / RESEARCH",
        "策略研究",
        "先选市场观点，再调整行权价、到期日与仓位。图表展示到期损益，不触发真实订单。",
        "8 STRATEGIES · EDUCATION MODEL",
    )

    plan = effective_plan(st.session_state.user)
    registry = StrategyRegistry()
    registry.sync_catalog()
    available_definitions = registry.list_for_plan(plan, family="option")
    available_keys = {item["key"] for item in available_definitions}
    ranking = [row for row in StrategyScorer().latest() if row.get("strategy_key") in available_keys]
    section_label(
        "次日 Top 3" if can(plan, "strategy_all") else "基础策略评分",
        "美股 + A 股歷史 K 線 · 每日收盤後五維評分",
    )
    if ranking:
        top = ranking[:3]
        columns = st.columns(len(top), gap="small")
        for column, item in zip(columns, top, strict=True):
            with column.container(border=True):
                st.caption(f"TOP {item['rank_position']} · {item['family'].upper()} · {item['risk_level'].upper()}")
                st.subheader(item["name"])
                st.metric("綜合評分", f"{float(item['weighted_score']):.1f} / 100")
                st.caption(item["scenario"])
        with st.expander("查看全部五維評分與生命週期", icon=":material/leaderboard:"):
            frame = pd.DataFrame(
                [
                    {
                        "排名": row["rank_position"], "策略": row["name"], "收益率": row["total_return"],
                        "最大回撤": row["max_drawdown"], "夏普": row["sharpe_ratio"],
                        "盈虧比": row["profit_loss_ratio"], "連續虧損": row["consecutive_losses"],
                        "總分": row["weighted_score"], "狀態": row["lifecycle_status"],
                    }
                    for row in ranking
                ]
            )
            st.dataframe(
                frame, hide_index=True, width="stretch",
                column_config={
                    "收益率": st.column_config.NumberColumn(format="percent"),
                    "最大回撤": st.column_config.NumberColumn(format="percent"),
                    "夏普": st.column_config.NumberColumn(format="%.2f"),
                    "盈虧比": st.column_config.NumberColumn(format="%.2f"),
                    "總分": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                },
            )
        st.caption(f"評估日期 {ranking[0]['eval_date']} · 30 日末位進入觀察池，60 日末位僅標記待淘汰，必須由管理員確認。")
    else:
        st.info(
            "每日评分等待下一次收盘评估。系统任务会自动运行；当前仍可查看策略目录、系统组合与损益实验室。",
            icon=":material/schedule:",
        )

    _render_system_performance()

    section_label("选择市场观点", "筛选后选择策略查看完整分析")
    available_names = {item["name"] for item in available_definitions}
    catalog = [item for item in STRATEGIES if item["name"] in available_names]
    if not catalog:
        st.warning("目前沒有可用的啟用策略；請稍後再試或聯絡管理員。", icon=":material/lock:")
        return

    category = st.segmented_control(
        "策略方向",
        ["全部", "看涨", "看跌", "看平", "看波动"],
        default="全部",
        key="strategy_category",
        width="stretch",
        required=True,
    )
    filtered = [item for item in catalog if category == "全部" or item["category"] == category]
    if not filtered:
        st.warning("当前方案没有此分类的可用策略。", icon=":material/lock:")
        return
    names = [item["name"] for item in filtered]
    if st.session_state.get("selected_strategy") not in names:
        st.session_state.selected_strategy = names[0]
    selected = st.selectbox("研究策略", names, key="selected_strategy")
    if st.session_state.get("last_strategy_detail") != selected:
        OrderService().log_core_action(st.session_state.user["id"], selected, "STRATEGY_DETAIL", {})
        st.session_state.last_strategy_detail = selected
    st.html(_cards(filtered, selected))

    strategy = next(item for item in catalog if item["name"] == selected)
    section_label("损益实验室", "所有价格均为教学模型，不是期权报价")
    controls, chart = st.columns([.72, 1.8], gap="small")
    with controls:
        with st.container(border=True):
            st.markdown("**模型参数**")
            strike_shift = st.slider("行权价偏移", -20, 20, 0, 1, format="%d%%")
            dte = st.slider("到期天数（DTE）", 30, 75, 45, 1)
            quantity = st.number_input("合约数量（张）", 1, 20, 1, 1)
            st.caption("研究范围：30–75 DTE · 到期损益 · 每张合约按 100 股")

            signal = (
                f"策略：{strategy['name']}\n"
                f"市场观点：{strategy['category']}\n"
                f"行权价偏移：{strike_shift:+d}%\n"
                f"到期天数：{dte} DTE\n"
                f"合约数量：{quantity} 张\n"
                "状态：研究摘要，不是交易指令\n"
                "风险提示：仅供研究参考"
            )
            st.code(signal, language="text")
            if st.button("生成可复制信号", icon=":material/content_copy:"):
                try:
                    closes, _ = get_resilient_data_source().history(("AAPL",), period="3mo")
                    generated = generate_signal(strategy["name"], "AAPL", closes["AAPL"])
                    st.session_state.generated_signal = (
                        f"策略：{generated['strategy']}\n标的：{generated['symbol']}\n方向：{generated['direction']}\n"
                        f"入场参考：{generated['entry']:.2f}\n止损参考：{generated['stop']:.2f}\n目标参考：{generated['target']:.2f}\n"
                        f"{generated['disclaimer']}"
                    )
                    OrderService().log_core_action(st.session_state.user["id"], strategy["name"], "SIGNAL_COPY", generated)
                except Exception as exc:
                    st.error(f"信号生成失败：{exc}")
            if generated_signal := st.session_state.get("generated_signal"):
                st.code(generated_signal, language="text")
            st.caption("代码块右上角复制按钮可复制研究摘要。")

    if not can(plan, "payoff"):
        st.warning("交互式损益图与参数实验室从标准版开放；免费版仍可查看基础策略与生成研究摘要。", icon=":material/lock:")
        return

    frame = strategy_curve(strategy, strike_shift, dte, int(quantity))
    points = breakeven_points(frame)
    max_profit = float(frame["预计损益"].max())
    max_loss = float(frame["预计损益"].min())
    max_profit_price = float(frame.loc[frame["预计损益"].idxmax(), "标的价格"])
    max_loss_price = float(frame.loc[frame["预计损益"].idxmin(), "标的价格"])
    with chart:
        with st.container(border=True):
            figure = go.Figure(
                go.Scatter(
                    x=frame["标的价格"],
                    y=frame["预计损益"],
                    mode="lines",
                    line={"color": "#37d996", "width": 2.5},
                    hovertemplate="标的价格 %{x:.2f}<br>到期损益 USD %{y:,.2f}<extra></extra>",
                )
            )
            figure.add_hline(y=0, line_dash="dot", line_color="#5e696c")
            figure.add_vline(x=100, line_dash="dot", line_color="#2fb9e8", annotation_text="当前标的价")
            for point in points:
                figure.add_vline(x=point, line_dash="dash", line_color="#eab25b", annotation_text=f"盈亏平衡 {point:.2f}")
            figure.update_layout(
                height=470,
                margin={"l": 8, "r": 8, "t": 38, "b": 8},
                title={"text": f"{strategy['name']} · 到期损益", "font": {"size": 14}},
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="#090b0d",
                font={"color": "#8d999b", "family": "IBM Plex Mono"},
                hovermode="x",
                showlegend=False,
                xaxis={"title": "到期时标的价格（USD）", "gridcolor": "#1b2327", "zeroline": False},
                yaxis={"title": "预计损益（USD）", "gridcolor": "#1b2327", "zeroline": False},
            )
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False, "scrollZoom": False})
            breakeven_summary = "、".join(f"{value:.2f}" for value in points) or "图示区间外"
            st.markdown(
                f"**图表摘要：** 标的价格 {max_profit_price:.2f} 时图示盈利最高，为 USD {max_profit:,.2f}；"
                f"标的价格 {max_loss_price:.2f} 时图示亏损最大，为 USD {max_loss:,.2f}；"
                f"盈亏平衡点为 {breakeven_summary}。"
            )
            payoff_details = st.expander(
                "查看期权损益数据", icon=":material/table_chart:", on_change="rerun"
            )
            if payoff_details.open:
                with payoff_details:
                    st.dataframe(
                        frame[["标的价格", "预计损益"]],
                        hide_index=True,
                        width="stretch",
                        column_config={
                            "标的价格": st.column_config.NumberColumn(format="USD %.2f"),
                            "预计损益": st.column_config.NumberColumn(format="USD %+.2f"),
                        },
                    )

    section_label("模型摘要", "图示价格区间内的结果")
    metric_grid(
        (
            ("图示最大盈利", f"USD {max_profit:,.0f}", "不代表理论上限", "positive"),
            ("图示最大亏损", f"USD {max_loss:,.0f}", "到期损益模型", "negative"),
            ("盈亏平衡点", " / ".join(f"{value:.2f}" for value in points) or "区间外", "估算值", ""),
            ("结构难度", f"{strategy['difficulty']} / 3", strategy["legs"], ""),
        )
    )

    with st.expander("构建步骤与风险检查", icon=":material/checklist:"):
        st.markdown(
            "1. 确认市场观点与可接受的最大亏损\n"
            "2. 选择行权价与 30–75 DTE 到期范围\n"
            "3. 核对流动性、买卖价差、事件风险与盈亏平衡点\n"
            "4. 设定仓位上限和退出条件后，再由人工决定是否交易"
        )

    with st.expander("玄学参考 · 纯娱乐", icon=":material/auto_awesome:"):
        if can(plan, "mystic"):
            index = sum(ord(char) for char in strategy["name"]) % len(MYSTIC_REFERENCES)
            dimensions = (
                ("节气与市场", "按当前节气分组复核历史波动；样本不足时不输出百分比结论。"),
                ("生肖与板块", "仅用于传统文化叙事，不改变行业、估值与量价评分。"),
                ("星象相位", "作为事件日历标签记录，不能替代消息面与波动率数据。"),
                ("八字择时", "未填写出生资料时不做个人化判断；即使填写也只作娱乐表达。"),
                ("塔罗 / 卦象", MYSTIC_REFERENCES[index]),
            )
            for dimension, text in dimensions:
                st.markdown(f"**{dimension}**  \n{text}")
            if st.button("记录本次娱乐参考", icon=":material/bookmark_add:"):
                now = datetime.now(UTC).isoformat(timespec="seconds")
                get_database().execute(
                    "INSERT INTO mystic_observations (user_id,symbol,dimension,prompt,issued_at) VALUES (?,?,?,?,?)",
                    (st.session_state.user["id"], "AAPL", "five_dimensions", MYSTIC_REFERENCES[index], now),
                )
                st.success("已记录；系统可在 3 个交易日后回填实际表现并统计命中率。")
            st.caption("玄学参考功能基于历史数据与传统文化的统计分析，纯属娱乐参考，不构成任何形式的投资预测或建议。")
        else:
            st.warning("当前方案未启用该娱乐功能。", icon=":material/lock:")
