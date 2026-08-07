"""Authenticated external strategy-event ingestion checks."""

from __future__ import annotations

import asyncio
import json

import pytest

import asgi_app
from core.database import DatabaseManager
from core.quant_journal import QuantJournal


class Request:
    method = "POST"

    def __init__(self, payload: dict, token: str | None):
        self.payload = payload
        self.headers = {"authorization": f"Bearer {token}"} if token is not None else {}

    async def stream(self):
        yield json.dumps(self.payload).encode("utf-8")


@pytest.fixture
def journal(tmp_path):
    return QuantJournal(DatabaseManager(str(tmp_path / "quant-ingest.db")))


def _payload() -> dict:
    return {
        "source": "strategy-node-1",
        "external_event_id": "evt-20260806-001",
        "strategy_name": "趋势交叉",
        "strategy_version": "2026.08.06",
        "occurred_at": "2026-08-06T01:30:00+00:00",
        "metadata": {"reason": "EMA20 上穿 EMA50"},
        "legs": [
            {
                "market": "US",
                "instrument_type": "stock",
                "symbol": "AAPL",
                "target_quantity": 10,
                "quantity_delta": 10,
                "price": 200,
            }
        ],
    }


def test_quant_ingest_requires_service_token(monkeypatch, journal):
    token = "test-strategy-token-that-is-at-least-32-characters"
    monkeypatch.setenv("TRADEAI_STRATEGY_INGEST_TOKEN", token)
    monkeypatch.setattr(asgi_app, "QuantJournal", lambda: journal)

    for supplied in (None, "wrong-token-that-is-also-at-least-32-characters"):
        with pytest.raises(asgi_app.ApiError) as error:
            asyncio.run(asgi_app.api_quant_events(Request(_payload(), supplied)))
        assert error.value.status == 401


def test_quant_ingest_is_idempotent_and_rejects_payload_mutation(monkeypatch, journal):
    token = "test-strategy-token-that-is-at-least-32-characters"
    monkeypatch.setenv("TRADEAI_STRATEGY_INGEST_TOKEN", token)
    monkeypatch.setattr(asgi_app, "QuantJournal", lambda: journal)

    first = asyncio.run(asgi_app.api_quant_events(Request(_payload(), token)))
    retry = asyncio.run(asgi_app.api_quant_events(Request(_payload(), token)))
    assert first.status_code == 201 and retry.status_code == 200
    assert json.loads(first.body)["id"] == json.loads(retry.body)["id"]
    assert journal.replay("tradeai-system")["event_count"] == 1

    changed = _payload()
    changed["legs"][0]["price"] = 201
    with pytest.raises(asgi_app.ApiError, match="idempotency"):
        asyncio.run(asgi_app.api_quant_events(Request(changed, token)))


def test_quant_ingest_rejects_unknown_top_level_fields(monkeypatch, journal):
    token = "test-strategy-token-that-is-at-least-32-characters"
    monkeypatch.setenv("TRADEAI_STRATEGY_INGEST_TOKEN", token)
    monkeypatch.setattr(asgi_app, "QuantJournal", lambda: journal)
    payload = _payload() | {"ledger_key": "attacker-selected"}

    with pytest.raises(asgi_app.ApiError, match="未知字段"):
        asyncio.run(asgi_app.api_quant_events(Request(payload, token)))


def test_quant_snapshot_ingest_validates_accounting_and_is_idempotent(monkeypatch, journal):
    token = "test-strategy-token-that-is-at-least-32-characters"
    monkeypatch.setenv("TRADEAI_STRATEGY_INGEST_TOKEN", token)
    monkeypatch.setattr(asgi_app, "QuantJournal", lambda: journal)
    payload = {
        "source": "strategy-node-1",
        "external_snapshot_id": "snapshot-20260806-001",
        "currency": "USD",
        "initial_cash": 100_000,
        "cash": 92_000,
        "market_value": 12_000,
        "realized_pnl": 1_500,
        "unrealized_pnl": 2_500,
        "captured_at": "2026-08-06T01:35:00+00:00",
    }

    first = asyncio.run(asgi_app.api_quant_snapshots(Request(payload, token)))
    retry = asyncio.run(asgi_app.api_quant_snapshots(Request(payload, token)))
    assert first.status_code == 201 and retry.status_code == 200
    assert json.loads(first.body)["total_equity"] == 104_000

    bad = payload | {"external_snapshot_id": "snapshot-bad", "market_value": 10_000}
    with pytest.raises(asgi_app.ApiError, match="equity must equal"):
        asyncio.run(asgi_app.api_quant_snapshots(Request(bad, token)))
