# -*- coding: utf-8 -*-
"""Backtrader 驱动的 8 策略滚动到期代理回测。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any

import backtrader as bt
import numpy as np
import pandas as pd

from core.database import DatabaseManager, get_database
from core.plans import backtest_years, can, effective_plan
from core.strategy_registry import StrategyRegistry
from data.datasource import get_data_source


@dataclass(frozen=True)
class BacktestResult:
    metrics: dict[str, float | int | str]
    equity: pd.DataFrame
    trades: pd.DataFrame


def option_payoff(name: str, start: float, end: float, shift: float, premium: float, wing: float) -> float:
    """返回每股到期损益；8 个策略共用一个可测试公式入口。"""
    strike = start * (1 + shift / 100)
    if name == "买入 Call":
        return max(end - strike, 0) - premium
    if name == "买入 Put":
        return max(strike - end, 0) - premium
    if name == "牛市价差":
        return max(end - strike, 0) - max(end - strike - wing, 0) - premium * 0.6
    if name == "熊市价差":
        return max(strike - end, 0) - max(strike - wing - end, 0) - premium * 0.6
    if name == "买入跨式":
        return abs(end - strike) - premium * 2
    if name == "蝶式":
        return max(end - strike + wing, 0) - 2 * max(end - strike, 0) + max(end - strike - wing, 0) - premium * 0.45
    if name == "备兑看涨":
        return end - start - max(end - strike, 0) + premium
    if name == "现金担保看跌":
        return premium - max(strike - end, 0)
    raise ValueError(f"不支持的策略：{name}")


class _RollingOptionStrategy(bt.Strategy):
    params = (
        ("strategy_name", "买入 Call"),
        ("dte", 45),
        ("strike_shift", 0.0),
        ("quantity", 1),
        ("initial_cash", 100_000.0),
    )

    def __init__(self):
        self.anchor_bar = 0
        self.anchor_price: float | None = None
        self.model_equity = float(self.p.initial_cash)
        self.equity_rows: list[dict[str, Any]] = []
        self.trade_rows: list[dict[str, Any]] = []

    def next(self):
        current_price = float(self.data.close[0])
        current_date = self.data.datetime.date(0)
        if self.anchor_price is None:
            self.anchor_price = current_price
            self.anchor_bar = len(self)
        elif len(self) - self.anchor_bar >= int(self.p.dte):
            history = np.asarray(self.data.close.get(size=min(21, len(self))), dtype=float)
            returns = np.diff(history) / history[:-1] if len(history) > 1 else np.array([])
            annual_vol = float(np.std(returns, ddof=1) * np.sqrt(252)) if len(returns) >= 5 else 0.25
            annual_vol = min(max(annual_vol, 0.08), 1.5)
            premium = self.anchor_price * annual_vol * np.sqrt(int(self.p.dte) / 365) * 0.4
            wing = self.anchor_price * 0.08
            raw_pnl = option_payoff(
                str(self.p.strategy_name), self.anchor_price, current_price, float(self.p.strike_shift), premium, wing
            ) * 100 * int(self.p.quantity)
            pnl = max(raw_pnl, -self.model_equity * 0.9)
            self.model_equity += pnl
            self.trade_rows.append(
                {
                    "开仓日": self.data.datetime.date(-int(self.p.dte)),
                    "到期日": current_date,
                    "开仓价": self.anchor_price,
                    "到期价": current_price,
                    "代理权利金": premium,
                    "损益": pnl,
                    "结果": "盈利" if pnl > 0 else "亏损" if pnl < 0 else "持平",
                }
            )
            self.anchor_price = current_price
            self.anchor_bar = len(self)
        self.equity_rows.append({"日期": current_date, "净值": self.model_equity})


class BacktestEngine:
    """真实标的 K 线进入 Backtrader；历史期权权利金由波动率代理。"""

    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def _simulate(
        series: pd.Series,
        strategy_name: str,
        dte: int,
        strike_shift: float,
        quantity: int,
        initial_cash: float,
    ) -> BacktestResult:
        index = pd.DatetimeIndex(series.index)
        if index.tz is not None:
            index = index.tz_localize(None)
        feed_frame = pd.DataFrame(
            {"open": series.values, "high": series.values, "low": series.values, "close": series.values, "volume": 0},
            index=index,
        )
        cerebro = bt.Cerebro(stdstats=False)
        cerebro.adddata(bt.feeds.PandasData(dataname=feed_frame))
        cerebro.addstrategy(
            _RollingOptionStrategy,
            strategy_name=strategy_name,
            dte=dte,
            strike_shift=strike_shift,
            quantity=quantity,
            initial_cash=initial_cash,
        )
        strategy = cerebro.run()[0]
        equity = pd.DataFrame(strategy.equity_rows)
        trades = pd.DataFrame(strategy.trade_rows)
        if trades.empty:
            raise ValueError("历史数据不足以完成至少 1 个到期周期。")
        peak = equity["净值"].cummax()
        drawdown = equity["净值"] / peak - 1
        returns = equity["净值"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 1 and returns.std() else 0.0
        metrics: dict[str, float | int | str] = {
            "return_rate": float(equity["净值"].iloc[-1] / initial_cash - 1),
            "max_drawdown": float(drawdown.min()),
            "win_rate": float((trades["损益"] > 0).mean()),
            "total_trades": len(trades),
            "ending_equity": float(equity["净值"].iloc[-1]),
            "sharpe": sharpe,
            "model": "Backtrader · 真实标的 K 线 + 波动率代理权利金",
        }
        return BacktestResult(metrics, equity, trades)

    def _save(
        self,
        user_id: int,
        strategy_name: str,
        symbol: str,
        series: pd.Series,
        result: BacktestResult,
        params: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        self.db.execute(
            """INSERT INTO backtest_records
               (user_id,strategy_name,symbol,start_date,end_date,return_rate,max_drawdown,win_rate,total_trades,params,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, strategy_name, symbol.upper(), str(series.index[0].date()), str(series.index[-1].date()),
                result.metrics["return_rate"], result.metrics["max_drawdown"], result.metrics["win_rate"],
                result.metrics["total_trades"], json.dumps(params, ensure_ascii=False), now,
            ),
        )
        self.db.execute(
            "INSERT INTO strategy_action_logs (user_id,strategy_name,action,params,result,created_at) VALUES (?,?,?,?,?,?)",
            (user_id, strategy_name, "BACKTEST", json.dumps(params, ensure_ascii=False), "success", now),
        )

    def run(
        self,
        user_id: int,
        strategy_name: str,
        symbol: str,
        years: int,
        dte: int,
        strike_shift: float,
        quantity: int,
        initial_cash: float = 100_000,
    ) -> BacktestResult:
        user = self.db.fetch_one(
            "SELECT plan_type,subscription_expire FROM users WHERE id=? AND is_active=1", (user_id,)
        )
        access = StrategyRegistry(self.db).check_plan_access(effective_plan(user or {}), strategy_name)
        if access is False:
            raise ValueError("当前订阅方案未开放该策略。")
        if access is True and years > backtest_years(effective_plan(user or {})):
            raise ValueError("回测年数超过当前订阅方案上限。")
        closes, _ = get_data_source().history((symbol,), period=f"{max(1, years)}y")
        series = closes.iloc[:, 0].dropna()
        result = self._simulate(series, strategy_name, dte, strike_shift, quantity, initial_cash)
        params = {
            "years": years, "dte": dte, "strike_shift": strike_shift, "quantity": quantity,
            "initial_cash": initial_cash, "model": result.metrics["model"],
        }
        self._save(user_id, strategy_name, symbol, series, result, params)
        return result

    def history(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM backtest_records WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
        )

    def optimize(self, user_id: int, strategy_name: str, symbol: str, years: int) -> tuple[dict[str, Any], pd.DataFrame]:
        user = self.db.fetch_one(
            "SELECT plan_type,subscription_expire FROM users WHERE id=? AND is_active=1", (user_id,)
        )
        plan = effective_plan(user or {})
        access = StrategyRegistry(self.db).check_plan_access(plan, strategy_name)
        if access is False:
            raise ValueError("当前订阅方案未开放该策略。")
        if not can(plan, "backtest_10y"):
            raise ValueError("参数优化仅对高级版及以上开放。")
        closes, _ = get_data_source().history((symbol,), period=f"{max(1, years)}y")
        series = closes.iloc[:, 0].dropna()
        rows = []
        results: dict[tuple[int, int], BacktestResult] = {}
        for dte in (30, 45, 60):
            for shift in (-5, 0, 5):
                result = self._simulate(series, strategy_name, dte, shift, 1, 100_000)
                results[(dte, shift)] = result
                rows.append({"DTE": dte, "行权价偏移": shift, **result.metrics})
        frame = pd.DataFrame(rows).sort_values(["return_rate", "max_drawdown"], ascending=[False, False]).reset_index(drop=True)
        best = frame.iloc[0].to_dict()
        best_result = results[(int(best["DTE"]), int(best["行权价偏移"]))]
        self._save(
            user_id, strategy_name, symbol, series, best_result,
            {"years": years, "dte": best["DTE"], "strike_shift": best["行权价偏移"], "quantity": 1, "optimization": "3x3 grid"},
        )
        return best, frame
