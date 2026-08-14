from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.stock_screener import StockScreenerAccessError
from core.stock_screener import StockScreenerError
from src.apps.api.stock_screener_adapter import ApiStockScreenerAdapter


def test_api_adapter_keeps_screening_and_preset_save_side_effect_free():
    adapter = ApiStockScreenerAdapter(has_capability=lambda capability: capability == "strategy_all")
    now = datetime(2026, 8, 14, tzinfo=ZoneInfo("UTC"))
    result = adapter.read(
        [{
            "symbol": "AAPL", "state": "research", "action": "wait", "score": 10,
            "price": 200, "change_pct": 0, "reason": "等待确认", "risk": "波动风险",
            "invalidation": "结构失效", "data_state": "delayed", "health": "degraded",
            "updated_at": now.isoformat(),
        }],
        {"filters": {"states": ["research"]}},
        now=now,
    )
    assert result["items"][0]["symbol"] == "AAPL"
    assert result["items"][0]["data_state"] == "delayed"
    saved = adapter.save_preset(None, {
        "version": 0, "name": "等待", "filters": {"actions": ["wait"]},
        "sort": {"field": "score", "direction": "desc"},
    })
    assert saved["version"] == 1


def test_api_adapter_rejects_missing_capability():
    with pytest.raises(StockScreenerAccessError):
        ApiStockScreenerAdapter().read([])


def test_api_adapter_maps_real_recommendation_without_fabricating_score():
    now = datetime(2026, 8, 14, tzinfo=ZoneInfo("UTC"))
    adapter = ApiStockScreenerAdapter(has_capability=lambda capability: capability == "strategy_all")
    result = adapter.read_recommendations([{
        "status": "official", "action": "BUY",
        "instrument": {"market": "US", "instrument_type": "stock", "symbol": "AAPL", "currency": "USD"},
        "evidence": {"supporting": ["趋势确认"], "counter": ["波动较高"]},
        "risk": {"invalidation": "跌破支撑", "maximum_modeled_loss": None, "data_freshness": "live", "risk": "波动风险"},
        "provenance": {"model_version": "v1", "generated_at": now.isoformat(), "source_snapshot": "snap-1"},
        "reference_price": 200,
    }], now=now)
    assert result["items"][0]["score"] is None
    assert result["items"][0]["paper_prefill"]["side"] == "BUY"


def test_api_adapter_rejects_malformed_recommendation_nested_objects():
    adapter = ApiStockScreenerAdapter(has_capability=lambda capability: capability == "strategy_all")
    with pytest.raises(StockScreenerError):
        adapter.read_recommendations([{
            "status": "official", "action": "BUY",
            "instrument": {"market": "US", "instrument_type": "stock", "symbol": "AAPL"},
            "evidence": "not-an-object", "risk": {}, "provenance": {},
        }])


def test_api_adapter_maps_existing_flat_recommendation_dto_without_score():
    adapter = ApiStockScreenerAdapter(has_capability=lambda capability: capability == "strategy_all")
    result = adapter.read_recommendations([{
        "event_id": 7, "state": "official", "action": "BUY", "market": "US",
        "instrument_type": "stock", "symbol": "MSFT", "currency": "USD",
        "reference_price": 400, "rationale": "趋势确认", "invalidation": "跌破支撑",
        "risk": "波动风险", "occurred_at": "2026-08-14T00:00:00+00:00",
    }])
    assert result["items"][0]["score"] is None
    assert result["items"][0]["paper_prefill"] is None
    assert result["items"][0]["blocked_reason"]
