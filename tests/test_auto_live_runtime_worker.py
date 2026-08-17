from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3

import pytest

from core.auto_live_control import AutoLiveConflict, AutoLiveControlError
from tests.test_auto_live_control import NOW, _db, _payload, _ready_control, _runtime


def _started(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="stopped", fencing_epoch=0)
    with db.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO user_controls(user_id,opening_paused,updated_at) VALUES(1,0,?)", (NOW,))
    started = control.start_mandate(
        1,
        mandate["public_id"],
        expected_fencing_epoch=0,
        idempotency_key="runtime-start-001",
        now=datetime.fromisoformat(NOW),
    )
    assert started["runtime_state"] == "starting"
    return db, control, mandate["public_id"], started["fencing_epoch"]


def test_runtime_lease_ack_and_heartbeat_make_open_gate_ready(tmp_path):
    db, control, mandate_id, epoch = _started(tmp_path)
    lease = control.claim_runtime_lease(
        mandate_id,
        worker_id="worker-a",
        expected_fencing_epoch=epoch,
        lease_seconds=30,
        now=datetime.fromisoformat(NOW),
    )
    assert lease["worker_id"] == "worker-a"
    assert lease["fencing_epoch"] == epoch
    assert lease["lease_token"]

    running = control.ack_runtime_running(
        mandate_id,
        worker_id="worker-a",
        lease_token=lease["lease_token"],
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW),
    )
    assert running["runtime_state"] == "running"
    assert running["heartbeat_state"] == "fresh"
    assert control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW))["allowed"] is True

    renewed = control.renew_runtime_heartbeat(
        mandate_id,
        worker_id="worker-a",
        lease_token=lease["lease_token"],
        expected_fencing_epoch=epoch,
        lease_seconds=30,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=10),
    )
    assert renewed["heartbeat_state"] == "fresh"
    assert renewed["lease_expires_at"] > lease["lease_expires_at"]

    with db.transaction() as conn:
        receipts = conn.execute(
            "SELECT event_type,state,fencing_epoch FROM auto_live_runtime_receipts WHERE mandate_public_id=? ORDER BY rowid",
            (mandate_id,),
        ).fetchall()
    assert ("running_ack", "running", epoch) in {tuple(row) for row in receipts}


def test_runtime_lease_rejects_concurrent_stale_and_tampered_workers(tmp_path):
    db, control, mandate_id, epoch = _started(tmp_path)
    lease = control.claim_runtime_lease(
        mandate_id,
        worker_id="worker-a",
        expected_fencing_epoch=epoch,
        lease_seconds=30,
        now=datetime.fromisoformat(NOW),
    )
    with pytest.raises(AutoLiveConflict, match="租约"):
        control.claim_runtime_lease(
            mandate_id,
            worker_id="worker-b",
            expected_fencing_epoch=epoch,
            lease_seconds=30,
            now=datetime.fromisoformat(NOW) + timedelta(seconds=5),
        )
    with pytest.raises(AutoLiveControlError, match="token"):
        control.ack_runtime_running(
            mandate_id,
            worker_id="worker-a",
            lease_token="tampered-token",
            expected_fencing_epoch=epoch,
            now=datetime.fromisoformat(NOW) + timedelta(seconds=5),
        )

    replacement = control.claim_runtime_lease(
        mandate_id,
        worker_id="worker-b",
        expected_fencing_epoch=epoch,
        lease_seconds=30,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=31),
    )
    assert replacement["worker_id"] == "worker-b"
    with pytest.raises(AutoLiveControlError, match="token|租约"):
        control.ack_runtime_running(
            mandate_id,
            worker_id="worker-a",
            lease_token=lease["lease_token"],
            expected_fencing_epoch=epoch,
            now=datetime.fromisoformat(NOW) + timedelta(seconds=32),
        )
    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE auto_live_runtime_lease_events SET event_type='heartbeat'")


def test_tampered_runtime_lease_projection_fails_closed(tmp_path):
    db, control, mandate_id, epoch = _started(tmp_path)
    lease = control.claim_runtime_lease(
        mandate_id,
        worker_id="worker-a",
        expected_fencing_epoch=epoch,
        lease_seconds=30,
        now=datetime.fromisoformat(NOW),
    )
    with db.transaction() as conn:
        conn.execute(
            "UPDATE auto_live_runtime_leases SET projection_sha256=? WHERE mandate_public_id=?",
            ("0" * 64, mandate_id),
        )
    with pytest.raises(AutoLiveControlError, match="租约"):
        control.ack_runtime_running(
            mandate_id,
            worker_id="worker-a",
            lease_token=lease["lease_token"],
            expected_fencing_epoch=epoch,
            now=datetime.fromisoformat(NOW) + timedelta(seconds=1),
        )
