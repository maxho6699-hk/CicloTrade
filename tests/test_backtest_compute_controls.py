from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import errno
import hashlib
import os
from pathlib import Path
import shutil
import time

import pytest

from core.backtest_artifacts import ArtifactError, ArtifactStore
from core.backtest_contracts import BacktestQueueError
from core.backtest_operations import BacktestOperations
from core.backtest_queue import BacktestQueue
from core.backtest_queue_database import BacktestQueueDatabase
from tests.test_backtest_queue import DATA, DIGEST, _job, _manifest, _ready


def _budget_manifest(version: str) -> dict:
    return _manifest(
        candidate_version=version,
        parent_version=None,
        parent_job_id=None,
        parent_manifest_sha256=None,
        parent_result_sha256=None,
        provenance={"source": "approved_seed", "generated_by": "ciclo-admin"},
        search_space={"window": [5]},
        experiment_budget={"runs": 1, "folds": 2},
    )


def test_compute_systemd_units_are_bounded_network_isolated_and_not_resident():
    root = Path(__file__).resolve().parents[1]
    service = (root / "ops" / "ciclotrade-strategy-compute.service").read_text(encoding="utf-8")
    timer = (root / "ops" / "ciclotrade-strategy-compute.timer").read_text(encoding="utf-8")
    resident = (root / "ops" / "ciclotrade-strategy-worker.service").read_text(encoding="utf-8")

    assert "Type=oneshot" in service
    assert "PrivateNetwork=true" in service
    assert "--once --execute-one" in service
    assert "Restart=" not in service and "[Install]" not in service
    assert "OnUnitActiveSec=15min" in timer
    assert "Persistent=false" in timer
    assert "enable-resident-worker.after-publish-gate" in resident
    assert "[Install]" not in resident


def test_database_applies_operator_cancellation_after_existing_compute_gate_migration(tmp_path):
    repository_migrations = Path(__file__).resolve().parents[1] / "migrations" / "backtest"
    prior_migrations = tmp_path / "prior-migrations"
    prior_migrations.mkdir()
    for name in ("0001_persistent_jobs.sql", "0002_attempt_deadlines.sql", "0004_compute_gate.sql"):
        shutil.copy2(repository_migrations / name, prior_migrations / name)
    database_path = tmp_path / "queue.db"

    BacktestQueueDatabase(database_path, prior_migrations)
    upgraded = BacktestQueueDatabase(database_path, repository_migrations)

    assert upgraded.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0005_operator_cancellation.sql'"
    )
    assert upgraded.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0006_system_attempt_budget.sql'"
    )
    columns = {row["name"] for row in upgraded.fetch_all("PRAGMA table_info(backtest_jobs)")}
    assert {
        "cancel_source",
        "cancel_reason",
        "system_daily_attempt_limit",
        "system_budget_timezone",
    } <= columns


def test_audited_system_cancel_is_manifest_bound_and_request_idempotent(tmp_path):
    queue = BacktestQueue(BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts"))
    job = _job(queue, "audited-cancel", internal=True)
    operations = BacktestOperations(queue)

    first = operations.cancel_system(
        job["id"],
        operator_subject="ops.hk",
        request_id="cancel-request-001",
        reason_code="maintenance",
        expected_manifest_sha256=job["manifest_sha256"],
    )
    repeated = operations.cancel_system(
        job["id"],
        operator_subject="ops.hk",
        request_id="cancel-request-001",
        reason_code="maintenance",
        expected_manifest_sha256=job["manifest_sha256"],
    )

    assert first["status"] == repeated["status"] == "cancelled"
    assert first["cancel_source"] == "operator:ops.hk"
    assert first["cancel_reason"] == "maintenance"
    assert queue.db.fetch_one(
        "SELECT request_id,job_id,operator_subject,reason_code,manifest_sha256,previous_status,resulting_status "
        "FROM backtest_operator_actions"
    ) == {
        "request_id": "cancel-request-001",
        "job_id": job["id"],
        "operator_subject": "ops.hk",
        "reason_code": "maintenance",
        "manifest_sha256": job["manifest_sha256"],
        "previous_status": "queued",
        "resulting_status": "cancelled",
    }
    with pytest.raises(BacktestQueueError) as reused:
        operations.cancel_system(
            job["id"],
            operator_subject="ops.hk",
            request_id="cancel-request-001",
            reason_code="different",
            expected_manifest_sha256=job["manifest_sha256"],
        )
    assert reused.value.status == 409


@pytest.mark.parametrize(
    ("operator", "request_id", "reason"),
    [
        ("", "cancel-request-001", "reason"),
        ("bad operator", "cancel-request-001", "reason"),
        ("x" * 72, "cancel-request-001", "reason"),
        ("ops", "short", "reason"),
        ("ops", "cancel-request-001", "bad reason"),
    ],
)
def test_audited_system_cancel_identity_and_reason_fail_closed(
    tmp_path, operator, request_id, reason
):
    queue = BacktestQueue(
        BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts")
    )
    job = _job(queue, "operator-invalid", internal=True)

    with pytest.raises(BacktestQueueError):
        BacktestOperations(queue).cancel_system(
            job["id"],
            operator_subject=operator,
            request_id=request_id,
            reason_code=reason,
            expected_manifest_sha256=job["manifest_sha256"],
        )


def test_audited_system_cancel_cannot_target_a_user_owned_job(tmp_path):
    queue = BacktestQueue(
        BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts")
    )
    user_job = _job(queue, "operator-user-job", internal=False)

    with pytest.raises(BacktestQueueError) as denied:
        BacktestOperations(queue).cancel_system(
            user_job["id"],
            operator_subject="ops.hk",
            request_id="cancel-request-user",
            reason_code="maintenance",
            expected_manifest_sha256=user_job["manifest_sha256"],
        )
    assert denied.value.status == 404


def test_audited_running_cancel_rejects_stale_manifest_and_second_request(tmp_path):
    queue = BacktestQueue(BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts"))
    job = _job(queue, "audited-running", internal=True)
    _ready(queue, job)
    lease = queue.claim("worker")
    assert lease and lease["id"] == job["id"]
    operations = BacktestOperations(queue)

    with pytest.raises(BacktestQueueError) as stale:
        operations.cancel_system(
            job["id"],
            operator_subject="ops.hk",
            request_id="cancel-request-stale",
            reason_code="risk_gate",
            expected_manifest_sha256="0" * 64,
        )
    assert stale.value.status == 409

    requested = operations.cancel_system(
        job["id"],
        operator_subject="ops.hk",
        request_id="cancel-request-running",
        reason_code="risk_gate",
        expected_manifest_sha256=job["manifest_sha256"],
    )
    assert requested["status"] == "running" and requested["cancel_requested"] is True
    assert requested["cancel_source"] == "operator:ops.hk"
    stopped = queue.heartbeat(
        job["id"], "worker", lease["lease_token"], lease["fencing_epoch"], 0.5, "executing"
    )
    assert stopped["cancel_requested"] is True
    with pytest.raises(BacktestQueueError) as duplicate:
        operations.cancel_system(
            job["id"],
            operator_subject="ops.hk",
            request_id="cancel-request-second",
            reason_code="maintenance",
            expected_manifest_sha256=job["manifest_sha256"],
        )
    assert duplicate.value.status == 409


def test_system_daily_and_pending_budget_is_enforced_inside_enqueue_transaction(tmp_path):
    queue = BacktestQueue(BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts"))
    first = _job(queue, "budget-first", internal=True)

    with pytest.raises(BacktestQueueError) as limited:
        queue.enqueue(
            None,
            {"type": "candidate.evaluate.v1", "manifest": {**first["manifest"], "candidate_version": "2"}},
            idempotency_scope="system:candidates",
            idempotency_key="budget-second",
            internal=True,
            system_daily_limit=1,
            system_pending_limit=1,
            system_day_start=first["created_at"][:10] + "T00:00:00+00:00",
        )
    assert limited.value.status == 429

    with pytest.raises(BacktestQueueError) as run_limited:
        queue.enqueue(
            None,
            {"type": "candidate.evaluate.v1", "manifest": {**first["manifest"], "candidate_version": "3"}},
            idempotency_scope="system:candidates",
            idempotency_key="budget-runs",
            internal=True,
            system_daily_limit=10,
            system_daily_runs_limit=1,
            system_pending_limit=10,
            system_day_start=first["created_at"][:10] + "T00:00:00+00:00",
            system_budget_timezone="Asia/Hong_Kong",
        )
    assert run_limited.value.status == 429


def test_actual_attempt_budget_blocks_retry_claim_for_the_same_hong_kong_day(tmp_path):
    queue = BacktestQueue(BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts"))
    day_start = datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"
    job, _ = queue.enqueue(
        None,
        {"type": "candidate.evaluate.v1", "manifest": _budget_manifest("attempt-budget"), "max_attempts": 3},
        idempotency_scope="system:attempt-budget",
        idempotency_key="attempt-budget-job",
        internal=True,
        system_daily_limit=10,
        system_daily_runs_limit=1,
        system_pending_limit=10,
        system_day_start=day_start,
        system_budget_timezone="Asia/Hong_Kong",
    )
    queue.register_input(job["id"], "prices.csv", DATA, DIGEST)
    lease = queue.claim("worker-one")
    assert lease and lease["id"] == job["id"]
    failed = queue.fail(
        job["id"],
        "worker-one",
        lease["lease_token"],
        lease["fencing_epoch"],
        {"error_code": "TRANSIENT", "message": "retry later", "retryable": True},
    )
    assert failed["status"] == "queued"
    queue.db.execute("UPDATE backtest_jobs SET available_at=? WHERE id=?", ("2000-01-01T00:00:00Z", job["id"]))

    competing = BacktestQueue(
        BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts")
    )
    assert competing.claim("worker-two") is None
    assert competing.db.fetch_one("SELECT count(*) AS total FROM backtest_job_attempts")["total"] == 1


def test_legacy_compute_job_cannot_claim_until_idempotent_replay_binds_attempt_budget(tmp_path):
    queue = BacktestQueue(BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts"))
    request = {"type": "candidate.evaluate.v1", "manifest": _budget_manifest("legacy-compute")}
    job, _ = queue.enqueue(
        None,
        request,
        idempotency_scope="system:compute-gate",
        idempotency_key="legacy-compute-request",
        internal=True,
    )
    queue.register_input(job["id"], "prices.csv", DATA, DIGEST)
    assert queue.claim("worker-before-budget") is None

    day_start = datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"
    replayed, created = queue.enqueue(
        None,
        request,
        idempotency_scope="system:compute-gate",
        idempotency_key="legacy-compute-request",
        internal=True,
        system_daily_limit=10,
        system_daily_runs_limit=1,
        system_pending_limit=10,
        system_day_start=day_start,
        system_budget_timezone="Asia/Hong_Kong",
    )
    assert created is False and replayed["system_daily_attempt_limit"] == 1
    assert queue.claim("worker-after-budget")["id"] == job["id"]


def test_two_database_instances_cannot_both_take_the_last_daily_enqueue_budget(tmp_path):
    database_path = tmp_path / "queue.db"
    artifact_path = tmp_path / "artifacts"
    queues = [
        BacktestQueue(BacktestQueueDatabase(database_path), ArtifactStore(artifact_path))
        for _ in range(2)
    ]
    day_start = datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"

    def enqueue(index: int) -> str | int:
        try:
            queues[index].enqueue(
                None,
                {"type": "candidate.evaluate.v1", "manifest": _budget_manifest(f"race-{index}")},
                idempotency_scope="system:budget-race",
                idempotency_key=f"budget-race-{index}",
                internal=True,
                system_daily_limit=1,
                system_daily_runs_limit=1,
                system_pending_limit=10,
                system_day_start=day_start,
                system_budget_timezone="Asia/Hong_Kong",
            )
            return "created"
        except BacktestQueueError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(enqueue, range(2)))

    assert sorted(outcomes, key=str) == [429, "created"]
    assert queues[0].db.fetch_one("SELECT count(*) AS total FROM backtest_jobs")["total"] == 1


def test_artifact_directory_fsync_disk_full_translation_and_orphan_cleanup(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "artifacts")
    calls: list[int] = []
    monkeypatch.setattr("core.backtest_artifacts.os.name", "posix")
    monkeypatch.setattr("core.backtest_artifacts.os.open", lambda *args: 77)
    monkeypatch.setattr("core.backtest_artifacts.os.fsync", calls.append)
    monkeypatch.setattr("core.backtest_artifacts.os.close", lambda descriptor: calls.append(-descriptor))
    store._fsync_directory(tmp_path)
    assert calls == [77, -77]

    monkeypatch.undo()
    digest = hashlib.sha256(b"registered").hexdigest()
    registered, _ = store.write("job-a", "input", "prices.csv", b"registered", digest)
    orphan_digest = hashlib.sha256(b"orphan").hexdigest()
    orphan, _ = store.write("job-b", "output", "result.json", b"orphan", orphan_digest, 1)
    temporary = store.root / ".upload-stale"
    temporary.write_bytes(b"partial")
    old = time.time() - 7200
    os.utime(store._path(registered), (old, old))
    os.utime(store._path(orphan), (old, old))
    os.utime(temporary, (old, old))

    cleaned = store.reconcile_orphans({registered}, minimum_age_seconds=3600)
    assert cleaned["removed_count"] == 2
    assert store._path(registered).exists()
    assert not store._path(orphan).exists() and not temporary.exists()

    monkeypatch.setattr(store, "finalize_temp", lambda *args, **kwargs: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    with pytest.raises(ArtifactError, match="存储空间不足"):
        store.write("job-c", "output", "result.json", b"x", hashlib.sha256(b"x").hexdigest())


def test_artifact_root_and_removed_directory_fsync_fail_closed(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "new-parent" / "artifacts")
    monkeypatch.setattr(
        store,
        "_fsync_directory",
        lambda path: (_ for _ in ()).throw(OSError(errno.EIO, f"fsync {path}")),
    )
    with pytest.raises(ArtifactError, match="临时存储初始化失败"):
        store.write("job-root", "input", "prices.csv", b"x", hashlib.sha256(b"x").hexdigest())

    cleanup_store = ArtifactStore(tmp_path / "cleanup-artifacts")
    empty = cleanup_store.root / "job-empty" / "input"
    empty.mkdir(parents=True)
    monkeypatch.setattr(
        cleanup_store,
        "_fsync_directory",
        lambda path: (_ for _ in ()).throw(OSError(errno.EIO, f"fsync {path}")),
    )
    with pytest.raises(ArtifactError, match="持久化目录删除"):
        cleanup_store.reconcile_orphans(set(), minimum_age_seconds=60)
