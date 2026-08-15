from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import hashlib
from pathlib import Path

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


def test_old_0013_database_applies_only_additive_0014_and_reopens_safely(tmp_path):
    migration_root = Path(__file__).resolve().parents[4] / "migrations/backtest"
    legacy_root = tmp_path / "legacy-migrations"
    legacy_root.mkdir()
    for source in migration_root.glob("*.sql"):
        if source.name <= "0013_expanded_research_invalidations.sql":
            (legacy_root / source.name).write_bytes(source.read_bytes())
    database_path = tmp_path / "backtest.db"
    legacy = BacktestQueueDatabase(database_path, migrations=legacy_root)
    receiver = ExpandedResearchReceiver(
        ExpandedResearchStore(legacy, clock=lambda: NOW), shared_secret=SECRET, enabled=True, clock=lambda: NOW,
    )
    _request(receiver, _result())
    applied_before = {row["version"] for row in legacy.fetch_all("SELECT version FROM schema_migrations")}
    columns_before = legacy.fetch_all("PRAGMA table_info(expanded_research_receipts)")

    upgraded = BacktestQueueDatabase(database_path, migrations=migration_root)
    applied_after = {row["version"] for row in upgraded.fetch_all("SELECT version FROM schema_migrations")}
    assert applied_after - applied_before == {"0014_expanded_research_projection_indexes.sql"}
    assert upgraded.fetch_all("PRAGMA table_info(expanded_research_receipts)") == columns_before
    assert [row["symbol"] for row in ExpandedResearchStore(upgraded, clock=lambda: NOW).latest_by_symbol()] == ["AAPL"]
    assert len(BacktestQueueDatabase(database_path, migrations=migration_root).fetch_all(
        "SELECT version FROM schema_migrations WHERE version='0014_expanded_research_projection_indexes.sql'"
    )) == 1

    migration_sql = (migration_root / "0014_expanded_research_projection_indexes.sql").read_text(encoding="utf-8").upper()
    assert migration_sql.count("CREATE INDEX") == 2
    assert migration_sql.count("IF NOT EXISTS") == 2
    assert not any(token in migration_sql for token in ("DROP ", "ALTER ", "DELETE ", "UPDATE ", "INSERT "))
    rolled_back = BacktestQueueDatabase(database_path, migrations=legacy_root)
    assert rolled_back.fetch_one("SELECT count(*) AS total FROM expanded_research_receipts")["total"] == 1


def test_0014_converges_when_intermediate_0013_already_created_indexes(tmp_path):
    migration_root = Path(__file__).resolve().parents[4] / "migrations/backtest"
    intermediate_root = tmp_path / "intermediate-migrations"
    intermediate_root.mkdir()
    for source in migration_root.glob("*.sql"):
        if source.name <= "0013_expanded_research_invalidations.sql":
            (intermediate_root / source.name).write_bytes(source.read_bytes())
    intermediate_0013 = intermediate_root / "0013_expanded_research_invalidations.sql"
    premature_indexes = (migration_root / "0014_expanded_research_projection_indexes.sql").read_text(
        encoding="utf-8"
    ).replace(" IF NOT EXISTS", "")
    intermediate_0013.write_text(
        intermediate_0013.read_text(encoding="utf-8") + "\n" + premature_indexes,
        encoding="utf-8",
    )
    database_path = tmp_path / "backtest.db"
    intermediate = BacktestQueueDatabase(database_path, migrations=intermediate_root)
    assert intermediate.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0014_expanded_research_projection_indexes.sql'"
    ) is None

    converged = BacktestQueueDatabase(database_path, migrations=migration_root)
    assert converged.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0014_expanded_research_projection_indexes.sql'"
    )
    indexes = converged.fetch_all("PRAGMA index_list(expanded_research_receipts)")
    assert sum(row["name"] == "idx_expanded_research_latest_symbol" for row in indexes) == 1
    assert sum(row["name"] == "idx_expanded_research_dataset_cycle" for row in indexes) == 1


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
    assert latest["available"] is True
    cycle = latest["cycle"]
    assert cycle["evaluation_date"] == "2026-08-13"
    assert len(cycle["symbols"]) == 97
    assert cycle["summary"]["wait_count"] == 1 and cycle["summary"]["no_data_count"] == 96
    assert next(item for item in cycle["symbols"] if item["symbol"] == "AAPL")["data_state"] != "missing"
    assert sum(item["data_state"] == "missing" for item in cycle["symbols"]) == 96


def test_latest_projection_never_mixes_dataset_end_cycles(tmp_path):
    database = BacktestQueueDatabase(tmp_path / "backtest.db")
    store = ExpandedResearchStore(database, clock=lambda: NOW)
    old = [
        (_result(symbol, tier="A" if symbol in TIER_A else "C", dataset_end="2026-08-12", result_id=f"expanded-{symbol}-old-aaaaaaaa"), "2026-08-14T11:00:00Z")
        for symbol in (*TIER_A, *TIER_C)
    ]
    _seed_receipts(database, old + [(_result("AAPL", dataset_end="2026-08-13", result_id="expanded-AAPL-new-aaaaaaaa"), "2026-08-14T11:30:00Z")])

    rows = store.latest_by_symbol()

    assert [row["symbol"] for row in rows] == ["AAPL"]
    assert {row["dataset_end"] for row in rows} == {"2026-08-13"}
    latest = ExpandedResearchReadModel(store, authorize=lambda identity: identity == "user").latest("user")
    assert latest["available"] is True
    assert latest["cycle"]["evaluation_date"] == "2026-08-13"
    assert len(latest["cycle"]["symbols"]) == 97
    assert sum(item["data_state"] == "missing" for item in latest["cycle"]["symbols"]) == 96


def test_read_model_exposes_partial_single_cycle_with_missing_slots(tmp_path):
    database = BacktestQueueDatabase(tmp_path / "backtest.db")
    store = ExpandedResearchStore(database, clock=lambda: NOW)
    symbols = (*TIER_A, *TIER_C)[:45]
    _seed_receipts(
        database,
        [
            (_result(symbol, tier="A" if symbol in TIER_A else "C", dataset_end="2026-08-13", result_id=f"expanded-{symbol}-aaaaaaaaaaaaaaaa"), "2026-08-14T11:00:00Z")
            for symbol in symbols
        ],
    )
    model = ExpandedResearchReadModel(store, authorize=lambda identity: identity == "user")

    latest = model.latest("user")

    assert latest["available"] is True
    assert latest["cycle"]["evaluation_date"] == "2026-08-13"
    assert len(latest["cycle"]["symbols"]) == 97
    assert latest["cycle"]["summary"]["wait_count"] == 45
    assert latest["cycle"]["summary"]["no_data_count"] == 52
    assert sum(item["data_state"] == "missing" for item in latest["cycle"]["symbols"]) == 52


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


def test_latest_lookup_uses_97_indexed_point_reads_with_non_linear_history_cost(tmp_path, monkeypatch):
    def measure(name: str, revisions: int) -> tuple[int, int, int, int, list[str]]:
        database = BacktestQueueDatabase(tmp_path / name)
        entries = [
            (_result(
                symbol, tier="A" if symbol in TIER_A else "C", dataset_end="2026-08-13",
                result_id=f"expanded-{symbol}-revision-{revision:03d}-aaaaaaaa",
            ), (NOW - timedelta(minutes=revision)).isoformat().replace("+00:00", "Z"))
            for revision in range(revisions) for symbol in (*TIER_A, *TIER_C)
        ]
        _seed_receipts(database, entries)
        steps = transactions = point_reads = 0
        original_transaction = database.transaction

        @contextmanager
        def instrumented_transaction():
            nonlocal steps, transactions, point_reads
            transactions += 1
            with original_transaction() as connection:
                connection.set_progress_handler(lambda: _count_step(), 1)
                class ConnectionSpy:
                    def execute(self, sql, params=()):
                        nonlocal point_reads
                        point_reads += "WHERE symbol=?" in sql
                        return connection.execute(sql, params)
                try:
                    yield ConnectionSpy()
                finally:
                    connection.set_progress_handler(None, 0)

        def _count_step() -> int:
            nonlocal steps
            steps += 1
            return 0

        monkeypatch.setattr(database, "transaction", instrumented_transaction)
        rows = ExpandedResearchStore(database, clock=lambda: NOW)._latest_candidate_rows()
        plan = database.fetch_all(
            """EXPLAIN QUERY PLAN SELECT * FROM expanded_research_receipts
               INDEXED BY idx_expanded_research_latest_symbol WHERE symbol=?
               ORDER BY received_at DESC,receipt_key DESC LIMIT 1""",
            ("AAPL",),
        )
        return len(rows), steps, transactions, point_reads, [str(row["detail"]) for row in plan]

    small = measure("small.db", 1)
    large = measure("large.db", 100)
    assert small[:1] == large[:1] == (97,) and small[2:4] == large[2:4] == (1, 97)
    assert large[1] < small[1] * 2
    assert any("SEARCH expanded_research_receipts USING INDEX idx_expanded_research_latest_symbol" in item for item in large[4])


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
    assert rows[0]["projection_state"] == "active"


def test_read_model_keeps_active_history_consistent_after_one_result_is_invalidated(tmp_path):
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "backtest.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    _request(receiver, _result(), key="expanded-aapl-0001")
    _request(receiver, _result("MSFT"), key="expanded-msft-0001")
    _invalidate(receiver, result_id=_result()["result_id"])
    model = ExpandedResearchReadModel(store, authorize=lambda _identity: True)
    latest = model.latest("user")
    history = model.history("user")
    assert latest["available"] is True
    assert latest["cycle"]["summary"]["wait_count"] == 1
    assert latest["cycle"]["summary"]["no_data_count"] == 96
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
    rows = [{"symbol": symbol, "dataset_end": "2026-08-13", "received_at": "stale" if index == 0 else "fresh"} for index, symbol in enumerate((*TIER_A, *TIER_C))]
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
