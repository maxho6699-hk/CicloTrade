from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from core.membership import add_membership_entitlement
from core.personal_paper.quote_proof import ActionableStockQuote, QuoteProofError
from src.apps.api.app import app
from src.apps.api.feature_catalog_adapter import FeatureCatalogAdapter
from src.apps.api.personal_paper import build_personal_paper_api
from src.apps.api.read_model import ReadOnlyLegacyRepository


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
SECRET = b"personal-paper-route-test-secret-32-bytes"
api_module = importlib.import_module("src.apps.api.app")


def _quote(**changes):
    values = {
        "market": "US", "symbol": "AAPL", "bid_minor": 9_999,
        "ask_minor": 10_001, "last_minor": 10_000, "as_of": NOW,
        "is_realtime": True, "actionable": True,
    }
    values.update(changes)
    return ActionableStockQuote(**values)


async def _asgi(path: str, *, method: str = "GET", token: str | None = None,
                payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = [(b"content-length", str(len(body)).encode())]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    delivered = False
    messages: list[dict] = []

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443), "root_path": "",
    }, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    response_body = b"".join(
        item.get("body", b"") for item in messages if item["type"] == "http.response.body"
    )
    return start["status"], json.loads(response_body or b"{}")


@pytest.fixture
def routed_api(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-jwt-secret-that-is-longer-than-32-characters")
    database = DatabaseManager(str(tmp_path / "routed.db"))
    auth = AuthService(database)
    first = auth.register("first@example.com", "StrongPass123", "First", True)
    second = auth.register("second@example.com", "StrongPass123", "Second", True)
    token_a = auth.login("first@example.com", "StrongPass123", "127.0.0.1", "pytest").access_token
    token_b = auth.login("second@example.com", "StrongPass123", "127.0.0.2", "pytest").access_token
    previous = {
        "repository": app.state.repository,
        "personal": getattr(app.state, "personal_paper_api", None),
        "feature": getattr(app.state, "feature_catalog_adapter", None),
    }
    app.state.repository = ReadOnlyLegacyRepository(tmp_path / "routed.db")
    app.state.personal_paper_api = build_personal_paper_api(
        database, quote_proof_secret=SECRET, authenticate=api_module._identity,
        actionable_quote=lambda **_: _quote(), clock=lambda: NOW,
    )
    app.state.feature_catalog_adapter = FeatureCatalogAdapter(database)
    try:
        yield database, first, second, token_a, token_b
    finally:
        app.state.repository = previous["repository"]
        app.state.personal_paper_api = previous["personal"]
        app.state.feature_catalog_adapter = previous["feature"]


def test_personal_feature_and_screener_routes_require_bearer(routed_api):
    expected = {
        ("/api/rewrite/v1/personal-paper/seasons", "POST"),
        ("/api/rewrite/v1/personal-paper/seasons/{season_id:str}", "GET"),
        ("/api/rewrite/v1/personal-paper/quotes", "POST"),
        ("/api/rewrite/v1/personal-paper/risk-proofs", "POST"),
        ("/api/rewrite/v1/personal-paper/orders", "POST"),
        ("/api/rewrite/v1/personal-paper/orders/cancel", "POST"),
        ("/api/rewrite/v1/features/catalog", "GET"),
        ("/api/rewrite/v1/features/preferences", "PUT"),
        ("/api/rewrite/v1/features/recent", "PUT"),
        ("/api/rewrite/v1/stock-screener/query", "POST"),
        ("/api/rewrite/v1/stock-screener/preset", "GET"),
        ("/api/rewrite/v1/stock-screener/preset", "PUT"),
    }
    actual = {
        (route.path, method) for route in app.routes for method in route.methods
        if route.path.startswith("/api/rewrite/v1/personal-paper")
        or route.path.startswith("/api/rewrite/v1/features/")
        or route.path.startswith("/api/rewrite/v1/stock-screener/")
    }
    assert expected <= actual
    for path, method in expected:
        concrete = path.replace("{season_id:str}", "pps_missing")
        status, _ = asyncio.run(_asgi(
            concrete, method=method, payload={} if method != "GET" else None,
        ))
        assert status == 401


def test_personal_routes_isolate_users_and_legacy_ledgers(routed_api):
    database, _, _, token_a, token_b = routed_api
    created, body = asyncio.run(_asgi(
        "/api/rewrite/v1/personal-paper/seasons", method="POST", token=token_b,
    ))
    assert created == 201
    season_id = body["season"]["id"]
    denied, _ = asyncio.run(_asgi(
        f"/api/rewrite/v1/personal-paper/seasons/{season_id}", token=token_a,
    ))
    assert denied == 404
    quote_status, quote = asyncio.run(_asgi(
        "/api/rewrite/v1/personal-paper/quotes", method="POST", token=token_b,
        payload={"market": "US", "symbol": "AAPL"},
    ))
    assert quote_status == 201
    order_payload = {
        "idempotency_key": "route-limit-order", "season_id": season_id,
        "market": "US", "symbol": "AAPL", "side": "BUY", "order_type": "LIMIT",
        "quantity": 1, "limit_price": 90, "stop_price": None, "time_in_force": "DAY",
        "quote_id": quote["quote_id"], "account_version": 0,
        "source_context": {"kind": "manual", "reference_id": None},
    }
    risk_status, risk_body = asyncio.run(_asgi(
        "/api/rewrite/v1/personal-paper/risk-proofs", method="POST", token=token_b,
        payload={key: value for key, value in order_payload.items() if key != "idempotency_key"},
    ))
    assert risk_status == 201
    order_payload["risk_proof_id"] = risk_body["risk_proof"]["id"]
    order_status, order = asyncio.run(_asgi(
        "/api/rewrite/v1/personal-paper/orders", method="POST", token=token_b,
        payload=order_payload,
    ))
    assert order_status == 201
    cancel_status, _ = asyncio.run(_asgi(
        "/api/rewrite/v1/personal-paper/orders/cancel", method="POST", token=token_a,
        payload={"season_id": season_id, "order_id": order["order"]["id"], "account_version": 1},
    ))
    assert cancel_status == 409
    assert database.fetch_one("SELECT COUNT(*) count FROM personal_paper_orders")["count"] == 1
    for table in ("orders", "trades", "official_paper_events_v2"):
        assert database.fetch_one(f"SELECT COUNT(*) count FROM {table}")["count"] == 0


def test_feature_routes_reject_client_authority_and_isolate_preferences(routed_api):
    _, _, _, token_a, token_b = routed_api
    read_status, initial = asyncio.run(_asgi("/api/rewrite/v1/features/catalog", token=token_a))
    assert read_status == 200
    injected_status, _ = asyncio.run(_asgi(
        "/api/rewrite/v1/features/preferences", method="PUT", token=token_a,
        payload={"expected_version": 0, "pinned": [], "recent": [], "plan": "高级版"},
    ))
    assert injected_status == 400
    saved_status, saved = asyncio.run(_asgi(
        "/api/rewrite/v1/features/preferences", method="PUT", token=token_a,
        payload={"expected_version": 0, "pinned": [], "recent": []},
    ))
    assert saved_status == 200 and saved["preferences"]["version"] == 1
    other_status, other = asyncio.run(_asgi("/api/rewrite/v1/features/catalog", token=token_b))
    assert other_status == 200 and other["preferences"]["version"] == 0
    stale_status, _ = asyncio.run(_asgi(
        "/api/rewrite/v1/features/preferences", method="PUT", token=token_a,
        payload={"expected_version": 0, "pinned": [], "recent": []},
    ))
    assert stale_status == 409


def test_stock_screener_routes_bind_policy_data_and_user_presets(routed_api):
    database, first, _, token_a, token_b = routed_api
    with database.transaction() as connection:
        add_membership_entitlement(
            connection, first["id"], "标准版", 30,
            source_kind="pytest", source_ref="screener-route", now=NOW,
        )
    repository = app.state.repository
    original = repository.recommendations
    repository.recommendations = lambda _identity, limit=100: {"items": [{
        "event_id": 7, "state": "official", "action": "BUY", "market": "US",
        "instrument_type": "stock", "symbol": "AAPL", "currency": "USD",
        "reference_price": 200, "current_price": 201, "rationale": "趋势确认",
        "invalidation": "跌破支撑", "risk": "波动风险", "contract_status": "complete",
        "actionable": True, "missing_fields": [], "quote_at": NOW.isoformat(),
        "occurred_at": NOW.isoformat(),
    }][:limit]}
    query = {
        "schema_version": 1, "preset": "all", "filters": {},
        "sort": {"field": "updated_at", "direction": "desc"},
        "page": 1, "page_size": 20,
    }
    try:
        status, result = asyncio.run(_asgi(
            "/api/rewrite/v1/stock-screener/query", method="POST", token=token_a, payload=query,
        ))
        assert status == 200
        assert result["items"][0]["symbol"] == "AAPL"
        assert result["items"][0]["paper_prefill"] == {"market": "US", "symbol": "AAPL", "side": "BUY"}
        denied, _ = asyncio.run(_asgi(
            "/api/rewrite/v1/stock-screener/query", method="POST", token=token_b, payload=query,
        ))
        assert denied == 403
        empty_status, empty = asyncio.run(_asgi(
            "/api/rewrite/v1/stock-screener/preset", token=token_a,
        ))
        assert empty_status == 200 and empty is None
        preset = {
            "schema_version": 1, "version": 0, "name": "我的筛选",
            "filters": {"symbols": ["AAPL"]},
            "sort": {"field": "updated_at", "direction": "desc"},
        }
        saved_status, saved = asyncio.run(_asgi(
            "/api/rewrite/v1/stock-screener/preset", method="PUT", token=token_a, payload=preset,
        ))
        assert saved_status == 200 and saved["version"] == 1
        stale_status, _ = asyncio.run(_asgi(
            "/api/rewrite/v1/stock-screener/preset", method="PUT", token=token_a, payload=preset,
        ))
        assert stale_status == 409
    finally:
        repository.recommendations = original


def test_personal_builder_requires_independent_secret(monkeypatch):
    monkeypatch.delenv("PERSONAL_PAPER_QUOTE_PROOF_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert api_module._build_personal_paper_http_api() is None
    monkeypatch.setenv("PERSONAL_PAPER_QUOTE_PROOF_SECRET", "too-short")
    assert api_module._build_personal_paper_http_api() is None


def test_personal_builder_derives_a_domain_separated_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("PERSONAL_PAPER_QUOTE_PROOF_SECRET", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "j" * 32)
    monkeypatch.setattr(api_module, "legacy_database_path", lambda: tmp_path / "paper.db")

    built = api_module._build_personal_paper_http_api()

    assert built is not None
    assert built.quote_proofs._secret == hmac.new(
        b"j" * 32,
        b"ciclotrade:personal-paper-quote-proof:v1",
        hashlib.sha256,
    ).digest()


def test_personal_builder_prefers_the_explicit_domain_secret(monkeypatch, tmp_path):
    explicit = "p" * 32
    monkeypatch.setenv("PERSONAL_PAPER_QUOTE_PROOF_SECRET", explicit)
    monkeypatch.setenv("JWT_SECRET_KEY", "j" * 32)
    monkeypatch.setattr(api_module, "legacy_database_path", lambda: tmp_path / "explicit.db")

    built = api_module._build_personal_paper_http_api()

    assert built is not None
    assert built.quote_proofs._secret == explicit.encode("utf-8")


def test_personal_builder_can_derive_from_the_legacy_root_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("PERSONAL_PAPER_QUOTE_PROOF_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "s" * 32)
    monkeypatch.setattr(api_module, "legacy_database_path", lambda: tmp_path / "legacy.db")

    built = api_module._build_personal_paper_http_api()

    assert built is not None
    assert built.quote_proofs._secret == hmac.new(
        b"s" * 32,
        b"ciclotrade:personal-paper-quote-proof:v1",
        hashlib.sha256,
    ).digest()


@pytest.mark.parametrize("changes", (
    {"us_realtime_entitlement": False}, {"actionable_snapshot": False},
    {"quote_at": (NOW - timedelta(seconds=31)).isoformat()},
    {"quote_at": (NOW + timedelta(seconds=1)).isoformat()},
    {"bid": 101.0, "ask": 100.0},
))
def test_personal_opend_quote_gate_fails_closed(monkeypatch, changes):
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    values = {
        "symbol": "AAPL", "bid": 99.995, "ask": 100.005, "last": 100.0,
        "quote_at": NOW.isoformat(), "us_realtime_entitlement": True,
        "actionable_snapshot": True,
    }
    values.update(changes)

    class Adapter:
        def stock_quote(self, _symbol):
            return values

    monkeypatch.setattr(api_module, "OpenDAdapter", Adapter)
    with pytest.raises(QuoteProofError):
        api_module._personal_actionable_quote(user_id=1, market="US", symbol="AAPL", now=NOW)


def test_personal_opend_quote_gate_never_falls_back_and_accepts_subpenny(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    calls = []

    class Adapter:
        def stock_quote(self, symbol):
            calls.append(symbol)
            return {
                "symbol": symbol, "bid": 99.994, "ask": 100.006, "last": 100.005,
                "quote_at": NOW.isoformat(), "us_realtime_entitlement": True,
                "actionable_snapshot": True,
            }

    monkeypatch.setattr(api_module, "OpenDAdapter", Adapter)
    monkeypatch.setattr(
        api_module, "get_resilient_data_source",
        lambda *_: pytest.fail("personal quote must not use a fallback provider"),
    )
    result = api_module._personal_actionable_quote(user_id=1, market="US", symbol="AAPL", now=NOW)
    assert calls == ["AAPL"]
    assert (result.bid_minor, result.ask_minor, result.last_minor) == (9999, 10001, 10001)
