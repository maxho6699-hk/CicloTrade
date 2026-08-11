from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import hmac
import importlib
import json
from pathlib import Path

from core.compat import UTC
from core.database import DatabaseManager
from core.official_option_sim_journal import OfficialOptionSimulationJournal
from src.apps.api.earnings_read_model import OpaqueIdCodec
from src.apps.api.official_option_sim_read_model import OfficialOptionSimulationReadModel
from src.apps.api.official_option_sim_receiver import OfficialOptionSimulationReceiver
from src.apps.api.official_option_simulation import OfficialOptionSimulationApi


NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)


def _proposal() -> dict:
    return {
        "schema_version": 1,
        "event_id": "proposal-aapl-api-1",
        "position_key": "official-aapl-api-1",
        "event_type": "PROPOSED",
        "action_at": "2026-08-12T14:30:00Z",
        "worker_id": "strategy-worker",
        "fencing_epoch": 1,
        "strategy_id": "options-event",
        "strategy_version": "v1",
        "model_version": "m1",
        "manifest_sha256": "a" * 64,
        "evidence_hashes": ["b" * 64],
        "position": {
            "structure_type": "LONG_CALL",
            "underlying": "AAPL",
            "currency": "USD",
            "account_equity": 100_000.0,
            "portfolio_risk_before_pct": 0.5,
            "portfolio_risk_limit_pct": 4.0,
            "risk": {
                "defined_risk": True,
                "max_loss": 1001.0,
                "max_account_pct": 1.01,
                "invalidation_condition": "Quote becomes unavailable.",
            },
            "legs": [{
                "contract_key": "AAPL-20260918-C-200",
                "side": "BUY",
                "quantity": 1,
                "expiry": "2026-09-18",
                "right": "CALL",
                "strike": 200.0,
                "multiplier": 100,
                "bid": 9.8,
                "ask": 10.0,
                "quote_at": "2026-08-12T14:29:30Z",
                "is_realtime": True,
                "actionable_quote": True,
                "fallback_from": None,
                "quote_source": "private-options-provider",
                "commission": 1.0,
            }],
        },
    }


async def _asgi(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict]:
    app = importlib.import_module("src.apps.api.app").app
    raw_path, _, raw_query = path.partition("?")
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    request_headers = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
    await app({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": raw_query.encode(),
        "headers": request_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "root_path": "",
    }, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {
        name.decode().lower(): value.decode() for name, value in start["headers"]
    }
    response_body = b"".join(
        message.get("body", b"") for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, json.loads(response_body or b"{}")


def _journal(tmp_path: Path) -> OfficialOptionSimulationJournal:
    return OfficialOptionSimulationJournal(
        DatabaseManager(str(tmp_path / "official-api.db")),
        clock=lambda: NOW,
    )


def test_factories_require_explicit_switches_and_independent_secrets(tmp_path, monkeypatch):
    module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(module, "legacy_database_path", lambda: tmp_path / "factory.db")
    for name in (
        "OFFICIAL_OPTION_SIMULATION_ENABLED",
        "OFFICIAL_OPTION_SIMULATION_RECEIVER_ENABLED",
        "OFFICIAL_OPTION_SIM_OPAQUE_ID_SECRET",
        "OFFICIAL_OPTION_SIMULATION_SHARED_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "j" * 64)
    monkeypatch.setenv("EARNINGS_OPAQUE_ID_SECRET", "e" * 64)
    assert module._build_official_option_sim_api() is None
    assert module._build_official_option_sim_receiver() is None

    monkeypatch.setenv("OFFICIAL_OPTION_SIMULATION_ENABLED", "true")
    monkeypatch.setenv("OFFICIAL_OPTION_SIM_OPAQUE_ID_SECRET", "o" * 31)
    assert module._build_official_option_sim_api() is None
    monkeypatch.setenv("OFFICIAL_OPTION_SIM_OPAQUE_ID_SECRET", "o" * 32)
    assert isinstance(module._build_official_option_sim_api(), OfficialOptionSimulationApi)
    assert module._build_official_option_sim_receiver() is None

    monkeypatch.setenv("OFFICIAL_OPTION_SIMULATION_RECEIVER_ENABLED", "true")
    monkeypatch.setenv("OFFICIAL_OPTION_SIMULATION_SHARED_SECRET", "s" * 31)
    assert module._build_official_option_sim_receiver() is None
    monkeypatch.setenv("OFFICIAL_OPTION_SIMULATION_SHARED_SECRET", "s" * 32)
    assert isinstance(
        module._build_official_option_sim_receiver(),
        OfficialOptionSimulationReceiver,
    )


def test_rewrite_routes_expose_capability_gated_source_anonymous_reads(tmp_path, monkeypatch):
    module = importlib.import_module("src.apps.api.app")
    journal = _journal(tmp_path)
    journal.record(_proposal(), idempotency_key="proposal-aapl-api-1")
    model = OfficialOptionSimulationReadModel(
        journal.database._db_path,
        OpaqueIdCodec(b"opaque-official-option-api-secret" + b"x" * 8),
    )
    locked_api = OfficialOptionSimulationApi(
        model,
        authenticate=lambda request: object(),
        has_capability=lambda identity, capability: False,
    )
    monkeypatch.setattr(module.app.state, "official_option_sim_api", locked_api)
    status, headers, payload = asyncio.run(
        _asgi("GET", "/api/rewrite/v1/official-option-simulation")
    )
    assert status == 200
    assert payload["state"] == "locked"
    assert headers["cache-control"] == "private, no-store"

    allowed_api = OfficialOptionSimulationApi(
        model,
        authenticate=lambda request: object(),
        has_capability=lambda identity, capability: capability == "option_auto_paper_official",
    )
    monkeypatch.setattr(module.app.state, "official_option_sim_api", allowed_api)
    status, _, payload = asyncio.run(
        _asgi("GET", "/api/rewrite/v1/official-option-simulation")
    )
    assert status == 200
    assert payload["state"] == "ready"
    assert payload["paper"] is True
    assert payload["broker_execution"] is False
    assert "private-options-provider" not in json.dumps(payload)
    assert "worker_id" not in json.dumps(payload)
    position_id = payload["items"][0]["id"]
    status, _, detail = asyncio.run(
        _asgi("GET", f"/api/rewrite/v1/official-option-simulation/{position_id}")
    )
    assert status == 200
    assert detail["broker_execution"] is False
    assert "manifest_sha256" not in json.dumps(detail)


def test_signed_receiver_is_fail_closed_and_duplicate_external_ids_are_controlled(tmp_path, monkeypatch):
    module = importlib.import_module("src.apps.api.app")
    journal = _journal(tmp_path)
    secret = b"r" * 32
    receiver = OfficialOptionSimulationReceiver(
        journal,
        shared_secret=secret,
        enabled=True,
    )
    monkeypatch.setattr(module.app.state, "official_option_sim_receiver", receiver)
    raw = json.dumps(_proposal(), separators=(",", ":")).encode()
    base_headers = {
        "x-ciclotrade-worker-id": "strategy-worker",
        "x-ciclotrade-fencing-epoch": "1",
        "x-ciclotrade-simulation-signature": "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest(),
    }

    status, _, _ = asyncio.run(_asgi(
        "POST",
        "/api/rewrite/internal/v1/official-option-simulation/receipts",
        body=raw,
        headers={**base_headers, "idempotency-key": "proposal-aapl-api-1"},
    ))
    assert status == 201
    status, _, _ = asyncio.run(_asgi(
        "POST",
        "/api/rewrite/internal/v1/official-option-simulation/receipts",
        body=raw,
        headers={**base_headers, "idempotency-key": "proposal-aapl-api-1"},
    ))
    assert status == 201
    status, _, payload = asyncio.run(_asgi(
        "POST",
        "/api/rewrite/internal/v1/official-option-simulation/receipts",
        body=raw,
        headers={**base_headers, "idempotency-key": "proposal-aapl-api-2"},
    ))
    assert status == 409
    assert "UNIQUE" not in json.dumps(payload)
    assert "sqlite" not in json.dumps(payload).lower()
    assert journal.database.fetch_one(
        "SELECT COUNT(*) count FROM official_option_sim_events"
    )["count"] == 1

    status, _, _ = asyncio.run(_asgi(
        "POST",
        "/api/rewrite/internal/v1/official-option-simulation/receipts",
        body=raw,
        headers={
            **base_headers,
            "idempotency-key": "proposal-aapl-api-3",
            "x-ciclotrade-simulation-signature": "sha256=" + "0" * 64,
        },
    ))
    assert status == 401
