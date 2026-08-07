# -*- coding: utf-8 -*-
"""8 策略历史回测与报告。"""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
from io import BytesIO
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backtest.engine import BacktestEngine
from core.database import get_database
from core.plans import (
    backtest_years, can, effective_plan, strategy_condition_limit,
    strategy_generation_limit,
)
from core.strategy_evaluation import chronological_validation_start, evaluate_rule_strategy
from core.strategy_generator import StrategyGenerationService
from core.strategy_parser import parse_strategy
from core.strategy_registry import StrategyRegistry
from core.strategy_tracking import StrategyPerformanceTracker
from data.datasource import get_data_source
from ui.components import metric_grid, page_heading, section_label
from ui.data import STRATEGIES
from ui.recommendations import load_recommendations, render_recommendations


def _excel_report(result) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([result.metrics]).to_excel(writer, sheet_name="绩效指标", index=False)
        result.equity.to_excel(writer, sheet_name="净值曲线", index=False)
        result.trades.to_excel(writer, sheet_name="交易周期", index=False)
    return output.getvalue()


def _natural_language_builder(user: dict, plan: str) -> None:
    section_label("一句話生成交易策略", "自然語言 → 規則驗證 → 下一根 K 線執行 → 歷史回測")
    example = "當 AAPL 股價突破 200 日均線時買入，當股價跌破 50 日均線時賣出"
    if not can(plan, "strategy_generate"):
        st.code(example, language="text")
        st.info("免費版可查看示例；標準版每天可生成 3 次簡單策略。", icon=":material/lock:")
        return
    limit = strategy_generation_limit(plan)
    today = datetime.now(UTC).date().isoformat()
    used_row = get_database().fetch_one(
        "SELECT COUNT(*) count FROM strategy_generations WHERE user_id=? AND created_at LIKE ?",
        (user["id"], f"{today}%"),
    )
    used = int(used_row["count"] if used_row else 0)
    remaining = "不限" if limit is None else str(max(limit - used, 0))
    description = st.text_area(
        "描述買入與賣出條件",
        placeholder=example,
        max_chars=1_000,
        height=112,
        help="必須包含美股代碼或 6 位 A 股代碼，以及可識別的買入和賣出條件。",
    )
    generate = st.button(
        f"生成並回測 · 今日剩餘 {remaining}", type="primary",
        icon=":material/auto_awesome:", width="stretch",
        disabled=limit is not None and used >= limit,
    )
    if generate:
        try:
            parsed = parse_strategy(
                description,
                max_conditions=strategy_condition_limit(plan),
                use_remote=True,
            )
            generation = StrategyGenerationService().save(int(user["id"]), description, parsed)
            years = backtest_years(plan)
            with st.spinner("正在讀取真實歷史 K 線並逐根回測…", show_time=True):
                closes, _ = get_data_source().history((parsed["symbol"],), period=f"{years}y")
                series = closes[parsed["symbol"]].dropna()
                validation_start = chronological_validation_start(series)
                configured = {"parameters": {}, "rules": {"entry": parsed["entry"], "exit": parsed["exit"]}}
                training = evaluate_rule_strategy(series[series.index < validation_start], configured)
                result = evaluate_rule_strategy(
                    series,
                    configured,
                    evaluation_start=validation_start,
                )
            now = datetime.now(UTC).isoformat(timespec="seconds")
            database = get_database()
            with database.transaction() as conn:
                conn.execute(
                    "UPDATE strategy_generations SET status='backtested' WHERE id=? AND user_id=?",
                    (generation["id"], user["id"]),
                )
                conn.execute(
                    """INSERT INTO backtest_records
                       (user_id,strategy_name,symbol,start_date,end_date,return_rate,max_drawdown,
                        win_rate,total_trades,params,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user["id"], "一句話策略", parsed["symbol"], result["evaluation_start"],
                        result["evaluation_end"], result["total_return"], result["max_drawdown"],
                        result["win_rate"], result["total_trades"],
                        json.dumps({
                            "generation_id": generation["id"], "parsed": parsed,
                            "validation": "70/30 chronological holdout",
                            "validation_start": result["evaluation_start"],
                            "execution_model": result["execution_model"],
                            "training_metrics": {
                                "return_rate": training["total_return"],
                                "max_drawdown": training["max_drawdown"],
                                "sharpe": training["sharpe_ratio"],
                                "total_trades": training["total_trades"],
                            },
                        }, ensure_ascii=False), now,
                    ),
                )
            st.session_state.generated_strategy_result = {
                "generation": generation, "result": result, "training": training,
                "dates": [str(value.date()) for value in series.index if value >= validation_start],
            }
        except (PermissionError, RuntimeError, ValueError) as exc:
            st.error(f"策略生成失敗：{exc}", icon=":material/error:")
    generated = st.session_state.get("generated_strategy_result")
    if not generated:
        return
    parsed = generated["generation"]["parsed"]
    result = generated["result"]
    st.success(
        f"解析完成：{parsed['symbol']} · 買入 {len(parsed['entry'])} 個條件 · "
        f"賣出 {len(parsed['exit'])} 個條件 · 前 70% 建模 / 後 30% 樣本外驗證"
    )
    metric_grid(
        (
            ("樣本外回報", f"{result['total_return']:+.2%}", "後 30% 時間區間", "positive" if result["total_return"] >= 0 else "negative"),
            ("樣本外回撤", f"{result['max_drawdown']:.2%}", "峰值至谷底", "negative"),
            ("樣本外 Sharpe", f"{result['sharpe_ratio']:.2f}", "日收益年化", ""),
            ("樣本外勝率", f"{result['win_rate']:.1%}", f"{result['total_trades']} 筆完成交易", ""),
        )
    )
    if result["total_trades"] < 30:
        st.warning("樣本外完成交易少於 30 筆，只能視為樣本不足，不能判定策略通過。", icon=":material/warning:")
    with st.expander("查看解析規則與生成代碼", icon=":material/code:"):
        st.json({key: parsed[key] for key in ("symbol", "market", "entry", "exit", "execution_timing")})
        st.code(generated["generation"]["code"], language="python")
    st.warning("基於歷史數據回測，不代表未來表現", icon=":material/info:")
    if can(plan, "strategy_tracking") and st.button(
        "保存為我的模板並持續追蹤" if can(plan, "strategy_template_save") else "保存策略並持續追蹤",
        icon=":material/bookmark_add:",
    ):
        StrategyPerformanceTracker().save_strategy(
            int(user["id"]), name=f"一句話策略 · {parsed['symbol']}", source_type="generated",
            config={"symbol": parsed["symbol"], "parsed": parsed},
        )
        st.success("策略已保存；系統會在交易日收盤後更新履歷。")


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    engine = BacktestEngine()
    registry = StrategyRegistry()
    registry.sync_catalog()
    max_years = backtest_years(plan)
    page_heading(
        "RESEARCH / BACKTEST",
        "策略回测",
        "使用真实标的历史 K 线滚动结算期权策略。免费数据缺少历史期权报价，因此权利金采用波动率代理并明确标注。",
        f"PLAN LIMIT · {max_years}Y",
    )
    _natural_language_builder(user, plan)
    available_definitions = registry.list_for_plan(plan, family="option")
    available_names_set = {item["name"] for item in available_definitions}
    # ponytail: payoff engine supports the core eight; add engine-key dispatch before dynamic definitions.
    available = [item for item in STRATEGIES if item["name"] in available_names_set]
    available_names = [item["name"] for item in available]
    if not available_names:
        st.warning("目前沒有可回測的啟用策略；請稍後再試或聯絡管理員。", icon=":material/lock:")
        return
    market = st.segmented_control("市场", ["美股", "A股"], default="美股", key="backtest_market", width="stretch")
    currency = "USD" if market == "美股" else "CNY"
    default_symbol = "AAPL" if market == "美股" else "510300"
    mode = st.segmented_control("使用方式", ["快速验证", "专业参数"], default="快速验证", width="stretch")
    if mode == "快速验证":
        try:
            candidates = load_recommendations(market)
            symbol = st.selectbox(
                "选择数据候选",
                candidates["标的"].tolist(),
                format_func=lambda value: f"{value} · {candidates.loc[candidates['标的'] == value, '观点'].iloc[0]} · {int(candidates.loc[candidates['标的'] == value, '评分'].iloc[0]):+d} 分",
            )
            candidate = candidates.loc[candidates["标的"] == symbol].iloc[0]
            render_recommendations(candidates.loc[candidates["标的"] == symbol], limit=1)
            strategy = str(candidate["期权策略"])
            years = max_years
            dte = int(candidate["DTE"])
            shift = float(candidate["行权价偏移"])
            quantity = 1
            blocked = strategy not in available_names or strategy == "暂不买期权"
            if strategy not in available_names and strategy != "暂不买期权":
                st.warning(f"当前建议使用“{strategy}”，此策略未在当前套餐启用或不在当前方案内。", icon=":material/lock:")
            submitted = st.button(
                "用历史数据验证这条建议",
                type="primary",
                icon=":material/fact_check:",
                disabled=blocked,
                width="stretch",
            )
        except Exception as exc:
            st.error(f"快速建议暂时不可用：{exc}", icon=":material/cloud_off:")
            strategy, symbol, years, dte, shift, quantity, submitted = available_names[0], default_symbol, max_years, 45, 0.0, 1, False
    else:
        with st.form("backtest_form"):
            first = st.columns(3, gap="small")
            second = st.columns(3, gap="small")
            with first[0]:
                strategy = st.selectbox("策略", available_names)
            with first[1]:
                symbol = st.text_input(
                    "标的代码",
                    value=default_symbol,
                    max_chars=12,
                    key=f"backtest_symbol_{market}",
                ).strip().upper()
            with first[2]:
                years = st.number_input("历史年数", 1, max_years, min(3, max_years), 1)
            with second[0]:
                dte = st.slider("距离到期（DTE）", 30, 75, 45, 5, help="每个回测周期持有到期的交易日数量。")
            with second[1]:
                shift = st.slider("行权价相对现价", -20, 20, 0, 1, format="%d%%")
            with second[2]:
                quantity = st.number_input("合约数量", 1, 20, 1, 1)
            submitted = st.form_submit_button("运行专业回测", type="primary", icon=":material/play_arrow:")
    if submitted:
        valid_symbol = (len(symbol) == 6 and symbol.isdigit()) if market == "A股" else bool(symbol) and not symbol.isdigit()
        if not valid_symbol:
            st.error("请输入符合当前市场的股票代码。", icon=":material/error:")
            submitted = False
    if submitted:
        try:
            with st.spinner("正在读取真实历史 K 线并计算回测…", show_time=True):
                st.session_state.backtest_result = engine.run(
                    user["id"], strategy, symbol, int(years), int(dte), float(shift), int(quantity)
                )
                st.session_state.backtest_currency = currency
                st.session_state.backtest_symbol = symbol
        except Exception as exc:
            st.error(f"回测失败：{exc}", icon=":material/error:")
    if can(plan, "backtest_10y") and st.button("运行 70/30 Walk-Forward 严谨验证", icon=":material/auto_graph:"):
        try:
            with st.spinner("正在训练窗选参、逐窗样本外验证并执行双倍成本压力测试…", show_time=True):
                best, frame = engine.optimize(user["id"], strategy, symbol, int(years))
                st.session_state.optimization_result = (best, frame)
        except Exception as exc:
            st.error(f"参数优化失败：{exc}", icon=":material/error:")
    if optimization := st.session_state.get("optimization_result"):
        best, frame = optimization
        representative = f"训练窗最常入选：DTE {int(best['DTE'])}，行权价偏移 {int(best['行权价偏移']):+d}%"
        if best["sample_quality"] == "insufficient":
            st.warning(f"{representative}。样本外交易少于 30 笔，结论为样本不足。", icon=":material/warning:")
        elif float(best["oos_return_rate"]) > 0 and float(best["stress_return_rate"]) > 0:
            st.success(f"{representative}。基础与双倍成本样本外结果均为正。")
        else:
            st.warning(f"{representative}。样本外或双倍成本压力结果未通过。", icon=":material/warning:")
        metric_grid(
            (
                ("样本外回报", f"{float(best['oos_return_rate']):+.2%}", f"{int(best['folds'])} 个验证窗", "positive" if float(best["oos_return_rate"]) > 0 else "negative"),
                ("样本外回撤", f"{float(best['oos_max_drawdown']):.2%}", "仅后 30% 时间区间", "negative"),
                ("双倍成本回报", f"{float(best['stress_return_rate']):+.2%}", "佣金与滑点 × 2", "positive" if float(best["stress_return_rate"]) > 0 else "negative"),
                ("样本外交易", str(int(best["oos_total_trades"])), f"入选稳定度 {float(best['selection_rate']):.0%}", ""),
            )
        )
        st.dataframe(
            frame[["DTE", "行权价偏移", "训练评分", "训练回报", "训练回撤", "训练Sharpe", "入选窗口"]],
            hide_index=True,
            width="stretch",
            column_config={
                "训练评分": st.column_config.NumberColumn(format="%.3f"),
                "训练回报": st.column_config.NumberColumn(format="percent"),
                "训练回撤": st.column_config.NumberColumn(format="percent"),
                "训练Sharpe": st.column_config.NumberColumn(format="%.2f"),
            },
        )

    result = st.session_state.get("backtest_result")
    if result:
        result_currency = st.session_state.get("backtest_currency", "USD")
        result_symbol = st.session_state.get("backtest_symbol", "标的")
        section_label("绩效摘要", str(result.metrics["model"]))
        metrics = result.metrics
        metric_grid(
            (
                ("总回报", f"{metrics['return_rate']:+.2%}", "期末 / 期初", "positive" if metrics["return_rate"] >= 0 else "negative"),
                ("最大回撤", f"{metrics['max_drawdown']:.2%}", "净值峰值至谷底", "negative"),
                ("周期胜率", f"{metrics['win_rate']:.1%}", f"{metrics['total_trades']} 个到期周期", ""),
                ("期末净值", f"{result_currency} {metrics['ending_equity']:,.2f}", f"{result_symbol} · 初始 {result_currency} 100,000", ""),
            )
        )
        return_rate = float(metrics["return_rate"])
        drawdown = float(metrics["max_drawdown"])
        win_rate = float(metrics["win_rate"])
        if int(metrics["total_trades"]) < 30:
            verdict, tone, next_step = "样本不足", "neutral", "完成周期少于 30 个，不能判定策略通过；请扩大历史区间。"
        elif return_rate > 0 and drawdown > -.25 and win_rate >= .5:
            verdict, tone, next_step = "历史验证通过", "positive", "可加入观察清单，并先用小仓位模拟验证。"
        elif return_rate > 0:
            verdict, tone, next_step = "结果可观察", "neutral", "有正回报，但回撤或胜率不理想，先降低仓位。"
        else:
            verdict, tone, next_step = "当前不建议采用", "negative", "历史样本没有正回报，换标的、策略或等待新信号。"
        st.html(
            f'<section class="verdict"><span>普通用户结论</span><strong class="{tone}">{verdict}</strong>'
            f'<p>{next_step}</p></section>'
        )
        figure = go.Figure(
            go.Scatter(
                x=result.equity["日期"], y=result.equity["净值"], mode="lines+markers",
                line={"color": "#37d996", "width": 2.5}, marker={"size": 4},
                hovertemplate=f"%{{x|%Y-%m-%d}}<br>净值 {result_currency} %{{y:,.2f}}<extra></extra>",
            )
        )
        figure.update_layout(
            height=430, margin={"l": 8, "r": 8, "t": 24, "b": 8}, paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#090b0d", font={"color": "#8d999b", "family": "IBM Plex Mono"},
            xaxis={"gridcolor": "#1b2327"}, yaxis={"gridcolor": "#1b2327", "tickprefix": f"{result_currency} "},
        )
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        equity_data = st.expander("查看净值曲线数据", icon=":material/table_chart:", on_change="rerun")
        if equity_data.open:
            with equity_data:
                st.dataframe(
                    result.equity,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "净值": st.column_config.NumberColumn(format=f"{result_currency} %,.2f")
                    },
                )
        details = st.expander("查看到期周期明细", icon=":material/table_chart:", on_change="rerun")
        if details.open:
            with details:
                st.dataframe(result.trades, hide_index=True, width="stretch")
        if can(plan, "reports"):
            st.download_button(
                "下载 Excel 回测报告", _excel_report(result), "ciclotrade-backtest.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:",
            )
        else:
            st.caption("Excel / PDF 报告导出仅限专业版与定制版。")

    section_label("回测历史", "最近 50 次结果保存在 SQLite")
    history = engine.history(user["id"])
    if history:
        frame = pd.DataFrame(history)[["strategy_name", "symbol", "start_date", "end_date", "return_rate", "max_drawdown", "win_rate", "total_trades", "created_at"]]
        frame.columns = ["策略", "标的", "开始", "结束", "回报", "最大回撤", "胜率", "周期数", "建立时间"]
        st.dataframe(
            frame, hide_index=True, width="stretch",
            column_config={"回报": st.column_config.NumberColumn(format="percent"), "最大回撤": st.column_config.NumberColumn(format="percent"), "胜率": st.column_config.NumberColumn(format="percent")},
        )
    else:
        st.info("尚无回测记录。完成第一次回测后会在这里保留结果。", icon=":material/history:")
