from datetime import datetime

import pytest

from core.stock_screener import (
    StockScreenerAccessError,
    StockScreenerConflict,
    StockScreenerError,
    StockScreenerAdapter,
    screen_candidates,
    update_preset,
)


NOW = datetime(2026, 8, 14, 1, 0, tzinfo=__import__("zoneinfo").ZoneInfo("UTC"))


def candidate(symbol: str, **changes):
    value = {
        "symbol": symbol,
        "name": symbol,
        "state": "official",
        "action": "buy",
        "score": 70,
        "price": 100.0,
        "change_pct": 2.0,
        "reasons": ["站上 EMA20 与 EMA50"],
        "counter_evidence": ["成交量尚未明显放大"],
        "risk": "波动扩大可能导致回撤。",
        "invalidation": "收盘跌破风险线。",
        "data_state": "fresh",
        "health": "healthy",
        "updated_at": "2026-08-14T00:00:00+00:00",
    }
    value.update(changes)
    return value


def test_screen_filters_sorts_paginates_and_exposes_safe_actions():
    result = screen_candidates(
        [candidate("MSFT", score=80), candidate("AAPL", score=60), candidate("NVDA", score=90)],
        {"preset": "momentum", "page": 1, "page_size": 2, "sort": {"field": "score", "direction": "desc"}},
        now=NOW,
    )

    assert result["total"] == 3
    assert [item["symbol"] for item in result["items"]] == ["NVDA", "MSFT"]
    item = result["items"][0]
    assert item["hong_kong_time"] == "2026-08-14T08:00:00+08:00"
    assert item["research_url"] == "/discover?tool=screener&symbol=NVDA"
    assert item["alert_prefill"] == {"market": "US", "symbol": "NVDA"}
    assert item["paper_prefill"]["market"] == "US"
    assert not {"auto_submit", "quote_proof", "account_version", "idempotency_key"} & set(item)


def test_custom_filters_keep_official_and_research_candidates_separate():
    result = screen_candidates(
        [candidate("AAPL", state="research", data_state="stale"), candidate("MSFT", state="official", data_state="delayed")],
        {"filters": {"states": ["research"], "data_states": ["fresh"]}},
        now=NOW,
    )
    assert result["total"] == 0
    result = screen_candidates(
        [candidate("AAPL", state="research"), candidate("MSFT", state="official", data_state="delayed")],
        {"filters": {"states": ["research"]}},
        now=NOW,
    )
    assert [item["symbol"] for item in result["items"]] == ["AAPL"]


@pytest.mark.parametrize(
    "payload",
    [
        {"filters": {"unknown": 1}},
        {"sort": {"field": "account_version", "direction": "desc"}},
        {"page_size": 101},
        {"filters": {"min_score": float("nan")}},
        {"preset": "option-auto"},
    ],
)
def test_invalid_request_fails_closed(payload):
    with pytest.raises(StockScreenerError):
        screen_candidates([candidate("AAPL")], payload, now=NOW)


def test_candidate_rejects_unknown_fields_nonfinite_and_naive_timestamps():
    with pytest.raises(StockScreenerError):
        screen_candidates([candidate("AAPL", submit=True)], now=NOW)
    with pytest.raises(StockScreenerError):
        screen_candidates([candidate("AAPL", score=float("inf"))], now=NOW)
    with pytest.raises(StockScreenerError):
        screen_candidates([candidate("AAPL", updated_at="2026-08-14T00:00:00")], now=NOW)


def test_preset_is_versioned_and_optimistic_without_persistence():
    payload = {
        "version": 0,
        "name": "我的动量",
        "filters": {"min_score": 50},
        "sort": {"field": "score", "direction": "desc"},
    }
    updated = update_preset(None, payload)
    assert updated["schema_version"] == 1
    assert updated["version"] == 1
    with pytest.raises(StockScreenerConflict):
        update_preset(updated, payload)


def test_plan_in_progress_or_free_fails_closed():
    with pytest.raises(StockScreenerAccessError):
        StockScreenerAdapter("免费版").screen([candidate("AAPL")], now=NOW)
    assert StockScreenerAdapter("专业版", has_capability=lambda capability: capability == "strategy_all").screen([candidate("AAPL")], now=NOW)["total"] == 1
    assert StockScreenerAdapter("标准版", authorized=True).screen([candidate("AAPL")], now=NOW)["total"] == 1


def test_retired_plan_does_not_inherit_legacy_capabilities():
    with pytest.raises(StockScreenerAccessError):
        StockScreenerAdapter("专业版").screen([candidate("AAPL")], now=NOW)


@pytest.mark.parametrize(
    ("action", "data_state", "health", "side"),
    [("buy", "fresh", "healthy", "BUY"), ("short", "fresh", "healthy", "SHORT"),
     ("hold", "fresh", "healthy", None), ("wait", "stale", "healthy", None),
     ("reduce", "fresh", "degraded", None), ("exit", "fresh", "healthy", None)],
)
def test_paper_prefill_is_only_for_fresh_healthy_entries(action, data_state, health, side):
    item = screen_candidates([candidate("AAPL", action=action, data_state=data_state, health=health)], now=NOW)["items"][0]
    assert item["paper_prefill"] is None if side is None else item["paper_prefill"]["side"] == side
    assert item["actionable"] is (side is not None)
    if side is None:
        assert item["blocked_reason"]


def test_score_is_optional_and_default_sort_uses_updated_at_then_symbol():
    result = screen_candidates(
        [candidate("MSFT", score=None, updated_at="2026-08-14T00:00:02+00:00"),
         candidate("AAPL", score=None, updated_at="2026-08-14T00:00:01+00:00")],
        {"sort": {"field": "updated_at", "direction": "asc"}}, now=NOW,
    )
    assert [item["symbol"] for item in result["items"]] == ["AAPL", "MSFT"]
    assert all(item["score"] is None for item in result["items"])


def test_limits_duplicates_symbols_and_keeps_symbol_tie_break_ascending():
    result = screen_candidates([candidate("MSFT", score=80), candidate("AAPL", score=80)], {
        "sort": {"field": "score", "direction": "desc"},
    }, now=NOW)
    assert [item["symbol"] for item in result["items"]] == ["AAPL", "MSFT"]
    with pytest.raises(StockScreenerError):
        screen_candidates([candidate("AAPL"), candidate("AAPL")], now=NOW)
    with pytest.raises(StockScreenerError):
        screen_candidates([candidate("AAPL", score=101)], now=NOW)
    with pytest.raises(StockScreenerError):
        bad = candidate("AAPL")
        bad["symbol"] = "bad symbol"
        screen_candidates([bad], now=NOW)


def test_data_health_states_are_preserved_and_not_promoted():
    result = screen_candidates(
        [candidate("AAPL", data_state="stale", health="degraded")],
        now=NOW,
    )
    assert result["items"][0]["data_state"] == "stale"
    assert result["items"][0]["health"] == "degraded"
