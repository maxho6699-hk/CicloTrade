from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from core.auto_live_control import AutoLiveConflict, AutoLiveControlError, AutoLiveControlPlane
from core.auto_live_control_common import sha256_json
from tests.test_auto_live_control import END, NOW, _payload, _ready_control, _runtime, _db


@pytest.mark.parametrize("revoked_setting", ["verified", "consent"])
def test_telegram_gate_blocks_active_binding_after_settings_consent_revocation(tmp_path, revoked_setting):
    db = _db(tmp_path)
    control = AutoLiveControlPlane(db)
    with db.transaction() as conn:
        settings = {"telegram": {"chat_id": "123456", "verified": True, "consent": True}}
        conn.execute(
            "INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES(?,?,?)",
            (1, json.dumps(settings, separators=(",", ":")), NOW),
        )
        conn.execute(
            """INSERT INTO telegram_accounts
               (user_id,chat_id,is_active,revoked_at,created_at,updated_at)
               VALUES(?,?,1,NULL,?,?)""",
            (1, "123456", NOW, NOW),
        )
        for key, value in {
            "auto_live_enabled": "1",
            "global_auto_live_paused": "0",
            "auto_live_strategy_gate": "ready",
            "auto_live_risk_gate": "ready",
            "auto_live_data_health_gate": "healthy",
        }.items():
            conn.execute(
                "INSERT INTO platform_controls(control_key,control_value,updated_at) VALUES(?,?,?)",
                (key, value, NOW),
            )
    mandate = control.create_mandate(1, _payload())
    valid_gates = control.evaluate_resume_gates(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    assert next(gate for gate in valid_gates["gates"] if gate["name"] == "telegram")["ok"] is True

    settings["telegram"][revoked_setting] = False
    with db.transaction() as conn:
        conn.execute(
            "UPDATE user_settings SET settings_json=?,updated_at=? WHERE user_id=?",
            (json.dumps(settings, separators=(",", ":")), NOW, 1),
        )
    gates = control.evaluate_resume_gates(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    telegram_gate = next(gate for gate in gates["gates"] if gate["name"] == "telegram")
    assert telegram_gate == {
        "name": "telegram",
        "ok": False,
        "reason": "telegram_unverified",
    }


def test_mandate_idempotency_is_owner_scoped_and_append_only(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    first = control.create_mandate(1, _payload(), idempotency_key="mandate-replay-1")
    replay = control.create_mandate(1, _payload(), idempotency_key="mandate-replay-1")
    assert replay == first

    with pytest.raises(AutoLiveConflict, match="相同 Idempotency-Key"):
        control.create_mandate(
            1,
            _payload() | {"capital_limit_minor": 200_000},
            idempotency_key="mandate-replay-1",
        )

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(email,password_hash,created_at,plan_type,subscription_expire) VALUES(?,?,?,?,?)",
            ("other-mandate@example.test", "hash", NOW, "高级版", "2027-01-01T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO broker_accounts
               (user_id,provider,account_alias,external_account_id,mode,is_active,status,metadata_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (2, "tiger", "other", "TGR-2", "live", 1, "authorized", "{}", NOW),
        )
    other = control.create_mandate(
        2,
        _payload() | {"broker_account_id": 2},
        idempotency_key="mandate-replay-1",
    )
    assert other["public_id"] != first["public_id"]

    with db.transaction() as conn:
        request_public_id = conn.execute(
            "SELECT public_id FROM auto_live_mandate_requests WHERE mandate_public_id=?",
            (first["public_id"],),
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE auto_live_mandate_requests SET request_sha256=? WHERE public_id=?",
                ("a" * 64, request_public_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "DELETE FROM auto_live_mandate_requests WHERE public_id=?",
                (request_public_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "INSERT OR REPLACE INTO auto_live_mandate_requests SELECT * FROM auto_live_mandate_requests WHERE public_id=?",
                (request_public_id,),
            )
        assert conn.execute("SELECT COUNT(*) FROM auto_live_mandate_requests").fetchone()[0] == 2


def test_start_requires_idempotency_replays_conflicts_and_is_append_only(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="stopped")
    with pytest.raises(AutoLiveControlError, match="Idempotency-Key"):
        control.start_mandate(1, mandate["public_id"], expected_fencing_epoch=0, now=datetime.fromisoformat(NOW))
    first = control.start_mandate(
        1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-replay-1", now=datetime.fromisoformat(NOW)
    )
    assert first["actor"] == "owner" and "user_id" not in str(first)
    assert control.start_mandate(
        1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-replay-1", now=datetime.fromisoformat(NOW)
    ) == first
    with pytest.raises(AutoLiveConflict):
        control.start_mandate(
            1, mandate["public_id"], expected_fencing_epoch=1, idempotency_key="start-replay-1", now=datetime.fromisoformat(NOW)
        )
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auto_live_start_requests").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM auto_live_start_requests")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("INSERT OR REPLACE INTO auto_live_start_requests SELECT * FROM auto_live_start_requests")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("INSERT OR REPLACE INTO auto_live_runtime_receipts SELECT * FROM auto_live_runtime_receipts LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("INSERT OR REPLACE INTO auto_live_mandate_events SELECT * FROM auto_live_mandate_events LIMIT 1")
        conn.execute(
            "INSERT INTO auto_live_order_receipt_projections(public_id,mandate_public_id,client_order_id,submission_state,observed_at,receipt_sha256) VALUES(?,?,?,?,?,?)",
            ("receipt_replace_guard_1", mandate["public_id"], "client-replace-1", "accepted", NOW, "d" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "INSERT OR REPLACE INTO auto_live_order_receipt_projections SELECT * FROM auto_live_order_receipt_projections LIMIT 1"
            )
        assert conn.execute("SELECT COUNT(*) FROM auto_live_runtime_receipts").fetchone()[0] >= 2


def test_start_failure_persists_blocked_mandate_and_safe_receipt(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    with db.transaction() as conn:
        conn.execute("DELETE FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],))
    blocked = control.start_mandate(
        1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-blocked-1", now=datetime.fromisoformat(NOW)
    )
    assert blocked["status"] == "blocked" and blocked["state"] == "blocked"
    assert control.get_mandate(1, mandate["public_id"], now=datetime.fromisoformat(NOW))["state"] == "blocked"
    receipt = control.list_snapshot(1, now=datetime.fromisoformat(NOW))["start_receipts"][0]
    assert receipt["idempotency_key_sha256"] and receipt["created_at"] == NOW
    assert "actor_user_id" not in str(receipt) and "'idempotency_key':" not in str(receipt)


def test_local_pause_does_not_toggle_aggregate_control_and_receipt_has_targets(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    first = control.create_mandate(1, _payload())
    second = control.create_mandate(1, _payload() | {"strategy_version": "strategy.v0"})
    for mandate in (first, second):
        pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
        control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
        _runtime(db, mandate["public_id"], state="paused")
    receipt = control.request_pause(
        1, {"scope": "mandate", "mandate_public_id": first["public_id"]}, idempotency_key="pause-local-1", now=datetime.fromisoformat(NOW)
    )
    with db.transaction() as conn:
        assert conn.execute("SELECT opening_paused FROM user_controls WHERE user_id=1").fetchone()[0] == 0
        payload = json.loads(conn.execute("SELECT receipt_json FROM auto_live_pause_requests WHERE public_id=?", (receipt["public_id"],)).fetchone()[0])
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("INSERT OR REPLACE INTO auto_live_pause_receipts SELECT * FROM auto_live_pause_receipts LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "INSERT OR REPLACE INTO auto_live_pause_request_targets SELECT * FROM auto_live_pause_request_targets LIMIT 1"
            )
    assert payload["actor"]["type"] == "user"
    assert payload["created_at"] == NOW and payload["idempotency_key_sha256"]
    assert payload["target_details"][0]["target_public_id"] == first["public_id"]
    assert control.get_mandate(1, second["public_id"], now=datetime.fromisoformat(NOW))["state"] == "active"


def test_tampered_stale_and_clock_skewed_runtime_heartbeat_fail_closed(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="running", running_ack=True)
    at_now = datetime.fromisoformat(NOW)
    assert control.action_gate(1, mandate["public_id"], "open", now=at_now)["allowed"] is True
    with db.transaction() as conn:
        conn.execute("UPDATE auto_live_runtime_projections SET projection_sha256=? WHERE mandate_public_id=?", ("a" * 64, mandate["public_id"]))
    assert control.action_gate(1, mandate["public_id"], "open", now=at_now)["allowed"] is False
    with db.transaction() as conn:
        body = {
            "mandate_public_id": mandate["public_id"],
            "runtime_state": "running",
            "can_reduce_exposure": 1,
            "fencing_epoch": 0,
            "last_error_code": None,
            "observed_at": NOW,
        }
        conn.execute("UPDATE auto_live_runtime_projections SET projection_sha256=? WHERE mandate_public_id=?", (sha256_json(body), mandate["public_id"]))
        conn.execute("UPDATE auto_live_heartbeat_projections SET observed_at=? WHERE mandate_public_id=?", ("2026-08-14T23:57:00+00:00", mandate["public_id"]))
    assert control.action_gate(1, mandate["public_id"], "open", now=at_now)["allowed"] is False


def test_terminalize_does_not_overwrite_ahead_runtime_epoch(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="running", fencing_epoch=2)
    with pytest.raises(AutoLiveConflict, match="runtime fencing"):
        control.revoke_mandate(1, mandate["public_id"], reason="operator", now=datetime.fromisoformat(NOW))
    assert control.get_mandate(1, mandate["public_id"], now=datetime.fromisoformat(NOW))["state"] == "active"
    with db.transaction() as conn:
        assert conn.execute("SELECT fencing_epoch FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],)).fetchone()[0] == 2


def test_confirm_initializes_stopped_runtime_startable_but_not_openable(tmp_path):
    control = AutoLiveControlPlane(_db(tmp_path), gate_checker=lambda *_: {
        "entitlement_account_capacity": True,
        "telegram": True,
        "broker_authorization": True,
        "broker_live_environment": True,
        "platform_switch": True,
        "global_pause": True,
        "strategy": True,
        "risk": True,
        "data_health": True,
    })
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    confirmed = control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    assert confirmed["state"] == "active"
    with control.db.transaction() as conn:
        runtime = conn.execute("SELECT state,fencing_epoch FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],)).fetchone()
        assert runtime["state"] == "stopped" and runtime["fencing_epoch"] == 0
    assert control.action_gate(1, mandate["public_id"], "start", now=datetime.fromisoformat(NOW))["allowed"] is True
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is False


def test_expiry_is_utc_and_public_ids_are_opaque(tmp_path):
    control = AutoLiveControlPlane(_db(tmp_path))
    mandate = control.create_mandate(1, _payload())
    assert mandate["public_id"].startswith("mandate_")
    expired = control.get_mandate(1, mandate["public_id"], now=datetime.fromisoformat(END))
    assert expired["state"] == "expired"


def test_each_external_opening_gate_is_fail_closed(tmp_path):
    keys = ("entitlement_account_capacity", "telegram", "broker_authorization", "broker_live_environment", "platform_switch", "global_pause", "strategy", "risk", "data_health")
    blocked = {"key": None}
    control = AutoLiveControlPlane(_db(tmp_path), gate_checker=lambda *_: {key: blocked["key"] != key for key in keys})
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    assert control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))["state"] == "active"
    _runtime(control.db, mandate["public_id"], state="running", running_ack=True)
    for key in keys:
        blocked["key"] = key
        assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is False


def test_pause_preserves_ahead_runtime_epoch_and_remains_partial(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="running", fencing_epoch=2)
    with db.transaction() as conn:
        before = tuple(
            conn.execute(
                "SELECT state,fencing_epoch,last_error_code,observed_at,projection_sha256 FROM auto_live_runtime_projections WHERE mandate_public_id=?",
                (mandate["public_id"],),
            ).fetchone()
        )
    receipt = control.request_pause(
        1, {"scope": "aggregate"}, idempotency_key="pause-ahead-epoch-1", now=datetime.fromisoformat(NOW)
    )
    assert receipt["status"] == "partial" and receipt["confirmed"] == 0
    with db.transaction() as conn:
        after = tuple(
            conn.execute(
                "SELECT state,fencing_epoch,last_error_code,observed_at,projection_sha256 FROM auto_live_runtime_projections WHERE mandate_public_id=?",
                (mandate["public_id"],),
            ).fetchone()
        )
        mandate_row = conn.execute(
            "SELECT state,fencing_epoch FROM auto_live_mandates WHERE public_id=?", (mandate["public_id"],)
        ).fetchone()
        assert conn.execute("SELECT opening_paused FROM user_controls WHERE user_id=1").fetchone()[0] == 1
    assert after == before
    assert mandate_row["state"] == "paused" and mandate_row["fencing_epoch"] == 1


@pytest.mark.parametrize("projection_case", ["tampered", "safety_fields", "stale", "missing"])
def test_untrusted_paused_runtime_projection_is_never_confirmed(tmp_path, projection_case):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="paused")
    with db.transaction() as conn:
        if projection_case == "tampered":
            conn.execute(
                "UPDATE auto_live_runtime_projections SET projection_sha256=? WHERE mandate_public_id=?",
                ("a" * 64, mandate["public_id"]),
            )
        elif projection_case == "safety_fields":
            body = {
                "mandate_public_id": mandate["public_id"],
                "runtime_state": "paused",
                "can_reduce_exposure": 0,
                "fencing_epoch": 0,
                "last_error_code": "TAMPERED",
                "observed_at": NOW,
            }
            conn.execute(
                "UPDATE auto_live_runtime_projections SET can_reduce_exposure=0,last_error_code='TAMPERED',projection_sha256=? WHERE mandate_public_id=?",
                (sha256_json(body), mandate["public_id"]),
            )
        elif projection_case == "stale":
            stale = "2026-08-14T23:57:00+00:00"
            body = {
                "mandate_public_id": mandate["public_id"],
                "runtime_state": "paused",
                "can_reduce_exposure": 1,
                "fencing_epoch": 0,
                "last_error_code": None,
                "observed_at": stale,
            }
            conn.execute(
                "UPDATE auto_live_runtime_projections SET observed_at=?,projection_sha256=? WHERE mandate_public_id=?",
                (stale, sha256_json(body), mandate["public_id"]),
            )
        else:
            conn.execute(
                "DELETE FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate["public_id"],)
            )
    receipt = control.request_pause(
        1,
        {"scope": "mandate", "mandate_public_id": mandate["public_id"]},
        idempotency_key=f"pause-untrusted-{projection_case}",
        now=datetime.fromisoformat(NOW),
    )
    assert receipt["status"] == "partial" and receipt["confirmed"] == 0
    with db.transaction() as conn:
        stored = json.loads(
            conn.execute(
                "SELECT receipt_json FROM auto_live_pause_receipts WHERE request_public_id=?", (receipt["public_id"],)
            ).fetchone()[0]
        )
    assert stored["target_details"][0]["confirmed"] is False


def test_open_requires_latest_matching_running_ack_receipt(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="running")
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is False
    _runtime(db, mandate["public_id"], state="running", running_ack=True)
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is True
    unsafe = {
        "mandate_public_id": mandate["public_id"],
        "runtime_state": "running",
        "can_reduce_exposure": 0,
        "fencing_epoch": 0,
        "last_error_code": "RUNTIME_ERROR",
        "observed_at": NOW,
    }
    with db.transaction() as conn:
        conn.execute(
            "UPDATE auto_live_runtime_projections SET can_reduce_exposure=0,last_error_code='RUNTIME_ERROR',projection_sha256=? WHERE mandate_public_id=?",
            (sha256_json(unsafe), mandate["public_id"]),
        )
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is False
    safe = {**unsafe, "can_reduce_exposure": 1, "last_error_code": None}
    with db.transaction() as conn:
        conn.execute(
            "UPDATE auto_live_runtime_projections SET can_reduce_exposure=1,last_error_code=NULL,projection_sha256=? WHERE mandate_public_id=?",
            (sha256_json(safe), mandate["public_id"]),
        )
    forged = {
        "mandate_public_id": mandate["public_id"],
        "event_type": "running_ack",
        "runtime_state": "running",
        "fencing_epoch": 0,
        "observed_at": NOW,
        "detail": None,
    }
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO auto_live_runtime_receipts
               (receipt_id,mandate_public_id,event_type,state,fencing_epoch,payload_json,payload_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                "runtime_forged_latest_1",
                mandate["public_id"],
                "running_ack",
                "running",
                0,
                json.dumps(forged, sort_keys=True, separators=(",", ":")),
                "f" * 64,
                NOW,
            ),
        )
    assert control.action_gate(1, mandate["public_id"], "open", now=datetime.fromisoformat(NOW))["allowed"] is False


def test_user_pause_blocks_start_and_public_actor_is_opaque(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    _runtime(db, mandate["public_id"], state="stopped")
    with db.transaction() as conn:
        conn.execute("UPDATE user_controls SET opening_paused=1 WHERE user_id=1")
    assert control.action_gate(1, mandate["public_id"], "start", now=datetime.fromisoformat(NOW))["allowed"] is False
    first = control.start_mandate(
        1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-user-paused-1", now=datetime.fromisoformat(NOW)
    )
    replay = control.start_mandate(
        1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-user-paused-1", now=datetime.fromisoformat(NOW)
    )
    assert first == replay
    assert first["status"] == "blocked" and first["actor"] == "owner"
    assert "user_id" not in str(first)


def test_expiry_boundary_start_result_is_stored_and_replayed(tmp_path):
    db = _db(tmp_path)
    control = _ready_control(db)
    mandate = control.create_mandate(1, _payload())
    pending = control.submit_confirmation(1, mandate["public_id"], now=datetime.fromisoformat(NOW))
    control.confirm_mandate(1, mandate["public_id"], pending["confirmation_phrase"], now=datetime.fromisoformat(NOW))
    first = control.start_mandate(
        1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-expiry-boundary-1", now=datetime.fromisoformat(END)
    )
    replay = control.start_mandate(
        1, mandate["public_id"], expected_fencing_epoch=0, idempotency_key="start-expiry-boundary-1", now=datetime.fromisoformat(END)
    )
    assert first == replay
    assert first["state"] == "expired" and first["runtime_state"] == "blocked"
    assert first["actor"] == "owner" and "user_id" not in str(first)
    with db.transaction() as conn:
        stored = conn.execute(
            "SELECT status,receipt_json FROM auto_live_start_requests WHERE user_id=? AND idempotency_key=?",
            (1, "start-expiry-boundary-1"),
        ).fetchone()
    assert stored["status"] == "blocked"
    assert control._safe_receipt(json.loads(stored["receipt_json"])) == first
