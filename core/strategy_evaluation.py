# -*- coding: utf-8 -*-
"""Deterministic catalog evaluation using historical US and A-share bars."""

from __future__ import annotations

from datetime import date, datetime
from core.compat import UTC
import json
import math
import os
from typing import Any

import numpy as np
import pandas as pd

from backtest.engine import BacktestEngine
from core.database import DatabaseManager, get_database
from core.quant_journal import QuantJournal
from core.strategy_registry import StrategyRegistry
from core.strategy_scoring import StrategyScorer
from core.strategy_tracking import StrategyPerformanceTracker
from core.plans import can, effective_plan
from data.datasource import DataSourceError, get_resilient_data_source


SYSTEM_UNIVERSE = {
    "US": ("AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"),
    "CN": ("000001", "000858", "300750", "510050", "510300", "600519", "601318"),
}
SYSTEM_INITIAL_CASH = {"USD": 100_000.0, "CNY": 100_000.0}
_ADAPTIVE_SOURCE = "ciclotrade-adaptive"
_CYCLE_LABELS = {
    "premarket": "盤前",
    "intraday": "盤中",
    "after_close": "收盤後",
    "overnight": "夜盤",
    "manual": "管理員手動",
}


def _normalize_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Align vendor daily indexes without shifting their local trading date."""
    prepared = frame.copy()
    index = pd.DatetimeIndex(pd.to_datetime(prepared.index))
    if index.tz is not None:
        index = index.tz_localize(None)
    prepared.index = index
    return prepared[~prepared.index.duplicated(keep="last")].sort_index()


def _system_history(
    *,
    data_source=None,
    symbols_by_market: dict[str, tuple[str, ...]] | None = None,
    period: str = "3y",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load each production market from its supported adapter.

    An explicitly injected source remains authoritative for tests and future
    strategy servers. Production uses OpenD for US instruments and the
    existing Yahoo daily adapter for A shares.
    """
    universes = symbols_by_market or SYSTEM_UNIVERSE
    symbols = tuple(symbol for market_symbols in universes.values() for symbol in market_symbols)
    if data_source is not None:
        closes, volumes = data_source.history(symbols, period=period)
        return _normalize_history(closes), _normalize_history(volumes)

    sources = {
        "US": get_resilient_data_source(),
        "CN": get_resilient_data_source("yfinance"),
    }
    close_frames: list[pd.DataFrame] = []
    volume_frames: list[pd.DataFrame] = []
    failures: list[str] = []
    for market, market_symbols in universes.items():
        if not market_symbols:
            continue
        source = sources.get(market) or get_resilient_data_source("yfinance")
        try:
            closes, volumes = source.history(tuple(market_symbols), period=period)
        except (DataSourceError, OSError, RuntimeError, ValueError) as exc:
            failures.append(f"{market}: {exc}")
            continue
        if not closes.empty:
            close_frames.append(_normalize_history(closes))
            volume_frames.append(_normalize_history(volumes))
    if not close_frames:
        detail = "; ".join(failures) or "所有市场均未返回数据"
        raise DataSourceError(f"系统量化循环没有可用历史行情：{detail}")
    closes = pd.concat(close_frames, axis=1).sort_index()
    volumes = pd.concat(volume_frames, axis=1).reindex(closes.index).fillna(0)
    return closes, volumes


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


def _current_rule_position(close: pd.Series, definition: dict) -> bool:
    parameters = definition.get("parameters") or {}
    rules = definition.get("rules") or {}
    entry = _combined(rules.get("entry") or [], parameters, close)
    exit_trade = _combined(rules.get("exit") or [], parameters, close)
    position = False
    for index in range(len(close)):
        if not position and bool(entry.iloc[index]):
            position = True
        elif position and bool(exit_trade.iloc[index]):
            position = False
    return position


def _loss_streak(values: list[float]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def chronological_validation_start(
    close: pd.Series,
    train_ratio: float = 0.70,
    min_train: int = 80,
    min_test: int = 20,
) -> pd.Timestamp:
    prepared = close.astype(float).replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    split = int(len(prepared) * train_ratio)
    if split < min_train or len(prepared) - split < min_test:
        raise ValueError("歷史資料不足以進行 70/30 樣本外驗證。")
    return pd.Timestamp(prepared.index[split])


def evaluate_rule_strategy(
    close: pd.Series,
    definition: dict,
    initial_cash: float = 100_000,
    *,
    evaluation_start: pd.Timestamp | str | None = None,
) -> dict[str, Any]:
    close = close.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if len(close) < 80:
        raise ValueError("歷史資料不足以評估策略。")
    close = close[~close.index.duplicated(keep="last")].sort_index()
    start_index = 1
    if evaluation_start is not None:
        start_index = int(close.index.searchsorted(pd.Timestamp(evaluation_start), side="left"))
        if start_index < 1 or len(close) - start_index < 20:
            raise ValueError("樣本外區間不足以評估策略。")
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
    for index in range(start_index, len(close)):
        market_return = float(close.iloc[index] / close.iloc[index - 1] - 1)
        strategy_return = market_return if position else 0.0
        equity *= 1 + strategy_return
        daily_returns.append(strategy_return)
        signal_index = index - 1
        if not position and bool(entry.iloc[signal_index]):
            equity *= 0.999
            position = True
            entry_price = float(close.iloc[index])
        elif position and bool(exit_trade.iloc[signal_index]):
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
        "total_trades": len(trade_returns),
        "execution_model": "next_bar_close_proxy",
        "evaluation_start": str(close.index[start_index].date()),
        "evaluation_end": str(close.index[-1].date()),
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
    closes, _ = _system_history(
        data_source=data_source,
        symbols_by_market={"US": ("AAPL",), "CN": ("510300",)},
        period="3y",
    )
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


def run_system_quant_cycle(
    database: DatabaseManager | None = None,
    *,
    data_source=None,
    eval_date: date | str | None = None,
    cycle_slot: str | None = None,
) -> dict[str, Any]:
    """Re-rank strategies and rebalance once per market checkpoint."""
    db = database or get_database()
    slot = str(cycle_slot or "").strip().lower()
    if slot and slot not in _CYCLE_LABELS:
        raise ValueError("未知的量化循環時段。")
    closes, _ = _system_history(data_source=data_source, period="3y")
    if closes.empty:
        raise ValueError("系统量化循环没有可用历史行情。")
    day = str(eval_date or max(pd.Timestamp(closes[symbol].dropna().index[-1]).date() for symbol in closes))
    ranked = score_daily_catalog(db, data_source=data_source, eval_date=day)
    registry = StrategyRegistry(db)
    definitions = {item["key"]: item for item in registry.list()}
    winner = next((row for row in ranked if definitions[row["strategy_key"]]["family"] == "equity"), None)
    if winner is None:
        raise ValueError("没有可用于系统组合的正股策略。")
    definition = definitions[winner["strategy_key"]]
    ledger_key = os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
    journal = QuantJournal(db)
    state = journal.replay(ledger_key, initial_cash=SYSTEM_INITIAL_CASH)
    symbols = tuple(symbol for market_symbols in SYSTEM_UNIVERSE.values() for symbol in market_symbols)
    current = {
        item["symbol"]: float(item["quantity"])
        for item in state["positions"].values()
        if item["instrument_type"] == "stock" and item["symbol"] in symbols
    }
    marks: dict[str, float] = {}
    selected: dict[str, float] = {}
    for market, market_symbols in SYSTEM_UNIVERSE.items():
        candidates: list[tuple[float, str, float]] = []
        for symbol in market_symbols:
            if symbol not in closes:
                continue
            close = closes[symbol].dropna()
            if close.empty:
                continue
            price = float(close.iloc[-1])
            marks[f"{market}:STOCK:{symbol}"] = price
            if _current_rule_position(close, definition):
                lookback = min(126, len(close) - 1)
                strength = float(price / close.iloc[-lookback - 1] - 1) if lookback else 0.0
                candidates.append((strength, symbol, price))
        for _, symbol, price in sorted(candidates, reverse=True)[:3]:
            selected[symbol] = current.get(symbol) or float(max(1, math.floor(20_000 / price)))

    legs = []
    risk_levels: dict[str, dict[str, float | str]] = {}
    for market, market_symbols in SYSTEM_UNIVERSE.items():
        currency = "USD" if market == "US" else "CNY"
        for symbol in market_symbols:
            mark_key = f"{market}:STOCK:{symbol}"
            if mark_key not in marks:
                continue
            before = current.get(symbol, 0.0)
            target = selected.get(symbol, 0.0)
            delta = target - before
            if abs(delta) < 1e-12:
                continue
            price = marks[mark_key]
            close = closes[symbol].dropna()
            daily_volatility = float(close.pct_change().tail(20).std()) if len(close) > 20 else 0.0
            risk_pct = float(np.clip(daily_volatility * math.sqrt(10), 0.04, 0.12))
            if target > 0:
                risk_levels[mark_key] = {
                    "entry_price": round(price, 4),
                    "stop_loss": round(price * (1 - risk_pct), 4),
                    "target_price": round(price * (1 + risk_pct * 2), 4),
                    "method": "20日实现波动率 · 2:1盈亏比",
                }
            legs.append(
                {
                    "market": market,
                    "instrument_type": "stock",
                    "symbol": symbol,
                    "currency": currency,
                    "target_quantity": target,
                    "quantity_delta": delta,
                    "price": price,
                    "multiplier": 1,
                    "commission": 0,
                }
            )

    event_created = False
    cycle_label = _CYCLE_LABELS.get(slot, "每日收盤後")
    external_event_id = f"adaptive-{day}{f'-{slot}' if slot else ''}"
    if legs and not db.fetch_one(
        "SELECT id FROM quant_events WHERE source=? AND external_event_id=?",
        (_ADAPTIVE_SOURCE, external_event_id),
    ):
        event = journal.append_event(
            ledger_key=ledger_key,
            source=_ADAPTIVE_SOURCE,
            external_event_id=external_event_id,
            strategy_name=definition["name"],
            strategy_version=f"catalog-{day}",
            legs=legs,
            metadata={
                "reason": (
                    f"{cycle_label}對全部啟用策略進行真實歷史樣本外評分；本期最佳正股策略為"
                    f"{definition['name']}，按每个市场最多 3 个标的、单标的初始资金 20% 建立模拟目标仓位。"
                ),
                "cycle_slot": slot or "daily",
                "risk_level": definition["risk"],
                "risk_levels": risk_levels,
                "research_only": True,
                "selected_symbols": sorted(selected),
                "score": winner["weighted_score"],
            },
        )
        event_created = bool(event["created"])

    position_keys = set(journal.replay(ledger_key)["positions"])
    replay = journal.replay(
        ledger_key,
        marks={key: value for key, value in marks.items() if key in position_keys},
        initial_cash=SYSTEM_INITIAL_CASH,
    )
    snapshots_created = 0
    captured_at = datetime.now(UTC)
    for currency in ("USD", "CNY"):
        snapshot_id = external_event_id
        if db.fetch_one(
            "SELECT id FROM quant_equity_snapshots WHERE source=? AND external_snapshot_id=? AND currency=?",
            (_ADAPTIVE_SOURCE, snapshot_id, currency),
        ):
            continue
        totals = replay["currencies"].get(currency) or {
            "cash": SYSTEM_INITIAL_CASH[currency],
            "market_value": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
        }
        snapshot = journal.append_equity_snapshot(
            ledger_key=ledger_key,
            source=_ADAPTIVE_SOURCE,
            external_snapshot_id=snapshot_id,
            currency=currency,
            initial_cash=SYSTEM_INITIAL_CASH[currency],
            cash=totals["cash"],
            market_value=totals["market_value"],
            realized_pnl=totals["realized_pnl"],
            unrealized_pnl=totals["unrealized_pnl"],
            captured_at=captured_at,
        )
        snapshots_created += int(snapshot["created"])
    db.log_system_event(
        "QUANT_CYCLE",
        "STRATEGY",
        "服务器自适应量化循环完成",
        f"date={day} slot={slot or 'daily'} strategy={definition['key']} "
        f"event={int(event_created)} snapshots={snapshots_created}",
    )
    return {
        "eval_date": day,
        "cycle_slot": slot or "daily",
        "strategy_key": definition["key"],
        "strategy_name": definition["name"],
        "event_created": event_created,
        "leg_count": len(legs),
        "snapshots_created": snapshots_created,
        "selected_symbols": sorted(selected),
    }


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
    source = data_source or get_resilient_data_source()
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
