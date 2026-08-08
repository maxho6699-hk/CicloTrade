# -*- coding: utf-8 -*-
"""One-click strategy templates with real historical evaluation."""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
import html
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.database import get_database
from core.plans import backtest_years, can, effective_plan
from core.strategy_evaluation import chronological_validation_start, evaluate_rule_strategy
from core.strategy_registry import StrategyRegistry
from core.strategy_tracking import StrategyPerformanceTracker
from data.datasource import get_resilient_data_source
from ui.components import metric_grid, page_heading, section_label


DISCLAIMER = "基於歷史數據回測，不代表未來表現"


def _parameter_input(name: str, value: object, enabled: bool) -> int | float:
    label = {
        "fast_period": "短期均線", "slow_period": "長期均線", "rsi_period": "RSI 週期",
        "buy_below": "買入閾值", "sell_above": "賣出閾值", "period": "觀察週期",
        "standard_deviations": "標準差倍數", "lookback": "動量週期",
        "entry_return": "買入漲幅", "exit_return": "退出漲幅", "entry_deviations": "偏離倍數",
    }.get(name, name)
    if isinstance(value, int):
        return int(st.number_input(label, 1, 500, value, 1, disabled=not enabled, key=f"template_param_{name}"))
    number = float(value)
    step = 0.01 if abs(number) < 1 else 0.1
    return float(st.number_input(label, value=number, step=step, disabled=not enabled, key=f"template_param_{name}"))


def _save_backtest(user_id: int, definition: dict, symbol: str, years: int, parameters: dict, result: dict, start: str, end: str) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    get_database().execute(
        """INSERT INTO backtest_records
           (user_id,strategy_name,symbol,start_date,end_date,return_rate,max_drawdown,win_rate,total_trades,params,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id, definition["name"], symbol, start, end, result["total_return"],
            result["max_drawdown"], result["win_rate"], result["total_trades"],
            json.dumps({
                "years": years, "parameters": parameters,
                "execution": result["execution_model"],
                "validation": "70/30 chronological holdout",
                "validation_start": result["evaluation_start"],
            }, ensure_ascii=False), now,
        ),
    )


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    registry = StrategyRegistry()
    registry.sync_catalog()
    templates = registry.list(family="equity")
    page_heading(
        "策略 / 模板", "策略模板庫",
        "選擇場景、套用參數並用真實歷史 K 線驗證；交易信號在下一根 K 線才生效。",
        "5 套核心模板 · 美股 + A 股",
    )
    if not templates:
        st.warning("目前沒有可用的啟用模板；請稍後再試或聯絡管理員。", icon=":material/lock:")
        return
    names = [item["name"] for item in templates]
    selected_name = st.selectbox("選擇策略模板", names)
    definition = next(item for item in templates if item["name"] == selected_name)
    st.html(
        f'<section class="verdict"><span>適用場景</span><strong>{html.escape(str(definition["scenario"]))}</strong>'
        f'<p>{html.escape(str(definition["description"]))}</p></section>'
    )
    market = st.segmented_control("市場", ["美股", "A股"], default="美股", width="stretch", key="template_market", required=True)
    default_symbol = "AAPL" if market == "美股" else "510300"
    symbol = st.text_input("標的代碼", value=default_symbol, max_chars=12, key=f"template_symbol_{market}").strip().upper()
    adjustable = can(plan, "strategy_template_parameters")
    parameters: dict[str, int | float] = {}
    columns = st.columns(min(3, max(1, len(definition["parameters"]))), gap="small")
    for index, (name, value) in enumerate(definition["parameters"].items()):
        with columns[index % len(columns)]:
            parameters[name] = _parameter_input(name, value, adjustable)
    if plan == "免费版":
        st.info("免費版可查看模板結構；標準版可使用全部固定參數模板。", icon=":material/lock:")
    elif not adjustable:
        st.caption("標準版使用已驗證的固定參數；高級版以上可自行調整。")
    years = backtest_years(plan)
    run = st.button(
        "一鍵回測此模板", type="primary", icon=":material/play_arrow:", width="stretch",
        disabled=not can(plan, "strategy_templates_use"),
    )
    if run:
        valid_symbol = (len(symbol) == 6 and symbol.isdigit()) if market == "A股" else bool(symbol) and not symbol.isdigit()
        if not valid_symbol:
            st.error("請輸入符合目前市場的股票代碼。", icon=":material/error:")
        else:
            try:
                with st.spinner("正在讀取歷史 K 線並執行逐根回測…", show_time=True):
                    closes, _ = get_resilient_data_source().history((symbol,), period=f"{years}y")
                    series = closes[symbol].dropna()
                    configured = {**definition, "parameters": parameters}
                    validation_start = chronological_validation_start(series)
                    training = evaluate_rule_strategy(series[series.index < validation_start], configured)
                    result = evaluate_rule_strategy(series, configured, evaluation_start=validation_start)
                    _save_backtest(
                        int(user["id"]), definition, symbol, years, parameters, result,
                        result["evaluation_start"], result["evaluation_end"],
                    )
                    st.session_state.template_result = {
                        "strategy": definition, "symbol": symbol, "parameters": parameters,
                        "result": result, "training": training,
                        "dates": [str(value.date()) for value in series.index],
                    }
            except Exception as exc:
                st.error(f"模板回測失敗：{exc}", icon=":material/error:")
    current = st.session_state.get("template_result")
    if current and current["strategy"]["key"] == definition["key"]:
        result = current["result"]
        section_label("樣本外回測結論", f"{current['symbol']} · 前 70% / 後 30% · 下一根收盤代理成交 · 已計 0.1% 單邊成本")
        metric_grid(
            (
                ("樣本外回報", f"{result['total_return']:+.2%}", "後 30% 時間區間", "positive" if result["total_return"] >= 0 else "negative"),
                ("樣本外回撤", f"{result['max_drawdown']:.2%}", "峰值至谷底", "negative"),
                ("樣本外 Sharpe", f"{result['sharpe_ratio']:.2f}", "日收益年化", ""),
                ("樣本外勝率", f"{result['win_rate']:.1%}", f"{result['total_trades']} 筆完成交易", ""),
            )
        )
        if result["total_trades"] < 30:
            st.warning("樣本外完成交易少於 30 筆，只能視為樣本不足。", icon=":material/warning:")
        dates = current["dates"][-len(result["equity_curve"]):]
        figure = go.Figure(go.Scatter(x=dates, y=result["equity_curve"], mode="lines", line={"color": "#37d996", "width": 2.5}))
        figure.update_layout(
            height=380, margin={"l": 8, "r": 8, "t": 20, "b": 8},
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#090b0d",
            font={"color": "#8d999b", "family": "IBM Plex Mono"},
            xaxis={"gridcolor": "#1b2327"}, yaxis={"gridcolor": "#1b2327"},
        )
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.warning(DISCLAIMER, icon=":material/info:")
        actions = st.columns(2, gap="small")
        with actions[0]:
            if st.button(
                "保存並持續追蹤", icon=":material/bookmark_add:", width="stretch",
                disabled=not can(plan, "strategy_tracking"),
            ):
                StrategyPerformanceTracker().save_strategy(
                    int(user["id"]), name=f"{definition['name']} · {current['symbol']}",
                    source_type="template", strategy_key=definition["key"],
                    config={"symbol": current["symbol"], "parameters": current["parameters"]},
                )
                st.success("已保存；系統會在交易日收盤後更新策略履歷。")
        with actions[1]:
            st.button("分享策略（規劃中）", icon=":material/share:", width="stretch", disabled=True)
