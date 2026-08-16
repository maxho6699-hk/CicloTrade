import asyncio
import builtins
import importlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from data.datasource import DataSourceError
from src.apps.api.app import capabilities, health
from src.apps.api.read_model import BrowserIdentity


api_module = importlib.import_module("src.apps.api.app")


def response_json(response):
    return json.loads(response.body.decode("utf-8"))


def test_rewrite_health_declares_protected_compatibility_writes():
    payload = response_json(asyncio.run(health(None)))

    assert payload["status"] == "ok"
    assert payload["mode"] == "compatibility-protected-writes"
    assert payload["canonical_data"] == "legacy-sqlite"


def test_rewrite_capabilities_forbid_external_side_effects():
    payload = response_json(asyncio.run(capabilities(None)))

    assert payload["recommendations"]["shadow_learning"] is True
    assert payload["recommendations"]["self_promotion"] is False
    assert payload["recommendations"]["mystic_isolated"] is True
    assert all(value is False for value in payload["external_side_effects"].values())
    assert "quant_timeline" in payload["compatibility_reads"]
    assert "watchlist" in payload["protected_writes"]
    assert "ui_locale" in payload["protected_writes"]
    assert "paper_orders" not in payload["protected_writes"]
    assert "/api/rewrite/v1/paper/orders" not in {route.path for route in api_module.routes}


def test_missing_opend_runtime_fails_closed_without_blocking_app_import(monkeypatch):
    original_import = builtins.__import__

    def without_opend(name, *args, **kwargs):
        if name == "data.opend_adapter":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_opend)
    with pytest.raises(DataSourceError, match="OpenD"):
        api_module.OpenDAdapter()


def test_market_search_state_prunes_expired_cache_and_rate_entries():
    now = 10_000.0
    with api_module._MARKET_SEARCH_LOCK:
        api_module._MARKET_SEARCH_CACHE.clear()
        api_module._MARKET_SEARCH_RATE.clear()
        api_module._MARKET_SEARCH_CACHE[("美股", "stale")] = (
            now - api_module._MARKET_SEARCH_TTL_SECONDS,
            [],
        )
        api_module._MARKET_SEARCH_RATE[99] = [now - 60]
        api_module._prune_market_search_state_locked(now)

        assert api_module._MARKET_SEARCH_CACHE == {}
        assert api_module._MARKET_SEARCH_RATE == {}


def test_lab_stress_route_uses_authenticated_server_owned_official_snapshot(monkeypatch):
    identity = BrowserIdentity(7, "Owner", "advanced", None)

    class Repository:
        def portfolio(self, current):
            assert current is identity
            return {
                "account_mode": "official",
                "accounts": {"US": {"status": "recorded", "captured_at": datetime.now(timezone.utc).isoformat()}},
                "positions": [
                    {"market": "US", "currency": "USD", "instrument_type": "stock", "symbol": "AAPL", "quantity": 2, "last_trade_price": 100},
                    {"market": "HK", "currency": "HKD", "instrument_type": "stock", "symbol": "0700", "quantity": 1, "last_trade_price": 500},
                    {"market": "US", "currency": "USD", "instrument_type": "option", "symbol": "AAPL", "quantity": 1, "last_trade_price": 5},
                ],
            }

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(repository=Repository()))

        async def json(self):
            return {"scenario_key": "market_drawdown"}

    monkeypatch.setattr(api_module, "_identity", lambda _request: identity)
    response = asyncio.run(api_module.lab_stress(Request()))
    payload = response_json(response)

    assert payload["account_mode"] == "official"
    assert payload["currency"] == "USD"
    assert [item["symbol"] for item in payload["positions"]] == ["AAPL"]
    assert payload["is_prediction"] is False
    assert payload["execution_eligible"] is False
    matching = [route for route in api_module.routes if route.path == api_module.LAB_STRESS_PATH]
    assert len(matching) == 1
    assert matching[0].methods == {"POST"}
