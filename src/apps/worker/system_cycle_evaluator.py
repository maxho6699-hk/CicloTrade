"""Pure, conservative research evaluation for the canonical system universe.

This module deliberately has no database dependency.  It reads the checked-in
strategy catalog and evaluates only active equity rules against injected daily
history, producing long/flat paper targets for the shadow-cycle spool.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from core.strategy_evaluation import (
    SYSTEM_UNIVERSE,
    _current_rule_position,
    chronological_validation_start,
    evaluate_rule_strategy,
)


MODEL_EQUITY = 100_000.0
MAX_RISK_PER_CANDIDATE = 0.005
MAX_NOTIONAL_PER_STOCK = 0.20
MINIMUM_BARS = 80
DEFAULT_CATALOG = Path(__file__).resolve().parents[3] / "strategies" / "catalog.yaml"


class SystemCycleEvaluationError(ValueError):
    """Raised when the local catalog is not a usable equity-rule catalog."""


def catalog_snapshot_sha256(catalog_path: str | Path = DEFAULT_CATALOG) -> str:
    """Return a content hash for the read-only strategy source."""
    return hashlib.sha256(Path(catalog_path).read_bytes()).hexdigest()


def load_active_equity_strategies(catalog_path: str | Path = DEFAULT_CATALOG) -> list[dict[str, Any]]:
    """Load active equity definitions directly from YAML; no registry/database sync."""
    path = Path(catalog_path)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SystemCycleEvaluationError(f"unable to read strategy catalog: {exc}") from exc
    rows = payload.get("strategies") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise SystemCycleEvaluationError("strategy catalog must contain a strategies list")
    active: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping) or item.get("family") != "equity" or item.get("active", True) is not True:
            continue
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("key", "name")):
            continue
        if not isinstance(item.get("parameters"), Mapping) or not isinstance(item.get("rules"), Mapping):
            continue
        active.append(dict(item))
    if not active:
        raise SystemCycleEvaluationError("strategy catalog has no active equity rules")
    return sorted(active, key=lambda item: str(item["key"]))


def source_snapshot_sha256(closes: pd.DataFrame, *, evaluation_date: date) -> str:
    """Hash the supplied raw close observations without inventing missing values."""
    entries: list[tuple[str, str, float]] = []
    if isinstance(closes, pd.DataFrame):
        for symbol in _symbols():
            if symbol not in closes.columns:
                continue
            series = _prepared_series(closes[symbol], evaluation_date)
            for timestamp, value in series.items():
                entries.append((symbol, pd.Timestamp(timestamp).date().isoformat(), float(value)))
    body = json.dumps(entries, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def evaluate_system_cycle(
    closes: pd.DataFrame,
    *,
    evaluation_date: date,
    catalog_path: str | Path = DEFAULT_CATALOG,
) -> dict[str, Any]:
    """Evaluate all active equity rules and return deterministic shadow inputs.

    The ranking is a conservative historical-research heuristic only.  It is
    intentionally not described as a validated or strict out-of-sample result.
    """
    definitions = load_active_equity_strategies(catalog_path)
    prepared = {symbol: _prepared_series(closes[symbol], evaluation_date) if isinstance(closes, pd.DataFrame) and symbol in closes else pd.Series(dtype=float) for symbol in _symbols()}
    ranked = _rank_definitions(definitions, prepared)
    strategy = ranked[0]["definition"] if ranked else definitions[0]
    stock_results: dict[str, dict[str, Any]] = {}
    for symbol in _symbols():
        series = prepared[symbol]
        if len(series) < MINIMUM_BARS:
            stock_results[symbol] = _no_data(len(series), "insufficient_valid_daily_history")
            continue
        try:
            is_long = bool(_current_rule_position(series, strategy))
        except (TypeError, ValueError, KeyError, OverflowError):
            stock_results[symbol] = _no_data(len(series), "rule_signal_unavailable")
            continue
        price = float(series.iloc[-1])
        quantity = _paper_quantity(price) if is_long else 0.0
        stock_results[symbol] = {
            "status": "coverage",
            "rows": len(series),
            "dataset_end": pd.Timestamp(series.index[-1]).date().isoformat(),
            "selected": bool(quantity > 0),
            "signal_state": "long" if quantity > 0 else "flat",
            "latest_price": price,
            "target_quantity": quantity,
        }
    return {
        "strategy": {
            "key": str(strategy["key"]),
            "name": str(strategy["name"]),
            "version": f"catalog-{catalog_snapshot_sha256(catalog_path)[:16]}",
            "research_ranking": [
                {"strategy_key": item["definition"]["key"], "conservative_score": item["score"], "observations": item["observations"]}
                for item in ranked
            ],
            "label": "conservative historical research ranking; not strict OOS or validated performance",
        },
        "stock_results": stock_results,
        "catalog_snapshot_sha256": catalog_snapshot_sha256(catalog_path),
        "source_snapshot_sha256": source_snapshot_sha256(closes, evaluation_date=evaluation_date),
    }


def _rank_definitions(definitions: list[dict[str, Any]], prepared: Mapping[str, pd.Series]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for definition in definitions:
        samples: list[dict[str, Any]] = []
        for series in prepared.values():
            if len(series) < MINIMUM_BARS:
                continue
            try:
                start = chronological_validation_start(series)
                samples.append(evaluate_rule_strategy(series, definition, initial_cash=MODEL_EQUITY, evaluation_start=start))
            except (TypeError, ValueError, KeyError, OverflowError, ZeroDivisionError):
                continue
        if not samples:
            continue
        # Penalise drawdown and loss streaks more heavily than returns.  This is
        # deterministic research ordering, never a trading recommendation.
        score = sum(
            float(item["total_return"]) * 100.0
            + float(item["sharpe_ratio"]) * 2.0
            - float(item["max_drawdown"]) * 150.0
            - float(item["consecutive_losses"]) * 0.25
            for item in samples
        ) / len(samples)
        ranked.append({"definition": definition, "score": float(format(score, ".12g")), "observations": len(samples)})
    return sorted(ranked, key=lambda item: (-item["score"], str(item["definition"]["key"])))


def _paper_quantity(price: float) -> float:
    """Use the stricter of the 20% notional and 0.5% model-risk budgets.

    A conservative fixed 10% adverse-move assumption makes the risk cap
    explicit without manufacturing a per-symbol stop from incomplete inputs.
    """
    if not math.isfinite(price) or price <= 0:
        return 0.0
    notional_cap = MODEL_EQUITY * MAX_NOTIONAL_PER_STOCK
    risk_based_notional = MODEL_EQUITY * MAX_RISK_PER_CANDIDATE / 0.10
    return float(max(0, math.floor(min(notional_cap, risk_based_notional) / price)))


def _prepared_series(value: Any, evaluation_date: date) -> pd.Series:
    if not isinstance(value, pd.Series):
        return pd.Series(dtype=float)
    try:
        index = pd.to_datetime(value.index, errors="coerce")
        if isinstance(index, pd.DatetimeIndex) and index.tz is not None:
            index = index.tz_localize(None)
        numeric = pd.to_numeric(value, errors="coerce")
        series = pd.Series(numeric.to_numpy(), index=index, dtype=float)
    except (TypeError, ValueError):
        return pd.Series(dtype=float)
    series = series[~series.index.isna()]
    series = series.replace([math.inf, -math.inf], float("nan")).dropna()
    series = series[series > 0]
    series = series[series.index <= pd.Timestamp(evaluation_date)]
    return series[~series.index.duplicated(keep="last")].sort_index()


def _no_data(rows: int, reason: str) -> dict[str, Any]:
    return {
        "status": "no_data",
        "rows": max(0, int(rows)),
        "reason": reason,
    }


def _symbols() -> tuple[str, ...]:
    return tuple(symbol for market in ("US", "CN") for symbol in SYSTEM_UNIVERSE[market])
