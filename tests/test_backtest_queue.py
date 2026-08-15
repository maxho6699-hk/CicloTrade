from __future__ import annotations

import errno
import hashlib
import math
import os
import threading
from datetime import datetime, timezone

import pytest

from core.backtest_artifacts import ArtifactError, ArtifactStore
from core.backtest_queue import BacktestQueue, BacktestQueueError
from core.backtest_queue_database import BacktestQueueDatabase


DATA = b"frozen canonical input"
DIGEST = hashlib.sha256(DATA).hexdigest()
CODE_DIGEST = hashlib.sha256(b"bundle").hexdigest()
PARENT_DIGEST = hashlib.sha256(b"parent-manifest").hexdigest()


def _authority():
    return {
        "origin_site": "hk-strategy-worker",
        "deployment_role": "strategy_worker",
        "publication_ceiling": "shadow",
        "outbound_publish_enabled": False,
        "user_visible": False,
        "execution_eligible": False,
        "recommendations_published": False,
    }


def _risk_contract():
    return {
        "defined_risk": True,
        "max_loss_amount": 500.0,
        "currency": "USD",
        "max_loss_pct_model_equity": 0.005,
        "risk_basis_equity": 100_000.0,
        "risk_basis_captured_at": "2026-08-10T00:00:00Z",
        "portfolio_open_risk_cap_pct": 0.03,
        "daily_new_risk_pause_pct": 0.015,
        "quarantine_drawdown_pct": 0.08,
        "invalidation_condition": "Close below the frozen long-flat regime boundary.",
    }


def _validation_plan():
    return {
        "oos_method": "point_in_time",
        "walk_forward": True,
        "cost_multipliers": [1.0, 2.0],
        "stress_tests": ["gap", "liquidity", "volatility"],
        "minimum_trades": 30,
        "minimum_coverage_days": 252,
        "market_regimes": ["bull", "bear", "sideways"],
    }


def _manifest(**extra):
    return {
        "schema_version": 1,
        "evaluation_date": "2026-08-10",
        "dataset_end": "2026-08-09",
        "candidate_id": "candidate-a",
        "candidate_version": "1",
        "provenance": {"source": "autonomous_research", "generated_by": "ciclo-worker"},
        "hypothesis": "test",
        "parent_version": "0",
        "parent_job_id": "parent-job",
        "parent_manifest_sha256": PARENT_DIGEST,
        "parent_result_sha256": hashlib.sha256(b"parent-result").hexdigest(),
        "template_key": "equity.trend.long_flat.v1",
        "asset_universe": {
            "market": "US",
            "instrument_family": "equity",
            "symbols": ["AAPL"],
            "direction": "long_flat",
            "research_proxy": False,
            "data_mode": "point_in_time_prices",
        },
        "search_space": {"window": [5, 10]},
        "experiment_budget": {"runs": 2, "folds": 2},
        "evidence_hashes": {"prices.csv": DIGEST},
        "authority": _authority(),
        "risk_contract": _risk_contract(),
        "validation_plan": _validation_plan(),
        "code_bundle_sha256": CODE_DIGEST,
        "inputs": [{"artifact_key": "prices.csv", "sha256": DIGEST, "dataset_end": "2026-08-09"}],
        **extra,
    }


def _queue(tmp_path):
    db = BacktestQueueDatabase(tmp_path / "queue.db")
    return BacktestQueue(db, ArtifactStore(tmp_path / "artifacts", max_bytes=2048)), db


def _job(queue, key="abcdefgh", *, internal=False):
    manifest = _manifest()
    if internal:
        manifest.update({
            "parent_version": None,
            "parent_job_id": None,
            "parent_manifest_sha256": None,
            "parent_result_sha256": None,
            "provenance": {"source": "approved_seed", "generated_by": "ciclo-admin"},
        })
    return queue.enqueue(None if internal else 1, {"type": "candidate.evaluate.v1" if internal else "backtest.run.v1", "manifest": manifest}, idempotency_scope="system:candidates" if internal else "user:1", idempotency_key=key, plan="专业版", internal=internal)[0]


def _ready(queue, job):
    queue.register_input(job["id"], "prices.csv", DATA, DIGEST)


def _result(job, **extra):
    return {
        "job_id": job["id"],
        "manifest_sha256": job["manifest_sha256"],
        "code_bundle_sha256": CODE_DIGEST,
        "fencing_epoch": job["fencing_epoch"],
        "input_hashes": {"prices.csv": DIGEST},
        "output_hashes": {},
        "evidence": {"kind": "research"},
        **extra,
    }


def _candidate_evidence(output_hashes, *, research_proxy=False, **extra):
    return {
        "kind": "shadow",
        "hashes": output_hashes,
        "authority": _authority(),
        "data_contract": {"research_proxy": research_proxy, "actionable": False},
        "validation": {
            "dataset_end": "2026-08-09",
            "evaluation_date": "2026-08-10",
            "oos_passed": True,
            "walk_forward_passed": True,
            "cost_1x_passed": True,
            "cost_2x_passed": True,
            "cost_multipliers": [1.0, 2.0],
            "stress_passed": True,
            "multi_regime_passed": True,
            "minimum_trades_passed": True,
            "minimum_coverage_passed": True,
            "candidate_status": "shadow",
            "trade_count": 60,
            "coverage_days": 504,
            "max_drawdown": 0.07,
            "tail_stress_loss_pct": 0.02,
            "market_regimes": ["bull", "bear", "sideways"],
        },
        "risk": _risk_contract(),
        **extra,
    }


def test_migration_idempotency_and_internal_system_owner(tmp_path):
    queue, db = _queue(tmp_path)
    assert db.fetch_one("SELECT version FROM schema_migrations WHERE version='0001_persistent_jobs.sql'")
    assert db.fetch_one("SELECT version FROM schema_migrations WHERE version='0002_attempt_deadlines.sql'")
    first, created = queue.enqueue(1, {"type": "backtest.run.v1", "manifest": _manifest()}, idempotency_scope="user:1", idempotency_key="abcdefgh", plan="专业版")
    second, repeated = queue.enqueue(1, {"type": "backtest.run.v1", "manifest": _manifest()}, idempotency_scope="user:1", idempotency_key="abcdefgh", plan="专业版")
    assert created and not repeated and first["id"] == second["id"]
    system = _job(queue, "systemjob", internal=True)
    assert system["owner_id"] is None and system["owner_scope"] == "system"
    with pytest.raises(BacktestQueueError):
        queue.enqueue(1, {"type": "candidate.evaluate.v1", "manifest": _manifest()}, idempotency_scope="user:1", idempotency_key="wrongkind", internal=False)
    BacktestQueueDatabase(db._db_path)


def test_old_0013_database_receives_attempt_deadline_upgrade(tmp_path):
    _, db = _queue(tmp_path)
    db.execute("DELETE FROM schema_migrations WHERE version='0002_attempt_deadlines.sql'")
    db.execute("ALTER TABLE backtest_jobs DROP COLUMN attempt_deadline_at")
    db.execute("ALTER TABLE backtest_job_attempts DROP COLUMN attempt_deadline_at")

    upgraded = BacktestQueueDatabase(db._db_path)
    job_columns = {row["name"] for row in upgraded.fetch_all("PRAGMA table_info(backtest_jobs)")}
    attempt_columns = {row["name"] for row in upgraded.fetch_all("PRAGMA table_info(backtest_job_attempts)")}
    assert "attempt_deadline_at" in job_columns
    assert "attempt_deadline_at" in attempt_columns
    assert upgraded.fetch_one("SELECT version FROM schema_migrations WHERE version='0002_attempt_deadlines.sql'")


def test_manifest_freeze_readiness_and_pending_while_running(tmp_path):
    queue, _ = _queue(tmp_path)
    pending = _job(queue)
    assert queue.claim("worker") is None  # public work can await canonical input registration
    with pytest.raises(BacktestQueueError):
        queue.register_input(pending["id"], "unexpected.csv", DATA, DIGEST)
    with pytest.raises(BacktestQueueError):
        queue.register_input(pending["id"], "prices.csv", DATA, "0" * 64)
    _ready(queue, pending)
    lease = queue.claim("worker-a", 120)
    assert lease and lease["id"] == pending["id"]
    # A personal running job does not prohibit one bounded pending job.
    second = _job(queue, "ijklmnop")
    assert second["status"] == "queued"
    assert queue.claim("worker-b") is None
    waiting_limit = queue._limits("专业版")[0]
    for number in range(2, waiting_limit + 1):
        _job(queue, f"pending-{number:02d}")
    with pytest.raises(BacktestQueueError) as pending_full:
        _job(queue, "pending-overflow")
    assert pending_full.value.status == 429
    with pytest.raises(BacktestQueueError):
        queue.enqueue(1, {"type": "backtest.run.v1", "manifest": _manifest(dataset_end="2026-08-11")}, idempotency_scope="user:1", idempotency_key="badfuture", plan="专业版")
    with pytest.raises(BacktestQueueError):
        queue.enqueue(1, {"type": "backtest.run.v1", "manifest": _manifest(inputs=[])}, idempotency_scope="user:1", idempotency_key="emptyinputs", plan="专业版")


def test_identical_input_freeze_is_idempotent_but_conflicting_retry_is_rejected(tmp_path):
    queue, _ = _queue(tmp_path)
    pending = _job(queue)

    first = queue.register_input(
        pending["id"], "prices.csv", DATA, DIGEST, row_count=1, media_type="text/csv",
    )
    repeated = queue.register_input(
        pending["id"], "prices.csv", DATA, DIGEST, row_count=1, media_type="text/csv",
    )

    assert repeated == first
    with pytest.raises(BacktestQueueError, match="不同内容"):
        queue.register_input(
            pending["id"], "prices.csv", DATA, DIGEST, row_count=1, media_type="application/octet-stream",
        )


def test_concurrent_identical_input_registration_converges_to_one_verified_row(tmp_path):
    queue, db = _queue(tmp_path)
    pending = _job(queue)
    results: list[dict] = []
    errors: list[Exception] = []

    def freeze() -> None:
        try:
            results.append(queue.register_input(pending["id"], "prices.csv", DATA, DIGEST, row_count=1))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=freeze) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [] and len(results) == 2 and results[0] == results[1]
    assert db.fetch_one(
        "SELECT count(*) AS total FROM backtest_job_artifacts WHERE job_id=? AND direction='input'",
        (pending["id"],),
    )["total"] == 1


def test_candidate_lineage_evidence_and_promotion_proposal_are_governed(tmp_path):
    queue, _ = _queue(tmp_path)
    proposal = {
        "target_stage": "official_simulation",
        "requires_human_approval": True,
        "rationale": "通过证据门后提交网站人工审批。",
    }
    seed = _job(queue, "candidate-seed", internal=True)
    _ready(queue, seed)
    seed_lease = queue.claim("seed-worker", 120)
    seed_body = b'{"seed":"approved"}'
    seed_digest = hashlib.sha256(seed_body).hexdigest()
    queue.upload_output(
        seed_lease["id"], "seed.json", seed_body, seed_digest, "seed-worker",
        seed_lease["lease_token"], seed_lease["fencing_epoch"],
    )
    queue.complete(
        seed_lease["id"], "seed-worker", seed_lease["lease_token"], seed_lease["fencing_epoch"],
        _result(seed_lease, output_hashes={"seed.json": seed_digest}, evidence=_candidate_evidence({"seed.json": seed_digest})),
    )
    child_manifest = _manifest(
        candidate_version="2",
        parent_version=seed["manifest"]["candidate_version"],
        parent_job_id=seed["id"],
        parent_manifest_sha256=seed["manifest_sha256"],
        parent_result_sha256=queue.get(seed["id"])["result_sha256"],
    )
    candidate, _ = queue.enqueue(
        None,
        {"type": "candidate.evaluate.v1", "manifest": {**child_manifest, "promotion_proposal": proposal}},
        idempotency_scope="system:candidates",
        idempotency_key="candidate-governed",
        internal=True,
    )
    _ready(queue, candidate)
    lease = queue.claim("candidate-worker", 120)
    evidence_body = b'{"walk_forward":"passed"}'
    evidence_digest = hashlib.sha256(evidence_body).hexdigest()
    queue.upload_output(
        lease["id"],
        "evidence.json",
        evidence_body,
        evidence_digest,
        "candidate-worker",
        lease["lease_token"],
        lease["fencing_epoch"],
    )
    completed = queue.complete(
        lease["id"],
        "candidate-worker",
        lease["lease_token"],
        lease["fencing_epoch"],
        _result(
            lease,
            output_hashes={"evidence.json": evidence_digest},
            evidence=_candidate_evidence({"evidence.json": evidence_digest}),
            promotion_proposal=proposal,
        ),
    )
    assert completed["status"] == "completed"
    with pytest.raises(BacktestQueueError) as public_proposal:
        queue.enqueue(
            1,
            {"type": "backtest.run.v1", "manifest": _manifest(promotion_proposal=proposal)},
            idempotency_scope="user:1",
            idempotency_key="public-proposal",
            plan="专业版",
        )
    assert public_proposal.value.status == 403
    with pytest.raises(BacktestQueueError):
        queue.enqueue(
            None,
            {"type": "candidate.evaluate.v1", "manifest": {**child_manifest, "evidence_hashes": {"prices.csv": "0" * 64}}},
            idempotency_scope="system:candidates",
            idempotency_key="candidate-unbound-evidence",
            internal=True,
        )
    with pytest.raises(BacktestQueueError) as missing_parent:
        queue.enqueue(
            None,
            {"type": "candidate.evaluate.v1", "manifest": {**child_manifest, "parent_manifest_sha256": "0" * 64}},
            idempotency_scope="system:candidates",
            idempotency_key="candidate-missing-parent",
            internal=True,
        )
    assert missing_parent.value.status == 409


def test_candidate_contract_rejects_unsafe_scope_proxy_and_risk(tmp_path):
    queue, _ = _queue(tmp_path)
    unsafe = [
        _manifest(search_space={"python_code": ["import os"]}),
        _manifest(asset_universe={**_manifest()["asset_universe"], "direction": "short"}),
        _manifest(risk_contract={**_risk_contract(), "max_loss_pct_model_equity": 0.01}),
        _manifest(template_key="option.naked_short_call.v1", asset_universe={
            "market": "US", "instrument_family": "option", "symbols": ["AAPL"],
            "direction": "limited_risk", "option_structure": "naked_short_call", "research_proxy": True,
            "data_mode": "underlying_volatility_proxy",
        }),
        _manifest(template_key="option.long_call.v1", asset_universe={
            "market": "US", "instrument_family": "option", "symbols": ["AAPL"],
            "direction": "limited_risk", "option_structure": "long_call", "research_proxy": False,
            "data_mode": "underlying_volatility_proxy",
        }),
    ]
    for number, manifest in enumerate(unsafe):
        manifest.update({
            "parent_version": None,
            "parent_job_id": None,
            "parent_manifest_sha256": None,
            "parent_result_sha256": None,
            "provenance": {"source": "approved_seed", "generated_by": "ciclo-admin"},
        })
        with pytest.raises(BacktestQueueError):
            queue.enqueue(None, {"type": "candidate.evaluate.v1", "manifest": manifest}, idempotency_scope="system:candidates", idempotency_key=f"unsafe-{number:02d}", internal=True)


def test_scope_nonfinite_cancel_and_retry_are_fail_closed(tmp_path):
    queue, db = _queue(tmp_path)
    with pytest.raises(BacktestQueueError):
        queue.enqueue(1, {"type": "backtest.run.v1", "manifest": _manifest()}, idempotency_scope="user:2", idempotency_key="wrongscope", plan="专业版")
    with pytest.raises(BacktestQueueError):
        queue.enqueue(1, {"type": "backtest.run.v1", "manifest": _manifest(parameters={"bad": math.nan})}, idempotency_scope="user:1", idempotency_key="nonfinite", plan="专业版")

    job = _job(queue, "cancel-heartbeat")
    _ready(queue, job)
    lease = queue.claim("worker", 120)
    queue.cancel(job["id"], 1)
    cancelled = queue.heartbeat(job["id"], "worker", lease["lease_token"], lease["fencing_epoch"], 0.2, "loading")
    assert cancelled["cancel_requested"] is True
    after = queue.get(job["id"])
    assert after["lease_expires_at"] == lease["lease_expires_at"]

    queue.fail(job["id"], "worker", lease["lease_token"], lease["fencing_epoch"], {"error_code": "CANCELLED", "message": "cancel", "retryable": False})
    retry_job = _job(queue, "retry-backoff")
    _ready(queue, retry_job)
    retry_lease = queue.claim("worker-2", 120)
    failed = queue.fail(retry_job["id"], "worker-2", retry_lease["lease_token"], retry_lease["fencing_epoch"], {"error_code": "TEMPORARY", "message": "temporary", "retryable": True})
    assert failed["status"] == "queued"
    assert datetime.fromisoformat(failed["available_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
    assert queue.claim("worker-3", 120) is None


def test_attempt_deadline_invalidates_lease_and_allows_fenced_retry(tmp_path):
    queue, db = _queue(tmp_path)
    job = _job(queue)
    _ready(queue, job)
    first = queue.claim("deadline-worker", 120)
    db.execute(
        "UPDATE backtest_jobs SET lease_expires_at='2000-01-01T00:00:00Z',attempt_deadline_at='2000-01-01T00:00:00Z' WHERE id=?",
        (first["id"],),
    )
    with pytest.raises(BacktestQueueError) as expired:
        queue.heartbeat(first["id"], "deadline-worker", first["lease_token"], first["fencing_epoch"], .1, "loading")
    assert expired.value.status == 409
    retry = queue.claim("deadline-retry", 120)
    assert retry and retry["fencing_epoch"] == first["fencing_epoch"] + 1


def test_global_atomic_claim_renewal_stale_fence_and_result_binding(tmp_path):
    queue, db = _queue(tmp_path)
    job = _job(queue)
    _ready(queue, job)
    other = BacktestQueue(BacktestQueueDatabase(db._db_path), queue.artifacts)
    results: list[dict | None] = []
    threads = [threading.Thread(target=lambda q=q, n=n: results.append(q.claim(n, 120))) for q, n in ((queue, "worker-a"), (other, "worker-b"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lease = next(item for item in results if item is not None)
    assert sum(item is not None for item in results) == 1
    renewed = queue.heartbeat(lease["id"], lease["worker_id"], lease["lease_token"], lease["fencing_epoch"], .4, "executing")
    assert renewed["lease_expires_at"] >= lease["lease_expires_at"] and renewed["stage"] == "executing"
    renewed_again = queue.heartbeat(lease["id"], lease["worker_id"], lease["lease_token"], lease["fencing_epoch"], .4, "executing")
    first_expiry = datetime.fromisoformat(renewed["lease_expires_at"].replace("Z", "+00:00"))
    second_expiry = datetime.fromisoformat(renewed_again["lease_expires_at"].replace("Z", "+00:00"))
    attempt_deadline = datetime.fromisoformat(lease["attempt_deadline_at"].replace("Z", "+00:00"))
    assert (second_expiry - first_expiry).total_seconds() <= 2
    assert second_expiry <= attempt_deadline
    with pytest.raises(BacktestQueueError):
        queue.heartbeat(lease["id"], lease["worker_id"], lease["lease_token"], lease["fencing_epoch"], .3, "executing")
    for bad in (
        _result(lease, input_hashes={"prices.csv": "0" * 64}),
        _result(lease, manifest_sha256="0" * 64),
        _result(lease, fencing_epoch=lease["fencing_epoch"] + 1),
        _result(lease, evidence={"kind": "live"}),
        _result(lease, evidence={"kind": "research", "is_live": True}),
        _result(lease, official_candidate=True),
    ):
        with pytest.raises(BacktestQueueError):
            queue.complete(lease["id"], lease["worker_id"], lease["lease_token"], lease["fencing_epoch"], bad)
    baseline = queue.upload_output(lease["id"], "worker.json", b"{}", hashlib.sha256(b"{}").hexdigest(), lease["worker_id"], lease["lease_token"], lease["fencing_epoch"])
    db.execute("UPDATE backtest_jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE id=?", (lease["id"],))
    retry = queue.claim("worker-c", 120)
    assert retry and retry["fencing_epoch"] == lease["fencing_epoch"] + 1
    for action in (
        lambda: queue.heartbeat(lease["id"], lease["worker_id"], lease["lease_token"], lease["fencing_epoch"], .5, "executing"),
        lambda: queue.input_bytes(lease["id"], "prices.csv", lease["worker_id"], lease["lease_token"], lease["fencing_epoch"]),
        lambda: queue.fail(lease["id"], lease["worker_id"], lease["lease_token"], lease["fencing_epoch"], {"error_code": "FAILED", "message": "x", "retryable": False}),
        lambda: queue.upload_output(lease["id"], "worker.json", b"changed", hashlib.sha256(b"changed").hexdigest(), lease["worker_id"], lease["lease_token"], lease["fencing_epoch"]),
    ):
        with pytest.raises(BacktestQueueError) as stale:
            action()
        assert stale.value.status == 409
    assert queue.artifacts.read(baseline["storage_key"]) == b"{}"
    replacement = queue.upload_output(retry["id"], "worker.json", b"changed", hashlib.sha256(b"changed").hexdigest(), "worker-c", retry["lease_token"], retry["fencing_epoch"])
    assert replacement["storage_key"] != baseline["storage_key"]
    final_result = _result(retry, output_hashes={"worker.json": replacement["sha256"]})
    completed = queue.complete(retry["id"], "worker-c", retry["lease_token"], retry["fencing_epoch"], final_result)
    assert completed["status"] == "completed"
    assert queue.complete(retry["id"], "worker-c", retry["lease_token"], retry["fencing_epoch"], final_result)["id"] == retry["id"]


def test_immutable_artifacts_do_not_overwrite_or_expose_inputs(tmp_path):
    queue, _ = _queue(tmp_path)
    job = _job(queue)
    _ready(queue, job)
    lease = queue.claim("worker", 120)
    output = b"{}"
    output_hash = hashlib.sha256(output).hexdigest()
    stored = queue.upload_output(job["id"], "result.json", output, output_hash, "worker", lease["lease_token"], lease["fencing_epoch"])
    assert stored["sha256"] == output_hash
    before = queue.artifacts.read(stored["storage_key"])
    with pytest.raises(BacktestQueueError):
        queue.upload_output(job["id"], "result.json", b"changed", hashlib.sha256(b"changed").hexdigest(), "worker", lease["lease_token"], lease["fencing_epoch"])
    assert queue.artifacts.read(stored["storage_key"]) == before
    queue.complete(job["id"], "worker", lease["lease_token"], lease["fencing_epoch"], _result(lease, output_hashes={"result.json": output_hash}))
    with pytest.raises(BacktestQueueError):
        queue.owner_artifact(job["id"], "prices.csv", 1)
    with pytest.raises(ArtifactError):
        queue.artifacts.write(job["id"], "output", "../escape", b"x", hashlib.sha256(b"x").hexdigest())

    queue.artifacts._path(stored["storage_key"]).write_bytes(b"tampered")
    with pytest.raises(BacktestQueueError, match="完整性"):
        queue.owner_artifact(job["id"], "result.json", 1)


def test_artifact_store_rejects_root_replacement(tmp_path):
    root = tmp_path / "replaceable-root"
    outside = tmp_path / "outside-root"
    store = ArtifactStore(root, max_bytes=2048)
    outside.mkdir()
    root.mkdir()
    root.rmdir()
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(ArtifactError, match="路径越界"):
        store._path("job/input/a0--prices.csv")


def test_artifact_orphan_reconcile_preserves_registered_files_and_removes_aged_debris(tmp_path):
    store = ArtifactStore(tmp_path / "reconcile", max_bytes=2048)
    registered_body = b"registered"
    orphan_body = b"orphan"
    registered_key, _ = store.write(
        "registered-job", "input", "prices.csv", registered_body, hashlib.sha256(registered_body).hexdigest(),
    )
    orphan_key, _ = store.write(
        "orphan-job", "output", "result.json", orphan_body, hashlib.sha256(orphan_body).hexdigest(), 1,
    )
    descriptor, temporary = store.create_temp()
    os.close(descriptor)
    old = 1_000.0
    os.utime(store._path(orphan_key), (old, old))
    os.utime(temporary, (old, old))

    report = store.reconcile_orphans({registered_key}, minimum_age_seconds=60, now=old + 120)

    assert report["removed_count"] == 2
    assert store.read(registered_key, hashlib.sha256(registered_body).hexdigest()) == registered_body
    assert not store._path(orphan_key).exists()
    assert not os.path.exists(temporary)
    assert store.reconcile_orphans(
        {registered_key}, minimum_age_seconds=60, now=old + 120
    ) == {"removed": [], "removed_count": 0}


def test_artifact_disk_full_fails_closed_and_cleans_temporary_file(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "disk-full", max_bytes=2048)

    def disk_full(_descriptor):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("core.backtest_artifacts.os.fsync", disk_full)
    with pytest.raises(ArtifactError, match="空间不足"):
        store.write("disk-job", "input", "prices.csv", b"body", hashlib.sha256(b"body").hexdigest())
    assert list(store.root.glob(".upload-*")) == []


def test_artifact_link_disk_full_fails_closed_without_published_file(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path / "link-disk-full", max_bytes=2048)
    body = b"body"

    def disk_full(_source, _destination):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("core.backtest_artifacts.os.link", disk_full)
    with pytest.raises(ArtifactError, match="空间不足"):
        store.write(
            "disk-job", "input", "prices.csv", body, hashlib.sha256(body).hexdigest()
        )
    assert list(store.root.glob(".upload-*")) == []
    assert not store._path("disk-job/input/a0--prices.csv").exists()


def test_output_count_and_job_byte_quota_are_bounded(tmp_path):
    db = BacktestQueueDatabase(tmp_path / "quota.db")
    queue = BacktestQueue(
        db,
        ArtifactStore(tmp_path / "quota-artifacts", max_bytes=2048),
        max_output_artifacts=1,
        max_job_bytes=1024,
    )
    job = _job(queue)
    _ready(queue, job)
    lease = queue.claim("quota-worker", 120)
    first_body = b"{}"
    first_hash = hashlib.sha256(first_body).hexdigest()
    queue.upload_output(job["id"], "first.json", first_body, first_hash, "quota-worker", lease["lease_token"], lease["fencing_epoch"])
    with pytest.raises(BacktestQueueError, match="数量"):
        queue.upload_output(job["id"], "second.json", b"x", hashlib.sha256(b"x").hexdigest(), "quota-worker", lease["lease_token"], lease["fencing_epoch"])

    byte_db = BacktestQueueDatabase(tmp_path / "byte-quota.db")
    byte_queue = BacktestQueue(
        byte_db,
        ArtifactStore(tmp_path / "byte-quota-artifacts", max_bytes=2048),
        max_output_artifacts=2,
        max_job_bytes=1024,
    )
    byte_job = _job(byte_queue)
    _ready(byte_queue, byte_job)
    byte_lease = byte_queue.claim("byte-worker", 120)
    large = b"x" * (1025 - len(DATA))
    with pytest.raises(BacktestQueueError, match="总大小"):
        byte_queue.upload_output(byte_job["id"], "large.bin", large, hashlib.sha256(large).hexdigest(), "byte-worker", byte_lease["lease_token"], byte_lease["fencing_epoch"])


def test_retryable_failure_requeues_and_cancelled_failure_stays_cancelled(tmp_path):
    queue, db = _queue(tmp_path)
    first = _job(queue)
    _ready(queue, first)
    lease = queue.claim("worker", 120)
    retried = queue.fail(lease["id"], "worker", lease["lease_token"], lease["fencing_epoch"], {"error_code": "TEMPORARY", "message": "Authorization: Bearer must not be stored", "retryable": True})
    assert retried["status"] == "queued"
    attempt = db.fetch_one("SELECT error_json FROM backtest_job_attempts WHERE job_id=? AND attempt_no=1", (first["id"],))
    assert "Bearer" not in attempt["error_json"] and "等待重试" in attempt["error_json"]
    db.execute("UPDATE backtest_jobs SET available_at='2000-01-01T00:00:00Z' WHERE id=?", (first["id"],))
    second = queue.claim("worker-2", 120)
    queue.cancel(second["id"], 1)
    with pytest.raises(BacktestQueueError):
        queue.upload_output(second["id"], "cancelled.json", b"{}", hashlib.sha256(b"{}").hexdigest(), "worker-2", second["lease_token"], second["fencing_epoch"])
    cancelled = queue.fail(second["id"], "worker-2", second["lease_token"], second["fencing_epoch"], {"error_code": "CANCELLED", "message": "operator cancellation", "retryable": False})
    assert cancelled["status"] == "cancelled"


def test_browser_failure_projection_is_owner_scoped_and_sanitized(tmp_path):
    queue, _ = _queue(tmp_path)
    job = _job(queue, "safe-failure")
    _ready(queue, job)
    lease = queue.claim("worker", 60)
    failed = queue.fail(
        job["id"],
        "worker",
        lease["lease_token"],
        lease["fencing_epoch"],
        {
            "error_code": "LOCAL_RUN_FAILED",
            "message": "Bearer secret C:\\private\\traceback.py",
            "retryable": False,
        },
    )

    assert failed["status"] == "failed"
    assert queue.owner_failure(job["id"], 1) == {
        "error_code": "LOCAL_RUN_FAILED",
        "summary": "任务执行失败。",
        "retryable": False,
    }
    with pytest.raises(BacktestQueueError):
        queue.owner_failure(job["id"], 2)


def test_terminal_failure_never_claims_that_an_exhausted_attempt_will_retry(tmp_path):
    queue, db = _queue(tmp_path)
    job = _job(queue, "terminal-retry")
    _ready(queue, job)
    db.execute("UPDATE backtest_jobs SET max_attempts=1 WHERE id=?", (job["id"],))
    lease = queue.claim("worker", 60)
    failed = queue.fail(
        job["id"],
        "worker",
        lease["lease_token"],
        lease["fencing_epoch"],
        {"error_code": "TIMEOUT", "message": "timeout", "retryable": True},
    )

    assert failed["status"] == "failed"
    assert queue.owner_failure(job["id"], 1) == {
        "error_code": "TIMEOUT",
        "summary": "任务执行失败。",
        "retryable": False,
    }


def test_browser_output_metadata_exposes_only_verified_completed_outputs(tmp_path):
    queue, db = _queue(tmp_path)
    job = _job(queue, "safe-outputs")
    _ready(queue, job)
    lease = queue.claim("worker", 60)
    body = b'{"safe":true}'
    digest = hashlib.sha256(body).hexdigest()
    queue.upload_output(
        job["id"], "research-evidence.json", body, digest, "worker",
        lease["lease_token"], lease["fencing_epoch"], media_type="application/json",
    )
    queue.complete(
        job["id"], "worker", lease["lease_token"], lease["fencing_epoch"],
        _result(lease, output_hashes={"research-evidence.json": digest}),
    )

    assert queue.owner_output_metadata(job["id"], 1) == [{
        "artifact_key": "research-evidence.json",
        "sha256": digest,
        "bytes": len(body),
        "verified": True,
    }]
    with pytest.raises(BacktestQueueError):
        queue.owner_output_metadata(job["id"], 2)
    db.execute(
        "UPDATE backtest_job_artifacts SET state='pending' WHERE job_id=? AND direction='output'",
        (job["id"],),
    )
    assert queue.owner_output_metadata(job["id"], 1) == []
