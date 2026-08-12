from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from core.autonomous_candidate_registry import CandidateRegistry, CandidateRegistryError, candidate_record
from core.backtest_queue_database import BacktestQueueDatabase


HASH = hashlib.sha256(b"test").hexdigest()


def _record(version: str, status: str, rank: float, *, rollback_target: str | None = None) -> dict[str, object]:
    return candidate_record(
        candidate_id="aapl.trend",
        candidate_version=version,
        hypothesis="point-in-time AAPL long-flat test",
        parent_version=None,
        universe_sha256=HASH,
        data_sha256=HASH,
        code_sha256=HASH,
        evidence_sha256=HASH,
        status=status,
        ranking_inputs={"score": rank},
        rejection_reason=None if status == "shadow" else "independent gate failed",
        rollback_target=rollback_target,
    )


def test_registry_is_append_only_idempotent_and_failed_challenger_cannot_replace_shadow_champion():
    registry = CandidateRegistry()
    champion = registry.register(_record("v1", "shadow", 1.0))
    repeated = registry.register(_record("v1", "shadow", 1.0))
    challenger = registry.register(_record("v2", "rejected", 100.0, rollback_target="v1"))

    assert champion.created is True and repeated.created is False
    assert challenger.champion["candidate_version"] == "v1"
    assert registry.champion("aapl.trend")["candidate_version"] == "v1"
    with pytest.raises(CandidateRegistryError):
        registry.register(_record("v1", "quarantine", 2.0))
    with pytest.raises(CandidateRegistryError):
        registry.register(_record("v3", "rejected", 2.0, rollback_target="missing"))


def test_registry_rejects_live_and_nonfinite_ranking_inputs():
    with pytest.raises(CandidateRegistryError):
        candidate_record("aapl.trend", "v1", "h", None, HASH, HASH, HASH, HASH, "live", {"score": 1}, None, None)
    with pytest.raises(CandidateRegistryError):
        candidate_record("aapl.trend", "v1", "h", None, HASH, HASH, HASH, HASH, "shadow", {"score": float("inf")}, None, None)


def test_autonomous_shadow_migration_upgrades_and_blocks_record_mutation(tmp_path):
    migrations = Path(__file__).resolve().parents[1] / "migrations" / "backtest"
    prior = tmp_path / "prior"
    prior.mkdir()
    for path in migrations.glob("000[1-8]_*.sql"):
        shutil.copy2(path, prior / path.name)
    database_path = tmp_path / "queue.db"
    BacktestQueueDatabase(database_path, prior)
    upgraded = BacktestQueueDatabase(database_path, migrations)

    assert upgraded.fetch_one("SELECT version FROM schema_migrations WHERE version='0009_autonomous_shadow_v1.sql'")
    upgraded.execute(
        "INSERT INTO backtest_autonomous_candidates(record_sha256,schema_version,candidate_id,candidate_version,record_json,status,created_at) VALUES(?,?,?,?,?,?,?)",
        (HASH, 1, "aapl.trend", "v1", json.dumps(_record("v1", "shadow", 1.0)), "shadow", "2025-01-01T00:00:00Z"),
    )
    with pytest.raises(Exception):
        upgraded.execute("UPDATE backtest_autonomous_candidates SET status='rejected' WHERE record_sha256=?", (HASH,))
    snapshot = {
        "schema_version": 1,
        "as_of": "2025-01-01",
        "layer_receipts": [],
        "members": [],
    }
    upgraded.execute(
        "INSERT INTO backtest_us_equity_universe_snapshots(snapshot_sha256,schema_version,as_of_date,members_json,created_at) VALUES(?,?,?,?,?)",
        (hashlib.sha256(b"snapshot").hexdigest(), 1, "2025-01-01", json.dumps(snapshot), "2025-01-01T00:00:00Z"),
    )
    with pytest.raises(Exception):
        upgraded.execute("DELETE FROM backtest_us_equity_universe_snapshots")
