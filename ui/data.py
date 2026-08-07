# -*- coding: utf-8 -*-
"""CicloTrade market data, paper portfolio data, and option education math."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

import numpy as np
import pandas as pd

from data.datasource import get_data_source
from trading.order_manager import trade_ledger_state


MARKET_SYMBOLS: tuple[str, ...] = ("AAPL", "MSFT", "NVDA")
PAPER_STARTING_CASH = {"USD": 100_000.0, "CNY": 500_000.0}

STRATEGIES: tuple[dict[str, Any], ...] = (
    {"name": "买入 Call", "category": "看涨", "scenario": "预期标的明显上涨，并愿意承担全部权利金风险", "max_loss": "已付权利金", "max_profit": "理论上无限", "difficulty": 1, "legs": "单腿", "accent": "bull"},
    {"name": "买入 Put", "category": "看跌", "scenario": "预期标的明显下跌，或需要为持仓提供保护", "max_loss": "已付权利金", "max_profit": "行权价减权利金", "difficulty": 1, "legs": "单腿", "accent": "bear"},
    {"name": "牛市价差", "category": "看涨", "scenario": "温和看涨，希望用封顶收益换取更低成本", "max_loss": "净权利金", "max_profit": "价差减净权利金", "difficulty": 2, "legs": "双腿", "accent": "bull"},
    {"name": "熊市价差", "category": "看跌", "scenario": "温和看跌，希望预先限定最大损失", "max_loss": "净权利金", "max_profit": "价差减净权利金", "difficulty": 2, "legs": "双腿", "accent": "bear"},
    {"name": "买入跨式", "category": "看波动", "scenario": "预期价格大幅波动，但方向仍不明确", "max_loss": "两腿权利金", "max_profit": "上行理论无限", "difficulty": 2, "legs": "双腿", "accent": "vol"},
    {"name": "蝶式", "category": "看平", "scenario": "预期到期价格接近中间行权价", "max_loss": "净权利金", "max_profit": "有限", "difficulty": 3, "legs": "三腿", "accent": "neutral"},
    {"name": "备兑看涨", "category": "看涨", "scenario": "持有正股，并预期短期温和上涨", "max_loss": "正股下跌风险", "max_profit": "权利金加有限上涨", "difficulty": 2, "legs": "正股 + 期权", "accent": "bull"},
    {"name": "现金担保看跌", "category": "看跌", "scenario": "愿意以目标价买入正股并收取权利金", "max_loss": "行权价减权利金", "max_profit": "已收权利金", "difficulty": 2, "legs": "单腿", "accent": "bear"},
)

MYSTIC_REFERENCES = (
    "今日参考：先观察趋势与成交量，不因短期波动追价。",
    "传统历法娱乐提示：重大操作前再次核对止损与仓位。",
    "娱乐签语：守正待时，避免把随机波动解释为确定信号。",
)


class MarketDataUnavailable(RuntimeError):
    """Raised when live market history cannot be loaded."""


def load_market_history(
    symbols: Iterable[str] = MARKET_SYMBOLS, source_name: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, datetime]:
    """Load adjusted daily closes and volume through the configured adapter."""
    try:
        source = get_data_source(source_name)
        closes, volumes = source.history(tuple(symbols), period="3mo", interval="1d")
    except Exception as exc:
        if isinstance(exc, MarketDataUnavailable):
            raise
        raise MarketDataUnavailable(f"行情请求失败：{exc}") from exc
    if closes.empty:
        raise MarketDataUnavailable("行情服务没有返回有效价格。")
    latest = pd.Timestamp(closes.index[-1]).to_pydatetime()
    return closes, volumes, latest


def paper_account_from_trades(
    trades: list[dict[str, Any]], starting_cash: float
) -> tuple[tuple[dict[str, Any], ...], float]:
    state = trade_ledger_state(trades)
    positions = tuple(
        {
            "symbol": symbol,
            "quantity": quantity,
            "cost": float(state["average_costs"][symbol]),
        }
        for symbol, quantity in state["positions"].items()
        if abs(float(quantity)) > 1e-9
    )
    return positions, float(starting_cash) + float(state["cash_change"])


def portfolio_snapshot(
    closes: pd.DataFrame,
    paper_positions: tuple[dict[str, Any], ...],
    cash: float,
    source_name: str = "Yahoo Finance",
) -> tuple[dict[str, float], pd.DataFrame]:
    """Value the paper portfolio against the latest real market prices."""
    rows: list[dict[str, Any]] = []
    for position in paper_positions:
        symbol = position["symbol"]
        series = closes[symbol].dropna()
        current = float(series.iloc[-1])
        previous = float(series.iloc[-2]) if len(series) > 1 else current
        quantity = int(position["quantity"])
        cost = float(position["cost"])
        rows.append(
            {
                "标的": symbol,
                "数量": quantity,
                "成本价": cost,
                "最新价": current,
                "日涨跌": (current / previous - 1) if previous else 0.0,
                "市值": current * quantity,
                "浮动盈亏": (current - cost) * quantity,
                "数据": source_name,
            }
        )

    frame = pd.DataFrame(rows, columns=["标的", "数量", "成本价", "最新价", "日涨跌", "市值", "浮动盈亏", "数据"])
    market_value = float(frame["市值"].sum()) if not frame.empty else 0.0
    unrealized = float(frame["浮动盈亏"].sum()) if not frame.empty else 0.0
    daily_pnl = float(
        sum(
            (closes[row["标的"]].dropna().iloc[-1] - closes[row["标的"]].dropna().iloc[-2]) * row["数量"]
            for row in rows
            if len(closes[row["标的"]].dropna()) > 1
        )
    )
    assets = cash + market_value
    account = {
        "assets": assets,
        "daily_pnl": daily_pnl,
        "available": cash,
        "positions_value": market_value,
        "unrealized": unrealized,
        "usage_pct": market_value / assets * 100 if assets else 0.0,
        "winning_pct": float((frame["浮动盈亏"] > 0).mean() * 100) if not frame.empty else 0.0,
    }
    return account, frame


def paper_equity_curve_from_trades(
    closes: pd.DataFrame,
    trades: list[dict[str, Any]],
    starting_cash: float,
) -> pd.DataFrame:
    prices = closes.ffill()
    parsed = sorted(
        ((pd.Timestamp(trade["trade_time"]).date(), trade) for trade in trades),
        key=lambda item: item[0],
    )
    cash = float(starting_cash)
    quantities: dict[str, float] = {}
    trade_index = 0
    values: list[float] = []
    for timestamp, row in prices.iterrows():
        current_date = pd.Timestamp(timestamp).date()
        while trade_index < len(parsed) and parsed[trade_index][0] <= current_date:
            trade = parsed[trade_index][1]
            symbol = str(trade["symbol"]).upper()
            signed = float(trade["quantity"]) * (1 if str(trade["side"]).upper() == "BUY" else -1)
            quantities[symbol] = quantities.get(symbol, 0.0) + signed
            cash -= signed * float(trade["price"]) + float(trade.get("commission") or 0)
            trade_index += 1
        market_value = sum(
            quantity * float(row[symbol])
            for symbol, quantity in quantities.items()
            if symbol in row and pd.notna(row[symbol])
        )
        values.append(cash + market_value)
    return pd.DataFrame({"日期": prices.index, "账户净值": values}).tail(60)


def market_summary(closes: pd.DataFrame) -> list[dict[str, float | str]]:
    summary = []
    for symbol in closes.columns:
        series = closes[symbol].dropna()
        latest = float(series.iloc[-1])
        previous = float(series.iloc[-2]) if len(series) > 1 else latest
        summary.append({"symbol": symbol, "price": latest, "change": latest / previous - 1 if previous else 0.0})
    return summary


def strategy_curve(strategy: dict[str, Any], strike_shift: float, dte: int, quantity: int) -> pd.DataFrame:
    """Generate expiry payoff for one of the eight education strategies."""
    spot = 100.0
    strike = spot * (1 + strike_shift / 100)
    wing = max(5.0, dte / 10)
    premium = max(1.2, np.sqrt(dte / 365) * 6)
    prices = np.linspace(max(1.0, spot - 3 * wing), spot + 3 * wing, 240)
    name = strategy["name"]

    if name == "买入 Call":
        pnl = np.maximum(prices - strike, 0) - premium
    elif name == "买入 Put":
        pnl = np.maximum(strike - prices, 0) - premium
    elif name == "牛市价差":
        pnl = np.maximum(prices - strike, 0) - np.maximum(prices - (strike + wing), 0) - premium * 0.6
    elif name == "熊市价差":
        pnl = np.maximum(strike - prices, 0) - np.maximum((strike - wing) - prices, 0) - premium * 0.6
    elif name == "买入跨式":
        pnl = np.abs(prices - strike) - premium * 2
    elif name == "蝶式":
        pnl = np.maximum(prices - (strike - wing), 0) - 2 * np.maximum(prices - strike, 0) + np.maximum(prices - (strike + wing), 0) - premium * 0.45
    elif name == "备兑看涨":
        pnl = prices - spot - np.maximum(prices - strike, 0) + premium
    elif name == "现金担保看跌":
        pnl = premium - np.maximum(strike - prices, 0)
    else:
        raise ValueError(f"不支持的策略：{name}")

    return pd.DataFrame({"标的价格": prices.round(2), "预计损益": (pnl * 100 * quantity).round(2)})


def breakeven_points(frame: pd.DataFrame) -> list[float]:
    """Estimate zero crossings with linear interpolation."""
    x = frame["标的价格"].to_numpy(dtype=float)
    y = frame["预计损益"].to_numpy(dtype=float)
    points: list[float] = []
    for index in np.flatnonzero(np.signbit(y[:-1]) != np.signbit(y[1:])):
        x1, x2, y1, y2 = x[index], x[index + 1], y[index], y[index + 1]
        points.append(round(float(x1 - y1 * (x2 - x1) / (y2 - y1)), 2))
    return points
