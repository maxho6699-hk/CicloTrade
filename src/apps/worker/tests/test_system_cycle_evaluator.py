from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from core.strategy_evaluation import SYSTEM_UNIVERSE
from src.apps.worker.system_cycle_evaluator import (
    MAX_NOTIONAL_PER_STOCK,
    MAX_RISK_PER_CANDIDATE,
    MODEL_EQUITY,
    evaluate_system_cycle,
)


def _closes(*, missing: set[str] = frozenset(), invalid: set[str] = frozenset()) -> pd.DataFrame:
    index = pd.date_range("2025-05-01", periods=330, freq="B")
    values: dict[str, pd.Series] = {}
    for offset, symbol in enumerate(symbol for market in ("US", "CN") for symbol in SYSTEM_UNIVERSE[market]):
        if symbol in missing:
            continue
        series = pd.Series(50.0 + offset + np.arange(len(index)) * 0.2, index=index)
        if symbol in invalid:
            series.iloc[-1] = np.nan
            series.iloc[-2] = np.inf
        values[symbol] = series
    return pd.DataFrame(values, index=index)


def test_evaluates_exact_canonical_thirteen_stock_universe_without_database():
    result = evaluate_system_cycle(_closes(), evaluation_date=date(2026, 8, 12))

    expected = [symbol for market in ("US", "CN") for symbol in SYSTEM_UNIVERSE[market]]
    assert list(result["stock_results"]) == expected
    assert len(result["stock_results"]) == 13
    assert all(row["status"] == "coverage" for row in result["stock_results"].values())
    assert "not strict OOS" in result["strategy"]["label"]
    assert len(result["catalog_snapshot_sha256"]) == len(result["source_snapshot_sha256"]) == 64


def test_partial_and_non_finite_history_is_truthful_no_data_not_fabricated():
    result = evaluate_system_cycle(_closes(missing={"MSFT"}, invalid={"000001"}), evaluation_date=date(2026, 8, 12))

    assert result["stock_results"]["MSFT"]["status"] == "no_data"
    # Two invalid values do not cause a price to be invented; valid history may
    # still support a transparent coverage record with the last finite price.
    assert result["stock_results"]["000001"]["latest_price"] > 0
    assert result["stock_results"]["000001"]["rows"] == 328


def test_paper_targets_are_long_flat_and_respect_notional_and_risk_caps():
    result = evaluate_system_cycle(_closes(), evaluation_date=date(2026, 8, 12))

    for row in result["stock_results"].values():
        if row["status"] == "no_data":
            assert row["target_quantity"] == 0
            continue
        assert row["signal_state"] in {"long", "flat"}
        assert row["target_quantity"] >= 0
        notional = row["target_quantity"] * row["latest_price"]
        assert notional <= MODEL_EQUITY * MAX_NOTIONAL_PER_STOCK + 1e-8
        assert notional * 0.10 <= MODEL_EQUITY * MAX_RISK_PER_CANDIDATE + 1e-8


def test_history_and_catalog_hashes_are_stable_for_equal_inputs():
    first = evaluate_system_cycle(_closes(), evaluation_date=date(2026, 8, 12))
    second = evaluate_system_cycle(_closes(), evaluation_date=date(2026, 8, 12))

    assert first["source_snapshot_sha256"] == second["source_snapshot_sha256"]
    assert first["catalog_snapshot_sha256"] == second["catalog_snapshot_sha256"]
    assert first["strategy"] == second["strategy"]
