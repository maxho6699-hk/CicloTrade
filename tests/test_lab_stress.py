from datetime import datetime, timezone

import pytest

from core.lab_stress import LabStressError, calculate_stress, scenario_catalog, sha256


def snapshot(**overrides):
    value = {
        "account_mode": "personal_paper",
        "currency": "USD",
        "as_of": "2026-08-16T00:00:00Z",
        "data_status": "fresh",
        "positions": [{"symbol": "AAPL", "instrument_type": "stock", "quantity": 10, "last_trade_price": 100}],
    }
    value.update(overrides)
    return value


def test_fixed_scenario_is_deterministic_and_auditable():
    result = calculate_stress({"scenario_key": "market_drawdown", "snapshot": snapshot()}, now=lambda: datetime(2026, 8, 16, tzinfo=timezone.utc))
    assert result["scenario"]["price_shock_pct"] == -20.0
    assert result["baseline_value"] == 1000.0
    assert result["stressed_value"] == 798.8
    assert result["is_prediction"] is False
    assert len(result["input_sha256"]) == 64
    assert len(result["result_sha256"]) == 64


@pytest.mark.parametrize("bad", [
    {"data_status": "stale"},
    {"currency": "HKD", "positions": [{"symbol": "AAPL", "currency": "USD", "quantity": 1, "last_trade_price": 100}]},
    {"positions": [{"symbol": "AAPL", "quantity": float("nan"), "last_trade_price": 100}]},
    {"positions": [{"symbol": "AAPL", "quantity": 1, "last_trade_price": 0}]},
])
def test_invalid_snapshot_fails_closed(bad):
    value = snapshot(**bad)
    with pytest.raises(LabStressError):
        calculate_stress({"scenario_key": "market_drawdown", "snapshot": value})


def test_unknown_scenario_is_not_client_defined():
    with pytest.raises(LabStressError) as error:
        calculate_stress({"scenario_key": "-99%", "snapshot": snapshot()})
    assert error.value.status == 403


def test_stale_and_future_snapshots_fail_closed():
    with pytest.raises(LabStressError) as stale:
        calculate_stress({"scenario_key": "market_drawdown", "snapshot": snapshot(as_of="2026-08-15T00:00:00Z")}, now=lambda: datetime(2026, 8, 16, tzinfo=timezone.utc))
    assert stale.value.code == "snapshot_stale"
    with pytest.raises(LabStressError) as future:
        calculate_stress({"scenario_key": "market_drawdown", "snapshot": snapshot(as_of="2026-08-16T02:00:00Z")}, now=lambda: datetime(2026, 8, 16, tzinfo=timezone.utc))
    assert future.value.code == "snapshot_future"


def test_short_position_cost_reduces_net_value():
    result = calculate_stress({"scenario_key": "market_drawdown", "snapshot": snapshot(positions=[{"symbol": "AAPL", "instrument_type": "stock", "quantity": -10, "last_trade_price": 100}])}, now=lambda: datetime(2026, 8, 16, tzinfo=timezone.utc))
    assert result["positions"][0]["stressed_value"] == -801.2


def test_scenario_catalog_is_server_owned_and_hash_bound():
    catalog = scenario_catalog()
    assert set(catalog) == {"method_version", "catalog_sha256", "fee_bps", "slippage_bps", "scenarios"}
    assert catalog["catalog_sha256"] == sha256({key: catalog[key] for key in ("method_version", "fee_bps", "slippage_bps", "scenarios")})
    assert all(set(item) == {"key", "label", "price_shock_pct", "volatility_shock_pct", "gap_risk"} for item in catalog["scenarios"])
