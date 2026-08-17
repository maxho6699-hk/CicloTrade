from __future__ import annotations

from datetime import datetime

import pytest

from core.auto_live_control import AutoLiveConflict, AutoLiveControlError
from core.auto_live_runtime_worker import AutoLiveShadowWorker
from tests.test_auto_live_control import NOW
from tests.test_auto_live_runtime_worker import _started


def _running(tmp_path, *, source):
    db, control, mandate_id, epoch = _started(tmp_path)
    lease = control.claim_runtime_lease(
        mandate_id,
        worker_id="shadow-worker",
        expected_fencing_epoch=epoch,
        lease_seconds=30,
        now=datetime.fromisoformat(NOW),
    )
    control.ack_runtime_running(
        mandate_id,
        worker_id="shadow-worker",
        lease_token=lease["lease_token"],
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW),
    )
    worker = AutoLiveShadowWorker(
        control,
        source=source,
        worker_id="shadow-worker",
        clock=lambda: datetime.fromisoformat(NOW),
    )
    return db, control, worker, mandate_id, epoch, lease["lease_token"]


def _intent(client_order_id="shadow-order-001", *, quantity=1, limit_price=100.0):
    return {
        "client_order_id": client_order_id,
        "action": "open",
        "instrument_type": "stock",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": quantity,
        "limit_price": limit_price,
        "currency": "USD",
        "quote_at": NOW,
        "quote_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
    }


def test_shadow_worker_persists_intent_before_any_broker_execution(tmp_path):
    class Source:
        def due(self, mandate_public_id, fencing_epoch, as_of):
            assert mandate_public_id and fencing_epoch == 1 and as_of == datetime.fromisoformat(NOW)
            return [_intent()]

    db, _, worker, mandate_id, epoch, token = _running(tmp_path, source=Source())
    result = worker.run_once(mandate_id, lease_token=token, fencing_epoch=epoch)
    assert result == {"status": "shadowed", "intents": 1, "reused": 0}

    with db.transaction() as conn:
        intent = conn.execute("SELECT * FROM auto_live_order_intents").fetchone()
        event = conn.execute("SELECT * FROM auto_live_order_intent_events").fetchone()
        broker_receipts = conn.execute("SELECT COUNT(*) FROM auto_live_order_receipt_projections").fetchone()[0]
    assert intent["execution_mode"] == "shadow"
    assert intent["strategy_version"] == "strategy.v1"
    assert intent["risk_version"] == "risk.v1"
    assert intent["client_order_id"] == "shadow-order-001"
    assert event["event_type"] == "shadowed"
    assert broker_receipts == 0


def test_shadow_intent_is_idempotent_and_rejects_changed_replay(tmp_path):
    class Source:
        def __init__(self):
            self.payload = _intent()

        def due(self, *_):
            return [self.payload]

    source = Source()
    _, _, worker, mandate_id, epoch, token = _running(tmp_path, source=source)
    assert worker.run_once(mandate_id, lease_token=token, fencing_epoch=epoch)["intents"] == 1
    replay = worker.run_once(mandate_id, lease_token=token, fencing_epoch=epoch)
    assert replay == {"status": "shadowed", "intents": 0, "reused": 1}

    source.payload = _intent(quantity=2)
    with pytest.raises(AutoLiveConflict, match="client_order_id"):
        worker.run_once(mandate_id, lease_token=token, fencing_epoch=epoch)


def test_shadow_worker_fails_closed_on_capital_and_frequency_limits(tmp_path):
    class Source:
        def __init__(self, payloads):
            self.payloads = payloads

        def due(self, *_):
            return self.payloads

    _, _, worker, mandate_id, epoch, token = _running(
        tmp_path,
        source=Source([_intent(quantity=20, limit_price=100.0)]),
    )
    with pytest.raises(AutoLiveControlError, match="资本"):
        worker.run_once(mandate_id, lease_token=token, fencing_epoch=epoch)

    payloads = [_intent(f"shadow-order-{index:03d}") for index in range(1, 7)]
    worker.source = Source(payloads)
    with pytest.raises(AutoLiveControlError, match="频率"):
        worker.run_once(mandate_id, lease_token=token, fencing_epoch=epoch)
