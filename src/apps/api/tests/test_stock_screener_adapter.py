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
        "state": "official", "action": "BUY", "market": "US", "instrument_type": "stock", "symbol": "AAPL",
        "current_price": 201, "reference_price": 200, "quote_at": now.isoformat(),
        "contract_status": "complete", "actionable": True, "missing_fields": [],
        "rationale": "趋势确认", "invalidation": "跌破支撑", "risk": "波动风险",
    }], now=now)
    assert result["items"][0]["score"] is None
    assert result["items"][0]["paper_prefill"]["side"] == "BUY"
    assert result["items"][0]["price"] == 201


def test_api_adapter_rejects_malformed_recommendation_nested_objects():
    adapter = ApiStockScreenerAdapter(has_capability=lambda capability: capability == "strategy_all")
    with pytest.raises(StockScreenerError):
        adapter.read_recommendations([{"state": "official", "action": "BUY", "market": "US", "instrument_type": "option", "symbol": "AAPL"}])


@pytest.mark.parametrize("action", ["SHORT", "COVER", "REDUCE", "EXIT"])
def test_api_adapter_maps_management_actions_without_paper_prefill(action):
    adapter = ApiStockScreenerAdapter(has_capability=lambda capability: capability == "strategy_all")
    result = adapter.read_recommendations([{
        "state": "official", "action": action, "market": "US", "instrument_type": "stock", "symbol": "AAPL",
        "current_price": 201, "reference_price": 200, "quote_at": "2026-08-14T00:00:00+00:00",
        "contract_status": "complete", "actionable": True, "missing_fields": [],
        "rationale": "持仓管理", "invalidation": "风险失效", "risk": "管理风险",
    }])
    assert result["items"][0]["paper_prefill"] is None
    assert result["items"][0]["action"] in {"short", "reduce", "exit"}


def test_api_adapter_blocks_incomplete_or_locked_recommendations():
    adapter = ApiStockScreenerAdapter(has_capability=lambda capability: capability == "strategy_all")
    incomplete = adapter.read_recommendations([{
        "state": "official", "action": "BUY", "market": "US", "instrument_type": "stock", "symbol": "AAPL",
        "current_price": 201, "reference_price": 200, "quote_at": None,
        "contract_status": "incomplete", "actionable": False, "missing_fields": ["quote_at"],
        "rationale": "等待报价", "invalidation": "失效", "risk": "数据风险",
    }])
    assert incomplete["items"][0]["paper_prefill"] is None
    with pytest.raises(StockScreenerError):
        adapter.read_recommendations([{
            "state": "locked", "action": "BUY", "market": "US", "instrument_type": "stock", "symbol": "AAPL",
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
