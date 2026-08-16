from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

import pytest

from core.auto_live_control import AutoLiveConflict, AutoLiveControlError, AutoLiveControlPlane, sha256_json
from core.database import DatabaseManager

NOW = "2026-08-15T00:00:00+00:00"
END = "2026-12-15T00:00:00+00:00"


def _db(tmp_path):
    db = DatabaseManager(str(tmp_path / "auto-live.db"))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(email,password_hash,created_at,plan_type,subscription_expire) VALUES(?,?,?,?,?)",
            ("auto@example.test", "hash", NOW, "高级版", "2027-01-01T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO broker_accounts
               (user_id,provider,account_alias,external_account_id,mode,is_active,status,metadata_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                1,
                "tiger",
                "primary",
                "TGR-1",
                "live",
                1,
                "authorized",
                json.dumps({"execution_authorized": True, "authorization_verified_at": NOW}),
                NOW,
            ),
        )
        for strategy in ("strategy.v0", "strategy.v1", "strategy.active", "strategy.expiring"):
            snapshot = {
                "contract_version": "approved-test-v1",
                "strategy_version": strategy,
                "risk_version": "risk.v1",
            }
            conn.execute(
                """INSERT INTO auto_live_strategy_risk_contracts
                   (strategy_version,risk_version,snapshot_json,snapshot_sha256,approved_at,valid_until,is_active)
                   VALUES(?,?,?,?,?,?,1)""",
                (
                    strategy,
                    "risk.v1",
                    json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                    sha256_json(snapshot),
                    NOW,
                    "2027-01-01T00:00:00+00:00",
                ),
            )
    return db


def _payload():
    return {
        "broker_account_id": 1,
        "strategy_version": "strategy.v1",
        "risk_version": "risk.v1",
        "capital_limit_minor": 100_000,
        "frequency_limit": 5,
        "valid_from": NOW,
        "valid_until": END,
    }


def _ready_control(db):
    return AutoLiveControlPlane(
        db,
        gate_checker=lambda *_: {
            "entitlement_account_capacity": True,
            "telegram": True,
            "broker_authorization": True,
            "broker_live_environment": True,
            "platform_switch": True,
            "global_pause": True,
            "strategy": True,
            "risk": True,
            "data_health": True,
        },
    )


def _runtime(db, mandate_public_id, state="stopped", fencing_epoch=0, *, running_ack=False):
    with db.transaction() as conn:
        body = {
            "mandate_public_id": mandate_public_id,
            "runtime_state": state,
            "can_reduce_exposure": 1,
            "fencing_epoch": fencing_epoch,
            "last_error_code": None,
            "observed_at": NOW,
        }
        conn.execute(
            """INSERT INTO auto_live_runtime_projections
               (mandate_public_id,state,can_reduce_exposure,fencing_epoch,observed_at,projection_sha256)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(mandate_public_id) DO UPDATE SET state=excluded.state,can_reduce_exposure=excluded.can_reduce_exposure,fencing_epoch=excluded.fencing_epoch,last_error_code=NULL,observed_at=excluded.observed_at,projection_sha256=excluded.projection_sha256""",
            (mandate_public_id, state, 1, fencing_epoch, NOW, sha256_json(body)),
        )
        heartbeat_body = {
            "mandate_public_id": mandate_public_id,
            "heartbeat_state": "fresh",
            "heartbeat_at": NOW,
            "fencing_epoch": fencing_epoch,
            "observed_at": NOW,
        }
        conn.execute(
            """INSERT INTO auto_live_heartbeat_projections
               (mandate_public_id,heartbeat_state,heartbeat_at,fencing_epoch,observed_at,projection_sha256)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(mandate_public_id) DO UPDATE SET heartbeat_state=excluded.heartbeat_state,heartbeat_at=excluded.heartbeat_at,fencing_epoch=excluded.fencing_epoch,observed_at=excluded.observed_at,projection_sha256=excluded.projection_sha256""",
            (mandate_public_id, "fresh", NOW, fencing_epoch, NOW, sha256_json(heartbeat_body)),
        )
        if running_ack:
            assert state == "running"
            receipt_body = {
                "mandate_public_id": mandate_public_id,
                "event_type": "running_ack",
                "runtime_state": "running",
                "fencing_epoch": fencing_epoch,
                "observed_at": NOW,
                "detail": None,
            }
            conn.execute(
                """INSERT INTO auto_live_runtime_receipts
                   (receipt_id,mandate_public_id,event_type,state,fencing_epoch,payload_json,payload_sha256,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    f"runtime_ack_{uuid.uuid4().hex}",
                    mandate_public_id,
                    "running_ack",
                    "running",
                    fencing_epoch,
                    json.dumps(receipt_body, sort_keys=True, separators=(",", ":")),
                    sha256_json(receipt_body),
                    NOW,
                ),
            )


def test_migration_has_opaque_ledger_tables_and_append_only_guards(tmp_path):
    db = _db(tmp_path)
    mandate = AutoLiveControlPlane(db).create_mandate(1, _payload())
    with db.transaction() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "auto_live_mandates",
            "auto_live_mandate_events",
            "auto_live_pause_requests",
            "auto_live_pause_request_targets",
            "auto_live_runtime_projections",
            "auto_live_heartbeat_projections",
            "auto_live_order_receipt_projections",
            "auto_live_start_requests",
            "auto_live_runtime_receipts",
        } <= names
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE auto_live_mandates SET snapshot_sha256=? WHERE public_id=?", ("a" * 64, mandate["public_id"])
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM auto_live_mandate_events")


def test_mandate_requires_exact_confirmation_and_snapshot_is_not_public_account_data(tmp_path):
    control = AutoLiveControlPlane(_db(tmp_path))
    mandate = control.create_mandate(1, _payload())
    assert mandate["state"] == "draft"
    assert "external_account_id" not in mandate
    assert "user_id" not in mandate and "broker_account_id" not in mandate
    pending = control.submit_confirmation(1, mandate["public_id"])
    assert pending["state"] == "pending_confirmation"
    with pytest.raises(AutoLiveControlError, match="确认短语"):
        control.confirm_mandate(1, mandate["public_id"], "ACTIVATE wrong")


def test_missing_gates_fail_closed_and_terminal_states_cannot_resume(tmp_path):
    control = AutoLiveControlPlane(_db(tmp_path))
    mandate = control.create_mandate(1, _payload())
    control.submit_confirmation(1, mandate["public_id"])
    blocked = control.confirm_mandate(1, mandate["public_id"], f"ACTIVATE {mandate['public_id']}")
    assert blocked["state"] == "blocked"
    expired = control.expire_mandate(1, mandate["public_id"])
    assert expired["state"] == "expired"
    with pytest.raises(AutoLiveControlError, match="不可恢复"):
        control.resume_mandate(1, mandate["public_id"])


def test_resume_requires_new_confirmation_before_active(tmp_path):
    control = _ready_control(_db(tmp_path))
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    active = control.confirm_mandate(
        1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW)
    )
    assert active["state"] == "active"
    _runtime(control.db, mandate["public_id"], state="paused")
    control.request_pause(
        1,
        {"scope": "mandate", "mandate_public_id": mandate["public_id"]},
        idempotency_key="pause-mandate-1",
        now=datetime.fromisoformat(NOW),
    )
    resumed = control.resume_mandate(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    assert resumed["state"] == "pending_confirmation"
    confirmed = control.confirm_mandate(1, mandate["public_id"], resumed["confirmation_phrase"])
    assert confirmed["state"] == "active"


def test_aggregate_pause_is_atomic_and_idempotent_with_conflict_detection(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    ids = []
    for i in range(2):
        payload = _payload() | {"strategy_version": f"strategy.v{i}"}
        mandate = control.create_mandate(1, payload)
        pending = control.submit_confirmation(1, mandate["public_id"])
        control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
        _runtime(db, mandate["public_id"], state="paused")
        ids.append(mandate["public_id"])
    first = control.request_pause(
        1, {"scope": "aggregate"}, idempotency_key="pause-aggregate-1", now=datetime.fromisoformat(NOW)
    )
    replay = control.request_pause(
        1, {"scope": "aggregate"}, idempotency_key="pause-aggregate-1", now=datetime.fromisoformat(NOW)
    )
    assert first == replay
    assert first["status"] == "paused" and first["confirmed"] == first["total"] == 2
    with db.transaction() as conn:
        assert conn.execute("SELECT opening_paused FROM user_controls WHERE user_id=1").fetchone()[0] == 1
        assert {r[0] for r in conn.execute("SELECT state FROM auto_live_mandates")} == {"paused"}
    with pytest.raises(AutoLiveConflict):
        control.request_pause(1, {"scope": "broker", "broker_account_id": 1}, idempotency_key="pause-aggregate-1")


def test_unknown_runtime_is_reported_as_partial_not_claimed_as_fully_paused(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
    with db.transaction() as conn:
        conn.execute("DELETE FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],))
        conn.execute(
            "INSERT INTO auto_live_runtime_projections(mandate_public_id,state,can_reduce_exposure,last_error_code,observed_at,projection_sha256) VALUES(?,?,?,?,?,?)",
            (mandate["public_id"], "unknown", 1, "NO_RUNTIME_RECEIPT", NOW, "b" * 64),
        )
    receipt = control.request_pause(1, {"scope": "aggregate"}, idempotency_key="pause-partial-1")
    assert receipt["status"] == "partial"
    assert receipt["confirmed"] == 0 and receipt["total"] == 1
    assert receipt["unconfirmed"] == [mandate["public_id"]]


def test_cross_user_lookup_and_duplicate_confirmation_are_denied(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(email,password_hash,created_at,plan_type,subscription_expire) VALUES(?,?,?,?,?)",
            ("other@example.test", "hash", NOW, "高级版", "2027-01-01T00:00:00+00:00"),
        )
    with pytest.raises(AutoLiveControlError, match="不存在"):
        control.get_mandate(2, mandate["public_id"])
    pending = control.submit_confirmation(1, mandate["public_id"])
    assert control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])["state"] == "active"
    with pytest.raises(AutoLiveControlError, match="尚未等待"):
        control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])


def test_catalog_key_futu_moomoo_is_accepted_but_execution_stays_fail_closed(tmp_path):
    db = _db(tmp_path)
    with db.transaction() as conn:
        conn.execute("UPDATE broker_accounts SET provider='futu_moomoo' WHERE id=1")
    control = AutoLiveControlPlane(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    blocked = control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
    assert blocked["state"] == "blocked"


def test_start_pause_fencing_cas_and_unknown_submission_block_opening_but_allow_safe_exit(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
    _runtime(db, mandate["public_id"], state="stopped")
    started = control.start_mandate(1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-fence-1", now=datetime.fromisoformat(NOW))
    assert started["runtime_state"] == "starting"
    with pytest.raises(AutoLiveConflict, match="epoch"):
        control.start_mandate(1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-fence-1-replay", now=datetime.fromisoformat(NOW))
    with db.transaction() as conn:
        conn.execute("UPDATE auto_live_order_receipt_projections SET submission_state='submission_unknown' WHERE 1=0")
        conn.execute(
            "INSERT INTO auto_live_order_receipt_projections(public_id,mandate_public_id,client_order_id,submission_state,observed_at,receipt_sha256) VALUES(?,?,?,?,?,?)",
            ("receipt_unknown_1", mandate["public_id"], "client-1", "submission_unknown", NOW, "c" * 64),
        )
    gate = control.action_gate(1, mandate["public_id"], "open")
    assert gate["allowed"] is False
    assert control.action_gate(1, mandate["public_id"], "cancel")["allowed"] is True
    assert control.action_gate(1, mandate["public_id"], "reduce_exposure")["allowed"] is True
    assert control.action_gate(1, mandate["public_id"], "close_position")["allowed"] is True
    receipt = control.request_pause(1, {"scope": "aggregate"}, idempotency_key="pause-fence-1")
    assert receipt["status"] == "partial"
    with pytest.raises(AutoLiveConflict, match="epoch"):
        control.start_mandate(1, mandate["public_id"], expected_fencing_epoch=1, idempotency_key="start-fence-2", now=datetime.fromisoformat(NOW))
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT fencing_epoch FROM auto_live_mandates WHERE public_id=?", (mandate["public_id"],)
            ).fetchone()[0]
            == 2
        )


def test_unknown_runtime_never_claims_running_or_allows_start(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
    with db.transaction() as conn:
        conn.execute("DELETE FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],))
        conn.execute(
            "INSERT INTO auto_live_runtime_projections(mandate_public_id,state,can_reduce_exposure,fencing_epoch,observed_at,projection_sha256) VALUES(?,?,?,?,?,?)",
            (mandate["public_id"], "unknown", 1, 0, NOW, "d" * 64),
        )
    started = control.start_mandate(1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-unknown-1", now=datetime.fromisoformat(NOW))
    assert started["runtime_state"] == "blocked"
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT state FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],)
            ).fetchone()[0]
            == "unknown"
        )


def test_owner_snapshot_and_opaque_broker_ref_are_strictly_scoped(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    broker_ref = control.broker_account_public_ref(1, 1)
    mandate_payload = _payload() | {"broker_account_public_id": broker_ref}
    mandate_payload.pop("broker_account_id")
    mandate = control.create_mandate_from_public_ref(1, mandate_payload)
    snapshot = control.list_snapshot(1)
    assert snapshot["broker_accounts"][0]["public_id"] == broker_ref
    assert snapshot["mandates"][0]["broker_account_public_id"] == broker_ref
    assert all(
        "user_id" not in item and "broker_account_id" not in item and "external_account_id" not in item
        for item in snapshot["mandates"]
    )
    assert "user_id" not in snapshot and "broker_account_id" not in str(snapshot)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(email,password_hash,created_at,plan_type,subscription_expire) VALUES(?,?,?,?,?)",
            ("other-snapshot@example.test", "hash", NOW, "高级版", "2027-01-01T00:00:00+00:00"),
        )
    with pytest.raises(AutoLiveControlError, match="不存在"):
        control.create_mandate_from_public_ref(2, mandate_payload)
    pending = control.submit_confirmation(1, mandate["public_id"])
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
    with db.transaction() as conn:
        conn.execute("DELETE FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],))
        conn.execute(
            "INSERT INTO auto_live_runtime_projections(mandate_public_id,state,can_reduce_exposure,fencing_epoch,observed_at,projection_sha256) VALUES(?,?,?,?,?,?)",
            (
                mandate["public_id"],
                "paused",
                1,
                0,
                NOW,
                sha256_json(
                    {
                        "mandate_public_id": mandate["public_id"],
                        "runtime_state": "paused",
                        "can_reduce_exposure": 1,
                        "fencing_epoch": 0,
                        "last_error_code": None,
                        "observed_at": NOW,
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO auto_live_heartbeat_projections(mandate_public_id,heartbeat_state,heartbeat_at,observed_at,projection_sha256) VALUES(?,?,?,?,?)",
            (mandate["public_id"], "fresh", NOW, NOW, "a" * 64),
        )
        conn.execute(
            "INSERT INTO auto_live_order_receipt_projections(public_id,mandate_public_id,client_order_id,submission_state,broker_order_id,observed_at,receipt_sha256) VALUES(?,?,?,?,?,?,?)",
            ("receipt-public-1", mandate["public_id"], "client-public-1", "accepted", "BROKER-SECRET", NOW, "b" * 64),
        )
    receipt = control.request_pause_public_ref(
        1, broker_ref, idempotency_key="pause-public-ref-1", now=datetime.fromisoformat(NOW)
    )
    assert receipt["status"] == "paused"
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM auto_live_pause_receipts WHERE request_public_id=?", (receipt["public_id"],)
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE auto_live_pause_receipts SET status='failed' WHERE request_public_id=?", (receipt["public_id"],)
            )
        conn.execute(
            "UPDATE auto_live_pause_requests SET receipt_sha256=? WHERE public_id=?", ("e" * 64, receipt["public_id"])
        )
    replay = control.request_pause_public_ref(1, broker_ref, idempotency_key="pause-public-ref-1")
    assert replay["receipt_sha256"] == receipt["receipt_sha256"]
    listed = control.list_snapshot(1)
    assert listed["pause_receipts"][0]["receipt_sha256"] == receipt["receipt_sha256"]
    assert listed["runtime_projections"][0]["state"] == "paused"
    assert listed["heartbeat_projections"][0]["heartbeat_state"] == "fresh"
    assert listed["order_receipts"][0]["submission_state"] == "accepted"
    assert "broker_order_id" not in str(listed) and "BROKER-SECRET" not in str(listed)


def test_expiry_confirmation_and_active_revoke_runtime_fencing_are_fail_closed(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    expired_confirmation = control.confirm_mandate(
        1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(END)
    )
    assert expired_confirmation["state"] == "expired"
    assert control.get_mandate(1, mandate["public_id"])["state"] == "expired"

    active_mandate = control.create_mandate(1, _payload() | {"strategy_version": "strategy.active"})
    active_pending = control.submit_confirmation(1, active_mandate["public_id"])
    control.confirm_mandate(1, active_mandate["public_id"], active_pending["confirmation_phrase"])
    _runtime(db, active_mandate["public_id"], state="stopped")
    control.start_mandate(1, active_mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-revoke-1", now=datetime.fromisoformat(NOW))
    revoked = control.revoke_mandate(1, active_mandate["public_id"], reason="operator", now=datetime.fromisoformat(NOW))
    assert revoked["state"] == "revoked"
    with db.transaction() as conn:
        runtime = conn.execute(
            "SELECT state,fencing_epoch FROM auto_live_runtime_projections WHERE mandate_public_id=?",
            (active_mandate["public_id"],),
        ).fetchone()
    assert runtime["state"] == "blocked" and runtime["fencing_epoch"] == 2
    assert control.action_gate(1, active_mandate["public_id"], "open")["allowed"] is False
    assert control.action_gate(1, active_mandate["public_id"], "close_position")["allowed"] is True

    expiring = control.create_mandate(1, _payload() | {"strategy_version": "strategy.expiring"})
    expiring_pending = control.submit_confirmation(1, expiring["public_id"])
    control.confirm_mandate(1, expiring["public_id"], expiring_pending["confirmation_phrase"])
    _runtime(db, expiring["public_id"], state="stopped")
    control.start_mandate(1, expiring["public_id"], expected_fencing_epoch=0, idempotency_key="start-expire-1", now=datetime.fromisoformat(NOW))
    expired = control.expire_mandate(1, expiring["public_id"], now=datetime.fromisoformat(END))
    assert expired["state"] == "expired"
    with db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT state FROM auto_live_runtime_projections WHERE mandate_public_id=?", (expiring["public_id"],)
            ).fetchone()[0]
            == "paused"
        )


def test_opening_gate_requires_window_and_running_runtime_but_safe_exit_survives(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    assert control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])["state"] == "active"
    for state in (None, "paused", "stopped", "blocked", "unknown"):
        if state is not None:
            _runtime(db, mandate["public_id"], state=state)
        assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is False
        if state is not None:
            with db.transaction() as conn:
                conn.execute("DELETE FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],))
    _runtime(db, mandate["public_id"], state="running", running_ack=True)
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is True
    with db.transaction() as conn:
        conn.execute("UPDATE user_controls SET opening_paused=1 WHERE user_id=1")
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is False
    assert control.action_gate(1, mandate["public_id"], "close_position")["allowed"] is True
    future = control.create_mandate(1, _payload() | {"valid_from": END, "valid_until": "2027-01-01T00:00:00+00:00"})
    future_pending = control.submit_confirmation(1, future["public_id"])
    assert control.confirm_mandate(1, future["public_id"], future_pending["confirmation_phrase"])["state"] == "blocked"


def test_contract_approval_expiry_and_snapshot_tamper_fail_closed(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    with pytest.raises(AutoLiveControlError, match="策略/风险合同"):
        control.create_mandate(1, _payload() | {"strategy_version": "strategy.unapproved"})
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
    _runtime(db, mandate["public_id"], state="running", running_ack=True)
    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="contract snapshot"):
            conn.execute(
                "UPDATE auto_live_strategy_risk_contracts SET snapshot_json=? WHERE strategy_version=?",
                ('{"forged":true}', "strategy.v1"),
            )
        conn.execute(
            "UPDATE auto_live_strategy_risk_contracts SET valid_until=? WHERE strategy_version=?",
            ("2026-08-15T00:00:01+00:00", "strategy.v1"),
        )
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat("2026-08-15T00:00:02+00:00"))["allowed"] is False


def test_scoped_pause_is_owner_checked_and_unconfirmed_runtime_is_partial(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    with db.transaction() as conn:
        conn.execute("INSERT INTO users(email,password_hash,created_at,plan_type,subscription_expire) VALUES(?,?,?,?,?)", ("other@example.test", "hash", NOW, "高级版", "2027-01-01T00:00:00+00:00"))
        conn.execute("INSERT INTO broker_accounts(user_id,provider,account_alias,external_account_id,mode,is_active,status,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (2, "tiger", "other", "TGR-2", "live", 1, "authorized", "{}", NOW))
    with pytest.raises(AutoLiveControlError) as broker_error:
        control.request_pause(1, {"scope": "broker", "broker_account_id": 2}, idempotency_key="cross-broker-1")
    assert broker_error.value.status_code == 404
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auto_live_pause_requests").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="owner mismatch"):
            conn.execute("INSERT INTO auto_live_broker_refs(public_id,user_id,broker_account_id,created_at) VALUES(?,?,?,?)", ("broker_forged_1", 1, 2, NOW))
    other_mandate = control.create_mandate(2, _payload() | {"broker_account_id": 2})
    with pytest.raises(AutoLiveControlError) as mandate_error:
        control.request_pause(1, {"scope": "mandate", "mandate_public_id": other_mandate["public_id"]}, idempotency_key="cross-mandate-1")
    assert mandate_error.value.status_code == 404
    with db.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="owner mismatch"):
            conn.execute(
                "INSERT INTO auto_live_pause_requests(public_id,user_id,scope,mandate_public_id,idempotency_key,request_fingerprint,status,confirmed,total,unconfirmed_json,receipt_json,receipt_sha256,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("pause_forged_1", 1, "mandate", other_mandate["public_id"], "forged-key-1", "a" * 64, "pausing", 0, 0, "[]", "{}", "b" * 64, NOW, NOW),
            )
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"])
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"])
    with db.transaction() as conn:
        conn.execute("DELETE FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],))
    receipt = control.request_pause(1, {"scope": "aggregate"}, idempotency_key="missing-runtime-1")
    assert receipt["status"] == "partial" and receipt["confirmed"] == 0
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],)).fetchone()[0] == 0
