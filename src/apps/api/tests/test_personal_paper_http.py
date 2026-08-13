from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from starlette.requests import Request

from core.auth import AuthService
from core.database import DatabaseManager
from core.personal_paper import PersonalPaperService
from core.personal_paper.quote_proof import ActionableStockQuote, QuoteProofSignerVerifier
from src.apps.api.personal_paper import PersonalPaperApi


NOW = datetime(2026, 8, 13, 0, 0, 0, 123456, tzinfo=timezone.utc)
SECRET = b"personal-paper-http-quote-secret"


class Quotes:
    def __init__(self, **changes):
        self.changes = changes
        self.calls = []

    def __call__(self, **request):
        self.calls.append(request)
        values = {
            "market": request["market"],
            "symbol": request["symbol"],
            "bid_minor": 9_900,
            "ask_minor": 10_000,
            "last_minor": 9_950,
            "as_of": request["now"],
            "is_realtime": True,
            "actionable": True,
        }
        values.update(self.changes)
        return ActionableStockQuote(**values)


def _api(database, user_id, quotes=None):
    signer = QuoteProofSignerVerifier(database, SECRET)
    provider = quotes or Quotes()
    return (
        PersonalPaperApi(
            PersonalPaperService(database, signer, clock=lambda: NOW),
            authenticate=lambda _: {"id": user_id},
            quote_proofs=signer,
            actionable_quote=provider,
            clock=lambda: NOW,
        ),
        provider,
    )


def _request(method="GET", body=None):
    encoded = json.dumps(body).encode() if body is not None else b""
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request(
        {"type": "http", "method": method, "path": "/", "headers": [],
         "query_string": b"", "server": ("test", 443), "client": ("127.0.0.1", 1),
         "scheme": "https", "http_version": "1.1"},
        receive,
    )


def test_personal_paper_http_adapter_creates_season_and_maps_idempotency_conflict(tmp_path):
    database = DatabaseManager(str(tmp_path / "http.db"))
    user = AuthService(database).register("http@example.com", "StrongPass123", "HTTP", True)
    api, quotes = _api(database, user["id"])
    created = asyncio.run(api.create_season(_request("POST")))
    assert created.status_code == 201
    season = json.loads(created.body)["season"]
    issued = asyncio.run(api.issue_quote(_request("POST", {"market": "US", "symbol": "AAPL"})))
    assert issued.status_code == 201
    quote_id = json.loads(issued.body)["quote_id"]
    assert quotes.calls == [{"user_id": user["id"], "market": "US", "symbol": "AAPL", "now": NOW}]
    payload = {
        "idempotency_key": "http-order-1", "season_id": season["id"], "market": "US",
        "symbol": "AAPL", "side": "BUY", "order_type": "MARKET", "quantity": 1,
        "limit_price": None, "stop_price": None, "time_in_force": "DAY",
        "quote_id": quote_id, "account_version": 0,
        "source_context": {"kind": "manual", "reference_id": None},
    }
    accepted = asyncio.run(api.submit_stock_order(_request("POST", payload)))
    assert accepted.status_code == 201
    payload["quantity"] = 2
    conflict = asyncio.run(api.submit_stock_order(_request("POST", payload)))
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "changes",
    (
        {"is_realtime": False},
        {"actionable": False},
        {"market": "HK"},
        {"symbol": "MSFT"},
    ),
)
def test_issue_quote_rejects_non_actionable_or_mismatched_provider_result(tmp_path, changes):
    database = DatabaseManager(str(tmp_path / "rejected.db"))
    user = AuthService(database).register("rejected@example.com", "StrongPass123", "HTTP", True)
    api, _ = _api(database, user["id"], Quotes(**changes))
    asyncio.run(api.create_season(_request("POST")))

    response = asyncio.run(api.issue_quote(_request("POST", {"market": "US", "symbol": "AAPL"})))

    assert response.status_code == 422
    assert database.fetch_one("SELECT COUNT(*) count FROM personal_paper_quote_proofs")["count"] == 0


@pytest.mark.parametrize(
    "body",
    (
        {"market": "US"},
        {"market": "US", "symbol": "AAPL", "extra": True},
        {"market": "HK", "symbol": "0700"},
        {"market": "US", "symbol": "aapl"},
        {"market": "US", "symbol": True},
        ["US", "AAPL"],
    ),
)
def test_issue_quote_rejects_invalid_request_shape_before_provider(tmp_path, body):
    database = DatabaseManager(str(tmp_path / "shape.db"))
    user = AuthService(database).register("shape@example.com", "StrongPass123", "HTTP", True)
    api, quotes = _api(database, user["id"])

    response = asyncio.run(api.issue_quote(_request("POST", body)))

    assert response.status_code == 400
    assert quotes.calls == []


def test_issue_quote_requires_an_active_personal_account(tmp_path):
    database = DatabaseManager(str(tmp_path / "account.db"))
    user = AuthService(database).register("account@example.com", "StrongPass123", "HTTP", True)
    api, _ = _api(database, user["id"])

    response = asyncio.run(api.issue_quote(_request("POST", {"market": "US", "symbol": "AAPL"})))

    assert response.status_code == 422
    assert database.fetch_one("SELECT COUNT(*) count FROM personal_paper_quote_proofs")["count"] == 0


@pytest.mark.parametrize(
    "changes",
    (
        {"bid_minor": 10_001},
        {"last_minor": 0},
        {"as_of": NOW - timedelta(seconds=30, microseconds=1)},
        {"as_of": NOW.replace(tzinfo=None)},
    ),
)
def test_issue_quote_fails_closed_on_invalid_price_or_time(tmp_path, changes):
    database = DatabaseManager(str(tmp_path / "invalid-quote.db"))
    user = AuthService(database).register("invalid@example.com", "StrongPass123", "HTTP", True)
    api, _ = _api(database, user["id"], Quotes(**changes))
    asyncio.run(api.create_season(_request("POST")))

    response = asyncio.run(api.issue_quote(_request("POST", {"market": "US", "symbol": "AAPL"})))

    assert response.status_code == 422
    assert database.fetch_one("SELECT COUNT(*) count FROM personal_paper_quote_proofs")["count"] == 0
