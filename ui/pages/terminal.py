# -*- coding: utf-8 -*-
"""高密度真实行情终端与分钟 K 线。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import html
import os
import re
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from core.plans import can, effective_plan
from core.quant_journal import QuantJournal
from core.user_settings import load_user_settings, merge_user_settings
from data.datasource import get_data_source, market_data_status
from data.yfinance_adapter import YFinanceAdapter
from ui.components import market_tape, metric_grid, page_heading, section_label
from ui.data import market_summary
from ui.recommendations import A_SHARE_UNIVERSE, US_UNIVERSE, load_recommendations, render_recommendations


INTERVALS = {
    "1 分钟": ("1d", "1m"),
    "5 分钟": ("5d", "5m"),
    "15 分钟": ("5d", "15m"),
    "1 小时": ("1mo", "1h"),
    "日线": ("6mo", "1d"),
}
WATCHLIST = US_UNIVERSE
A_SHARE_WATCHLIST = A_SHARE_UNIVERSE


def _system_ledger_key() -> str:
    return os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")


def _events_for_symbol(
    symbol: str,
    market: str,
    journal: QuantJournal | None = None,
    *,
    include_options: bool = True,
) -> list[dict[str, Any]]:
    """Return execution deltas for a symbol, including superseded history."""
    market_code = "CN" if market == "A股" else "US"
    result: list[dict[str, Any]] = []
    ledger = journal or QuantJournal()
    for event in ledger.list_events(_system_ledger_key()):
        try:
            execution = ledger.execution_legs(int(event["id"]))
        except (KeyError, TypeError, ValueError, RuntimeError, StopIteration):
            continue
        hidden_options = any(
            leg.get("instrument_type") == "option" and not include_options
            for leg in execution
        )
        event_view = {key: value for key, value in event.items() if key not in {"legs", "metadata"}}
        # Event metadata may contain contract terms even when this event's execution is stock-only.
        event_view["metadata"] = event.get("metadata", {}) if include_options else {}
        if hidden_options:
            event_view["_hidden_options"] = True
        for leg in execution:
            if leg.get("instrument_type") == "option" and not include_options:
                continue
            if leg["market"] == market_code and leg["symbol"] == symbol:
                result.append({**event_view, "leg": leg})
    return result


@st.cache_data(ttl=5, max_entries=20, show_spinner=False)
def _bars(symbol: str, period: str, interval: str, source_name: str = "yfinance") -> tuple[pd.DataFrame, datetime]:
    frame = get_data_source(source_name).bars(symbol, period, interval)
    latest = pd.Timestamp(frame.index[-1])
    return frame, latest.to_pydatetime()


@st.cache_data(ttl=30, max_entries=20, show_spinner=False)
def _watchlist(symbols: tuple[str, ...] = WATCHLIST, market: str = "美股", source_name: str = "yfinance") -> pd.DataFrame:
    source = get_data_source("yfinance" if market == "A股" else source_name)
    closes, volumes = source.history(symbols, period="5d")
    rows = []
    for symbol in symbols:
        series = closes[symbol].dropna()
        latest = float(series.iloc[-1])
        previous = float(series.iloc[-2]) if len(series) > 1 else latest
        rows.append(
            {
                "标的": symbol,
                "最新": latest,
                "涨跌": latest / previous - 1 if previous else 0,
                "成交量": int(volumes[symbol].dropna().iloc[-1]),
                "走势": series.tail(5).round(2).tolist(),
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(ttl=600, max_entries=64, show_spinner=False)
def _symbol_search(query: str, market: str) -> list[dict[str, str]]:
    return YFinanceAdapter.search(query, market)


def _canonical_symbol(symbol: str, market: str) -> str:
    value = symbol.strip().upper()
    if market == "A股" and value.endswith((".SS", ".SZ")):
        value = value[:-3]
    return value


def _merge_symbols(*groups: tuple[str, ...], market: str) -> tuple[str, ...]:
    pattern = r"\d{6}" if market == "A股" else r"[A-Z][A-Z0-9=-]{0,14}"
    return tuple(
        dict.fromkeys(
            value
            for group in groups
            for symbol in group
            if re.fullmatch(pattern, value := _canonical_symbol(symbol, market))
        )
    )


def _saved_watchlist(user_id: int, market: str) -> tuple[str, ...]:
    settings = load_user_settings(user_id)
    watchlists = settings.get("watchlists", {})
    values = watchlists.get("a_share" if market == "A股" else "us", ()) if isinstance(watchlists, dict) else ()
    return _merge_symbols(tuple(values) if isinstance(values, (list, tuple)) else (), market=market)


def _save_watchlist(user_id: int, market: str, symbols: tuple[str, ...]) -> None:
    settings = load_user_settings(user_id)
    watchlists = settings.get("watchlists", {})
    watchlists = dict(watchlists) if isinstance(watchlists, dict) else {}
    watchlists["a_share" if market == "A股" else "us"] = list(_merge_symbols(symbols, market=market))
    merge_user_settings(user_id, {"watchlists": watchlists})


def _open_symbol(selector_key: str, active_key: str, symbol: str) -> None:
    st.session_state[active_key] = symbol
    st.session_state[selector_key] = symbol
    st.session_state.terminal_notice = f"已打开 {symbol} K 线。"


def _add_symbol(user_id: int, market: str, selector_key: str, active_key: str, symbol: str) -> None:
    saved = _saved_watchlist(user_id, market)
    _save_watchlist(user_id, market, _merge_symbols(saved, (symbol,), market=market))
    _open_symbol(selector_key, active_key, symbol)
    st.session_state.terminal_notice = f"{symbol} 已加入个人自选并打开 K 线。"


def _remove_symbol(user_id: int, market: str, symbol: str) -> None:
    _save_watchlist(user_id, market, tuple(item for item in _saved_watchlist(user_id, market) if item != symbol))
    st.session_state.terminal_notice = f"{symbol} 已从个人自选移除。"


def _render_symbol_search(user_id: int, market: str, selector_key: str, active_key: str) -> None:
    results_key = f"terminal_search_results_{market}"
    query_key = f"terminal_search_query_{market}"
    with st.container(border=True, key="terminal_symbol_finder"):
        with st.form(f"terminal_search_form_{market}", border=False):
            with st.container(horizontal=True, vertical_alignment="bottom"):
                query = st.text_input(
                    "搜索全市场股票",
                    key=query_key,
                    placeholder="代码或公司名称，例如 PLTR" if market == "美股" else "6 位代码，例如 600519",
                )
                submitted = st.form_submit_button("搜索", icon=":material/search:", type="primary")
        if submitted:
            if not query.strip():
                st.session_state[results_key] = []
                st.warning("请输入股票代码或公司名称。", icon=":material/info:")
            else:
                try:
                    with st.spinner("正在查询 Yahoo Finance 证券目录…"):
                        st.session_state[results_key] = _symbol_search(query, market)
                except Exception as exc:
                    st.session_state[results_key] = []
                    st.error(str(exc), icon=":material/cloud_off:")
                if not st.session_state[results_key]:
                    st.info("没有找到符合当前市场的股票，请检查代码或换一个公司名称。", icon=":material/search_off:")

        results = st.session_state.get(results_key, [])
        if results:
            labels = {
                f"{_canonical_symbol(item['symbol'], market)} · {item['name']} · {item['exchange']}": item
                for item in results
            }
            selected = st.selectbox("搜索结果", tuple(labels), key=f"terminal_search_pick_{market}")
            picked = _canonical_symbol(labels[selected]["symbol"], market)
            saved = _saved_watchlist(user_id, market)
            with st.container(horizontal=True):
                st.button(
                    "打开 K 线",
                    icon=":material/candlestick_chart:",
                    on_click=_open_symbol,
                    args=(selector_key, active_key, picked),
                    key=f"terminal_open_{market}_{picked}",
                )
                if picked not in saved:
                    st.button(
                        "加入自选",
                        icon=":material/add:",
                        type="primary",
                        on_click=_add_symbol,
                        args=(user_id, market, selector_key, active_key, picked),
                        key=f"terminal_add_{market}_{picked}",
                    )
                else:
                    st.caption("已在个人自选中")


@st.cache_data(ttl=60, max_entries=4, show_spinner=False)
def _tape_data(market: str = "美股", source_name: str = "yfinance"):
    symbols = ("SPY", "QQQ", "^VIX") if market == "美股" else ("000001.SS", "399001.SZ", "000300.SS")
    source = get_data_source("yfinance" if market == "A股" else source_name)
    closes, _ = source.history(symbols, period="5d")
    return closes, pd.Timestamp(closes.index[-1]).to_pydatetime(), source.name


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["EMA20"] = result["Close"].ewm(span=20, adjust=False).mean()
    result["EMA50"] = result["Close"].ewm(span=50, adjust=False).mean()
    typical = (result["High"] + result["Low"] + result["Close"]) / 3
    volume_sum = result["Volume"].replace(0, np.nan).cumsum()
    result["VWAP"] = (typical * result["Volume"]).cumsum() / volume_sum
    delta = result["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rsi = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    result["RSI"] = rsi.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
    true_range = pd.concat(
        [result["High"] - result["Low"], (result["High"] - result["Close"].shift()).abs(), (result["Low"] - result["Close"].shift()).abs()],
        axis=1,
    ).max(axis=1)
    result["ATR"] = true_range.rolling(14).mean()
    return result


def _candlestick(
    frame: pd.DataFrame,
    symbol: str,
    interval: str,
    revision: int = 0,
    events: list[dict[str, Any]] | None = None,
) -> go.Figure:
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.77, 0.23], vertical_spacing=0.025)
    figure.add_trace(
        go.Candlestick(
            x=frame.index, open=frame["Open"], high=frame["High"], low=frame["Low"], close=frame["Close"],
            increasing_line_color="#37d996", decreasing_line_color="#f05c67", increasing_fillcolor="#37d996", decreasing_fillcolor="#f05c67",
            name=symbol,
        ),
        row=1, col=1,
    )
    figure.add_trace(go.Scatter(x=frame.index, y=frame["EMA20"], line={"color": "#2fb9e8", "width": 1.4}, name="EMA 20"), row=1, col=1)
    figure.add_trace(go.Scatter(x=frame.index, y=frame["EMA50"], line={"color": "#eab25b", "width": 1.2}, name="EMA 50"), row=1, col=1)
    figure.add_trace(go.Scatter(x=frame.index, y=frame["VWAP"], line={"color": "#b89cff", "width": 1, "dash": "dot"}, name="VWAP"), row=1, col=1)
    marker_groups: dict[str, dict[str, Any]] = {
        "stock_buy": {"name": "正股买入", "color": "#67d9ad", "symbol": "triangle-up", "x": [], "y": [], "text": []},
        "stock_sell": {"name": "正股卖出", "color": "#f27883", "symbol": "triangle-down", "x": [], "y": [], "text": []},
        "option_buy": {"name": "期权买入", "color": "#58bfe8", "symbol": "diamond", "x": [], "y": [], "text": []},
        "option_sell": {"name": "期权卖出", "color": "#eab25b", "symbol": "diamond", "x": [], "y": [], "text": []},
        "inactive": {"name": "已更正 / 撤销", "color": "#7f8b92", "symbol": "x", "x": [], "y": [], "text": []},
    }
    for event in events or []:
        leg = event["leg"]
        timestamp = pd.Timestamp(event["occurred_at"])
        if frame.index.tz is None:
            timestamp = timestamp.tz_convert(None) if timestamp.tzinfo else timestamp
        else:
            timestamp = timestamp.tz_localize(frame.index.tz) if timestamp.tzinfo is None else timestamp.tz_convert(frame.index.tz)
        if timestamp < frame.index[0] or timestamp > frame.index[-1]:
            continue
        nearest = frame.index.get_indexer([timestamp], method="nearest")[0]
        is_option = leg["instrument_type"] == "option"
        side = "buy" if float(leg["quantity_delta"]) > 0 else "sell"
        key = "inactive" if not event["active"] else f"{'option' if is_option else 'stock'}_{side}"
        price = leg.get("price")
        y_value = float(frame.iloc[nearest]["Close"]) if is_option or price is None else float(price)
        price_text = f"{float(price):,.2f}" if price is not None else "--"
        contract = html.escape(str(leg["instrument_key"] if is_option else leg["symbol"]))
        strategy = html.escape(str(event["strategy_name"]))
        version = html.escape(str(event["strategy_version"]))
        occurred_at = html.escape(str(event["occurred_at"]))
        marker_groups[key]["x"].append(timestamp)
        marker_groups[key]["y"].append(y_value)
        marker_groups[key]["text"].append(
            f"{marker_groups[key]['name']} · {contract}<br>"
            f"数量 {float(leg['quantity_delta']):+g} · 记录价 {price_text}<br>"
            f"{strategy} · {version}<br>{occurred_at}"
        )
    for group in marker_groups.values():
        if group["x"]:
            figure.add_trace(
                go.Scatter(
                    x=group["x"], y=group["y"], mode="markers", name=group["name"], text=group["text"],
                    hovertemplate="%{text}<extra></extra>",
                    marker={"color": group["color"], "symbol": group["symbol"], "size": 12, "line": {"color": "#060708", "width": 1}},
                ),
                row=1,
                col=1,
            )
    colors = np.where(frame["Close"] >= frame["Open"], "#2a8f69", "#a6424c")
    figure.add_trace(go.Bar(x=frame.index, y=frame["Volume"], marker_color=colors, name="成交量"), row=2, col=1)
    figure.update_layout(
        height=450, margin={"l": 8, "r": 8, "t": 38, "b": 8}, title={"text": f"{symbol} · {interval}", "font": {"size": 13}},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#060708", font={"color": "#94a0a3", "family": "IBM Plex Mono"},
        hovermode="x unified", showlegend=True, legend={"orientation": "h", "y": 1.02, "x": 1, "xanchor": "right"},
        uirevision=f"{symbol}-{interval}-{revision}",
        xaxis_rangeslider_visible=False,
    )
    if interval != "1d":
        figure.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}, {"pattern": "hour", "bounds": [16, 9.5]}])
    else:
        figure.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}])
    figure.update_xaxes(showgrid=False, showspikes=True, spikecolor="#758184", spikethickness=1)
    figure.update_yaxes(gridcolor="#1b2327", zeroline=False, showspikes=True, spikecolor="#758184")
    return figure


def _volume_profile(frame: pd.DataFrame) -> pd.DataFrame:
    if frame["Close"].nunique() < 2:
        return pd.DataFrame({"价格区间": [], "成交量": []})
    bins = pd.cut(frame["Close"], bins=min(16, max(4, int(np.sqrt(len(frame))))), duplicates="drop")
    grouped = frame.groupby(bins, observed=True)["Volume"].sum().sort_index()
    return pd.DataFrame({"价格区间": [f"{item.left:.2f}–{item.right:.2f}" for item in grouped.index], "成交量": grouped.values})


@st.fragment(run_every="5s")
def _live_panel(
    symbol: str,
    period: str,
    interval: str,
    delayed: bool,
    market: str,
    source_name: str,
    watchlist: tuple[str, ...],
    show_signals: bool,
    show_options: bool = True,
) -> None:
    try:
        raw, fetched_at = _bars(symbol, period, interval, source_name)
    except Exception as exc:
        st.session_state.market_live = False
        st.error(f"K 线请求失败：{exc}", icon=":material/cloud_off:")
        return
    st.session_state.market_live = True
    if delayed and interval != "1d":
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=15)
        utc_index = raw.index.tz_convert("UTC") if raw.index.tz is not None else raw.index.tz_localize("UTC")
        raw = raw.loc[utc_index <= cutoff]
    if raw.empty:
        st.warning("当前方案的行情筛选窗口内尚无可显示 K 线。")
        return
    frame = _indicators(raw.tail(260))
    latest = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) > 1 else latest
    change = float(latest["Close"] / previous["Close"] - 1) if previous["Close"] else 0
    session_change = float(latest["Close"] / frame.iloc[0]["Open"] - 1) if frame.iloc[0]["Open"] else 0
    realized_vol = float(frame["Close"].pct_change().std() * np.sqrt(252))
    currency = "CNY" if market == "A股" else "USD"
    metric_grid(
        (
            ("最新价格", f"{currency} {latest['Close']:,.2f}", f"最近一根 {change:+.2%}", "positive" if change >= 0 else "negative"),
            ("区间涨跌", f"{session_change:+.2%}", f"开盘 {frame.iloc[0]['Open']:.2f}", "positive" if session_change >= 0 else "negative"),
            ("区间高 / 低", f"{frame['High'].max():.2f} / {frame['Low'].min():.2f}", f"ATR {latest['ATR']:.2f}" if pd.notna(latest["ATR"]) else "ATR 计算中", ""),
            ("RSI / 波动率", f"{latest['RSI']:.1f} / {realized_vol:.1%}" if pd.notna(latest["RSI"]) else f"-- / {realized_vol:.1%}", "14 周期 / 年化", ""),
        )
    )
    watch_col, chart_col, flow_col = st.columns([0.95, 2.15, 0.9], gap="small")
    with watch_col:
        with st.container(border=True, key="terminal_watchlist"):
            st.markdown("**观察列表**")
            try:
                watch = _watchlist(watchlist, market, os.getenv("DATA_SOURCE", "yfinance"))
                st.dataframe(
                    watch, hide_index=True, width="stretch", height=330,
                    column_config={
                        "最新": st.column_config.NumberColumn(format="%.2f"),
                        "涨跌": st.column_config.NumberColumn(format="percent"),
                        "成交量": st.column_config.NumberColumn(format="compact"),
                        "走势": st.column_config.LineChartColumn("5 日", y_min=None, y_max=None),
                    },
                )
            except Exception as exc:
                st.error(f"观察列表不可用：{exc}")
    with chart_col:
        with st.container(border=True, key="terminal_chart"):
            reset_key = f"chart_revision_{symbol}_{interval}"
            st.session_state.setdefault(reset_key, 0)
            with st.container(horizontal=True, horizontal_alignment="right"):
                if st.button("回到最新 K 线", icon=":material/vertical_align_bottom:", key=f"reset_{symbol}_{interval}"):
                    st.session_state[reset_key] += 1
            st.plotly_chart(
                _candlestick(
                    frame,
                    symbol,
                    interval,
                    st.session_state[reset_key],
                    _events_for_symbol(symbol, market, include_options=show_options) if show_signals else [],
                ),
                width="stretch",
                config={
                    "displayModeBar": True,
                    "displaylogo": False,
                    "scrollZoom": False,
                    "responsive": True,
                    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
                },
            )
            st.caption(
                f"最新 K 线：开 {latest['Open']:.2f} · 高 {latest['High']:.2f} · "
                f"低 {latest['Low']:.2f} · 收 {latest['Close']:.2f} · 成交量 {int(latest['Volume']):,}"
            )
            with st.expander("查看最近 20 根 K 线数据", icon=":material/table_chart:"):
                accessible = frame.tail(20).reset_index()
                accessible.columns = ["时间", *accessible.columns[1:]]
                accessible = accessible.rename(
                    columns={"Open": "开盘", "High": "最高", "Low": "最低", "Close": "收盘", "Volume": "成交量"}
                )
                st.dataframe(
                    accessible[["时间", "开盘", "最高", "最低", "收盘", "成交量"]],
                    hide_index=True,
                    width="stretch",
                )
    with flow_col:
        with st.container(border=True, key="terminal_flow"):
            st.markdown("**成交量结构**")
            up_volume = float(frame.loc[frame["Close"] >= frame["Open"], "Volume"].sum())
            down_volume = float(frame.loc[frame["Close"] < frame["Open"], "Volume"].sum())
            flow = pd.DataFrame({"方向": ["上涨 K", "下跌 K"], "成交量": [up_volume, down_volume]})
            flow = flow[flow["成交量"] > 0]
            if flow.empty:
                st.info("当前区间没有有效成交量。", icon=":material/info:")
            else:
                st.bar_chart(flow, x="方向", y="成交量", color="#37d996", horizontal=True)
            st.caption("按 K 线方向归类的成交量代理，不是 Level 2 主动买卖盘。")
            st.metric("累计成交量", f"{int(frame['Volume'].sum()):,}", border=True)
            st.metric("VWAP", f"{currency} {latest['VWAP']:.2f}" if pd.notna(latest["VWAP"]) else "--", border=True)
            st.metric("数据时间", str(frame.index[-1])[:19], border=True)
    section_label("价格成交分布", f"最新 K 线 {fetched_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')}")
    profile, correlation = st.columns([1, 1.4], gap="small")
    with profile:
        with st.container(border=True):
            distribution = _volume_profile(frame)
            distribution = distribution[distribution["成交量"] > 0]
            if distribution.empty:
                st.info("当前区间不足以形成价格成交分布。", icon=":material/info:")
            else:
                st.bar_chart(distribution, x="成交量", y="价格区间", horizontal=True, color="#2fb9e8")
    with correlation:
        with st.container(border=True):
            st.markdown("**主要资产 20 日相关矩阵**")
            try:
                correlation_symbols = ("AAPL", "MSFT", "NVDA", "SPY", "QQQ") if market == "美股" else ("000001", "000858", "300750", "510300", "600519")
                source = get_data_source(source_name)
                closes, _ = source.history(correlation_symbols, period="3mo")
                corr = closes.pct_change().tail(20).corr()
                st.dataframe(corr.style.background_gradient(cmap="RdYlGn", vmin=-1, vmax=1), width="stretch")
            except Exception as exc:
                st.error(f"相关矩阵不可用：{exc}")


def _render_quant_action_center(symbol: str, market: str, plan: str) -> None:
    section_label("量化操作中心", "事件、持仓、K 线标记与交易日志共用同一份不可变账本")
    if not can(plan, "signal_web"):
        st.info("量化操作时间线从标准版开放；免费版仍可查看延迟行情与研究候选。", icon=":material/lock:")
        return
    try:
        journal = QuantJournal()
        events = _events_for_symbol(
            symbol,
            market,
            journal,
            include_options=can(plan, "option_chain"),
        )
        replay = journal.replay(_system_ledger_key(), initial_cash={"USD": 100_000, "CNY": 100_000})
    except (RuntimeError, ValueError) as exc:
        st.error(f"量化账本暂时不可用：{exc}", icon=":material/database_off:")
        return
    if not events:
        st.info(
            f"{symbol} 尚无经过验证的量化操作记录。接收到正式策略事件后，动作会永久保留在这里和审计日志中。",
            icon=":material/history:",
        )
        return
    latest = events[-1]
    leg = latest["leg"]
    action = "买入 / 增持" if float(leg["quantity_delta"]) > 0 else "卖出 / 减持"
    if latest.get("event_type") == "reversal":
        action = f"撤销 · {action}"
    elif latest.get("event_type") == "correction":
        action = f"更正 · {action}"
    positions = [
        position
        for position in replay["positions"].values()
        if position["symbol"] == symbol and position["market"] == ("CN" if market == "A股" else "US")
    ]
    stock_quantity = sum(float(position["quantity"]) for position in positions if position["instrument_type"] == "stock")
    option_positions = (
        sum(1 for position in positions if position["instrument_type"] == "option")
        if can(plan, "option_chain")
        else None
    )
    occurred = pd.Timestamp(latest["occurred_at"]).tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M HKT")
    with st.container(horizontal=True):
        st.metric("最新动作", action, border=True)
        st.metric("正股目标仓位", f"{stock_quantity:g} 股", border=True)
        st.metric("期权持仓", f"{option_positions} 个合约" if option_positions is not None else "升级查看", border=True)
        st.metric("策略版本", f"{latest['strategy_name']} · {latest['strategy_version']}", border=True)
    metadata = latest.get("metadata") or {}
    price = leg.get("price")
    price_text = (
        f"记录价 {leg['currency']} {float(price):,.2f}"
        if price is not None else "记录价 --"
    )
    with st.container(border=True):
        st.markdown(f"**{leg['instrument_key']} · 数量变化 {float(leg['quantity_delta']):+g} · {price_text}**")
        st.caption(f"事件 #{latest['id']} · {occurred} · 来源 {latest['source']} · 幂等编号 {latest['external_event_id']}")
        if reason := metadata.get("reason") or metadata.get("rationale"):
            st.write(str(reason))


def render() -> None:
    user = st.session_state.user
    user_id = int(user["id"])
    plan = effective_plan(user)
    source_name = "yfinance" if st.session_state.get("terminal_market") == "A股" else os.getenv("DATA_SOURCE", "yfinance")
    source_status = market_data_status(source_name)
    page_heading(
        "MARKET / LIVE TERMINAL",
        "实时数据看板",
        f"真实分钟 K 线、成交量与关键技术数据集中展示。当前为{source_status['freshness']}。",
        f"5S K线 · 60S摘要 · {source_status['source'].upper()}",
    )
    market = st.segmented_control("市场", ["美股", "A股"], default="美股", key="terminal_market", width="stretch")
    source_name = "yfinance" if market == "A股" else os.getenv("DATA_SOURCE", "yfinance")
    defaults = WATCHLIST if market == "美股" else A_SHARE_WATCHLIST
    saved = _saved_watchlist(user_id, market)
    selector_key = f"terminal_symbol_{market}"
    active_key = f"terminal_active_symbol_{market}"
    active = st.session_state.get(active_key, "")
    tracked_watchlist = _merge_symbols(defaults, saved, market=market)
    watchlist = _merge_symbols(tracked_watchlist, (active,), market=market)
    intervals = INTERVALS if market == "美股" else {"日线": ("1y", "1d")}
    try:
        tape_closes, updated, source_label = _tape_data(market, source_name)
        market_tape(market_summary(tape_closes), updated, source_label)
    except Exception:
        st.warning("市场摘要暂时不可用，K 线仍会独立重试。", icon=":material/warning:")

    section_label("选股与实时 K 线", "搜索美股全市场或 A 股代码 · 个人自选会保存到账户")
    _render_symbol_search(user_id, market, selector_key, active_key)
    if notice := st.session_state.pop("terminal_notice", None):
        st.success(notice, icon=":material/check_circle:")
    if st.session_state.get(selector_key) not in watchlist:
        st.session_state[selector_key] = watchlist[0]
    controls = st.columns([1.2, .8, 1.6], gap="small")
    with controls[0]:
        symbol = st.selectbox("当前标的", watchlist, key=selector_key)
    with controls[1]:
        timeframe = st.selectbox("周期", list(intervals), index=1 if market == "美股" else 0)
    with controls[2]:
        if symbol in saved:
            st.button(
                "从个人自选移除",
                icon=":material/remove:",
                on_click=_remove_symbol,
                args=(user_id, market, symbol),
                key=f"terminal_remove_{market}_{symbol}",
            )
        else:
            st.caption("系统候选可直接查看；通过上方搜索可把其他股票加入个人自选。")
    period, interval = intervals[timeframe]
    _render_quant_action_center(symbol, market, plan)
    _live_panel(
        symbol,
        period,
        interval,
        plan == "免费版" and interval != "1d",
        market,
        source_name,
        tracked_watchlist,
        can(plan, "signal_web"),
        can(plan, "option_chain"),
    )

    section_label(
        "数据建议",
        f"系统候选 + {len(saved)} 只个人自选 · 6 个月真实日线量价综合评分",
    )
    try:
        recommendations = load_recommendations(market, saved)
        render_recommendations(recommendations)
        st.caption("研究候选按量价评分生成，不包含个人适当性判断；A 股个股期权需另行核对交易所合约，执行前必须核对流动性与风险。")
    except Exception as exc:
        st.warning(f"数据建议暂时不可用：{exc}", icon=":material/cloud_off:")
