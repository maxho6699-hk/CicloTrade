# -*- coding: utf-8 -*-
"""Live-market paper portfolio overview."""

from __future__ import annotations

import os

import plotly.graph_objects as go
import pandas as pd
import streamlit as st

from core.database import get_database
from core.quant_journal import QuantJournal
from core.strategy_registry import StrategyRegistry
from core.user_profiles import UserProfileService
from data.datasource import market_data_status
from ui.components import gauge, market_tape, metric_grid, page_heading, section_label
from ui.data import (
    PAPER_STARTING_CASH,
    MarketDataUnavailable,
    load_market_history,
    market_summary,
    paper_account_from_trades,
    paper_equity_curve_from_trades,
    portfolio_snapshot,
)


@st.cache_data(ttl=60, max_entries=20, show_spinner=False)
def _market_data(symbols: tuple[str, ...], source_name: str = "yfinance"):
    return load_market_history(symbols, source_name)


def _market_trades(user_id: int, market: str) -> list[dict]:
    rows = get_database().fetch_all(
        """SELECT t.trade_time,t.symbol,t.side,t.quantity,t.price,t.commission,o.account_mode,o.strategy_name
           FROM trades t JOIN orders o ON o.order_id=t.order_id
           WHERE o.reason=? AND o.account_mode='paper' ORDER BY t.trade_time""",
        (f"user={user_id}",),
    )
    if market == "A股":
        return [row for row in rows if str(row["symbol"]).isdigit() and len(str(row["symbol"])) == 6]
    return [row for row in rows if not (str(row["symbol"]).isdigit() and len(str(row["symbol"])) == 6)]


def _chart_layout(height: int) -> dict:
    return {
        "height": height,
        "margin": {"l": 8, "r": 8, "t": 18, "b": 8},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "#090b0d",
        "font": {"color": "#8d999b", "family": "IBM Plex Mono"},
        "hovermode": "x unified",
    }


def _render_system_performance(currency: str) -> None:
    section_label("CicloTrade 量化系统组合", f"{currency} · 模拟跟踪 · 与个人账户分开核算")
    try:
        performance = QuantJournal().performance_windows(
            os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system"), currency
        )
    except (RuntimeError, ValueError) as exc:
        st.error(f"系统组合账本暂时不可用：{exc}", icon=":material/database_off:")
        return
    if performance is None:
        st.info(
            "尚未接收到经过验证的系统净值快照。接入策略服务器后才会显示真实连续模拟业绩。",
            icon=":material/monitoring:",
        )
        return
    current = performance["current"]
    total_pnl = float(current["total_pnl"])
    metric_grid(
        (
            ("系统总权益", f"{currency} {float(current['total_equity']):,.2f}", f"基准 {currency} {float(current['initial_cash']):,.2f}", ""),
            ("累计盈亏", f"{currency} {total_pnl:+,.2f}", "已实现 + 浮动", "positive" if total_pnl >= 0 else "negative"),
            ("当前现金", f"{currency} {float(current['cash']):,.2f}", "系统模型账户", ""),
            ("持仓市值", f"{currency} {float(current['market_value']):,.2f}", "最后验证快照", ""),
        )
    )
    rows = []
    for label, window in performance["windows"].items():
        rows.append(
            {
                "周期": label,
                "期间盈亏": f"{currency} {float(window['pnl']):+,.2f}" if window["available"] else "数据不足",
                "期间收益率": f"{float(window['return']):+.2%}" if window.get("return") is not None else "--",
                "比较基准": pd.Timestamp(window["baseline_at"]).tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M") if window["available"] else "--",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    captured = pd.Timestamp(current["captured_at"]).tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M HKT")
    st.caption(
        f"最后快照 {captured} · 来源 {current['source']} · {performance['snapshot_count']} 个永久快照。"
        "这是系统模拟组合记录，不是客户实盘收益或收益保证。"
    )


def render(config: dict | None = None) -> None:
    del config
    page_heading(
        "PORTFOLIO / OVERVIEW",
        "账户总览",
        "系统量化组合与当前用户模拟账户分开核算；没有正式记录时保持空状态。",
        "AUDITABLE MODEL · USER-SCOPED PAPER ACCOUNT",
    )

    profile_service = UserProfileService()
    profile = profile_service.get(int(st.session_state.user["id"]))
    registry = StrategyRegistry()
    registry.sync_catalog()
    matched = profile_service.matching_strategies(int(st.session_state.user["id"]), registry.list(), 3)
    section_label("為你匹配的策略", "僅依平台內部回測行為，每週更新，不對外共享")
    st.caption("研究偏好：" + " · ".join(profile["tags"]))
    match_columns = st.columns(len(matched), gap="small")
    for column, item in zip(match_columns, matched, strict=True):
        with column.container(border=True):
            st.caption(f"{item['family'].upper()} · {item['risk'].upper()}")
            st.subheader(item["name"])
            st.write(item["scenario"])
            st.caption("匹配推薦不是買賣指令；請先完成歷史回測與風險檢查。")

    market = st.segmented_control("模拟账户", ["美股", "A股"], default="美股", key="dashboard_market", width="stretch")
    currency = "USD" if market == "美股" else "CNY"
    _render_system_performance(currency)
    starting_cash = PAPER_STARTING_CASH[currency]
    trades = _market_trades(int(st.session_state.user["id"]), market)
    paper_positions, available_cash = paper_account_from_trades(trades, starting_cash)
    default_symbols = ("AAPL", "MSFT", "NVDA") if market == "美股" else ("000001", "600519", "300750")
    symbols = tuple(position["symbol"] for position in paper_positions) or default_symbols

    try:
        source_name = "yfinance" if market == "A股" else os.getenv("DATA_SOURCE", "yfinance")
        with st.spinner("正在连接市场数据…", show_time=True):
            closes, volumes, updated_at = _market_data(symbols, source_name)
    except MarketDataUnavailable as exc:
        st.session_state.market_live = False
        st.error(f"真实行情暂时不可用：{exc}", icon=":material/cloud_off:")
        st.caption("系统不会用固定演示价格冒充实时行情。请恢复网络或数据源后重试。")
        if st.button("重新连接", icon=":material/refresh:"):
            _market_data.clear()
            st.rerun()
        return

    st.session_state.market_live = True
    data_status = market_data_status(source_name)
    account, positions = portfolio_snapshot(closes, paper_positions, available_cash, str(data_status["source"]))
    market_tape(market_summary(closes), updated_at, str(data_status["source"]))

    section_label("组合快照", f"{currency} · {data_status['source']} · {data_status['freshness']}")
    daily_tone = "positive" if account["daily_pnl"] >= 0 else "negative"
    pnl_sign = "+" if account["daily_pnl"] >= 0 else ""
    unrealized_sign = "+" if account["unrealized"] >= 0 else ""
    metric_grid(
        (
            ("组合总资产", f"{currency} {account['assets']:,.2f}", "模拟现金 + 当前账户持仓", ""),
            ("当日变化", f"{pnl_sign}{currency} {account['daily_pnl']:,.2f}", "按最近两个交易日估算", daily_tone),
            ("可用现金", f"{currency} {account['available']:,.2f}", "当前模拟账户余额", ""),
            ("未实现盈亏", f"{unrealized_sign}{currency} {account['unrealized']:,.2f}", "相对账户平均成本", "positive" if account["unrealized"] >= 0 else "negative"),
        )
    )

    section_label("组合走势", "最近 60 个交易日 · 可悬停查看")
    main_col, allocation_col = st.columns([1.8, 1], gap="small")
    with main_col:
        with st.container(border=True):
            curve = paper_equity_curve_from_trades(closes, trades, starting_cash)
            figure = go.Figure(
                go.Scatter(
                    x=curve["日期"],
                    y=curve["账户净值"],
                    mode="lines",
                    line={"color": "#37d996", "width": 2},
                    fill="tozeroy",
                    fillcolor="rgba(55,217,150,.06)",
                    hovertemplate=f"%{{x|%Y-%m-%d}}<br>组合净值 {currency} %{{y:,.2f}}<extra></extra>",
                )
            )
            figure.update_layout(**_chart_layout(370))
            figure.update_xaxes(showgrid=False, zeroline=False)
            figure.update_yaxes(gridcolor="#1b2327", zeroline=False, tickprefix=f"{currency} ")
            st.plotly_chart(figure, width="stretch", config={"displayModeBar": False, "scrollZoom": False})
            start_value = float(curve["账户净值"].iloc[0])
            end_value = float(curve["账户净值"].iloc[-1])
            change = end_value / start_value - 1 if start_value else 0.0
            st.markdown(
                f"**图表摘要：** 期初 {currency} {start_value:,.2f}，期末 {currency} {end_value:,.2f}，"
                f"区间变化 {change:+.2%}；最低 {currency} {curve['账户净值'].min():,.2f}，"
                f"最高 {currency} {curve['账户净值'].max():,.2f}。"
            )
            curve_details = st.expander(
                "查看组合净值数据", icon=":material/table_chart:", on_change="rerun"
            )
            if curve_details.open:
                with curve_details:
                    st.dataframe(
                        curve,
                        hide_index=True,
                        width="stretch",
                        column_config={
                            "账户净值": st.column_config.NumberColumn(format=f"{currency} %,.2f")
                        },
                    )
    with allocation_col:
        with st.container(border=True):
            allocation_frame = pd.DataFrame(
                {
                    "资产": [*positions["标的"].tolist(), "现金"],
                    "市值": [*positions["市值"].tolist(), account["available"]],
                }
            )
            allocation_total = float(allocation_frame["市值"].sum())
            allocation_frame["占比"] = (
                allocation_frame["市值"] / allocation_total if allocation_total else 0.0
            )
            allocation = go.Figure(
                go.Pie(
                    labels=allocation_frame["资产"],
                    values=allocation_frame["市值"],
                    hole=.68,
                    marker={"colors": ["#37d996", "#2fb9e8", "#eab25b", "#f05c67", "#273034"], "line": {"color": "#111518", "width": 2}},
                    textinfo="label+percent",
                    textfont={"size": 11},
                    hovertemplate=f"%{{label}}<br>{currency} %{{value:,.2f}}<br>%{{percent}}<extra></extra>",
                )
            )
            allocation.update_layout(**_chart_layout(370), showlegend=False, annotations=[{"text": "资产配置", "showarrow": False, "font": {"color": "#f1f4f2", "size": 13}}])
            st.plotly_chart(allocation, width="stretch", config={"displayModeBar": False})
            largest = allocation_frame.loc[allocation_frame["占比"].idxmax()]
            cash_weight = float(allocation_frame.loc[allocation_frame["资产"] == "现金", "占比"].iloc[0])
            st.markdown(
                f"**图表摘要：** 最大配置为 {largest['资产']}，占 {float(largest['占比']):.1%}；"
                f"现金占 {cash_weight:.1%}。"
            )
            allocation_details = st.expander(
                "查看资产配置数据", icon=":material/table_chart:", on_change="rerun"
            )
            if allocation_details.open:
                with allocation_details:
                    st.dataframe(
                        allocation_frame,
                        hide_index=True,
                        width="stretch",
                        column_config={
                            "市值": st.column_config.NumberColumn(format=f"{currency} %,.2f"),
                            "占比": st.column_config.NumberColumn(format="percent"),
                        },
                    )

    section_label("风险概览", "颜色用于快速识别，数字提供准确判断")
    gauge_columns = st.columns(3, gap="small")
    with gauge_columns[0]:
        gauge("资金使用率", account["usage_pct"], "持仓市值占组合总资产", "#2fb9e8")
    with gauge_columns[1]:
        gauge("盈利持仓", account["winning_pct"], "按模拟成本价计算", "#37d996")
    with gauge_columns[2]:
        risk_budget = min(account["usage_pct"] / 80 * 100, 100)
        gauge("风险预算", risk_budget, "80% 为当前组合上限", "#eab25b" if risk_budget < 90 else "#f05c67")

    section_label("当前持仓", f"价格真实 · {currency} 模拟账户 · 当前用户成交重建")
    if positions.empty:
        st.info(f"当前 {market} 模拟账户没有持仓。可前往“交易执行”提交模拟订单。", icon=":material/account_balance_wallet:")
    else:
        st.dataframe(
            positions,
            hide_index=True,
            width="stretch",
            column_config={
                "数量": st.column_config.NumberColumn(format="%d"),
                "成本价": st.column_config.NumberColumn(format=f"{currency} %.2f"),
                "最新价": st.column_config.NumberColumn(format=f"{currency} %.2f"),
                "日涨跌": st.column_config.NumberColumn(format="percent"),
                "市值": st.column_config.NumberColumn(format=f"{currency} %,.2f"),
                "浮动盈亏": st.column_config.NumberColumn(format=f"{currency} %+.2f"),
            },
        )

    section_label("最近成交", "仅显示当前账户的模拟/实盘记录")
    if trades:
        frame = pd.DataFrame(list(reversed(trades[-50:])))
        frame.columns = ["时间", "标的", "方向", "数量", "价格", "佣金", "账户", "策略"]
        st.dataframe(frame, hide_index=True, width="stretch")
    else:
        st.info(f"当前 {market} 模拟账户尚无成交记录。先去“交易执行”提交一笔模拟订单，账户总览会自动重建持仓。", icon=":material/receipt_long:")
