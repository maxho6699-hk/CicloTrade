from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import importlib
import json

import pytest

from core.compat import UTC
from src.apps.api.market_stream import RealtimeCandleTracker
from src.apps.api.tests.test_session import _login_token, _request


def _event(raw: bytes) -> tuple[str, dict]:
    event_line, data_line = raw.decode("utf-8").splitlines()[:2]
    return event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))


def test_tracker_aggregates_changed_snapshots_without_historical_state():
    tracker = RealtimeCandleTracker()
    start = datetime(2026, 8, 12, 16, 1, 10, tzinfo=UTC)
    first = tracker.update(symbol="AAPL", timeframe="1分", observed_at=start, last=100, cumulative_volume=10)
    unchanged = tracker.update(symbol="AAPL", timeframe="1分", observed_at=start, last=100, cumulative_volume=10)
    second = tracker.update(
        symbol="AAPL", timeframe="1分", observed_at=start + timedelta(seconds=20), last=102, cumulative_volume=14
    )
    assert first is not None and unchanged is None and second is not None
    assert first["state"] == "forming" and first["forming"] is True and first["volume"] == 0.0
    assert second["sequence"] == first["sequence"] + 1
    assert second["open"] == 100.0 and second["high"] == 102.0
    assert second["low"] == 100.0 and second["close"] == 102.0 and second["volume"] == 4.0


def test_realtime_stream_emits_exact_snapshot_overlay_for_eligible_member(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    now = datetime.now(UTC)
    browser_api["database"].execute(
        "UPDATE users SET plan_type='标准版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")

    class OpenD:
        def stock_quote(self, symbol):
            assert symbol == "AAPL"
            return {"last": 210.5, "volume": 1_000, "quote_at": now.isoformat(), "us_realtime_entitlement": True}

    monkeypatch.setattr(api_module, "OpenDAdapter", OpenD)
    response = asyncio.run(api_module.market_stream(_request(
        "/api/rewrite/v1/market/stream", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL", "timeframe": "1分"},
    )))

    async def collect():
        iterator = response.body_iterator
        try:
            return await anext(iterator), await anext(iterator)
        finally:
            await iterator.aclose()

    status, forming = asyncio.run(collect())
    assert _event(status) == ("status", {"state": "connected"})
    forming_name, payload = _event(forming)
    assert forming_name == "forming_bar"
    assert set(payload) == {
        "sequence", "symbol", "timeframe", "bar_start", "open", "high", "low", "close", "volume",
        "state", "forming", "observed_at", "visible_as_of", "realtime", "authorized", "stale",
    }
    assert payload["state"] == "forming" and payload["forming"] is True
    assert payload["authorized"] is payload["realtime"] is True and payload["stale"] is False


def test_free_stream_never_constructs_a_current_snapshot(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")

    class UnexpectedOpenD:
        def __init__(self):
            raise AssertionError("free stream must not build an OpenD client")

    monkeypatch.setattr(api_module, "OpenDAdapter", UnexpectedOpenD)
    response = asyncio.run(api_module.market_stream(_request(
        "/api/rewrite/v1/market/stream", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL", "timeframe": "1分"},
    )))

    async def collect_one():
        iterator = response.body_iterator
        try:
            return await anext(iterator)
        finally:
            await iterator.aclose()

    assert _event(asyncio.run(collect_one())) == ("status", {"state": "catching_up"})


def test_bootstrap_and_search_do_not_expose_provider_names(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "public_market_status", lambda **_: {
        "source": "Futu OpenD", "active_source": "Yahoo Finance", "fallback_from": "OpenD", "display_source": "US feed",
    })

    class SearchSource:
        name = "Futu OpenD"
        def search(self, *_args):
            return [{"symbol": "AAPL", "name": "Apple", "exchange": "Futu OpenD", "type": "股票"}]

    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_: SearchSource())
    bootstrap = asyncio.run(api_module.bootstrap(_request(
        "/api/rewrite/v1/bootstrap", authorization=f"Bearer {_login_token()}"
    )))
    search = asyncio.run(api_module.market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {_login_token()}", query={"q": "AAPL", "market": "美股"},
    )))
    bootstrap_payload = json.loads(bootstrap.body.decode("utf-8"))
    search_payload = json.loads(search.body.decode("utf-8"))
    assert bootstrap_payload["market_data"] == {"display_source": "真实数据来源"}
    assert search_payload["items"][0]["exchange"] == "真实数据来源"


@pytest.mark.parametrize("failure", ("stale", "permission"))
def test_realtime_stream_fails_closed_when_snapshot_cannot_be_claimed(browser_api, monkeypatch, failure):
    api_module = importlib.import_module("src.apps.api.app")
    browser_api["database"].execute(
        "UPDATE users SET plan_type='标准版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    quote_at = "2020-01-01T00:00:00+00:00" if failure == "stale" else datetime.now(UTC).isoformat()

    class OpenD:
        def stock_quote(self, _symbol):
            return {"last": 210.5, "volume": 1_000, "quote_at": quote_at, "us_realtime_entitlement": failure != "permission"}

    monkeypatch.setattr(api_module, "OpenDAdapter", OpenD)
    response = asyncio.run(api_module.market_stream(_request(
        "/api/rewrite/v1/market/stream", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL", "timeframe": "1分"},
    )))

    async def collect_one():
        iterator = response.body_iterator
        try:
            return await anext(iterator)
        finally:
            await iterator.aclose()

    assert _event(asyncio.run(collect_one())) == ("status", {"state": "disconnected"})
