# -*- coding: utf-8 -*-
"""真实量价数据驱动的正股与期权研究候选。"""

from __future__ import annotations

import html
import os

import numpy as np
import pandas as pd
import streamlit as st

from core.admin_service import AdminService
from data.datasource import get_data_source


US_UNIVERSE = ("AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ")
A_SHARE_UNIVERSE = ("000001", "000858", "300750", "510050", "510300", "600519", "601318")
UNIVERSE = US_UNIVERSE  # 兼容已有调用；新页面按市场选择具体列表。
A_SHARE_OPTION_ETFS = {"510050", "510300"}


def _strike(value: float) -> int:
    return max(1, int(round(value / 5) * 5))


def score_candidates(closes: pd.DataFrame, volumes: pd.DataFrame, market: str = "美股") -> pd.DataFrame:
    is_a_share = market == "A股"
    currency = "CNY" if is_a_share else "USD"
    rows = []
    for symbol in closes.columns:
        series = closes[symbol].dropna()
        if len(series) < 50:
            continue
        price = float(series.iloc[-1])
        ema20 = float(series.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(series.ewm(span=50, adjust=False).mean().iloc[-1])
        return20 = float(price / series.iloc[-21] - 1)
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = -delta.clip(upper=0).rolling(14).mean().iloc[-1]
        rsi = 100.0 if loss == 0 and gain > 0 else 50.0 if loss == 0 else float(100 - 100 / (1 + gain / loss))
        volatility = float(series.pct_change().tail(20).std() * np.sqrt(252))
        symbol_volume = volumes[symbol].dropna()
        volume_ratio = float(symbol_volume.iloc[-1] / symbol_volume.tail(20).mean()) if not symbol_volume.empty and symbol_volume.tail(20).mean() else 1.0
        score = (
            (30 if price >= ema20 else -30)
            + (25 if ema20 >= ema50 else -25)
            + float(np.clip(return20 * 250, -25, 25))
            + float(np.clip((rsi - 50) * .35, -10, 10))
            + float(np.clip((volume_ratio - 1) * 8, -5, 5))
        )
        score = int(round(np.clip(score, -100, 100)))
        dte = 30 if volatility >= .45 else 45
        if score >= 60:
            view, stock_action, strategy = "强势偏多", "持有；回调至 EMA20 附近再分批关注", "买入 Call"
            option = f"{dte}–{dte + 15} DTE · 买入 {_strike(price * 1.03)} Call"
            stop, target = price * .93, price * 1.12
        elif score >= 30:
            view, stock_action, strategy = "温和偏多", "继续持有；不追高，等待回调确认", "牛市价差"
            option = f"{dte}–{dte + 15} DTE · 买 {_strike(price * 1.02)}C / 卖 {_strike(price * 1.08)}C"
            stop, target = price * .94, price * 1.10
        elif score <= -60:
            view, stock_action, strategy = "强势偏空", "回避或降低仓位；不逆势抄底", "买入 Put"
            option = f"{dte}–{dte + 15} DTE · 买入 {_strike(price * .97)} Put"
            stop, target = price * 1.07, price * .88
        elif score <= -30:
            view, stock_action, strategy = "温和偏空", "减少仓位；等待重新站上 EMA20", "熊市价差"
            option = f"{dte}–{dte + 15} DTE · 买 {_strike(price * .98)}P / 卖 {_strike(price * .92)}P"
            stop, target = price * 1.06, price * .90
        else:
            view, stock_action, strategy = "方向不清", "观察；暂不新增仓位", "暂不买期权"
            option = "等待价格与 EMA20/50 同向后再选择结构"
            stop, target = price * .94, price * 1.08
        if is_a_share:
            # A 股个股没有可由 Yahoo Finance 验证的标准个股期权链，避免把美股合约建议伪装成可执行订单。
            if str(symbol).isdigit() and str(symbol) not in A_SHARE_OPTION_ETFS:
                strategy = "暂不买期权"
                option = "个股期权链未接入；先按正股建议观察，ETF 期权需单独核对合约与流动性"
            elif str(symbol) in A_SHARE_OPTION_ETFS and strategy != "暂不买期权":
                direction = "认购" if score > 0 else "认沽"
                structure = "单腿" if abs(score) >= 60 else "价差"
                option = f"{dte}–{dte + 15} DTE · 近平值{direction}{structure}研究候选 · 需使用上交所期权链复核"
        reasons = f"20 日 {return20:+.1%} · RSI {rsi:.0f} · 年化波动 {volatility:.0%} · 成交量 {volume_ratio:.1f}×"
        rows.append(
            {
                "市场": market,
                "标的": symbol,
                "最新价": price,
                "货币": currency,
                "评分": score,
                "观点": view,
                "正股建议": stock_action,
                "期权策略": strategy,
                "期权建议": option,
                "止损参考": stop,
                "目标参考": target,
                "DTE": dte,
                "行权价偏移": 3 if score >= 0 else -3,
                "依据": reasons,
            }
        )
    return pd.DataFrame(rows).sort_values("评分", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=300, max_entries=20, show_spinner=False)
def _load_recommendations(
    market: str = "美股", extra_symbols: tuple[str, ...] = (), source_name: str = "yfinance"
) -> pd.DataFrame:
    base = A_SHARE_UNIVERSE if market == "A股" else US_UNIVERSE
    symbols = tuple(dict.fromkeys((*base, *(symbol.upper() for symbol in extra_symbols))))
    source = get_data_source("yfinance" if market == "A股" else source_name)
    closes, volumes = source.history(symbols, period="6mo")
    return score_candidates(closes, volumes, market)


def load_recommendations(market: str = "美股", extra_symbols: tuple[str, ...] = ()) -> pd.DataFrame:
    if not AdminService().control_enabled("recommendations_published", True):
        raise RuntimeError("推荐发布已由研究后台暂停。")
    return _load_recommendations(market, extra_symbols, os.getenv("DATA_SOURCE", "yfinance"))


def render_recommendations(frame: pd.DataFrame, limit: int = 3) -> None:
    cards = []
    for rank, row in frame.head(limit).iterrows():
        score = int(row["评分"])
        tone = "positive" if score >= 30 else "negative" if score <= -30 else "neutral"
        cards.append(
            '<article class="decision-card">'
            f'<header><span>#{rank + 1} · {html.escape(str(row["标的"]))}</span>'
            f'<b class="{tone}">{html.escape(str(row["观点"]))} · {score:+d}</b></header>'
            f'<strong>{html.escape(str(row.get("货币", "USD")))} {float(row["最新价"]):,.2f}</strong>'
            '<dl>'
            f'<div><dt>正股</dt><dd>{html.escape(str(row["正股建议"]))}</dd></div>'
            f'<div><dt>期权</dt><dd>{html.escape(str(row["期权建议"]))}</dd></div>'
            f'<div><dt>风险线</dt><dd>止损 {float(row["止损参考"]):.2f} · 目标 {float(row["目标参考"]):.2f}</dd></div>'
            '</dl>'
            f'<p>{html.escape(str(row["依据"]))}</p></article>'
        )
    st.html('<section class="decision-grid" aria-label="数据研究候选">' + "".join(cards) + "</section>")
