from __future__ import annotations

from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.auto_live_broker_reconciliation import AutoLiveBrokerReconciler
from core.auto_live_control import AutoLiveConflict, AutoLiveControlError, canonical_json, sha256_json
from core.auto_live_control_common import sha256_text
from tests.test_auto_live_control import NOW
from tests.test_auto_live_runtime_worker import _started
from trading.tiger_api import TigerAPI
from trading.tiger_reconciliation import TigerOrderObservationSource, TigerOrdersReader


def _running_live_intent(tmp_path, *, send_claim_epoch: int | None = None, send_claim_payload_sha256: str | None = None):
    db, control, mandate_id, epoch = _started(tmp_path)
    lease = control.claim_runtime_lease(
        mandate_id,
        worker_id="broker-reconcile-worker",
        expected_fencing_epoch=epoch,
        lease_seconds=60,
        now=datetime.fromisoformat(NOW),
    )
    control.ack_runtime_running(
        mandate_id,
        worker_id="broker-reconcile-worker",
        lease_token=lease["lease_token"],
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW),
    )
    client_order_id = "live-client-order-001"
    intent_id = "intent_live_000000000000000001"
    payload = {
        "schema_version": 1,
        "mandate_public_id": mandate_id,
        "client_order_id": client_order_id,
        "execution_mode": "live",
        "fencing_epoch": epoch,
        "strategy_version": "strategy.v1",
        "risk_version": "risk.v1",
        "action": "open",
        "instrument_type": "stock",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 1,
        "limit_price": 100.0,
        "currency": "USD",
        "quote_at": NOW,
        "quote_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
    }
    digest = sha256_json(payload)
    claim_epoch = epoch if send_claim_epoch is None else send_claim_epoch
    claim = {
        "intent_public_id": intent_id,
        "mandate_public_id": mandate_id,
        "event_type": "send_claimed",
        "fencing_epoch": claim_epoch,
        "intent_sha256": digest,
    }
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO auto_live_order_intents
               (public_id,mandate_public_id,client_order_id,execution_mode,fencing_epoch,
                strategy_version,risk_version,action,instrument_type,symbol,side,quantity,
                limit_price,currency,quote_at,quote_sha256,evidence_sha256,intent_json,intent_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                intent_id, mandate_id, client_order_id, "live", epoch,
                "strategy.v1", "risk.v1", "open", "stock", "AAPL", "BUY", 1,
                100.0, "USD", NOW, "a" * 64, "b" * 64, canonical_json(payload), digest, NOW,
            ),
        )
        conn.execute(
            """INSERT INTO auto_live_order_intent_events
               (event_id,intent_public_id,mandate_public_id,event_type,fencing_epoch,payload_json,payload_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                "intentevt_send_0000000000000001", intent_id, mandate_id, "send_claimed", claim_epoch,
                canonical_json(claim), send_claim_payload_sha256 or sha256_json(claim), NOW,
            ),
        )
    return db, control, mandate_id, epoch, intent_id, client_order_id


def _observation(
    state: str,
    *,
    evidence: str | None = None,
    broker_order_id: str | None = None,
    observed_at: str | None = None,
    provider: str = "tiger",
    client_order_id: str = "live-client-order-001",
    broker_account_sha256: str = sha256_text("TGR-1"),
):
    broker_status = {
        "submission_unknown": "QUERY_PENDING",
        "accepted": "FILLED",
        "rejected": "REJECTED",
        "cancelled": "CANCELLED",
    }[state]
    facts = {
        "provider": provider,
        "client_order_id": client_order_id,
        "broker_order_id": broker_order_id,
        "broker_status": broker_status,
        "submission_state": state,
        "broker_account_sha256": broker_account_sha256,
    }
    return {
        "provider": provider,
        "submission_state": state,
        "broker_order_id": broker_order_id,
        "broker_status": broker_status,
        "observed_at": observed_at or (datetime.fromisoformat(NOW) + timedelta(seconds=1)).isoformat(),
        "evidence_sha256": evidence or sha256_json(facts),
        "broker_account_sha256": broker_account_sha256,
    }


def _reader_for_test(api):
    reader = TigerOrdersReader()
    reader._TigerOrdersReader__api = api
    return reader


def test_migration_adds_append_only_broker_reconciliation_receipts(tmp_path):
    db, _, _, _, _, _ = _running_live_intent(tmp_path)
    with db.transaction() as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "auto_live_broker_reconciliation_receipts" in names
        columns = {row[1] for row in conn.execute("PRAGMA table_info(auto_live_broker_reconciliation_receipts)")}
        assert {"receipt_id", "intent_public_id", "mandate_public_id", "client_order_id", "provider", "broker_account_sha256", "submission_state", "broker_status", "payload_json", "payload_sha256"} <= columns
        conn.execute(
            """INSERT INTO auto_live_broker_reconciliation_receipts
               (receipt_id,intent_public_id,mandate_public_id,client_order_id,provider,broker_account_sha256,submission_state,broker_order_id,broker_status,observed_at,evidence_sha256,payload_json,payload_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "brokerreceipt_0000000000000001", "intent_live_000000000000000001",
                conn.execute("SELECT public_id FROM auto_live_mandates LIMIT 1").fetchone()[0],
                "live-client-order-001", "tiger", sha256_text("TGR-1"), "submission_unknown", None, "QUERY_PENDING", NOW,
                "c" * 64, "{}", "d" * 64, NOW,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE auto_live_broker_reconciliation_receipts SET broker_status='tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM auto_live_broker_reconciliation_receipts")


def test_unknown_reconciles_to_accepted_without_rewriting_history_and_unblocks_open(tmp_path):
    db, control, mandate_id, epoch, intent_id, client_order_id = _running_live_intent(tmp_path)
    unknown = control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("submission_unknown"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )
    assert unknown["submission_state"] == "submission_unknown"
    assert control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW) + timedelta(seconds=2))["allowed"] is False

    class Source:
        def __init__(self): self.calls = []
        def lookup(self, provider, broker_account_sha256, requested_client_order_id):
            self.calls.append((provider, broker_account_sha256, requested_client_order_id))
            return _observation("accepted", broker_order_id="BROKER-ACCEPTED-1")

    source = Source()
    result = AutoLiveBrokerReconciler(control, source=source, clock=lambda: datetime.fromisoformat(NOW) + timedelta(seconds=3)).reconcile_once(
        mandate_id,
        client_order_id,
        expected_fencing_epoch=epoch,
    )
    assert source.calls == [("tiger", sha256_text("TGR-1"), client_order_id)]
    assert result["submission_state"] == "accepted"
    assert result["reconciled"] is True
    assert "broker_order_id" not in result
    assert control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW) + timedelta(seconds=3))["allowed"] is True

    with db.transaction() as conn:
        receipts = conn.execute(
            "SELECT submission_state FROM auto_live_broker_reconciliation_receipts WHERE intent_public_id=? ORDER BY rowid",
            (intent_id,),
        ).fetchall()
        projections = conn.execute(
            "SELECT submission_state FROM auto_live_order_receipt_projections WHERE client_order_id=? ORDER BY rowid",
            (client_order_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT event_type FROM auto_live_order_intent_events WHERE intent_public_id=? ORDER BY rowid",
            (intent_id,),
        ).fetchall()
    assert [row[0] for row in receipts] == ["submission_unknown", "accepted"]
    assert [row[0] for row in projections] == ["submission_unknown", "accepted"]
    assert [row[0] for row in events][-2:] == ["submission_unknown", "reconciled"]

    snapshot = control.list_snapshot(1, now=datetime.fromisoformat(NOW) + timedelta(seconds=3))
    assert snapshot["order_receipts"] == [
        {
            "public_id": result["public_id"],
            "mandate_public_id": mandate_id,
            "client_order_id": client_order_id,
            "submission_state": "accepted",
            "observed_at": result["observed_at"],
            "receipt_sha256": result["receipt_sha256"],
        }
    ]
    assert "BROKER-ACCEPTED-1" not in str(snapshot)


def test_reconciliation_is_idempotent_and_not_found_keeps_unknown_blocked(tmp_path):
    db, control, mandate_id, epoch, _, client_order_id = _running_live_intent(tmp_path)
    unknown_observation = _observation("submission_unknown")
    first = control.record_broker_order_receipt(
        mandate_id, client_order_id, unknown_observation,
        expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )
    replay = control.record_broker_order_receipt(
        mandate_id, client_order_id, unknown_observation,
        expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=4),
    )
    assert replay == first
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auto_live_broker_reconciliation_receipts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM auto_live_order_receipt_projections").fetchone()[0] == 1

    class MissingSource:
        def lookup(self, provider, broker_account_sha256, requested_client_order_id):
            assert provider == "tiger" and broker_account_sha256 == sha256_text("TGR-1") and requested_client_order_id == client_order_id
            return None

    missing = AutoLiveBrokerReconciler(control, source=MissingSource(), clock=lambda: datetime.fromisoformat(NOW) + timedelta(seconds=5)).reconcile_once(
        mandate_id, client_order_id, expected_fencing_epoch=epoch,
    )
    assert missing == {"status": "not_found", "mandate_public_id": mandate_id, "client_order_id": client_order_id}
    assert control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW) + timedelta(seconds=5))["allowed"] is False


def test_reconciliation_rejects_regression_wrong_provider_epoch_shadow_and_bad_evidence(tmp_path):
    db, control, mandate_id, epoch, intent_id, client_order_id = _running_live_intent(tmp_path)
    accepted = _observation("accepted", broker_order_id="BROKER-1")
    control.record_broker_order_receipt(
        mandate_id, client_order_id, accepted,
        expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )
    with pytest.raises(AutoLiveConflict, match="回退|状态"):
        control.record_broker_order_receipt(
            mandate_id, client_order_id, _observation("submission_unknown"),
            expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
        )
    wrong_provider = _observation("accepted", provider="ibkr", broker_order_id="BROKER-1")
    with pytest.raises(AutoLiveControlError, match="provider|券商"):
        control.record_broker_order_receipt(
            mandate_id, client_order_id, wrong_provider,
            expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
        )
    wrong_account = _observation("accepted", broker_order_id="BROKER-1", broker_account_sha256=sha256_text("OTHER"))
    with pytest.raises(AutoLiveControlError, match="账户|account"):
        control.record_broker_order_receipt(
            mandate_id, client_order_id, wrong_account,
            expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
        )
    with pytest.raises(AutoLiveConflict, match="epoch"):
        control.record_broker_order_receipt(
            mandate_id, client_order_id, accepted,
            expected_fencing_epoch=epoch + 1, now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
        )
    with pytest.raises(AutoLiveControlError, match="证据"):
        control.record_broker_order_receipt(
            mandate_id, client_order_id, dict(accepted, evidence_sha256="bad"),
            expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
        )
    with pytest.raises(AutoLiveControlError, match="证据"):
        control.record_broker_order_receipt(
            mandate_id, client_order_id, dict(accepted, evidence_sha256="9" * 64),
            expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
        )

    shadow_client_order_id = "shadow-client-order-001"
    shadow_intent_id = "intent_shadow_0000000000000001"
    with db.transaction() as conn:
        original = conn.execute("SELECT * FROM auto_live_order_intents WHERE public_id=?", (intent_id,)).fetchone()
        shadow_payload = json.loads(str(original["intent_json"]))
        shadow_payload["client_order_id"] = shadow_client_order_id
        shadow_payload["execution_mode"] = "shadow"
        conn.execute(
            """INSERT INTO auto_live_order_intents
               (public_id,mandate_public_id,client_order_id,execution_mode,fencing_epoch,
                strategy_version,risk_version,action,instrument_type,symbol,side,quantity,
                limit_price,currency,quote_at,quote_sha256,evidence_sha256,intent_json,intent_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                shadow_intent_id, mandate_id, shadow_client_order_id, "shadow", epoch,
                original["strategy_version"], original["risk_version"], original["action"], original["instrument_type"],
                original["symbol"], original["side"], original["quantity"], original["limit_price"], original["currency"],
                original["quote_at"], original["quote_sha256"], original["evidence_sha256"],
                canonical_json(shadow_payload), sha256_json(shadow_payload), original["created_at"],
            ),
        )
    with pytest.raises(AutoLiveControlError, match="shadow"):
        control.record_broker_order_receipt(
            mandate_id, shadow_client_order_id, _observation("accepted", broker_order_id="BROKER-1", client_order_id=shadow_client_order_id),
            expected_fencing_epoch=epoch, now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
        )


@pytest.mark.parametrize(
    ("broker_status", "expected_state"),
    [
        ("NEW", "accepted"),
        ("PARTIALLY_FILLED", "accepted"),
        ("FILLED", "accepted"),
        ("REJECTED", "rejected"),
        ("EXPIRED", "rejected"),
        ("CANCELLED", "cancelled"),
        ("UNKNOWN_VENDOR_STATE", "submission_unknown"),
    ],
)
def test_tiger_observation_source_is_read_only_and_maps_safe_states(broker_status, expected_state):
    calls = []

    class ReadOnlyTiger:
        def orders(self, *, expected_account_sha256=None):
            calls.append("orders")
            return [
                SimpleNamespace(user_mark="another-intent", id=1, status="FILLED"),
                SimpleNamespace(user_mark="live-client-order-001", id=99, status=SimpleNamespace(value=broker_status)),
            ]

        def place_stock_limit(self, *_args, **_kwargs):
            raise AssertionError("read-only reconciliation must never send an order")

    source = TigerOrderObservationSource(
        reader=_reader_for_test(ReadOnlyTiger()),
        clock=lambda: datetime.fromisoformat(NOW) + timedelta(seconds=5),
    )
    result = source.lookup("tiger", sha256_text("TGR-1"), "live-client-order-001")
    for mutating_name in ("send", "cancel", "replace", "retry", "place_stock_limit", "place_order"):
        assert not hasattr(source, mutating_name)
    assert not hasattr(source, "api_factory")
    assert not hasattr(source, "reader")
    public_surface = {name: getattr(source, name) for name in dir(source) if not name.startswith("_")}
    assert set(public_surface) == {"lookup", "supported_providers"}
    assert calls == ["orders"]
    assert result["provider"] == "tiger"
    assert result["broker_account_sha256"] == sha256_text("TGR-1")
    assert result["submission_state"] == expected_state
    assert result["broker_order_id"] == "99"
    assert result["broker_status"] == broker_status
    assert result["observed_at"] == (datetime.fromisoformat(NOW) + timedelta(seconds=5)).isoformat()
    assert len(result["evidence_sha256"]) == 64
    assert source.lookup("tiger", sha256_text("TGR-1"), "missing-client-order") is None
    assert source.lookup("ibkr", sha256_text("TGR-1"), "live-client-order-001") is None


def test_tiger_observation_source_fails_closed_on_duplicate_match_and_missing_broker_id():
    class DuplicateTiger:
        def orders(self, *, expected_account_sha256=None):
            return [
                SimpleNamespace(user_mark="live-client-order-001", id=1, status="NEW"),
                SimpleNamespace(user_mark="live-client-order-001", id=2, status="FILLED"),
            ]

    source = TigerOrderObservationSource(reader=_reader_for_test(DuplicateTiger()), clock=lambda: datetime.fromisoformat(NOW))
    with pytest.raises(AutoLiveControlError, match="重复|多个"):
        source.lookup("tiger", sha256_text("TGR-1"), "live-client-order-001")

    class MissingIdTiger:
        def orders(self, *, expected_account_sha256=None):
            return [SimpleNamespace(user_mark="live-client-order-001", status="FILLED")]

    missing = TigerOrderObservationSource(reader=_reader_for_test(MissingIdTiger()), clock=lambda: datetime.fromisoformat(NOW)).lookup(
        "tiger", sha256_text("TGR-1"), "live-client-order-001"
    )
    assert missing["submission_state"] == "submission_unknown"
    assert missing["broker_order_id"] is None

    reader = TigerOrdersReader()
    assert callable(reader.orders)
    for mutating_name in ("send", "cancel", "replace", "retry", "place_stock_limit", "place_order"):
        assert not hasattr(reader, mutating_name)
    assert not hasattr(reader, "api_factory")
    assert not hasattr(reader, "api")

    class MutatingDuckReader:
        def orders(self, **_kwargs): return []
        def place_order(self): raise AssertionError

    class FalseyMutatingDuckReader(MutatingDuckReader):
        def __bool__(self): return False

    class ReaderSubclass(TigerOrdersReader):
        pass

    with pytest.raises(AutoLiveControlError, match="reader|只读"):
        TigerOrderObservationSource(reader=MutatingDuckReader(), clock=lambda: datetime.fromisoformat(NOW))
    with pytest.raises(AutoLiveControlError, match="reader|只读"):
        TigerOrderObservationSource(reader=FalseyMutatingDuckReader(), clock=lambda: datetime.fromisoformat(NOW))
    with pytest.raises(AutoLiveControlError, match="reader|只读"):
        TigerOrderObservationSource(reader=TigerAPI(), clock=lambda: datetime.fromisoformat(NOW))
    with pytest.raises(AutoLiveControlError, match="reader|只读"):
        TigerOrderObservationSource(reader=ReaderSubclass(), clock=lambda: datetime.fromisoformat(NOW))


def test_tiger_api_orders_verifies_account_fingerprint_before_querying():
    calls = []

    class Client:
        def get_orders(self, *, account, limit):
            calls.append((account, limit))
            return []

    tiger = TigerAPI()
    tiger.account = "TGR-1"
    tiger._client = Client()
    assert tiger.orders(expected_account_sha256=sha256_text("TGR-1")) == []
    assert calls == [("TGR-1", 100)]
    with pytest.raises(RuntimeError, match="账户|account"):
        tiger.orders(expected_account_sha256=sha256_text("OTHER"))
    assert calls == [("TGR-1", 100)]


def test_reconcile_pending_scans_only_latest_unknown_and_stops_after_resolution(tmp_path):
    _, control, mandate_id, epoch, _, client_order_id = _running_live_intent(tmp_path)
    control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("submission_unknown"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )

    class Source:
        def lookup(self, provider, broker_account_sha256, requested_client_order_id):
            assert provider == "tiger" and broker_account_sha256 == sha256_text("TGR-1") and requested_client_order_id == client_order_id
            return _observation("accepted", broker_order_id="BROKER-RESOLVED-1")

    reconciler = AutoLiveBrokerReconciler(
        control,
        source=Source(),
        clock=lambda: datetime.fromisoformat(NOW) + timedelta(seconds=3),
    )
    pending = control.pending_broker_reconciliations(limit=10)
    assert pending == [
        {
            "mandate_public_id": mandate_id,
            "client_order_id": client_order_id,
            "provider": "tiger",
            "broker_account_sha256": sha256_text("TGR-1"),
            "expected_fencing_epoch": epoch,
        }
    ]
    first = reconciler.reconcile_pending(limit=10)
    assert first == {"status": "completed", "total": 1, "resolved": 1, "unresolved": 0, "failed": 0}
    assert control.pending_broker_reconciliations(limit=10) == []
    second = reconciler.reconcile_pending(limit=10)
    assert second == {"status": "completed", "total": 0, "resolved": 0, "unresolved": 0, "failed": 0}


def test_pending_reconciliation_limit_is_bounded(tmp_path):
    _, control, _, _, _, _ = _running_live_intent(tmp_path)
    for invalid in (0, 501, True, "10"):
        with pytest.raises(AutoLiveControlError, match="limit"):
            control.pending_broker_reconciliations(limit=invalid)


def test_pending_reconciliation_excludes_provider_without_configured_source(tmp_path):
    db, control, mandate_id, epoch, _, client_order_id = _running_live_intent(tmp_path)
    control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("submission_unknown"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )
    with db.transaction() as conn:
        conn.execute("UPDATE broker_accounts SET provider='ibkr' WHERE id=1")
    assert control.pending_broker_reconciliations(limit=10, providers=("tiger",)) == []


def test_unknown_cannot_be_cleared_by_a_known_projection_without_matching_broker_receipt(tmp_path):
    db, control, mandate_id, epoch, _, client_order_id = _running_live_intent(tmp_path)
    control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("submission_unknown"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO auto_live_order_receipt_projections
               (public_id,mandate_public_id,client_order_id,submission_state,broker_order_id,observed_at,receipt_sha256)
               VALUES(?,?,?,?,?,?,?)""",
            (
                "forged_known_receipt_000000001",
                mandate_id,
                client_order_id,
                "accepted",
                "FORGED-BROKER-ID",
                (datetime.fromisoformat(NOW) + timedelta(seconds=3)).isoformat(),
                "f" * 64,
            ),
        )
    gate = control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW) + timedelta(seconds=3))
    assert gate["allowed"] is False
    assert gate["reason"] == "opening_paused_or_uncertain"
    for action in ("cancel", "reduce_exposure", "close_position"):
        assert control.action_gate(1, mandate_id, action, now=datetime.fromisoformat(NOW) + timedelta(seconds=3))["allowed"] is True


def test_projection_must_match_broker_receipt_id_hash_broker_id_and_observed_at(tmp_path):
    db, control, mandate_id, epoch, intent_id, client_order_id = _running_live_intent(tmp_path)
    control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("submission_unknown"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )
    receipt_id = "brokerreceipt_binding_0000000001"
    observation = _observation(
        "accepted",
        broker_order_id="BROKER-TRUE",
        observed_at=(datetime.fromisoformat(NOW) + timedelta(seconds=3)).isoformat(),
    )
    payload = {
        "schema_version": 1,
        "receipt_id": receipt_id,
        "intent_public_id": intent_id,
        "mandate_public_id": mandate_id,
        "client_order_id": client_order_id,
        **observation,
    }
    digest = sha256_json(payload)
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO auto_live_broker_reconciliation_receipts
               (receipt_id,intent_public_id,mandate_public_id,client_order_id,provider,broker_account_sha256,submission_state,
                broker_order_id,broker_status,observed_at,evidence_sha256,payload_json,payload_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                receipt_id, intent_id, mandate_id, client_order_id, "tiger", observation["broker_account_sha256"], "accepted",
                "BROKER-TRUE", "FILLED", observation["observed_at"], observation["evidence_sha256"],
                canonical_json(payload), digest, observation["observed_at"],
            ),
        )
        conn.execute(
            """INSERT INTO auto_live_order_receipt_projections
               (public_id,mandate_public_id,client_order_id,submission_state,broker_order_id,observed_at,receipt_sha256)
               VALUES(?,?,?,?,?,?,?)""",
            (
                receipt_id, mandate_id, client_order_id, "accepted", "BROKER-FORGED",
                (datetime.fromisoformat(NOW) + timedelta(seconds=4)).isoformat(), digest,
            ),
        )
    assert control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW) + timedelta(seconds=4))["allowed"] is False


def test_reconciliation_requires_claim_epoch_and_payload_binding(tmp_path):
    _, stale_control, mandate_id, epoch, _, client_order_id = _running_live_intent(
        tmp_path / "stale", send_claim_epoch=0
    )
    with pytest.raises(AutoLiveControlError, match="send claim"):
        stale_control.record_broker_order_receipt(
            mandate_id,
            client_order_id,
            _observation("submission_unknown"),
            expected_fencing_epoch=epoch,
            now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
        )

    _, tampered_control, mandate_id, epoch, _, client_order_id = _running_live_intent(
        tmp_path / "tampered", send_claim_payload_sha256="0" * 64
    )
    with pytest.raises(AutoLiveControlError, match="send claim"):
        tampered_control.record_broker_order_receipt(
            mandate_id,
            client_order_id,
            _observation("submission_unknown"),
            expected_fencing_epoch=epoch,
            now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
        )


def test_reconciliation_after_emergency_pause_records_receipt_without_reopening(tmp_path):
    _, control, mandate_id, epoch, _, client_order_id = _running_live_intent(tmp_path)
    control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("submission_unknown"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )
    paused = control.request_pause(
        1,
        {"scope": "mandate", "mandate_public_id": mandate_id},
        idempotency_key="pause-before-reconcile-001",
        now=datetime.fromisoformat(NOW) + timedelta(seconds=3),
    )
    assert paused["status"] in {"paused", "partial"}
    receipt = control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("accepted", broker_order_id="BROKER-AFTER-PAUSE"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=4),
    )
    assert receipt["reconciled"] is True
    assert control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW) + timedelta(seconds=4))["allowed"] is False


def test_batch_source_exception_is_counted_and_safe_exits_remain(tmp_path):
    _, control, mandate_id, epoch, _, client_order_id = _running_live_intent(tmp_path)
    control.record_broker_order_receipt(
        mandate_id,
        client_order_id,
        _observation("submission_unknown"),
        expected_fencing_epoch=epoch,
        now=datetime.fromisoformat(NOW) + timedelta(seconds=2),
    )

    class BrokenSource:
        def lookup(self, *_args):
            raise RuntimeError("broker unavailable")

    result = AutoLiveBrokerReconciler(
        control,
        source=BrokenSource(),
        clock=lambda: datetime.fromisoformat(NOW) + timedelta(seconds=3),
    ).reconcile_pending(limit=10)
    assert result == {"status": "completed", "total": 1, "resolved": 0, "unresolved": 0, "failed": 1}
    assert control.action_gate(1, mandate_id, "open", now=datetime.fromisoformat(NOW) + timedelta(seconds=3))["allowed"] is False
    for action in ("cancel", "reduce_exposure", "close_position"):
        assert control.action_gate(1, mandate_id, action, now=datetime.fromisoformat(NOW) + timedelta(seconds=3))["allowed"] is True
