# -*- coding: utf-8 -*-
"""Backtrader 驱动的 8 策略滚动到期代理回测。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from core.compat import UTC
from hashlib import sha256
import json
from typing import Any

import backtrader as bt
import numpy as np
import pandas as pd

from core.database import DatabaseManager, get_database
from core.plans import backtest_years, can, effective_plan
from core.strategy_registry import StrategyRegistry
from data.datasource import get_data_source


MODEL_VERSION = "option-proxy-v2"
PROVENANCE_VERSION = 1
DEFAULT_COMMISSION_PER_CONTRACT = 0.65
DEFAULT_SLIPPAGE_PCT = 0.01
OPTION_LEGS = {
    "买入 Call": 1,
    "买入 Put": 1,
    "牛市价差": 2,
    "熊市价差": 2,
    "买入跨式": 2,
    "蝶式": 3,
    "备兑看涨": 1,
    "现金担保看跌": 1,
}


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
        ("commission_per_contract", DEFAULT_COMMISSION_PER_CONTRACT),
        ("slippage_pct", DEFAULT_SLIPPAGE_PCT),
        ("friction_multiplier", 1.0),
    )

    def __init__(self):
        self.anchor_bar = 0
        self.anchor_price: float | None = None
        self.anchor_date = None
        self.anchor_premium: float | None = None
        self.anchor_wing: float | None = None
        self.model_equity = float(self.p.initial_cash)
        self.equity_rows: list[dict[str, Any]] = []
        self.trade_rows: list[dict[str, Any]] = []
        self.bankrupt = False

    def _open_cycle(self, price: float, current_date) -> None:
        history = np.asarray(self.data.close.get(size=min(21, len(self))), dtype=float)
        if len(history) < 21:
            return
        returns = np.diff(history) / history[:-1]
        annual_vol = float(np.std(returns, ddof=1) * np.sqrt(252))
        annual_vol = min(max(annual_vol, 0.08), 1.5)
        self.anchor_price = price
        self.anchor_date = current_date
        self.anchor_bar = len(self)
        self.anchor_premium = price * annual_vol * np.sqrt(int(self.p.dte) / 365) * 0.4
        self.anchor_wing = price * 0.08

    def next(self):
        current_price = float(self.data.close[0])
        current_date = self.data.datetime.date(0)
        if self.bankrupt:
            self.equity_rows.append({"日期": current_date, "净值": self.model_equity})
            return
        if self.anchor_price is None:
            self._open_cycle(current_price, current_date)
        elif len(self) - self.anchor_bar >= int(self.p.dte):
            premium = float(self.anchor_premium or 0)
            wing = float(self.anchor_wing or 0)
            legs = OPTION_LEGS[str(self.p.strategy_name)]
            multiplier = float(self.p.friction_multiplier)
            commission = float(self.p.commission_per_contract) * legs * 2 * int(self.p.quantity) * multiplier
            slippage = premium * float(self.p.slippage_pct) * 100 * legs * 2 * int(self.p.quantity) * multiplier
            costs = commission + slippage
            raw_pnl = option_payoff(
                str(self.p.strategy_name), self.anchor_price, current_price, float(self.p.strike_shift), premium, wing
            ) * 100 * int(self.p.quantity)
            pnl = raw_pnl - costs
            self.model_equity += pnl
            self.trade_rows.append(
                {
                    "开仓日": self.anchor_date,
                    "到期日": current_date,
                    "开仓价": self.anchor_price,
                    "到期价": current_price,
                    "代理权利金": premium,
                    "交易成本": costs,
                    "毛损益": raw_pnl,
                    "损益": pnl,
                    "结果": "盈利" if pnl > 0 else "亏损" if pnl < 0 else "持平",
                }
            )
            self.anchor_price = None
            self.anchor_date = None
            self.anchor_premium = None
            self.anchor_wing = None
            self.bankrupt = self.model_equity <= 0
            if not self.bankrupt:
                self._open_cycle(current_price, current_date)
        self.equity_rows.append({"日期": current_date, "净值": self.model_equity})


class BacktestEngine:
    """真实标的 K 线进入 Backtrader；历史期权权利金由波动率代理。"""

    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def _prepare_series(series: pd.Series) -> pd.Series:
        prepared = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        prepared.index = pd.DatetimeIndex(prepared.index)
        if prepared.index.tz is not None:
            prepared.index = prepared.index.tz_localize(None)
        prepared = prepared[~prepared.index.duplicated(keep="last")].sort_index()
        if (prepared <= 0).any():
            raise ValueError("历史收盘价必须全部大于 0。")
        return prepared.astype(float)

    @staticmethod
    def _summarize(
        equity: pd.DataFrame,
        trades: pd.DataFrame,
        initial_cash: float,
        model: str,
    ) -> BacktestResult:
        if trades.empty:
            raise ValueError("历史数据不足以完成至少 1 个到期周期。")
        peak = equity["净值"].cummax()
        drawdown = equity["净值"] / peak - 1
        returns = equity["净值"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 and returns.std(ddof=1) else 0.0
        pnl = trades["损益"].astype(float)
        gains = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        profit_factor = min(float(gains.sum() / abs(losses.sum())), 999.0) if len(losses) else (999.0 if len(gains) else 0.0)
        metrics: dict[str, float | int | str] = {
            "return_rate": float(equity["净值"].iloc[-1] / initial_cash - 1),
            "max_drawdown": float(drawdown.min()),
            "win_rate": float((pnl > 0).mean()),
            "total_trades": len(trades),
            "ending_equity": float(equity["净值"].iloc[-1]),
            "sharpe": sharpe,
            "profit_factor": profit_factor,
            "expectancy": float(pnl.mean()),
            "total_costs": float(trades["交易成本"].sum()),
            "sample_quality": "sufficient" if len(trades) >= 30 else "insufficient",
            "bankrupt": int(equity["净值"].iloc[-1] <= 0),
            "model": model,
        }
        return BacktestResult(metrics, equity.reset_index(drop=True), trades.reset_index(drop=True))

    @staticmethod
    def _simulate(
        series: pd.Series,
        strategy_name: str,
        dte: int,
        strike_shift: float,
        quantity: int,
        initial_cash: float,
        commission_per_contract: float = DEFAULT_COMMISSION_PER_CONTRACT,
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
        friction_multiplier: float = 1.0,
    ) -> BacktestResult:
        if strategy_name not in OPTION_LEGS:
            raise ValueError(f"不支持的策略：{strategy_name}")
        if not 1 <= int(dte) <= 365 or int(quantity) < 1 or initial_cash <= 0:
            raise ValueError("DTE、合约数量或初始资金无效。")
        if commission_per_contract < 0 or not 0 <= slippage_pct <= 0.25 or friction_multiplier < 0:
            raise ValueError("交易成本参数无效。")
        series = BacktestEngine._prepare_series(series)
        if len(series) < 21 + int(dte) + 1:
            raise ValueError("历史数据不足以完成波动率预热和至少 1 个到期周期。")
        index = pd.DatetimeIndex(series.index)
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
            commission_per_contract=commission_per_contract,
            slippage_pct=slippage_pct,
            friction_multiplier=friction_multiplier,
        )
        strategy = cerebro.run()[0]
        equity = pd.DataFrame(strategy.equity_rows)
        trades = pd.DataFrame(strategy.trade_rows)
        return BacktestEngine._summarize(
            equity,
            trades,
            initial_cash,
            "Backtrader · 开仓时点波动率代理 · 含佣金与滑点 · 非真实历史期权",
        )

    @staticmethod
    def _provenance(series: pd.Series, source_name: str, params: dict[str, Any]) -> dict[str, Any]:
        prepared = BacktestEngine._prepare_series(series)
        data_bytes = prepared.rename("close").to_csv(
            date_format="%Y-%m-%dT%H:%M:%S", float_format="%.10g"
        ).encode("utf-8")
        params_bytes = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {
            "schema_version": PROVENANCE_VERSION,
            "model_version": MODEL_VERSION,
            "source": source_name,
            "interval": "1d",
            "adjustment": "adapter_default",
            "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "start": str(prepared.index[0].date()),
            "end": str(prepared.index[-1].date()),
            "rows": len(prepared),
            "data_sha256": sha256(data_bytes).hexdigest(),
            "params_sha256": sha256(params_bytes).hexdigest(),
        }

    @staticmethod
    def _selection_score(metrics: dict[str, float | int | str]) -> float:
        trade_penalty = 0.25 if int(metrics["total_trades"]) < 10 else 0.0
        return float(metrics["return_rate"]) + float(metrics["max_drawdown"]) + 0.1 * float(metrics["sharpe"]) - trade_penalty

    @staticmethod
    def _walk_forward_splits(length: int, max_dte: int = 60) -> list[tuple[int, int]]:
        train_end = int(length * 0.70)
        remaining = length - train_end
        minimum_test = 21 + max_dte + 1
        if train_end < 21 + max_dte + 1 or remaining < minimum_test:
            raise ValueError("严谨验证至少需要约 14 个月日线；请选择更长历史区间。")
        fold_count = min(3, max(1, remaining // minimum_test))
        boundaries = np.linspace(train_end, length, fold_count + 1, dtype=int)
        return [(int(boundaries[index]), int(boundaries[index + 1])) for index in range(fold_count)]

    @classmethod
    def _walk_forward(
        cls,
        series: pd.Series,
        strategy_name: str,
        initial_cash: float = 100_000,
    ) -> tuple[BacktestResult, BacktestResult, dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
        series = cls._prepare_series(series)
        splits = cls._walk_forward_splits(len(series))
        grid = [(dte, shift) for dte in (30, 45, 60) for shift in (-5, 0, 5)]
        candidate_metrics: dict[tuple[int, int], list[dict[str, float | int | str]]] = defaultdict(list)
        selected_counts: dict[tuple[int, int], int] = defaultdict(int)
        equity_frames: list[pd.DataFrame] = []
        trade_frames: list[pd.DataFrame] = []
        stress_equity_frames: list[pd.DataFrame] = []
        stress_trade_frames: list[pd.DataFrame] = []
        windows: list[dict[str, Any]] = []
        capital = stress_capital = float(initial_cash)

        for fold, (test_start, test_end) in enumerate(splits, start=1):
            train = series.iloc[:test_start]
            candidates: dict[tuple[int, int], BacktestResult] = {}
            for dte, shift in grid:
                candidate = cls._simulate(train, strategy_name, dte, shift, 1, initial_cash)
                candidates[(dte, shift)] = candidate
                candidate_metrics[(dte, shift)].append(candidate.metrics)
            selected = max(
                grid,
                key=lambda item: (
                    cls._selection_score(candidates[item].metrics),
                    float(candidates[item].metrics["max_drawdown"]),
                    -item[0],
                    -abs(item[1]),
                ),
            )
            selected_counts[selected] += 1
            context = series.iloc[max(0, test_start - 20):test_end]
            base = cls._simulate(context, strategy_name, selected[0], selected[1], 1, capital)
            stress = cls._simulate(
                context, strategy_name, selected[0], selected[1], 1, stress_capital, friction_multiplier=2.0
            )
            first_oos_date = series.index[test_start]
            base_equity = base.equity[pd.to_datetime(base.equity["日期"]) >= first_oos_date].copy()
            stress_equity = stress.equity[pd.to_datetime(stress.equity["日期"]) >= first_oos_date].copy()
            base_trades = base.trades[pd.to_datetime(base.trades["开仓日"]) >= first_oos_date].copy()
            stress_trades = stress.trades[pd.to_datetime(stress.trades["开仓日"]) >= first_oos_date].copy()
            base_trades["验证窗口"] = fold
            stress_trades["验证窗口"] = fold
            equity_frames.append(base_equity)
            trade_frames.append(base_trades)
            stress_equity_frames.append(stress_equity)
            stress_trade_frames.append(stress_trades)
            capital = float(base_equity["净值"].iloc[-1])
            stress_capital = float(stress_equity["净值"].iloc[-1])
            windows.append(
                {
                    "fold": fold,
                    "train_start": str(train.index[0].date()),
                    "train_end": str(train.index[-1].date()),
                    "test_start": str(first_oos_date.date()),
                    "test_end": str(series.index[test_end - 1].date()),
                    "selected": {"dte": selected[0], "strike_shift": selected[1]},
                    "train_score": cls._selection_score(candidates[selected].metrics),
                    "test_return": float(base.metrics["return_rate"]),
                    "stress_return": float(stress.metrics["return_rate"]),
                }
            )
            if capital <= 0 or stress_capital <= 0:
                break

        equity = pd.concat(equity_frames, ignore_index=True).drop_duplicates("日期", keep="last")
        trades = pd.concat(trade_frames, ignore_index=True)
        stress_equity = pd.concat(stress_equity_frames, ignore_index=True).drop_duplicates("日期", keep="last")
        stress_trades = pd.concat(stress_trade_frames, ignore_index=True)
        result = cls._summarize(
            equity, trades, initial_cash,
            "70/30 Walk-Forward 样本外 · 开仓时点波动率代理 · 非真实历史期权",
        )
        stress_result = cls._summarize(
            stress_equity, stress_trades, initial_cash,
            "70/30 Walk-Forward 样本外 · 双倍交易成本压力",
        )
        rows = []
        for dte, shift in grid:
            metrics = candidate_metrics[(dte, shift)]
            rows.append(
                {
                    "DTE": dte,
                    "行权价偏移": shift,
                    "训练评分": float(np.mean([cls._selection_score(item) for item in metrics])),
                    "训练回报": float(np.mean([float(item["return_rate"]) for item in metrics])),
                    "训练回撤": float(np.mean([float(item["max_drawdown"]) for item in metrics])),
                    "训练Sharpe": float(np.mean([float(item["sharpe"]) for item in metrics])),
                    "入选窗口": selected_counts[(dte, shift)],
                }
            )
        frame = pd.DataFrame(rows).sort_values(
            ["入选窗口", "训练评分", "训练回撤"], ascending=[False, False, False]
        ).reset_index(drop=True)
        representative = (int(frame.iloc[0]["DTE"]), int(frame.iloc[0]["行权价偏移"]))
        best = {
            "DTE": representative[0],
            "行权价偏移": representative[1],
            "folds": len(windows),
            "selection_rate": selected_counts[representative] / len(windows),
            "oos_return_rate": result.metrics["return_rate"],
            "oos_max_drawdown": result.metrics["max_drawdown"],
            "oos_win_rate": result.metrics["win_rate"],
            "oos_sharpe": result.metrics["sharpe"],
            "oos_total_trades": result.metrics["total_trades"],
            "stress_return_rate": stress_result.metrics["return_rate"],
            "stress_max_drawdown": stress_result.metrics["max_drawdown"],
            "sample_quality": result.metrics["sample_quality"],
        }
        return result, stress_result, best, frame, windows

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
        source = get_data_source()
        closes, _ = source.history((symbol,), period=f"{max(1, years)}y")
        series = closes.iloc[:, 0].dropna()
        result = self._simulate(series, strategy_name, dte, strike_shift, quantity, initial_cash)
        params = {
            "years": years, "dte": dte, "strike_shift": strike_shift, "quantity": quantity,
            "initial_cash": initial_cash, "model": result.metrics["model"],
            "commission_per_contract": DEFAULT_COMMISSION_PER_CONTRACT,
            "slippage_pct": DEFAULT_SLIPPAGE_PCT,
            "provenance": self._provenance(series, source.name, {
                "strategy": strategy_name, "dte": dte, "strike_shift": strike_shift,
                "quantity": quantity, "initial_cash": initial_cash,
            }),
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
        source = get_data_source()
        closes, _ = source.history((symbol,), period=f"{max(1, years)}y")
        series = closes.iloc[:, 0].dropna()
        result, stress_result, best, frame, windows = self._walk_forward(series, strategy_name)
        params = {
            "years": years,
            "validation": "70/30 expanding walk-forward",
            "windows": windows,
            "representative": {"dte": best["DTE"], "strike_shift": best["行权价偏移"]},
            "commission_per_contract": DEFAULT_COMMISSION_PER_CONTRACT,
            "slippage_pct": DEFAULT_SLIPPAGE_PCT,
            "stress_friction_multiplier": 2.0,
            "stress_metrics": stress_result.metrics,
        }
        params["provenance"] = self._provenance(
            series, source.name,
            {"strategy": strategy_name, "years": years, "grid": "dte=30/45/60;shift=-5/0/5"},
        )
        first_oos = int(len(series) * 0.70)
        self._save(
            user_id, strategy_name, symbol, series.iloc[first_oos:], result, params,
        )
        return best, frame
