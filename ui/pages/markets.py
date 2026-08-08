# -*- coding: utf-8 -*-
"""真实行情、日频期权链与价格预警。"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from core.alerts import AlertService, condition_preview
from core.plans import can, effective_plan
from core.user_settings import load_user_settings
from data.datasource import DataSourceError, get_resilient_data_source, public_market_status
from notification.telegram_bot import send_telegram, telegram_configured, verified_user_target
from ui.components import market_tape, page_heading, section_label
from ui.data import market_summary
from ui.recommendations import A_SHARE_UNIVERSE, US_UNIVERSE


@st.cache_data(ttl=60, max_entries=20, show_spinner=False)
def _history(symbols: tuple[str, ...], market: str, source_name: str):
    source = get_resilient_data_source("yfinance" if market == "A股" else source_name)
    closes, volumes = source.history(symbols, period="3mo")
    public = public_market_status("yfinance" if market == "A股" else source_name, market)
    return closes, volumes, pd.Timestamp(closes.index[-1]).to_pydatetime(), public["display_source"]


@st.cache_data(ttl=300, max_entries=10, show_spinner=False)
def _option_chain(symbol: str, source_name: str = "yfinance"):
    return get_resilient_data_source(source_name).option_chain(symbol)


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    alert_service = AlertService()
    page_heading(
        "MARKET / ALERTS",
        "预警与期权链",
        "查看真实历史价格、日频期权链并建立持久化价格预警。",
        "市场数据 · 60 秒缓存",
    )
    market = st.segmented_control("市场", ["美股", "A股"], default="美股", key="markets_market", width="stretch", required=True)
    universe = US_UNIVERSE if market == "美股" else A_SHARE_UNIVERSE
    symbols = st.multiselect(
        "监控标的",
        list(universe),
        default=list(universe[:3]),
        accept_new_options=True,
        max_selections=4,
        placeholder="选择或输入美股/A 股代码…",
    )
    if not symbols:
        st.info("至少选择 1 个标的后才会请求行情。", icon=":material/search:")
        return
    try:
        closes, volumes, updated_at, source_name = _history(
            tuple(symbols), market, os.getenv("DATA_SOURCE", "yfinance")
        )
    except DataSourceError:
        st.error("市场数据暂时不可用，请稍后重试。", icon=":material/cloud_off:")
        return
    market_tape(market_summary(closes), updated_at, source_name)
    price_frame = closes.tail(30).reset_index().rename(columns={closes.index.name or "index": "日期"})
    status = public_market_status("yfinance" if market == "A股" else os.getenv("DATA_SOURCE", "yfinance"), market)
    section_label("近 30 个交易日", f"真实收盘价 · {status['freshness']}")
    st.line_chart(price_frame, x="日期", y=list(closes.columns), x_label="日期", y_label="价格")
    with st.expander("查看近 30 日价格数据", icon=":material/table_chart:"):
        st.caption(f"共 {len(price_frame)} 个交易日；列标题为各标的代码，单位为对应市场货币。")
        st.dataframe(price_frame, hide_index=True, width="stretch")
    latest_prices = {symbol: float(closes[symbol].dropna().iloc[-1]) for symbol in closes.columns}
    triggered = alert_service.evaluate(user["id"], latest_prices)
    telegram_target = verified_user_target(load_user_settings(user["id"]), "price_alert") if can(plan, "tg_stock_signal") else None
    for alert in triggered:
        st.toast(f"{alert['symbol']} 已触发 {alert['operator']} {alert['target_price']:.2f}", icon=":material/notifications_active:")
        if telegram_target and telegram_configured(telegram_target):
            try:
                send_telegram(
                    f"CicloTrade 價格預警建議\n{alert['symbol']} {alert['operator']} {alert['target_price']:.2f}\n目前價格 {alert['current_price']:.2f}",
                    chat_id=telegram_target,
                    protect_content=True,
                )
            except RuntimeError:
                st.warning("预警已触发，但 Telegram 推送失败。")

    alert_col, chain_col = st.columns([1, 1.65], gap="small")
    with alert_col:
        with st.container(border=True):
            st.markdown("**建立组合预警**")
            with st.form("price_alert"):
                alert_symbol = st.selectbox("标的", list(latest_prices))
                max_conditions = 1 if plan == "免费版" else 3 if plan == "标准版" else 5
                condition_count = st.number_input("条件数量", min_value=1, max_value=max_conditions, value=1, step=1)
                logic = st.segmented_control("组合逻辑", ["AND", "OR"], default="AND", required=True) if condition_count > 1 else "AND"
                conditions = []
                for index in range(int(condition_count)):
                    left, middle, right = st.columns([1.05, .8, 1.15], gap="small")
                    with left:
                        options = ["price"] if plan == "免费版" else ["price", "volume", "volume_ratio", "rsi", "macd", "ma", "change"]
                        kind = st.selectbox("条件类型", options, key=f"alert_kind_{index}", format_func=lambda value: {"price": "价格", "volume": "成交量", "volume_ratio": "量比", "rsi": "RSI", "macd": "MACD", "ma": "均线", "change": "涨跌幅"}.get(value, value))
                    if kind in {"macd", "ma"}:
                        with middle:
                            value = st.selectbox("信号", ["golden_cross", "death_cross"] if kind == "macd" else ["ma20_breakout", "ma50_breakout"], key=f"alert_value_{index}", format_func=lambda item: {"golden_cross": "金叉", "death_cross": "死叉", "ma20_breakout": "突破 MA20", "ma50_breakout": "突破 MA50"}.get(item, item))
                        conditions.append({"type": kind, "value": value})
                    else:
                        with middle:
                            op = st.selectbox("运算", [">=", "<=", ">", "<", "="], key=f"alert_operator_{index}")
                        with right:
                            default = float(latest_prices[alert_symbol]) if kind == "price" else 0.0
                            value = st.number_input("数值", value=default, step=0.5, key=f"alert_target_{index}")
                        conditions.append({"type": kind, "operator": op, "value": value})
                st.caption(condition_preview(alert_symbol, conditions, logic))
                submitted = st.form_submit_button("建立预警", type="primary", icon=":material/add_alert:")
            if submitted:
                try:
                    alert_service.create(user["id"], plan, alert_symbol, conditions=conditions, logic=logic)
                    st.success("组合预警已建立。", icon=":material/check_circle:")
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
            alerts = alert_service.list(user["id"])
            active = [row for row in alerts if row["is_active"]]
            st.caption(f"当前启用 {len(active)} 条预警")
            if active:
                selected_id = st.selectbox("停用预警", [row["id"] for row in active], format_func=lambda alert_id: next(f"{row['symbol']} {row['operator']} {row['target_price']:.2f}" for row in active if row["id"] == alert_id))
                if st.button("停用所选预警", icon=":material/notifications_off:"):
                    alert_service.deactivate(user["id"], int(selected_id))
                    st.rerun()
    with chain_col:
        with st.container(border=True):
            st.markdown("**日频期权链**")
            if market == "A股":
                st.info("当前数据源未提供沪深交易所期权链。A 股个股不能直接套用美股期权报价，ETF 期权需接入具备授权的交易所数据。", icon=":material/info:")
            elif not can(plan, "option_chain"):
                st.warning("期权链属于高级版及以上方案。", icon=":material/lock:")
            else:
                chain_symbol = st.selectbox("期权标的", [value for value in symbols if not value.isdigit()] or ["AAPL"])
                if st.button("读取最新期权链", icon=":material/refresh:"):
                    try:
                        source_name = os.getenv("DATA_SOURCE", "yfinance")
                        expiry, calls, puts = _option_chain(chain_symbol, source_name)
                        st.session_state.option_chain = (chain_symbol, expiry, calls, puts)
                    except Exception:
                        st.error("期权链暂时不可用，请稍后重试或改用另一到期日。")
                chain = st.session_state.get("option_chain")
                if chain and len(chain) == 4 and chain[0] == chain_symbol:
                    _, expiry, calls, puts = chain
                    chain_status = public_market_status(os.getenv("DATA_SOURCE", "yfinance"), "美股")
                    st.caption(f"到期日 {expiry} · {chain_status['display_source']} · {chain_status['freshness']}")
                    option_type = st.segmented_control("类型", ["Call", "Put"], default="Call", required=True)
                    frame = calls if option_type == "Call" else puts
                    columns = [name for name in ["contractSymbol", "strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"] if name in frame]
                    st.dataframe(frame[columns].head(50), hide_index=True, width="stretch")
                else:
                    st.info(f"点击“读取最新期权链”加载 {chain_symbol} 的报价。", icon=":material/touch_app:")
