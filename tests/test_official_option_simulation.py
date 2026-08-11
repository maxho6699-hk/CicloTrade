from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import hmac
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.database import DatabaseManager
from core.official_option_sim_contracts import OfficialOptionSimulationError
from core.official_option_sim_journal import OfficialOptionSimulationJournal
from scheduler.official_option_simulation import OfficialOptionSimulationScheduler, seal_simulation_task
from src.apps.api.official_option_sim_read_model import OfficialOptionSimulationReadModel
from src.apps.api.official_option_sim_receiver import (
    MAX_RECEIPT_BODY_BYTES,
    OfficialOptionSimulationReceiver,
    OfficialOptionSimulationReceiverError,
    official_option_sim_receipt,
)
from src.apps.api.earnings_read_model import OpaqueIdCodec


NOW = datetime.fromisoformat("2026-08-12T14:30:00+00:00")


def _leg(*, side="BUY", bid=9.8, ask=10.0, execution_price=None):
    value = {
        "contract_key": "AAPL-20260918-C-200", "side": side, "quantity": 1,
        "expiry": "2026-09-18", "right": "CALL", "strike": 200.0, "multiplier": 100,
        "bid": bid, "ask": ask, "quote_at": "2026-08-12T14:29:30Z",
        "is_realtime": True, "actionable_quote": True, "fallback_from": None,
        "quote_source": "authoritative-options-feed", "commission": 1.0,
    }
    if execution_price is not None:
        value["execution_price"] = execution_price
    return value


def _proposal():
    return {
        "schema_version": 1, "event_id": "proposal-aapl-1", "position_key": "official-aapl-1",
        "event_type": "PROPOSED", "action_at": "2026-08-12T14:30:00Z", "worker_id": "strategy-worker",
        "fencing_epoch": 1, "strategy_id": "options-event", "strategy_version": "v1",
        "model_version": "m1", "manifest_sha256": "a" * 64, "evidence_hashes": ["b" * 64],
        "position": {
            "structure_type": "LONG_CALL", "underlying": "AAPL", "currency": "USD",
            "account_equity": 100_000.0, "portfolio_risk_before_pct": 0.5,
            "portfolio_risk_limit_pct": 4.0,
            "risk": {"defined_risk": True, "max_loss": 1001.0, "max_account_pct": 1.01, "invalidation_condition": "Quote becomes unavailable."},
            "legs": [_leg()],
        },
    }


def _event(event_type, event_id, **extra):
    value = _proposal()
    value.pop("position")
    value.update(event_type=event_type, event_id=event_id)
    value.update(extra)
    return value


@pytest.fixture()
def journal(tmp_path):
    return OfficialOptionSimulationJournal(DatabaseManager(str(tmp_path / "official-sim.db")), clock=lambda: NOW)


def test_official_simulation_lifecycle_is_append_only_and_paper_only(journal):
    proposed = journal.record(_proposal(), idempotency_key="proposal-aapl-1")
    assert proposed["lifecycle_state"] == "proposed"
    journal.record(_event("ACCEPTED", "accepted-aapl-1"), idempotency_key="accepted-aapl-1")
    opened = _event("OPENED", "opened-aapl-1", execution={"slippage_bps": 0, "legs": [_leg(execution_price=10.0)]})
    opening = journal.record(opened, idempotency_key="opened-aapl-1")
    assert opening["cash_flow"] == -1001.0
    marked = _event("MARKED", "marked-aapl-1", execution={"legs": [_leg(bid=12.0, ask=12.2)]})
    mark = journal.record(marked, idempotency_key="marked-aapl-1")
    assert mark["unrealized_pnl"] == pytest.approx(199.0)
    journal.record(_event("CLOSING", "closing-aapl-1"), idempotency_key="closing-aapl-1")
    closed = _event("CLOSED", "closed-aapl-1", execution={"slippage_bps": 0, "legs": [_leg(side="SELL", bid=13.0, ask=13.2, execution_price=13.0)]})
    result = journal.record(closed, idempotency_key="closed-aapl-1")
    assert result["lifecycle_state"] == "closed"
    assert result["realized_pnl"] == pytest.approx(298.0)
    assert len(journal.position_events("official-aapl-1")) == 6
    with journal.database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM official_option_sim_events WHERE id=?", (result["id"],))


def test_quote_safety_fok_defined_risk_and_fencing_fail_closed(journal):
    unsafe = _proposal()
    unsafe["position"]["legs"][0]["fallback_from"] = "delayed"
    with pytest.raises(OfficialOptionSimulationError, match="actionable realtime"):
        journal.record(unsafe, idempotency_key="unsafe-quote")
    proposal = journal.record(_proposal(), idempotency_key="proposal-aapl-1")
    journal.record(_event("ACCEPTED", "accepted-aapl-1"), idempotency_key="accepted-aapl-1")
    missing = _event("OPENED", "missing-aapl-1", execution={"slippage_bps": 0, "legs": []})
    with pytest.raises(OfficialOptionSimulationError, match="FOK"):
        journal.record(missing, idempotency_key="missing-aapl-1")
    wrong = _event("OPENED", "wrong-price-aapl-1", execution={"slippage_bps": 0, "legs": [_leg(execution_price=9.9)]})
    with pytest.raises(OfficialOptionSimulationError, match="adverse"):
        journal.record(wrong, idempotency_key="wrong-price-aapl-1")
    stale = _event("OPENED", "stale-fence-aapl-1", fencing_epoch=0, execution={"slippage_bps": 0, "legs": [_leg(execution_price=10)]})
    with pytest.raises(OfficialOptionSimulationError, match="fencing_epoch"):
        journal.record(stale, idempotency_key="stale-fence-aapl-1")
    assert proposal["event_type"] == "PROPOSED"


def test_idempotency_and_source_anonymous_read_projection(journal, tmp_path):
    one = journal.record(_proposal(), idempotency_key="proposal-aapl-1")
    two = journal.record(_proposal(), idempotency_key="proposal-aapl-1")
    assert one["id"] == two["id"]
    changed = _proposal()
    changed["strategy_version"] = "v2"
    with pytest.raises(OfficialOptionSimulationError, match="idempotency"):
        journal.record(changed, idempotency_key="proposal-aapl-1")
    model = OfficialOptionSimulationReadModel(journal.database._db_path, OpaqueIdCodec(b"s" * 32))
    overview = model.overview(has_capability=True)
    assert overview["execution_label"] == "真实行情模拟执行"
    assert overview["broker_execution"] is False
    assert "quote_source" not in json.dumps(overview)
    detail = model.detail(has_capability=True, opaque_id=overview["items"][0]["id"])
    assert "quote_source" not in json.dumps(detail)
    assert model.overview(has_capability=False)["state"] == "locked"


def test_signed_receiver_requires_enable_signature_and_header_binding(journal):
    secret = b"x" * 32
    receiver = OfficialOptionSimulationReceiver(journal, shared_secret=secret)
    raw = json.dumps(_proposal(), separators=(",", ":")).encode()
    headers = {"x-ciclotrade-worker-id": "strategy-worker", "x-ciclotrade-fencing-epoch": "1", "idempotency-key": "proposal-aapl-1"}
    headers["x-ciclotrade-simulation-signature"] = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    with pytest.raises(OfficialOptionSimulationReceiverError, match="尚未启用"):
        receiver.accept(raw, headers)
    receiver.enabled = True
    accepted = receiver.accept(raw, headers)
    assert accepted["event_type"] == "PROPOSED"
    bad = dict(headers, **{"x-ciclotrade-fencing-epoch": "2"})
    with pytest.raises(OfficialOptionSimulationReceiverError, match="围栏"):
        receiver.accept(raw, bad)


def test_signed_receiver_rejects_oversized_receipts_with_413_before_signature_check(journal):
    receiver = OfficialOptionSimulationReceiver(journal, shared_secret=b"x" * 32, enabled=True)

    with pytest.raises(OfficialOptionSimulationReceiverError, match="128 KiB") as error:
        receiver.accept(b"x" * (MAX_RECEIPT_BODY_BYTES + 1), {})

    assert error.value.status == 413


def test_receipt_endpoint_stops_streaming_at_the_128_kib_limit(journal):
    receiver = OfficialOptionSimulationReceiver(journal, shared_secret=b"x" * 32, enabled=True)

    class StreamingRequest:
        headers = {}
        app = SimpleNamespace(
            state=SimpleNamespace(official_option_sim_receiver=receiver)
        )

        async def stream(self):
            yield b"x" * MAX_RECEIPT_BODY_BYTES
            yield b"x"

    with pytest.raises(OfficialOptionSimulationReceiverError, match="128 KiB") as error:
        asyncio.run(official_option_sim_receipt(StreamingRequest()))

    assert error.value.status == 413


def test_scheduler_is_paper_only_and_idempotently_sealed():
    task = seal_simulation_task("proposal", {"candidate": "aapl"})
    assert task.authority["broker_execution"] is False
    class Source:
        def due(self, _): return [{"type": "proposal", "payload": {"candidate": "aapl"}}]
    class Sink:
        def seal(self, incoming): return incoming, True
    result = OfficialOptionSimulationScheduler(Source(), Sink(), clock=lambda: NOW).run()
    assert result == {"status": "sealed", "due": 1, "created": 1, "reused": 0}


def test_migration_is_repeatable_and_does_not_repurpose_legacy_orders(tmp_path):
    path = str(tmp_path / "fresh.db")
    first = DatabaseManager(path)
    second = DatabaseManager(path)
    assert first.fetch_one("SELECT version FROM schema_migrations WHERE version=?", ("0017_official_option_simulation.sql",))
    assert second.fetch_one("SELECT count(*) count FROM official_option_sim_events")["count"] == 0
    assert second.fetch_one("SELECT count(*) count FROM orders")["count"] == 0
