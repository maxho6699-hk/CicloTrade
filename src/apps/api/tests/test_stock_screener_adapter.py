from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.stock_screener import StockScreenerAccessError
from src.apps.api.stock_screener_adapter import ApiStockScreenerAdapter


def test_api_adapter_keeps_screening_and_preset_save_side_effect_free():
    adapter = ApiStockScreenerAdapter("标准版")
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
        ApiStockScreenerAdapter("免费版").read([])
