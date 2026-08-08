# -*- coding: utf-8 -*-
"""Public Tiger PAPER and user-scoped A-share virtual portfolio."""

from __future__ import annotations

from datetime import datetime
import os
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.database import get_database
from data.datasource import public_market_status
from trading.tiger_api import TigerAPI
from ui.components import metric_grid, page_heading, section_label
from ui.data import (
    PAPER_STARTING_CASH,
    MarketDataUnavailable,
    load_market_history,
    paper_account_from_trades,
    portfolio_snapshot,
)


LOCAL_ZONE = ZoneInfo("Asia/Taipei")


@st.cache_data(ttl=60, max_entries=4, show_spinner=False)
def _tiger_paper_snapshot() -> dict:
    return TigerAPI().paper_snapshot()


@st.cache_data(ttl=60, max_entries=12, show_spinner=False)
def _market_data(symbols: tuple[str, ...], source_name: str):
    return load_market_history(symbols, source_name)


def _market_trades(user_id: int, market: str) -> list[dict]:
    rows = get_database().fetch_all(
        """SELECT t.trade_time,t.symbol,t.side,t.quantity,t.price,t.commission,
                  o.account_mode,o.strategy_name
           FROM trades t JOIN orders o ON o.order_id=t.order_id
           WHERE o.reason=? AND o.account_mode='paper' ORDER BY t.trade_time""",
        (f"user={user_id}",),
    )
    if market == "A股":
        return [row for row in rows if str(row["symbol"]).isdigit() and len(str(row["symbol"])) == 6]
    return [row for row in rows if not (str(row["symbol"]).isdigit() and len(str(row["symbol"])) == 6)]


def _local_snapshot(user_id: int, market: str) -> dict:
    currency = "CNY" if market == "A股" else "USD"
    trades = _market_trades(user_id, market)
    paper_positions, cash = paper_account_from_trades(trades, PAPER_STARTING_CASH[currency])
    defaults = ("000001", "600519", "300750") if market == "A股" else ("AAPL", "MSFT", "NVDA")
    symbols = tuple(position["symbol"] for position in paper_positions) or defaults
    source_name = "yfinance" if market == "A股" else os.getenv("DATA_SOURCE", "yfinance")
    closes, _, updated_at = _market_data(symbols, source_name)
    status = public_market_status(source_name, market)
    account, positions = portfolio_snapshot(closes, paper_positions, cash, str(status["display_source"]))
    account.update(currency=currency, total_assets=account["assets"], market_value=account["positions_value"])
    return {
        "account": account,
        "positions": [
            {
                "instrument_type": "stock",
                "symbol": row["标的"],
                "currency": currency,
                "quantity": row["数量"],
                "average_cost": row["成本价"],
                "market_price": row["最新价"],
                "market_value": row["市值"],
                "unrealized_pnl": row["浮动盈亏"],
                "today_pnl": row["日涨跌"] * row["市值"],
            }
            for row in positions.to_dict("records")
        ],
        "orders": [
            {
                "time": row["trade_time"],
                "instrument_type": "stock",
                "symbol": row["symbol"],
                "action": row["side"],
                "quantity": row["quantity"],
                "filled": row["quantity"],
                "avg_fill_price": row["price"],
                "commission": row["commission"],
                "status": "FILLED",
            }
            for row in reversed(trades[-100:])
        ],
        "updated_at": updated_at,
        "label": (
            "CicloTrade A 股虚拟组合 · 个人模拟账本"
            if market == "A股"
            else "CicloTrade 美股虚拟组合 · 本地开发回退"
        ),
        "source": str(status["display_source"]),
        "freshness": str(status["freshness"]),
    }


def _tiger_snapshot() -> dict:
    snapshot = _tiger_paper_snapshot()
    snapshot.update(
        updated_at=datetime.now(LOCAL_ZONE),
        label="CicloTrade Tiger PAPER · 公开模拟组合",
        source="美国券商模拟账户",
        freshness="60 秒刷新",
    )
    return snapshot


def _position_frame(rows: list[dict], instrument_type: str) -> pd.DataFrame:
    result = []
    for row in rows:
        if row["instrument_type"] != instrument_type:
            continue
        label = row["symbol"]
        if instrument_type == "option":
            right = "Call" if str(row.get("right", "")).upper() == "CALL" else "Put"
            label = f"{label} {row.get('expiry') or '--'} {row.get('strike') or '--'} {right}"
        result.append(
            {
                "标的 / 合约": label,
                "数量": row["quantity"],
                "成本价": row["average_cost"],
                "最新价": row["market_price"],
                "市值": row["market_value"],
                "浮动盈亏": row["unrealized_pnl"],
                "今日盈亏": row["today_pnl"],
            }
        )
    return pd.DataFrame(result)


def _render_positions(snapshot: dict, instrument_type: str) -> None:
    label = "正股持仓" if instrument_type == "stock" else "期权持仓"
    frame = _position_frame(snapshot["positions"], instrument_type)
    section_label(label, "模拟账户当前仓位 · 价格和盈亏来自账户最近快照")
    if frame.empty:
        st.info(
            f"当前没有{label}。" + ("期权个人下单通道尚未开放，可先在今日行动查看结构研究。" if instrument_type == "option" else "下一步可前往今日行动查看等待或模拟验证方案。"),
            icon=":material/inventory_2:",
        )
        return
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "数量": st.column_config.NumberColumn(format="%.4f"),
            "成本价": st.column_config.NumberColumn(format="%.2f"),
            "最新价": st.column_config.NumberColumn(format="%.2f"),
            "市值": st.column_config.NumberColumn(format="%,.2f"),
            "浮动盈亏": st.column_config.NumberColumn(format="%+.2f"),
            "今日盈亏": st.column_config.NumberColumn(format="%+.2f"),
        },
    )


def _render_allocation(snapshot: dict) -> None:
    account = snapshot["account"]
    rows = [
        {"资产": row["symbol"], "市值": max(0.0, float(row["market_value"]))}
        for row in snapshot["positions"]
        if float(row["market_value"]) > 0
    ]
    rows.append({"资产": "现金", "市值": max(0.0, float(account.get("cash", account.get("available", 0))))})
    frame = pd.DataFrame(rows)
    section_label("资产分析", "只分析当前账户快照，不混入系统策略组合")
    if frame["市值"].sum() <= 0:
        st.info("当前账户尚无可分析资产。", icon=":material/donut_large:")
        return
    chart_col, data_col = st.columns([1.15, 1], gap="small")
    with chart_col.container(border=True):
        figure = go.Figure(
            go.Pie(
                labels=frame["资产"],
                values=frame["市值"],
                hole=.68,
                marker={"colors": ["#67d9ad", "#58bfe8", "#eab25b", "#f05c67", "#879298"]},
                textinfo="label+percent",
                hovertemplate="%{label}<br>%{value:,.2f}<br>%{percent}<extra></extra>",
            )
        )
        figure.update_layout(
            height=320,
            margin={"l": 8, "r": 8, "t": 8, "b": 8},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#aab3b5"},
            showlegend=False,
        )
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    with data_col.container(border=True):
        total = float(frame["市值"].sum())
        frame["占比"] = frame["市值"] / total
        st.dataframe(
            frame.sort_values("市值", ascending=False),
            hide_index=True,
            width="stretch",
            column_config={
                "市值": st.column_config.NumberColumn(format="%,.2f"),
                "占比": st.column_config.NumberColumn(format="percent"),
            },
        )
        usage = float(account.get("market_value", 0)) / float(account.get("total_assets", 0) or 1)
        st.metric("资金使用率", f"{usage:.1%}", "持仓市值 / 总资产", border=True)


def _render_orders(snapshot: dict) -> None:
    section_label("交易记录", "按时间倒序 · 模拟成交与订单状态保持可追溯")
    if not snapshot["orders"]:
        st.info("当前没有交易记录。", icon=":material/receipt_long:")
        return
    frame = pd.DataFrame(snapshot["orders"])
    frame = frame[["time", "instrument_type", "symbol", "action", "quantity", "filled", "avg_fill_price", "commission", "status"]]
    frame.columns = ["时间", "类型", "标的", "方向", "数量", "已成交", "成交均价", "佣金", "状态"]
    st.dataframe(frame, hide_index=True, width="stretch")


def render(config: dict | None = None) -> None:
    del config
    page_heading(
        "PORTFOLIO / POSITIONS",
        "目前仓位分析",
        "集中查看账户资产、正股、期权和交易记录；系统策略表现已与客户账户完全分开。",
        "ACCOUNT FIRST · NO MIXED BALANCES",
    )
    market = st.segmented_control(
        "账户",
        ["公开组合 · Tiger PAPER", "我的美股模拟", "我的 A 股模拟"],
        default="公开组合 · Tiger PAPER",
        key="dashboard_market",
        width="stretch",
        required=True,
    ) or "公开组合 · Tiger PAPER"
    is_public = market.startswith("公开组合")
    is_us = market != "我的 A 股模拟"
    tiger = TigerAPI()
    try:
        if is_public:
            if not tiger.configured:
                raise RuntimeError("Tiger PAPER 尚未在当前环境配置；可切换到“我的美股模拟”继续验证。")
            with st.spinner("正在同步 Tiger PAPER 账户…", show_time=True):
                snapshot = _tiger_snapshot()
        else:
            with st.spinner("正在重建虚拟组合…", show_time=True):
                snapshot = _local_snapshot(int(st.session_state.user["id"]), "美股" if is_us else "A股")
    except (RuntimeError, MarketDataUnavailable, ValueError) as exc:
        st.error(f"账户快照暂时不可用：{exc}", icon=":material/cloud_off:")
        st.caption("系统不会用另一套账户或固定演示资产冒充当前组合。")
        if st.button("重新同步", icon=":material/refresh:"):
            _tiger_paper_snapshot.clear()
            _market_data.clear()
            st.rerun()
        return

    account = snapshot["account"]
    currency = str(account.get("currency") or ("USD" if is_us else "CNY"))
    updated = pd.Timestamp(snapshot["updated_at"])
    if updated.tzinfo is None:
        updated = updated.tz_localize("UTC")
    updated = updated.tz_convert("Asia/Taipei")
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        st.badge(snapshot["label"], icon=":material/account_balance_wallet:", color="blue")
        st.badge(snapshot["freshness"], icon=":material/schedule:", color="gray")
    st.caption(f"{snapshot['source']} · 最后同步 {updated.strftime('%Y-%m-%d %H:%M:%S')}（台北）")
    total = float(account.get("total_assets", 0))
    today = float(account.get("today_pnl", account.get("daily_pnl", 0)) or 0)
    unrealized = float(account.get("unrealized_pnl", account.get("unrealized", 0)) or 0)
    metric_grid(
        (
            ("总资产", f"{currency} {total:,.2f}", "账户最近快照", ""),
            ("可用资金", f"{currency} {float(account.get('available', 0)):,.2f}", "可用于模拟交易", ""),
            ("今日盈亏", f"{currency} {today:+,.2f}", "按账户最近快照", "positive" if today >= 0 else "negative"),
            ("浮动盈亏", f"{currency} {unrealized:+,.2f}", "未平仓仓位", "positive" if unrealized >= 0 else "negative"),
        )
    )
    view = st.segmented_control(
        "查看内容",
        ["资产分析", "正股持仓", "期权持仓", "交易记录"],
        default="正股持仓",
        key=f"dashboard_view_{'US' if is_us else 'CN'}",
        width="stretch",
        required=True,
    ) or "正股持仓"
    if view == "资产分析":
        _render_allocation(snapshot)
    elif view == "正股持仓":
        _render_positions(snapshot, "stock")
    elif view == "期权持仓":
        _render_positions(snapshot, "option")
    else:
        _render_orders(snapshot)
    st.caption(
        "Tiger PAPER 为平台公开模拟组合，不是你的个人券商资产；A 股虚拟组合是本站模拟账本。"
        "所有数据用于研究与流程验证，不代表收益保证。"
    )
