# -*- coding: utf-8 -*-
"""Deterministic catalog evaluation using historical US and A-share bars."""

from __future__ import annotations

from datetime import UTC, date, datetime
import json
import math
from typing import Any

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from core.database import DatabaseManager, get_database
from core.strategy_registry import StrategyRegistry
from core.strategy_scoring import StrategyScorer
from core.strategy_tracking import StrategyPerformanceTracker
from core.plans import can, effective_plan
from data.datasource import get_data_source


def _value(rule: dict, parameters: dict, key: str, default: float = 0) -> float:
    if key in rule:
        return float(rule[key])
    name = rule.get(f"{key}_param") or rule.get(f"{key}_from_param")
    value = float(parameters.get(name, default)) if name else float(default)
    return -value if key == "value" and rule.get("negate") else value


def _rule(rule: dict, parameters: dict, close: pd.Series) -> pd.Series:
    operator = rule.get("operator")
    if operator in {"cross_above_ma", "cross_below_ma"}:
        period = int(_value(rule, parameters, "period"))
        left = close.rolling(period, min_periods=period).mean() if rule.get("confirm_period_param") else close
        if confirm := rule.get("confirm_period_param"):
            confirm_period = int(parameters[confirm])
            right = close.rolling(confirm_period, min_periods=confirm_period).mean()
        else:
            right = close.rolling(period, min_periods=period).mean()
        above = (left > right) & (left.shift(1) <= right.shift(1))
        below = (left < right) & (left.shift(1) >= right.shift(1))
        return (above if operator == "cross_above_ma" else below).fillna(False)
    if operator in {"cross_above_bollinger", "cross_below_bollinger"}:
        period = int(_value(rule, parameters, "period"))
        deviations = _value(rule, parameters, "deviation", 2)
        middle = close.rolling(period, min_periods=period).mean()
        standard = close.rolling(period, min_periods=period).std(ddof=0)
        band = middle + standard * deviations if operator == "cross_above_bollinger" else middle - standard * deviations
        crossed = (close > band) & (close.shift(1) <= band.shift(1)) if operator == "cross_above_bollinger" else (close < band) & (close.shift(1) >= band.shift(1))
        return crossed.fillna(False)
    if rule.get("indicator") == "rsi":
        period = int(_value(rule, parameters, "period", 14))
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
        loss = -delta.clip(upper=0).rolling(period, min_periods=period).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        value = _value(rule, parameters, "value")
        return ((rsi < value) if operator == "lt" else (rsi > value)).fillna(False)
    if rule.get("indicator") == "return":
        period = int(_value(rule, parameters, "period"))
        momentum = close.pct_change(period)
        value = _value(rule, parameters, "value")
        return ((momentum < value) if operator == "lt" else (momentum > value)).fillna(False)
    if rule.get("indicator") == "zscore":
        period = int(_value(rule, parameters, "period"))
        average = close.rolling(period, min_periods=period).mean()
        standard = close.rolling(period, min_periods=period).std(ddof=0).replace(0, np.nan)
        zscore = (close - average) / standard
        value = _value(rule, parameters, "value")
        return ((zscore < value) if operator == "lt" else (zscore > value)).fillna(False)
    raise ValueError(f"不支援的策略規則：{rule.get('indicator')} / {operator}")


def _combined(rules: list[dict], parameters: dict, close: pd.Series) -> pd.Series:
    result = pd.Series(True, index=close.index)
    for item in rules:
        result &= _rule(item, parameters, close)
    return result


def _loss_streak(values: list[float]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def evaluate_rule_strategy(close: pd.Series, definition: dict, initial_cash: float = 100_000) -> dict[str, Any]:
    close = close.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(close) < 80:
        raise ValueError("歷史資料不足以評估策略。")
    parameters = definition.get("parameters") or {}
    rules = definition.get("rules") or {}
    entry = _combined(rules.get("entry") or [], parameters, close)
    exit_trade = _combined(rules.get("exit") or [], parameters, close)
    equity = float(initial_cash)
    peak = equity
    drawdown = 0.0
    position = False
    entry_price: float | None = None
    daily_returns: list[float] = []
    trade_returns: list[float] = []
    values = [equity]
    for index in range(1, len(close)):
        market_return = float(close.iloc[index] / close.iloc[index - 1] - 1)
        strategy_return = market_return if position else 0.0
        equity *= 1 + strategy_return
        daily_returns.append(strategy_return)
        if not position and bool(entry.iloc[index]):
            equity *= 0.999
            position = True
            entry_price = float(close.iloc[index])
        elif position and bool(exit_trade.iloc[index]):
            equity *= 0.999
            if entry_price:
                trade_returns.append(float(close.iloc[index]) / entry_price - 1 - 0.002)
            position = False
            entry_price = None
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - 1)
        values.append(equity)
    if position and entry_price:
        trade_returns.append(float(close.iloc[-1]) / entry_price - 1 - 0.001)
    returns = pd.Series(daily_returns)
    sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(252)) if len(returns) > 1 and returns.std(ddof=1) else 0.0
    wins = [value for value in trade_returns if value > 0]
    losses = [value for value in trade_returns if value < 0]
    ratio = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else (1.0 if wins else 0.0)
    return {
        "total_return": equity / initial_cash - 1,
        "max_drawdown": abs(drawdown),
        "sharpe_ratio": sharpe,
        "profit_loss_ratio": ratio,
        "consecutive_losses": _loss_streak(trade_returns),
        "equity_curve": values,
        "win_rate": len(wins) / len(trade_returns) if trade_returns else 0.0,
    }


def _option_metrics(close: pd.Series, definition: dict) -> dict[str, Any]:
    parameters = definition.get("parameters") or {}
    result = BacktestEngine._simulate(
        close,
        str((definition.get("rules") or {}).get("option_strategy_name", definition["name"])),
        int(parameters.get("dte", 45)),
        float(parameters.get("strike_shift", 0)),
        int(parameters.get("quantity", 1)),
        100_000,
    )
    pnl_column = "損益" if "損益" in result.trades.columns else "损益"
    pnl = result.trades[pnl_column].astype(float).tolist()
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    ratio = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses else (1.0 if wins else 0.0)
    return {
        "total_return": result.metrics["return_rate"],
        "max_drawdown": abs(float(result.metrics["max_drawdown"])),
        "sharpe_ratio": result.metrics["sharpe"],
        "profit_loss_ratio": ratio,
        "consecutive_losses": _loss_streak(pnl),
    }


def score_daily_catalog(
    database: DatabaseManager | None = None,
    *,
    data_source=None,
    eval_date: date | str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every active definition and persist one real daily ranking."""
    db = database or get_database()
    registry = StrategyRegistry(db)
    registry.sync_catalog()
    definitions = registry.list()
    source = data_source or get_data_source()
    closes, _ = source.history(("AAPL", "510300"), period="3y")
    metrics: list[dict[str, Any]] = []
    for definition in definitions:
        if definition["family"] == "option":
            values = _option_metrics(closes["AAPL"].dropna(), definition)
        else:
            samples = [
                evaluate_rule_strategy(closes[symbol].dropna(), definition)
                for symbol in ("AAPL", "510300") if symbol in closes and closes[symbol].notna().sum() >= 80
            ]
            if not samples:
                raise ValueError("美股與 A 股歷史資料均不足。")
            values = {
                "total_return": sum(item["total_return"] for item in samples) / len(samples),
                "max_drawdown": max(item["max_drawdown"] for item in samples),
                "sharpe_ratio": sum(item["sharpe_ratio"] for item in samples) / len(samples),
                "profit_loss_ratio": sum(item["profit_loss_ratio"] for item in samples) / len(samples),
                "consecutive_losses": max(item["consecutive_losses"] for item in samples),
            }
        metrics.append({"strategy_key": definition["key"], **values})
    ranked = StrategyScorer(db).evaluate(metrics, eval_date=eval_date)
    db.log_system_event(
        "STRATEGY_SCORE", "STRATEGY", "每日策略評分完成",
        f"candidates={len(ranked)} top3={','.join(row['strategy_key'] for row in ranked[:3])}",
    )
    return ranked


def update_saved_strategy_performance(
    database: DatabaseManager | None = None,
    *,
    data_source=None,
    eval_date: date | str | None = None,
) -> dict[str, int]:
    """Update evaluable, entitled saved strategies without inventing missing data."""
    db = database or get_database()
    registry = StrategyRegistry(db)
    registry.sync_catalog()
    tracker = StrategyPerformanceTracker(db)
    source = data_source or get_data_source()
    rows = db.fetch_all(
        """SELECT s.*,u.plan_type,u.subscription_expire FROM saved_strategies s
           JOIN users u ON u.id=s.user_id WHERE s.is_active=1 AND u.is_active=1 ORDER BY s.id"""
    )
    completed = skipped = 0
    for row in rows:
        if not can(effective_plan(row), "strategy_tracking"):
            skipped += 1
            continue
        try:
            config = json.loads(row["config_json"])
            symbol = str(config.get("symbol") or "AAPL").strip().upper()
            closes, _ = source.history((symbol,), period="3y")
            close = closes[symbol].dropna()
            if row.get("strategy_key"):
                definition = registry.get(str(row["strategy_key"]))
                definition["parameters"] = {**definition.get("parameters", {}), **(config.get("parameters") or {})}
            elif isinstance(config.get("parsed"), dict):
                parsed = config["parsed"]
                definition = {
                    "family": "equity", "parameters": {},
                    "rules": {"entry": parsed.get("entry", []), "exit": parsed.get("exit", [])},
                }
            else:
                skipped += 1
                continue
            if definition.get("family") == "option":
                parameters = definition.get("parameters") or {}
                result = BacktestEngine._simulate(
                    close, str((definition.get("rules") or {}).get("option_strategy_name", definition.get("name"))),
                    int(parameters.get("dte", 45)), float(parameters.get("strike_shift", 0)),
                    int(parameters.get("quantity", 1)), 100_000,
                )
                curve = result.equity["净值"].astype(float).tolist()
                win_rate = float(result.metrics["win_rate"])
            else:
                result = evaluate_rule_strategy(close, definition)
                curve = [float(value) for value in result["equity_curve"]]
                win_rate = float(result["win_rate"])
            series = pd.Series(curve)
            returns = series.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
            peak = series.cummax()
            years = max(len(series) / 252, 1 / 252)
            annual_return = float((series.iloc[-1] / series.iloc[0]) ** (1 / years) - 1) if series.iloc[0] > 0 and series.iloc[-1] > 0 else -1.0
            sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(252)) if len(returns) > 1 and returns.std(ddof=1) else 0.0
            baseline = float(series.iloc[-min(22, len(series))])
            tracker.record_performance(
                int(row["user_id"]), int(row["id"]),
                {
                    "return_30d": float(series.iloc[-1] / baseline - 1) if baseline else 0.0,
                    "max_drawdown": abs(float((series / peak - 1).min())),
                    "annual_return": annual_return,
                    "sharpe_ratio": sharpe,
                    "win_rate": win_rate,
                    "equity_curve": curve[-90:],
                },
                eval_date=eval_date,
            )
            completed += 1
        except (KeyError, TypeError, ValueError, RuntimeError):
            skipped += 1
    db.log_system_event(
        "STRATEGY_TRACK", "STRATEGY", "策略履歷每日更新完成",
        f"completed={completed} skipped={skipped}",
    )
    return {"completed": completed, "skipped": skipped}
