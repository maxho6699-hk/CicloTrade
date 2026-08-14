from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib

import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.expanded_research_contracts import (
    AUTHORITY,
    INVALIDATION_KIND,
    TIER_A,
    TIER_C,
    UNIVERSE_SHA256,
    UNIVERSE_VERSION,
    canonical_json,
    receiver_signature,
)
from core.expanded_research_store import ExpandedResearchStore
from src.apps.api.expanded_research_read_model import ExpandedResearchReadModel
from src.apps.api.expanded_research_receiver import ExpandedResearchReceiver, ExpandedResearchReceiverError


SECRET = "s" * 32
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _result(
    symbol: str = "AAPL",
    *,
    tier: str = "A",
    authority: dict | None = None,
    dataset_end: str = "2026-08-13",
    result_id: str | None = None,
) -> dict:
    digest = "a" * 64
    evidence = {"runner": "equity-research-v1", "code_bundle_sha256": "b" * 64, "validation": {"candidate_status": "shadow"}}
    return {
        "schema_version": 1, "kind": "tradeai.expanded-local-research.v1",
        "result_id": result_id or f"expanded-{symbol}-aaaaaaaaaaaaaaaaaaaaaaaa", "symbol": symbol, "tier": tier,
        "source_sha256": digest, "universe_sha256": UNIVERSE_SHA256, "dataset_end": dataset_end,
        "equity": {"equity.trend.long_flat.v1": evidence, "equity.mean_reversion.long_flat.v1": evidence, "equity.breakout.long_flat.v1": evidence},
        "option_proxy": {"decision": "WAIT", "actionable": False} if tier == "A" else None,
        "authority": AUTHORITY if authority is None else authority,
    }


def _request(receiver: ExpandedResearchReceiver, value: dict, *, key: str = "expanded-aapl-0001", epoch: int = 1, sent: str = "2026-08-14T12:00:00Z"):
    raw = canonical_json(value)
    body_sha = hashlib.sha256(raw).hexdigest()
    headers = {
        "content-type": "application/json", "idempotency-key": key,
        "x-ciclotrade-research-worker-id": "expanded-research-worker",
        "x-ciclotrade-research-fencing-epoch": str(epoch), "x-ciclotrade-research-sent-at": sent,
        "x-ciclotrade-research-sha256": body_sha,
        "x-ciclotrade-research-signature": receiver_signature(SECRET, worker_id="expanded-research-worker", fencing_epoch=epoch, idempotency_key=key, sent_at=sent, body_sha256=body_sha),
    }
    return receiver.accept(raw, headers)


def _invalidate(
    receiver: ExpandedResearchReceiver,
    *,
    result_id: str,
    symbol: str = "AAPL",
    key: str = "expanded-invalidate-0001",
    epoch: int = 1,
    invalidated_at: str = "2026-08-14T12:00:00Z",
):
    value = {
        "schema_version": 1, "kind": INVALIDATION_KIND,
        "invalidation_id": key, "target_result_id": result_id, "symbol": symbol,
        "reason": "source_invalidated", "universe_sha256": UNIVERSE_SHA256,
        "invalidated_at": invalidated_at, "authority": AUTHORITY,
    }
    raw = canonical_json(value)
    body_sha = hashlib.sha256(raw).hexdigest()
    headers = {
        "content-type": "application/json", "idempotency-key": key,
        "x-ciclotrade-research-worker-id": "expanded-research-worker",
        "x-ciclotrade-research-fencing-epoch": str(epoch), "x-ciclotrade-research-sent-at": "2026-08-14T12:00:00Z",
        "x-ciclotrade-research-sha256": body_sha,
        "x-ciclotrade-research-signature": receiver_signature(SECRET, worker_id="expanded-research-worker", fencing_epoch=epoch, idempotency_key=key, sent_at="2026-08-14T12:00:00Z", body_sha256=body_sha),
    }
    return receiver.accept(raw, headers)


def _seed_receipts(database: BacktestQueueDatabase, entries: list[tuple[dict, str]]) -> None:
    with database.transaction() as connection:
        for index, (value, received_at) in enumerate(entries):
            body = canonical_json(value)
            digest = hashlib.sha256(body).hexdigest()
            connection.execute(
                """INSERT INTO expanded_research_receipts(
                       receipt_key,result_id,worker_id,fencing_epoch,universe_version,
                       universe_sha256,symbol,tier,source_sha256,payload_sha256,
                       payload_json,received_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"seed-{index:08d}-{value['symbol']}", value["result_id"], "seed-worker", 1,
                    UNIVERSE_VERSION, UNIVERSE_SHA256, value["symbol"], value["tier"],
                    value["source_sha256"], digest, body.decode("utf-8"), received_at,
                ),
            )


def test_canonical_universe_matches_running_97_chain():
    assert len(TIER_A) == 13 and len(TIER_C) == 84
    assert UNIVERSE_SHA256 == "ae95ca26edc28385c495b055f57f28dd78fdc088a3a7cdd683b0244e55f1b4b7"


def test_invalidation_schema_is_migrated_before_store_construction(tmp_path, monkeypatch):
    database = BacktestQueueDatabase(tmp_path / "backtest.db")
    assert database.fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name='expanded_research_invalidations'")
    indexes = {
        row["name"]
        for row in database.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='expanded_research_receipts'"
        )
    }
    assert {"idx_expanded_research_latest_symbol", "idx_expanded_research_dataset_cycle"} <= indexes
    monkeypatch.setattr(database, "transaction", lambda: (_ for _ in ()).throw(AssertionError("store constructor must not write")))
    ExpandedResearchStore(database, clock=lambda: NOW)


def test_receiver_accepts_existing_strategy_authority_and_replays_idempotently(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    value = _result()
    first = _request(receiver, value)
    second = _request(receiver, value)
    assert first["created"] is True and second["created"] is False
    assert first["state"] == "shadow" and first["actionable"] is False and first["execution"] is False
    assert first["payload_sha256"] == first["result_sha256"]


def test_receiver_rejects_noncanonical_duplicate_and_wrong_authority(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    raw = b'{"schema_version":1,"schema_version":1}'
    with pytest.raises(ExpandedResearchReceiverError) as duplicate:
        receiver.accept(raw, {"content-type": "application/json"})
    assert duplicate.value.status == 401
    with pytest.raises(ExpandedResearchReceiverError) as authority:
        _request(receiver, _result(authority={**AUTHORITY, "execution_eligible": True}), key="expanded-aapl-0002")
    assert authority.value.status == 400


def test_receiver_rejects_stale_fence_and_bad_signature(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    _request(receiver, _result(), epoch=2)
    with pytest.raises(ExpandedResearchReceiverError) as stale:
        _request(receiver, _result("MSFT"), key="expanded-msft-0001", epoch=1)
    assert stale.value.status == 409
    with pytest.raises(ExpandedResearchReceiverError) as bad:
        receiver.accept(canonical_json(_result("NVDA")), {"content-type": "application/json"})
    assert bad.value.status == 401


def test_read_model_emits_exact_97_symbol_dto_and_requires_authentication(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    _request(receiver, _result())
    model = ExpandedResearchReadModel(store, authorize=lambda identity: identity == "user")
    with pytest.raises(PermissionError):
        model.latest("guest")
    latest = model.latest("user")
    cycle = latest["cycle"]
    assert set(latest) == {"available", "authority", "validation_label", "cycle"}
    assert AUTHORITY["user_visible"] is False
    assert latest["authority"] == {
        "publication_ceiling": "shadow",
        "projection_scope": "authenticated_research",
        "source_user_visible": False,
        "research_only": True,
        "actionable": False,
        "outbound": False,
        "execution": False,
        "official": False,
        "live": False,
    }
    assert "user_visible" not in latest["authority"]
    assert not {"raw", "worker_id", "receipt_key", "payload_json", "signature", "shared_secret", "secret"} & _nested_keys(latest)
    assert len(cycle["symbols"]) == 97
    assert cycle["summary"]["no_data_count"] == 96
    assert {item["data_state"] for item in cycle["symbols"] if item["symbol"] != "AAPL"} == {"missing"}
    assert all(item["signal"] == "wait" for item in cycle["symbols"])
    assert sum(item["tier"] == "A" for item in cycle["symbols"]) == 13
    assert sum(item["tier"] == "C" for item in cycle["symbols"]) == 84
    assert next(item for item in cycle["symbols"] if item["symbol"] == "AAPL")["tier"] == "A"
    assert next(item for item in cycle["symbols"] if item["symbol"] == TIER_C[0])["tier"] == "C"


def test_read_model_unavailable_projections_are_exact_and_safe():
    status = ExpandedResearchReadModel.unavailable_status()
    latest = ExpandedResearchReadModel.unavailable_latest()
    history = ExpandedResearchReadModel.unavailable_history(20)
    assert set(status) == {"available", "state", "authority", "universe", "last_heartbeat_at", "last_result_at", "expires_at", "coverage_count", "no_data_count", "spool"}
    assert status["available"] is False and status["state"] == "waiting"
    assert status["coverage_count"] == 0 and status["no_data_count"] == 97
    assert status["universe"]["count"] == 97 and status["authority"]["actionable"] is False
    assert status["authority"]["source_user_visible"] is False
    assert status["authority"]["projection_scope"] == "authenticated_research"
    assert set(latest) == {"available", "authority", "validation_label", "cycle"}
    assert latest["available"] is False and latest["cycle"] is None
    assert history == {"available": False, "authority": status["authority"], "limit": 20, "items": []}
    for invalid in (True, 0, 21):
        with pytest.raises(ValueError):
            ExpandedResearchReadModel.unavailable_history(invalid)


def test_signed_invalidation_is_idempotent_and_removes_result_from_active_projection(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    value = _result()
    _request(receiver, value)
    first = _invalidate(receiver, result_id=value["result_id"])
    second = _invalidate(receiver, result_id=value["result_id"])
    assert first["created"] is True and second["created"] is False
    assert first["state"] == "invalidated" and first["target_result_id"] == value["result_id"]
    assert store.latest_by_symbol() == []
    assert store.history()[0]["result_id"] == value["result_id"]
    assert store.history()[0]["projection_state"] == "invalidated"


def test_tombstone_arriving_before_result_still_blocks_late_delivery(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    value = _result()
    _invalidate(receiver, result_id=value["result_id"], key="expanded-invalidate-before-result-0001")
    _request(receiver, value)
    assert store.latest_by_symbol() == []


def test_known_target_invalidation_requires_matching_symbol(tmp_path):
    database = BacktestQueueDatabase(tmp_path / "backtest.db")
    store = ExpandedResearchStore(database, clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    value = _result()
    _request(receiver, value)
    with pytest.raises(ExpandedResearchReceiverError) as mismatch:
        _invalidate(receiver, result_id=value["result_id"], symbol="MSFT", key="expanded-invalidate-wrong-symbol-0001")
    assert mismatch.value.status == 409
    assert database.fetch_one("SELECT count(*) AS total FROM expanded_research_invalidations")["total"] == 0
    assert [row["symbol"] for row in store.latest_by_symbol()] == ["AAPL"]


def test_wrong_symbol_tombstone_before_result_does_not_suppress_late_delivery(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    value = _result()
    _invalidate(
        receiver,
        result_id=value["result_id"],
        symbol="MSFT",
        key="expanded-invalidate-before-result-wrong-symbol-0001",
    )
    _request(receiver, value)
    assert [row["symbol"] for row in store.latest_by_symbol()] == ["AAPL"]
    assert store.history()[0]["projection_state"] == "active"


def test_invalidation_timestamp_allows_five_minute_skew_and_rejects_more(tmp_path):
    allowed_store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "allowed.db"), clock=lambda: NOW)
    allowed_receiver = ExpandedResearchReceiver(allowed_store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    allowed_value = _result()
    _request(allowed_receiver, allowed_value)
    accepted = _invalidate(
        allowed_receiver,
        result_id=allowed_value["result_id"],
        invalidated_at="2026-08-14T12:05:00Z",
    )
    assert accepted["state"] == "invalidated"

    rejected_database = BacktestQueueDatabase(tmp_path / "rejected.db")
    rejected_store = ExpandedResearchStore(rejected_database, clock=lambda: NOW)
    rejected_receiver = ExpandedResearchReceiver(rejected_store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    rejected_value = _result()
    _request(rejected_receiver, rejected_value)
    with pytest.raises(ExpandedResearchReceiverError) as future:
        _invalidate(
            rejected_receiver,
            result_id=rejected_value["result_id"],
            invalidated_at="2026-08-14T12:05:01Z",
        )
    assert future.value.status == 400
    assert rejected_database.fetch_one("SELECT count(*) AS total FROM expanded_research_invalidations")["total"] == 0


def test_latest_by_symbol_decodes_at_most_one_candidate_per_symbol(tmp_path, monkeypatch):
    database = BacktestQueueDatabase(tmp_path / "backtest.db")
    symbols = (*TIER_A, *TIER_C)
    entries: list[tuple[dict, str]] = []
    for revision in range(5):
        dataset_end = (date(2026, 8, 13) - timedelta(days=revision)).isoformat()
        received_at = f"2026-08-14T11:{revision:02d}:00Z"
        for symbol in symbols:
            tier = "A" if symbol in TIER_A else "C"
            entries.append((_result(
                symbol,
                tier=tier,
                dataset_end=dataset_end,
                result_id=f"expanded-{symbol}-revision-{revision:02d}-aaaaaaaa",
            ), received_at))
    _seed_receipts(database, entries)
    store = ExpandedResearchStore(database, clock=lambda: NOW)
    decoded = 0
    original_decode = store._decode

    def counting_decode(row):
        nonlocal decoded
        decoded += 1
        return original_decode(row)

    monkeypatch.setattr(store, "_decode", counting_decode)
    rows = store.latest_by_symbol()
    assert len(rows) == 97
    assert decoded == 97


def test_history_returns_twenty_complete_97_symbol_cycles(tmp_path):
    database = BacktestQueueDatabase(tmp_path / "backtest.db")
    symbols = (*TIER_A, *TIER_C)
    entries: list[tuple[dict, str]] = []
    for cycle in range(21):
        dataset_end = (date(2026, 8, 13) - timedelta(days=cycle)).isoformat()
        received_at = (NOW - timedelta(minutes=cycle)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        for symbol in symbols:
            tier = "A" if symbol in TIER_A else "C"
            entries.append((_result(
                symbol,
                tier=tier,
                dataset_end=dataset_end,
                result_id=f"expanded-{symbol}-cycle-{cycle:02d}-aaaaaaaaaa",
            ), received_at))
    _seed_receipts(database, entries)
    store = ExpandedResearchStore(database, clock=lambda: NOW)
    rows = store.history(20)
    history = ExpandedResearchReadModel(store, authorize=lambda _identity: True).history("user", 20)
    assert len(rows) == 20 * 97
    assert len(history["items"]) == 20 and history["limit"] == 20
    assert all(item["receipt_count"] == 97 and item["coverage_count"] == 97 for item in history["items"])
    assert history["items"][0]["active_count"] == 97
    assert all(item["superseded_count"] == 97 for item in history["items"][1:])
    assert history["items"][-1]["evaluation_date"] == "2026-07-25"


def test_history_uses_global_latest_result_when_limited_cycle_excludes_it(tmp_path):
    database = BacktestQueueDatabase(tmp_path / "backtest.db")
    selected = _result(dataset_end="2026-08-13", result_id="expanded-AAPL-selected-cycle-aaaaaaaa")
    global_latest = _result(dataset_end="2026-08-12", result_id="expanded-AAPL-global-latest-aaaaaaaaa")
    _seed_receipts(database, [
        (selected, "2026-08-14T11:00:00Z"),
        (global_latest, "2026-08-14T11:01:00Z"),
    ])
    rows = ExpandedResearchStore(database, clock=lambda: NOW).history(1)
    assert [row["result_id"] for row in rows] == [selected["result_id"]]
    assert rows[0]["projection_state"] == "superseded"


def test_read_model_keeps_active_history_consistent_after_one_result_is_invalidated(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    _request(receiver, _result(), key="expanded-aapl-0001")
    _request(receiver, _result("MSFT"), key="expanded-msft-0001")
    _invalidate(receiver, result_id=_result()["result_id"])
    model = ExpandedResearchReadModel(store, authorize=lambda _identity: True)
    latest = model.latest("user")
    history = model.history("user")
    assert latest["cycle"]["summary"]["wait_count"] == 1
    assert history["items"][0]["coverage_count"] == 2
    assert history["items"][0]["active_count"] == 1
    assert history["items"][0]["invalidated_count"] == 1


def test_expired_result_is_removed_from_active_projection_but_remains_in_history(tmp_path):
    from datetime import timedelta

    current = [NOW]
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: current[0])
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: current[0])
    value = _result()
    _request(receiver, value)
    assert [row["symbol"] for row in store.latest_by_symbol()] == ["AAPL"]
    current[0] = NOW + timedelta(hours=12, seconds=1)
    assert store.latest_by_symbol() == []
    assert len(store.history()) == 1
    assert store.history()[0]["projection_state"] == "expired"
    history = ExpandedResearchReadModel(store, authorize=lambda _identity: True).history("user")
    assert history["items"][0]["expired_count"] == 1


def test_full_coverage_with_one_stale_symbol_is_not_healthy(monkeypatch):
    store = object.__new__(ExpandedResearchStore)
    rows = [{"symbol": symbol, "received_at": "stale" if index == 0 else "fresh"} for index, symbol in enumerate((*TIER_A, *TIER_C))]
    monkeypatch.setattr(store, "latest_by_symbol", lambda: rows)
    monkeypatch.setattr("src.apps.api.expanded_research_read_model._stale", lambda value: value == "stale")
    model = ExpandedResearchReadModel(store, authorize=lambda _identity: True)
    assert model.status("user")["state"] == "stale"


def _nested_keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_nested_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_nested_keys(item) for item in value), set())
    return set()
