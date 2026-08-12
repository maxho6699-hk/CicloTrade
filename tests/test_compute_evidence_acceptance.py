from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from core.backtest_artifacts import ArtifactStore
from core.backtest_contracts import sha256_json
from core.backtest_queue import BacktestQueue
from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.compute_evidence_contracts import (
    AUTHORITY,
    ComputeEvidenceError,
    canonical_json,
    delivery_signature,
    package_id,
    sha256_bytes,
    validate_package,
)
from src.apps.api.compute_evidence_read_model import ComputeEvidenceReadModel
from src.apps.api.compute_evidence_receiver import (
    ComputeEvidenceReceiver,
    ComputeEvidenceReceiverError,
    build_compute_evidence_receiver,
)
from src.apps.worker.backtest_runtime import BacktestRuntime, ResourceSnapshot, WorkerSettings
from src.apps.worker.compute_evidence_package import build_completed_equity_package
from src.apps.worker.compute_evidence_publisher import (
    ComputeEvidencePublisher,
    ComputeEvidencePublisherSettings,
    PublisherResponse,
    PublisherRetryableTransportError,
    PublisherUncertainTransportError,
    run_compute_evidence_publisher,
)
from src.apps.worker.compute_evidence_spool import PersistentComputeEvidenceSpool
from src.apps.worker.research_canary import _request
from src.apps.worker.research_executor import execute_research


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SECRET = b"e" * 32
SITE_ID = "hk-strategy-worker"
PUBLISHER_ID = "compute-evidence-publisher"


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


class ReceiverTransport:
    def __init__(self, receiver: ComputeEvidenceReceiver) -> None:
        self.receiver = receiver
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, path, headers, body, **_limits) -> PublisherResponse:
        self.calls.append((path, dict(headers), body))
        receipt = self.receiver.accept(body, headers)
        return PublisherResponse(
            201 if receipt["created"] else 200,
            {"content-type": "application/json"},
            canonical_json(receipt),
        )


class ScriptedTransport:
    def __init__(self, outcome) -> None:
        self.outcome = outcome

    def post(self, *_args, **_kwargs) -> PublisherResponse:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def package_fixture(job_id: str = "candidate-job-0001") -> dict:
    frozen, manifest, inputs = _request(500, minimum_coverage_days=252, minimum_trades=30)
    local = execute_research(manifest, inputs)
    local_body = canonical_json(local)
    output_hash = sha256_bytes(local_body)
    manifest_hash = sha256_json(manifest)
    result = {
        "job_id": job_id,
        "manifest_sha256": manifest_hash,
        "code_bundle_sha256": manifest["code_bundle_sha256"],
        "fencing_epoch": 1,
        "input_hashes": {item["artifact_key"]: item["sha256"] for item in manifest["inputs"]},
        "output_hashes": {"research-evidence.json": output_hash},
        "evidence": {
            "kind": "research",
            "hashes": {"research-evidence.json": output_hash},
            "authority": manifest["authority"],
            "data_contract": {"research_proxy": False, "actionable": False},
            "validation": local["validation"],
            "risk": local["risk"],
        },
    }
    result_hash = sha256_json(result)
    artifacts = [
        {
            "direction": "input",
            "artifact_key": item["artifact_key"],
            "attempt_no": 0,
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "row_count": item["rows"],
            "media_type": "application/json" if item["artifact_key"].endswith(".json") else "text/csv",
        }
        for item in manifest["inputs"]
    ]
    artifacts.append(
        {
            "direction": "output",
            "artifact_key": "research-evidence.json",
            "attempt_no": 1,
            "sha256": output_hash,
            "bytes": len(local_body),
            "row_count": None,
            "media_type": "application/json",
        }
    )
    return validate_package(
        {
            "schema_version": 1,
            "kind": "compute.equity-shadow.package.v1",
            "package_id": package_id(job_id, manifest_hash, result_hash),
            "site_id": SITE_ID,
            "worker_id": "strategy-worker",
            "job_id": job_id,
            "job_type": "candidate.evaluate.v1",
            "attempt_no": 1,
            "fencing_epoch": 1,
            "completed_at": "2026-08-12T11:59:00Z",
            "manifest_sha256": manifest_hash,
            "result_sha256": result_hash,
            "manifest": manifest,
            "result": result,
            "artifacts": artifacts,
            "authority": dict(AUTHORITY),
        }
    )


def receiver(tmp_path: Path, clock: Clock) -> tuple[ComputeEvidenceReceiver, BacktestQueueDatabase]:
    database = BacktestQueueDatabase(tmp_path / "receiver.db")
    service = ComputeEvidenceReceiver(
        database,
        shared_secret=SECRET,
        site_id=SITE_ID,
        publisher_id=PUBLISHER_ID,
        enabled=True,
        clock=clock,
    )
    return service, database


def delivery(
    package: dict,
    tmp_path: Path,
    clock: Clock,
    *,
    epoch: int = 1,
    nonce: str = "n" * 43,
) -> tuple[bytes, dict[str, str]]:
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "signing.db"), clock=clock)
    spool.enqueue(package)
    claim = spool.claim(PUBLISHER_ID)
    assert claim
    if epoch != claim["fencing_epoch"]:
        with spool.database.transaction() as connection:
            connection.execute(
                "UPDATE compute_evidence_spool SET fencing_epoch=? WHERE id=?",
                (epoch, claim["id"]),
            )
        claim["fencing_epoch"] = epoch
    headers = spool.signed_headers(
        claim,
        SECRET,
        nonce=nonce,
        expires_at=clock() + timedelta(minutes=2),
    )
    return canonical_json(package), headers


def test_contract_rejects_artifact_tamper_and_non_equity_proxy_or_wrong_site():
    value = package_fixture()
    for field, replacement in (
        ("bytes", 1),
        ("row_count", 1),
        ("attempt_no", 2),
        ("sha256", "0" * 64),
    ):
        changed = deepcopy(value)
        changed["artifacts"][0][field] = replacement
        with pytest.raises(ComputeEvidenceError):
            validate_package(changed)

    wrong_site = deepcopy(value)
    wrong_site["site_id"] = "other-site"
    with pytest.raises(ComputeEvidenceError, match="non-proxy"):
        validate_package(wrong_site)

    proxy = deepcopy(value)
    proxy["manifest"]["asset_universe"]["research_proxy"] = True
    proxy["manifest"]["authority"]["origin_site"] = SITE_ID
    proxy["result"]["evidence"]["authority"] = proxy["manifest"]["authority"]
    proxy["result"]["evidence"]["data_contract"]["research_proxy"] = True
    _rebind(proxy)
    with pytest.raises(ComputeEvidenceError):
        validate_package(proxy)

    option = deepcopy(value)
    option["manifest"]["asset_universe"]["instrument_family"] = "option"
    _rebind(option)
    with pytest.raises(ComputeEvidenceError):
        validate_package(option)

    rejected = deepcopy(value)
    rejected["result"]["evidence"]["validation"]["candidate_status"] = "rejected"
    _rebind(rejected)
    with pytest.raises(ComputeEvidenceError, match="shadow"):
        validate_package(rejected)


def test_contract_accepts_legacy_frozen_input_without_optional_size_metadata():
    value = package_fixture()
    for item in value["manifest"]["inputs"]:
        item.pop("bytes", None)
        item.pop("rows", None)
    _rebind(value)
    accepted = validate_package(value)
    assert accepted["manifest"]["inputs"]
    assert all("bytes" not in item and "rows" not in item for item in accepted["manifest"]["inputs"])


def test_package_builder_accepts_completed_system_shadow_and_detects_artifact_tamper(tmp_path):
    queue = _completed_queue(tmp_path)
    job_id = queue.db.fetch_one("SELECT id FROM backtest_jobs")["id"]
    built = build_completed_equity_package(queue, job_id, site_id=SITE_ID)
    assert built["job_id"] == job_id and built["authority"] == AUTHORITY

    output = queue.db.fetch_one("SELECT storage_key FROM backtest_job_artifacts WHERE direction='output'")
    path = queue.artifacts._path(output["storage_key"])
    path.write_bytes(b"tampered")
    with pytest.raises(ComputeEvidenceError, match="integrity"):
        build_completed_equity_package(queue, job_id, site_id=SITE_ID)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE backtest_jobs SET status='failed'",
        "UPDATE backtest_jobs SET owner_scope='user',owner_id=7",
    ],
)
def test_package_builder_rejects_uncompleted_or_user_owned_jobs(tmp_path, sql):
    queue = _completed_queue(tmp_path)
    queue.db.execute(sql)
    job_id = queue.db.fetch_one("SELECT id FROM backtest_jobs")["id"]
    with pytest.raises(ComputeEvidenceError, match="does not exist"):
        build_completed_equity_package(queue, job_id, site_id=SITE_ID)


def test_receiver_is_atomic_quarantine_only_and_rejects_replay_stale_and_body_revision(tmp_path):
    clock = Clock()
    service, database = receiver(tmp_path, clock)
    first_package = package_fixture("candidate-job-0001")
    body, headers = delivery(first_package, tmp_path / "first", clock, epoch=2)

    first = service.accept(body, headers)
    assert first["created"] is True and first["publication_state"] == "quarantine"
    assert first["research_only"] is True
    assert first["actionable"] is first["user_visible"] is False
    with pytest.raises(ComputeEvidenceReceiverError, match="replayed") as replay:
        service.accept(body, headers)
    assert replay.value.status == 409

    retry_body, retry_headers = delivery(
        first_package,
        tmp_path / "retry",
        clock,
        epoch=2,
        nonce="r" * 43,
    )
    assert service.accept(retry_body, retry_headers)["created"] is False

    alternate_headers = dict(retry_headers)
    alternate_headers["idempotency-key"] = "alternate-package-key"
    alternate_headers["x-ciclotrade-nonce"] = "a" * 43
    alternate_headers["x-ciclotrade-evidence-signature"] = delivery_signature(
        SECRET,
        site_id=SITE_ID,
        publisher_id=PUBLISHER_ID,
        source_worker_id=first_package["worker_id"],
        fencing_epoch=2,
        idempotency_key="alternate-package-key",
        nonce="a" * 43,
        expires_at=alternate_headers["x-ciclotrade-expires-at"],
        package_sha256=alternate_headers["x-ciclotrade-package-sha256"],
    )
    with pytest.raises(ComputeEvidenceReceiverError, match="not authorized") as alternate:
        service.accept(retry_body, alternate_headers)
    assert alternate.value.status == 401

    older = package_fixture("candidate-job-0002")
    old_body, old_headers = delivery(
        older,
        tmp_path / "old",
        clock,
        epoch=1,
        nonce="o" * 43,
    )
    with pytest.raises(ComputeEvidenceReceiverError, match="stale") as stale:
        service.accept(old_body, old_headers)
    assert stale.value.status == 409

    changed = deepcopy(first_package)
    output = next(item for item in changed["artifacts"] if item["direction"] == "output")
    output["media_type"] = "application/vnd.tradeai.evidence+json"
    changed = validate_package(changed)
    changed_body, changed_headers = delivery(
        changed,
        tmp_path / "changed",
        clock,
        epoch=2,
        nonce="c" * 43,
    )
    with pytest.raises(ComputeEvidenceReceiverError, match="identity changed") as conflict:
        service.accept(changed_body, changed_headers)
    assert conflict.value.status == 409

    assert database.fetch_one("SELECT count(*) AS total FROM compute_evidence_receipts")["total"] == 1
    assert database.fetch_one("SELECT count(*) AS total FROM compute_evidence_receiver_nonces")["total"] == 2


def test_receiver_rejects_expired_signature_and_builder_is_disabled_and_isolated(tmp_path):
    clock = Clock()
    service, _ = receiver(tmp_path, clock)
    value = package_fixture()
    body, headers = delivery(value, tmp_path / "expired", clock)
    clock.value += timedelta(minutes=3)
    with pytest.raises(ComputeEvidenceReceiverError, match="expired") as expired:
        service.accept(body, headers)
    assert expired.value.status == 401
    assert build_compute_evidence_receiver({}) is None

    shared = str((tmp_path / "shared.db").resolve())
    env = {
        "TRADEAI_COMPUTE_EVIDENCE_RECEIVER_ENABLED": "true",
        "TRADEAI_COMPUTE_EVIDENCE_RECEIVER_DATABASE": shared,
        "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET": "e" * 32,
        "TRADEAI_COMPUTE_EVIDENCE_SITE_ID": SITE_ID,
        "TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID": PUBLISHER_ID,
        "DATABASE_URL": f"sqlite:///{shared}",
    }
    with pytest.raises(RuntimeError, match="isolated"):
        build_compute_evidence_receiver(env)


def test_spool_skips_future_retry_head_and_sending_can_only_become_uncertain(tmp_path):
    clock = Clock()
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "spool.db"), clock=clock)
    first, _ = spool.enqueue(package_fixture("candidate-job-0001"))
    second, _ = spool.enqueue(package_fixture("candidate-job-0002"))
    first_claim = spool.claim(PUBLISHER_ID, lease_seconds=30)
    assert first_claim and first_claim["id"] == first["id"]
    spool.fail(
        first_claim["id"],
        PUBLISHER_ID,
        first_claim["lease_token"],
        first_claim["fencing_epoch"],
        error="safe pre-request failure",
        retry_delay_seconds=300,
    )
    second_claim = spool.claim(PUBLISHER_ID, lease_seconds=30)
    assert second_claim and second_claim["id"] == second["id"]
    spool.begin_delivery(*_lease(second_claim))
    assert spool.claim(PUBLISHER_ID, lease_seconds=30) is None
    clock.value += timedelta(seconds=31)
    assert spool.quarantine_expired_deliveries() == 1
    assert (
        spool.database.fetch_one("SELECT state FROM compute_evidence_spool WHERE id=?", (second["id"],))["state"]
        == "uncertain"
    )


def test_publisher_round_trip_retryable_and_uncertain_semantics(tmp_path):
    clock = Clock()
    receiver_service, receiver_db = receiver(tmp_path / "roundtrip", clock)
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "roundtrip-spool.db"), clock=clock)
    spool.enqueue(package_fixture())
    settings = _settings(tmp_path)
    service = ComputeEvidencePublisher(spool, settings, ReceiverTransport(receiver_service), clock=clock)
    outcome = service.run_once()
    assert outcome["state"] == "delivered"
    assert receiver_db.fetch_one("SELECT publication_state FROM compute_evidence_receipts") == {
        "publication_state": "quarantine"
    }

    retry_spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "retry-spool.db"), clock=clock)
    retry_spool.enqueue(package_fixture("candidate-job-retry"))
    retry = ComputeEvidencePublisher(
        retry_spool,
        settings,
        ScriptedTransport(PublisherRetryableTransportError("connect failed")),
        clock=clock,
    ).run_once()
    assert retry["state"] == "retryable"

    uncertain_spool = PersistentComputeEvidenceSpool(
        BacktestQueueDatabase(tmp_path / "uncertain-spool.db"), clock=clock
    )
    uncertain_spool.enqueue(package_fixture("candidate-job-uncertain"))
    uncertain = ComputeEvidencePublisher(
        uncertain_spool,
        settings,
        ScriptedTransport(PublisherUncertainTransportError("response timed out", status=503)),
        clock=clock,
    ).run_once()
    assert uncertain["state"] == "uncertain" and uncertain["http_status"] == 503
    assert uncertain_spool.claim(PUBLISHER_ID) is None
    assert run_compute_evidence_publisher(env={}) == {
        "state": "disabled",
        "origin": "https://ciclotrade.com",
    }


def test_read_model_is_sanitized_and_migration_is_append_only_and_product_isolated(tmp_path):
    clock = Clock()
    service, database = receiver(tmp_path, clock)
    value = package_fixture()
    body, headers = delivery(value, tmp_path / "delivery", clock)
    service.accept(body, headers)

    model = ComputeEvidenceReadModel(database)
    status, latest, history = model.status(), model.latest(), model.history(10)
    assert status["counts"] == {"quarantine": 1, "shadow": 0}
    assert latest["evidence"]["candidate_status"] == "shadow"
    assert history["items"][0]["publication_state"] == "quarantine"
    serialized = json.dumps({"status": status, "latest": latest, "history": history})
    assert "payload_json" not in serialized and "metrics" not in serialized
    assert all(item["research_only"] is True for item in history["items"])
    assert all(item["actionable"] is item["user_visible"] is False for item in history["items"])

    tables = {row["name"] for row in database.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    forbidden = {"recommendations", "orders", "notifications", "telegram_events", "quant_events"}
    assert not (forbidden & tables)
    assert not any(name.startswith(("official_", "live_")) for name in tables)
    with database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE compute_evidence_receipts SET publication_state='shadow'")


def _completed_queue(tmp_path: Path) -> BacktestQueue:
    frozen, manifest, inputs = _request(500, minimum_coverage_days=252, minimum_trades=30)
    queue = BacktestQueue(
        BacktestQueueDatabase(tmp_path / "queue.db"),
        ArtifactStore(tmp_path / "artifacts"),
    )
    job, _ = queue.enqueue(
        None,
        {"type": "candidate.evaluate.v1", "manifest": manifest},
        idempotency_scope="system:compute-evidence-test",
        idempotency_key="compute-evidence-test-job",
        internal=True,
    )
    descriptors = {item["artifact_key"]: item for item in manifest["inputs"]}
    for key, body in inputs.items():
        queue.register_input(
            job["id"],
            key,
            body,
            descriptors[key]["sha256"],
            row_count=descriptors[key]["rows"],
            media_type="application/json" if key.endswith(".json") else "text/csv",
        )

    class HealthyProbe:
        @staticmethod
        def snapshot() -> ResourceSnapshot:
            return ResourceSnapshot(0.0, 0.0)

    outcome = BacktestRuntime(
        queue,
        WorkerSettings(tmp_path / "queue.db", tmp_path / "artifacts", hard_timeout_seconds=10),
        resource_probe=HealthyProbe(),
    ).run_once()
    assert outcome.state == "completed" and frozen.row_count == 500
    return queue


def _rebind(value: dict) -> None:
    value["manifest_sha256"] = sha256_json(value["manifest"])
    value["result"]["manifest_sha256"] = value["manifest_sha256"]
    value["result_sha256"] = sha256_json(value["result"])
    value["package_id"] = package_id(value["job_id"], value["manifest_sha256"], value["result_sha256"])


def _lease(claim: dict) -> tuple[int, str, str, int]:
    return (
        int(claim["id"]),
        PUBLISHER_ID,
        str(claim["lease_token"]),
        int(claim["fencing_epoch"]),
    )


def _settings(tmp_path: Path) -> ComputeEvidencePublisherSettings:
    return ComputeEvidencePublisherSettings(
        enabled=True,
        database_path=tmp_path / "spool.db",
        shared_secret=SECRET,
        publisher_id=PUBLISHER_ID,
        connect_timeout_seconds=2,
        total_timeout_seconds=5,
        max_response_bytes=64 * 1024,
        lease_seconds=30,
        delivery_expiry_seconds=120,
        max_retry_after_seconds=3_600,
    )
