import asyncio
import importlib
import json

from src.apps.api.app import capabilities, health


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
