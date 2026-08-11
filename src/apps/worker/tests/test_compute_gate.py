from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path

import pytest

from core.backtest_artifacts import ArtifactStore
from core.backtest_contracts import BacktestQueueError, sha256_json
from core.backtest_queue import BacktestQueue
from core.backtest_queue_database import BacktestQueueDatabase
from src.apps.worker import compute_gate_cli as compute_gate_cli_module
from src.apps.worker.backtest_runtime import BacktestRuntime, ResourceSnapshot
from src.apps.worker.compute_gate import ComputeGate, ComputeGateError, ComputeGateSettings, main


NOW = datetime(2026, 1, 2, 18, 0, tzinfo=timezone.utc)  # 02:00 Asia/Hong_Kong


class Probe:
    def __init__(self, cpu: float = 10.0, memory: float = 20.0):
        self.value = ResourceSnapshot(cpu, memory)

    def snapshot(self) -> ResourceSnapshot:
        return self.value


class Disk:
    def __init__(self, free: int):
        self.free = free


def raw_prices(*, symbol: str = "AAPL", rows: int = 260, close_shift: float = 0.0) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow((
        "symbol",
        "session_date",
        "session_open_at",
        "session_close_at",
        "available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ))
    start, price = date(2025, 1, 2), 100.0
    for offset in range(rows):
        session = start + timedelta(days=offset)
        open_ = price
        close = open_ * (1 + ((offset % 9) - 4) * 0.001) + close_shift
        writer.writerow((
            symbol,
            session.isoformat(),
            f"{session.isoformat()}T14:30:00Z",
            f"{session.isoformat()}T21:00:00Z",
            f"{session.isoformat()}T21:00:00Z",
            f"{open_:.8f}",
            f"{max(open_, close) * 1.01:.8f}",
            f"{min(open_, close) * .99:.8f}",
            f"{close:.8f}",
            str(1_000_000 + offset),
        ))
        price = close
    return target.getvalue().encode("utf-8")


def request(source_file: str = "aapl.csv", *, source_body: bytes | None = None, **overrides) -> dict:
    body = raw_prices() if source_body is None else source_body
    value = {
        "schema_version": 1,
        "request_id": "aapl-trend-20260101",
        "symbol": "AAPL",
        "evaluation_date": "2026-01-01",
        "template_key": "equity.trend.long_flat.v1",
        "source_file": source_file,
        "source_sha256": hashlib.sha256(body).hexdigest(),
        "source_bytes": len(body),
    }
    value.update(overrides)
    return value


def settings(tmp_path: Path, **overrides) -> ComputeGateSettings:
    drop = tmp_path / "inbox"
    drop.mkdir()
    values = {
        "drop_dir": drop,
        "queue_db": tmp_path / "queue.db",
        "artifact_dir": tmp_path / "artifacts",
        "allowed_symbols": frozenset({"AAPL"}),
        "minimum_free_bytes": 1,
        "max_requests_per_run": 4,
        "max_daily_jobs": 8,
        "max_pending_jobs": 8,
    }
    values.update(overrides)
    return ComputeGateSettings(**values)


def queue_for(config: ComputeGateSettings) -> BacktestQueue:
    return BacktestQueue(BacktestQueueDatabase(config.queue_db), ArtifactStore(config.artifact_dir))


def test_environment_binds_worker_timeout_and_rejects_publish_flags(tmp_path):
    environment = {
        "TRADEAI_COMPUTE_DROP_DIR": str(tmp_path / "inbox"),
        "TRADEAI_STRATEGY_WORKER_QUEUE_DB": str(tmp_path / "queue.db"),
        "TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "TRADEAI_COMPUTE_ALLOWED_SYMBOLS": "AAPL",
        "TRADEAI_STRATEGY_WORKER_LEASE_SECONDS": "45",
        "TRADEAI_STRATEGY_WORKER_HARD_TIMEOUT_SECONDS": "123",
    }

    worker = ComputeGateSettings.from_environment(environment).worker_settings()

    assert worker.lease_seconds == 45
    assert worker.hard_timeout_seconds == 123
    with pytest.raises(ComputeGateError):
        ComputeGateSettings.from_environment(
            {**environment, "TRADEAI_STRATEGY_WORKER_OUTBOUND_PUBLISH_ENABLED": "true"}
        )


def write_drop(config: ComputeGateSettings, name: str = "request.json", *, payload: dict | None = None, body: bytes | None = None) -> None:
    source = body if body is not None else raw_prices()
    (config.drop_dir / "aapl.csv").write_bytes(source)
    (config.drop_dir / name).write_text(json.dumps(payload or request(source_body=source)), encoding="utf-8")


def gate(
    config: ComputeGateSettings,
    queue: BacktestQueue,
    *,
    probe: Probe | None = None,
    free: int = 10**12,
    now: datetime = NOW,
) -> ComputeGate:
    return ComputeGate(
        queue,
        config,
        resource_probe=probe or Probe(),
        disk_probe=lambda _: Disk(free),
        clock=lambda: now,
    )


def test_controlled_drop_freezes_and_enqueues_one_idempotent_system_candidate(tmp_path):
    config = settings(tmp_path)
    write_drop(config)
    queue = queue_for(config)

    first = gate(config, queue).run_once()
    assert not (config.drop_dir / "request.json").exists()
    assert len(list((config.drop_dir / ".processed").glob("*.json"))) == 1
    (config.drop_dir / "request.json").write_text(json.dumps(request()), encoding="utf-8")
    second = gate(config, queue, now=NOW + timedelta(minutes=15)).run_once()

    assert first["state"] == "produced" and first["created"] == 1
    assert second["state"] == "reused" and second["created"] == 0
    assert first["job_ids"] == second["job_ids"]
    job = queue.get(first["job_ids"][0])
    assert job["owner_scope"] == "system"
    assert job["job_type"] == "candidate.evaluate.v1"
    assert job["max_attempts"] == 1
    assert job["manifest"]["provenance"] == {
        "source": "approved_seed",
        "generated_by": "compute-gate",
        "request_id": "aapl-trend-20260101",
        "request_sha256": sha256_json(request()),
    }
    assert job["manifest"]["authority"] == {
        "origin_site": "hk-strategy-worker",
        "deployment_role": "strategy_worker",
        "publication_ceiling": "shadow",
        "outbound_publish_enabled": False,
        "user_visible": False,
        "execution_eligible": False,
        "recommendations_published": False,
    }
    declared = {item["artifact_key"]: item for item in job["manifest"]["inputs"]}
    stored = queue.db.fetch_all(
        "SELECT artifact_key,sha256,bytes,row_count,state FROM backtest_job_artifacts WHERE job_id=? AND direction='input'",
        (job["id"],),
    )
    assert {item["artifact_key"] for item in stored} == {"source.csv", "source-snapshot.json", "prices.csv"}
    for item in stored:
        descriptor = declared[item["artifact_key"]]
        assert item == {
            "artifact_key": item["artifact_key"],
            "sha256": descriptor["sha256"],
            "bytes": descriptor["bytes"],
            "row_count": descriptor["rows"],
            "state": "verified",
        }
    snapshot = queue.db.fetch_one("SELECT snapshot_id,prices_sha256,canonical_rows FROM backtest_source_snapshots")
    assert snapshot["prices_sha256"] == declared["prices.csv"]["sha256"]
    assert snapshot["canonical_rows"] == 260
    assert len(snapshot["snapshot_id"]) == 64
    assert queue.db.fetch_one("SELECT count(*) AS total FROM backtest_source_snapshots")["total"] == 1


def test_restart_after_enqueue_before_input_registration_reuses_job_and_completes(tmp_path, monkeypatch):
    config = settings(tmp_path)
    write_drop(config)
    queue = queue_for(config)
    original = queue.register_input

    monkeypatch.setattr(queue, "register_input", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        gate(config, queue).run_once()
    pending = queue.db.fetch_one("SELECT id,status FROM backtest_jobs")
    assert pending and pending["status"] == "queued"
    assert queue.db.fetch_one("SELECT id FROM backtest_job_artifacts") is None

    monkeypatch.setattr(queue, "register_input", original)
    recovered = gate(config, queue).run_once()
    assert recovered["state"] == "reused"

    runtime = BacktestRuntime(
        queue,
        config.worker_settings(),
        resource_probe=Probe(),
    )
    outcome = runtime.run_once()
    assert outcome.state == "completed"
    assert queue.get(pending["id"])["status"] == "completed"


@pytest.mark.parametrize(
    ("now", "probe", "free", "state"),
    [
        (datetime(2026, 1, 2, 8, 0, tzinfo=timezone.utc), Probe(), 10**12, "outside_window"),
        (NOW, Probe(61.0, 20.0), 10**12, "resource_gated"),
        (NOW, Probe(20.0, 76.0), 10**12, "resource_gated"),
        (NOW, Probe(), 0, "disk_gated"),
    ],
)
def test_schedule_resource_and_disk_gates_fail_before_enqueue(tmp_path, now, probe, free, state):
    config = settings(tmp_path)
    write_drop(config)
    queue = queue_for(config)
    service = ComputeGate(queue, config, resource_probe=probe, disk_probe=lambda _: Disk(free), clock=lambda: now)

    assert service.run_once()["state"] == state
    assert queue.db.fetch_one("SELECT id FROM backtest_jobs") is None


def test_execution_gate_rechecks_the_fresh_offpeak_window_after_import(tmp_path):
    config = settings(tmp_path)
    write_drop(config)
    queue = queue_for(config)
    moments = iter((NOW, datetime(2026, 1, 3, 8, 0, tzinfo=timezone.utc)))
    service = ComputeGate(
        queue,
        config,
        resource_probe=Probe(),
        disk_probe=lambda _: Disk(10**12),
        clock=lambda: next(moments),
    )

    assert service.run_once()["state"] == "produced"
    assert service.execution_gate_state() == "outside_window"


def test_execution_gate_rechecks_resource_and_disk_drift_after_import(tmp_path):
    resource_root = tmp_path / "resource"
    resource_root.mkdir()
    resource_config = settings(resource_root)
    write_drop(resource_config)
    resource_queue = queue_for(resource_config)
    probe = Probe()
    resource_service = ComputeGate(
        resource_queue,
        resource_config,
        resource_probe=probe,
        disk_probe=lambda _: Disk(10**12),
        clock=lambda: NOW,
    )
    assert resource_service.run_once()["state"] == "produced"
    probe.value = ResourceSnapshot(61.0, 20.0)
    assert resource_service.execution_gate_state() == "resource_gated"

    disk_root = tmp_path / "disk"
    disk_root.mkdir()
    disk_config = settings(disk_root)
    write_drop(disk_config)
    disk_queue = queue_for(disk_config)
    free = [10**12]
    disk_service = ComputeGate(
        disk_queue,
        disk_config,
        resource_probe=Probe(),
        disk_probe=lambda _: Disk(free[0]),
        clock=lambda: NOW,
    )
    assert disk_service.run_once()["state"] == "produced"
    free[0] = 0
    assert disk_service.execution_gate_state() == "disk_gated"


def test_daily_and_pending_budget_are_atomic_but_existing_request_can_recover(tmp_path):
    config = settings(tmp_path, max_daily_jobs=1, max_pending_jobs=1)
    write_drop(config, "a.json")
    (config.drop_dir / "b.json").write_text(json.dumps(request(request_id="aapl-mean-20260101", template_key="equity.mean_reversion.long_flat.v1")), encoding="utf-8")
    queue = queue_for(config)

    result = gate(config, queue).run_once()

    assert result["state"] == "budget_gated"
    assert result["created"] == 1
    assert queue.db.fetch_one("SELECT count(*) AS count FROM backtest_jobs")["count"] == 1
    (config.drop_dir / "b.json").unlink()
    (config.drop_dir / "a.json").write_text(json.dumps(request()), encoding="utf-8")
    assert gate(config, queue).run_once()["state"] == "reused"


def test_one_request_budget_archives_success_and_allows_the_next_request(tmp_path):
    config = settings(tmp_path, max_requests_per_run=1)
    write_drop(config, "a.json")
    (config.drop_dir / "b.json").write_text(
        json.dumps(
            request(
                request_id="aapl-mean-20260101",
                template_key="equity.mean_reversion.long_flat.v1",
            )
        ),
        encoding="utf-8",
    )
    queue = queue_for(config)

    first = gate(config, queue).run_once()
    second = gate(config, queue).run_once()

    assert first["state"] == second["state"] == "produced"
    assert first["request_ids"] == ["aapl-trend-20260101"]
    assert second["request_ids"] == ["aapl-mean-20260101"]
    assert queue.db.fetch_one("SELECT count(*) AS total FROM backtest_jobs")["total"] == 2
    assert not list(config.drop_dir.glob("*.json"))


@pytest.mark.parametrize(
    "payload",
    [
        request("../outside.csv"),
        request(symbol="MSFT"),
        request(evaluation_date="2026-01-03"),
        request(template_key="option.long_call.v1"),
        {**request(), "unknown": True},
    ],
)
def test_import_contract_rejects_traversal_unknown_symbols_future_dates_and_templates(tmp_path, payload):
    config = settings(tmp_path)
    write_drop(config, payload=payload)
    queue = queue_for(config)

    with pytest.raises(ComputeGateError):
        gate(config, queue).run_once()
    assert queue.db.fetch_one("SELECT id FROM backtest_jobs") is None


def test_reused_request_id_with_changed_source_snapshot_is_a_hard_conflict(tmp_path):
    config = settings(tmp_path)
    write_drop(config)
    queue = queue_for(config)
    first = gate(config, queue).run_once()
    changed = raw_prices(close_shift=0.01)
    (config.drop_dir / "aapl.csv").write_bytes(changed)
    (config.drop_dir / "request.json").write_text(
        json.dumps(request(source_body=changed)), encoding="utf-8"
    )

    with pytest.raises(BacktestQueueError) as conflict:
        gate(config, queue).run_once()

    assert conflict.value.status == 409
    assert len(first["job_ids"]) == 1
    assert queue.db.fetch_one("SELECT count(*) AS total FROM backtest_jobs")["total"] == 1
    assert queue.db.fetch_one("SELECT count(*) AS total FROM backtest_source_snapshots")["total"] == 2


def test_request_checksum_and_size_must_match_the_opened_source(tmp_path):
    config = settings(tmp_path)
    write_drop(config, payload=request(source_sha256="0" * 64))
    queue = queue_for(config)

    with pytest.raises(ComputeGateError, match="checksum and size"):
        gate(config, queue).run_once()

    assert queue.db.fetch_one("SELECT id FROM backtest_jobs") is None


@pytest.mark.parametrize("incident", ["tampered", "missing"])
def test_registered_artifact_integrity_incident_gates_compute_before_new_enqueue(
    tmp_path, incident
):
    config = settings(tmp_path)
    write_drop(config)
    queue = queue_for(config)
    first = gate(config, queue).run_once()
    artifact = queue.db.fetch_one(
        "SELECT storage_key FROM backtest_job_artifacts WHERE job_id=? ORDER BY artifact_key LIMIT 1",
        (first["job_ids"][0],),
    )
    artifact_path = queue.artifacts._path(artifact["storage_key"])
    if incident == "tampered":
        artifact_path.write_bytes(b"tampered")
    else:
        artifact_path.unlink()

    result = gate(config, queue).run_once()

    assert result["state"] == "artifact_integrity_gated"
    assert result["artifact_audit"]["mismatched" if incident == "tampered" else "missing"] == [
        artifact["storage_key"]
    ]
    assert queue.db.fetch_one("SELECT count(*) AS total FROM backtest_jobs")["total"] == 1


def test_execute_one_never_runs_when_artifact_integrity_is_gated(tmp_path, monkeypatch):
    config = settings(tmp_path)
    queue = queue_for(config)
    monkeypatch.setattr(ComputeGateSettings, "from_environment", staticmethod(lambda: config))
    monkeypatch.setattr(compute_gate_cli_module, "build_local_queue", lambda _: queue)
    monkeypatch.setattr(
        ComputeGate,
        "run_once",
        lambda self: {"state": "produced", "publication": "disabled"},
    )
    monkeypatch.setattr(ComputeGate, "execution_gate_state", lambda self: "artifact_integrity_gated")

    class ForbiddenRuntime:
        def __init__(self, *args, **kwargs):
            raise AssertionError("runtime must not start after an artifact integrity gate")

    monkeypatch.setattr(compute_gate_cli_module, "BacktestRuntime", ForbiddenRuntime)

    assert main(["--once", "--execute-one"]) == 0
