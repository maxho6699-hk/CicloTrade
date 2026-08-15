from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import signal
import sys

import pytest

from src.apps.worker.backtest_runtime import (
    BacktestRuntime,
    BoundedChildProcess,
    MAX_EXECUTOR_OUTPUT_BYTES,
    MAX_EXECUTOR_RAW_INPUT_BYTES,
    MAX_EXECUTOR_REQUEST_BYTES,
    PublishDenied,
    ResourceSnapshot,
    RunOutcome,
    WorkerSettings,
    WorkerExecutionError,
    WorkerTimedOut,
    _executor_payload,
    _completion_evidence,
    build_local_queue,
)
from src.apps.worker.research_canary import _request
from src.apps.worker.research_executor import execute_research


_, CANARY_MANIFEST, CANARY_INPUTS = _request(96, minimum_coverage_days=252)


def settings(tmp_path: Path, **overrides: object) -> WorkerSettings:
    values: dict[str, object] = {
        "queue_db": tmp_path / "queue.db",
        "artifact_dir": tmp_path / "artifacts",
        "poll_seconds": 0.01,
        "hard_timeout_seconds": 0.2,
    }
    values.update(overrides)
    return WorkerSettings(**values)  # type: ignore[arg-type]


def job() -> dict:
    return {
        "id": "backtest-1",
        "job_type": "candidate.evaluate.v1",
        "lease_token": "lease-token",
        "fencing_epoch": 3,
        "manifest_sha256": "a" * 64,
        "manifest": CANARY_MANIFEST,
    }


def test_build_local_queue_leaves_fresh_artifact_root_for_durable_creation(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifact-parent" / "artifacts"
    fsync_calls: list[Path] = []
    monkeypatch.setattr(
        "core.backtest_artifacts.ArtifactStore._fsync_directory",
        staticmethod(lambda path: fsync_calls.append(Path(path))),
    )

    queue = build_local_queue(settings(tmp_path, artifact_dir=artifact_dir))

    assert not artifact_dir.exists()
    descriptor, temporary = queue.artifacts.create_temp()
    os.close(descriptor)
    Path(temporary).unlink()
    assert artifact_dir in fsync_calls
    assert artifact_dir.parent in fsync_calls


class Probe:
    def __init__(self, cpu: float, memory: float) -> None:
        self.value = ResourceSnapshot(cpu, memory)

    def snapshot(self) -> ResourceSnapshot:
        return self.value


class Queue:
    def __init__(self, jobs: list[dict] | None = None, *, cancel_on_heartbeat: bool = False) -> None:
        self.jobs = list(jobs or [])
        self.cancel_on_heartbeat = cancel_on_heartbeat
        self.claim_count = 0
        self.completed: list[dict] = []
        self.failed: list[dict] = []
        self.uploaded: list[tuple[str, bytes]] = []

    def claim(self, worker_id: str, lease_seconds: int = 60) -> dict | None:
        self.claim_count += 1
        return self.jobs.pop(0) if self.jobs else None

    def heartbeat(self, *args: object) -> dict:
        return {"cancel_requested": self.cancel_on_heartbeat}

    def input_bytes(self, *args: object) -> bytes:
        return CANARY_INPUTS[str(args[1])]

    def upload_output(self, job_id: str, artifact_key: str, body: bytes, *args: object, **kwargs: object) -> dict:
        self.uploaded.append((artifact_key, body))
        return {"artifact_key": artifact_key}

    def complete(self, *args: object) -> dict:
        self.completed.append(args[-1])
        return {"status": "completed"}

    def fail(self, *args: object) -> dict:
        self.failed.append(args[-1])
        return {"status": "failed"}


class ImmediateExecutor:
    def run(self, payload: bytes, **kwargs: object) -> bytes:
        value = json.loads(payload)
        inputs = {key: base64.b64decode(body) for key, body in value["inputs"].items()}
        return json.dumps(execute_research(value["manifest"], inputs), sort_keys=True).encode()


class WrongBundleExecutor(ImmediateExecutor):
    def run(self, payload: bytes, **kwargs: object) -> bytes:
        value = json.loads(super().run(payload, **kwargs))
        value["code_bundle_sha256"] = "0" * 64
        return json.dumps(value, sort_keys=True).encode()


def test_resource_gate_does_not_claim_when_cpu_or_memory_is_above_limit(tmp_path):
    queue = Queue([job()])

    outcome = BacktestRuntime(queue, settings(tmp_path), resource_probe=Probe(70.1, 20), executor=ImmediateExecutor()).run_once()

    assert outcome == RunOutcome("resource_gated")
    assert queue.claim_count == 0
    assert BacktestRuntime(Queue([job()]), settings(tmp_path), resource_probe=Probe(20, 80.1), executor=ImmediateExecutor()).run_once().state == "resource_gated"


def test_truthy_publish_setting_fails_closed_before_any_paths_are_used():
    with pytest.raises(PublishDenied):
        WorkerSettings.from_environment({"TRADEAI_STRATEGY_WORKER_OUTBOUND_PUBLISH_ENABLED": "true"})
    with pytest.raises(PublishDenied):
        WorkerSettings.from_environment({"TRADEAI_STRATEGY_WORKER_PUBLISH": "1"})


def test_once_processes_only_one_global_claim(tmp_path):
    first, second = job(), job()
    second["id"] = "backtest-2"
    queue = Queue([first, second])

    outcome = BacktestRuntime(queue, settings(tmp_path), resource_probe=Probe(20, 20), executor=ImmediateExecutor()).run_once()

    assert outcome == RunOutcome("completed", "backtest-1")
    assert queue.claim_count == 1
    assert len(queue.completed) == 1
    assert len(queue.jobs) == 1
    result = queue.completed[0]
    assert result["evidence"]["kind"] == "research"
    assert result["evidence"]["authority"]["publication_ceiling"] == "shadow"
    assert result["evidence"]["data_contract"] == {"research_proxy": False, "actionable": False}
    assert result["input_hashes"] == CANARY_MANIFEST["evidence_hashes"]


def test_public_completion_evidence_flattens_metrics_into_percentage_points():
    receipt = execute_research(CANARY_MANIFEST, CANARY_INPUTS)

    evidence = _completion_evidence(
        "backtest.run.v1",
        CANARY_MANIFEST,
        receipt,
        "research-evidence.json",
        "a" * 64,
    )

    assert set(evidence["metrics"]) == {
        "total_return_pct",
        "cost_adjusted_return_pct",
        "max_drawdown_pct",
        "oos_return_pct",
        "stress_tail_loss_pct",
        "trade_count",
        "coverage_days",
        "walk_forward_passed",
        "stress_passed",
    }
    assert evidence["metrics"]["total_return_pct"] == pytest.approx(
        receipt["metrics"]["costs"]["1x"]["return_pct"] * 100
    )
    assert evidence["metrics"]["max_drawdown_pct"] == pytest.approx(
        receipt["metrics"]["costs"]["1x"]["max_drawdown"] * 100
    )
    assert evidence["metrics"]["oos_return_pct"] == pytest.approx(
        receipt["metrics"]["oos"]["metrics"]["return_pct"] * 100
    )
    assert evidence["metrics"]["trade_count"] == receipt["validation"]["trade_count"]
    assert evidence["local_receipt"]["input_hashes"] == receipt["input_hashes"]


def test_child_must_attest_the_same_source_and_runtime_bundle(tmp_path):
    queue = Queue([job()])

    outcome = BacktestRuntime(
        queue,
        settings(tmp_path),
        resource_probe=Probe(20, 20),
        executor=WrongBundleExecutor(),
    ).run_once()

    assert outcome.state == "failed"
    assert queue.completed == []
    assert queue.failed[0]["error_code"] == "LOCAL_RUN_FAILED"


def test_child_timeout_terminates_the_child(tmp_path):
    runner = BoundedChildProcess(
        poll_interval=0.01,
        posix=False,
        command=(sys.executable, "-c", "import time; time.sleep(10)"),
    )

    with pytest.raises(WorkerTimedOut):
        runner.run(b"{}", timeout_seconds=0.03, cancellation_probe=lambda: False)


def test_file_ipc_rejects_oversized_request_and_output_without_blocking():
    runner = BoundedChildProcess(posix=False)
    with pytest.raises(WorkerExecutionError, match="request"):
        runner.run(b"x" * (MAX_EXECUTOR_REQUEST_BYTES + 1), timeout_seconds=1, cancellation_probe=lambda: False)
    with pytest.raises(WorkerExecutionError, match="memory budget"):
        _executor_payload(CANARY_MANIFEST, {"prices.csv": b"x" * (MAX_EXECUTOR_RAW_INPUT_BYTES + 1)})

    oversized = BoundedChildProcess(
        posix=False,
        command=(
            sys.executable,
            "-c",
            "import pathlib,sys; p=pathlib.Path(sys.argv[sys.argv.index('--output-file')+1]); "
            f"p.write_bytes(b'x'*{MAX_EXECUTOR_OUTPUT_BYTES + 1})",
        ),
    )
    with pytest.raises(WorkerExecutionError, match="output"):
        oversized.run(b"{}", timeout_seconds=2, cancellation_probe=lambda: False)


def test_fixed_child_returns_a_local_receipt():
    body = BoundedChildProcess(posix=False).run(
        _executor_payload(CANARY_MANIFEST, CANARY_INPUTS),
        timeout_seconds=5,
        cancellation_probe=lambda: False,
    )

    receipt = json.loads(body)
    assert receipt["runner"] == "equity-research-v1"
    assert receipt["input_hashes"] == CANARY_MANIFEST["evidence_hashes"]
    assert receipt["template_key"] == "equity.trend.long_flat.v1"


def test_cooperative_cancellation_cleans_up_child_and_records_cancellation(tmp_path):
    queue = Queue([job()], cancel_on_heartbeat=True)
    runner = BoundedChildProcess(
        poll_interval=0.01,
        posix=False,
        command=(sys.executable, "-c", "import time; time.sleep(10)"),
    )

    outcome = BacktestRuntime(queue, settings(tmp_path), resource_probe=Probe(20, 20), executor=runner).run_once()

    assert outcome == RunOutcome("cancelled", "backtest-1")
    assert queue.failed == [{"error_code": "CANCELLED", "message": "local research worker did not complete", "retryable": False}]


def test_posix_cleanup_targets_the_child_process_group(monkeypatch):
    calls: list[tuple[int, int]] = []

    class Process:
        pid = 1234

        def poll(self):
            return None

        def wait(self, timeout: float):
            return 0

    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append((pid, sig)), raising=False)
    BoundedChildProcess(posix=True)._terminate_process_group(Process())  # type: ignore[arg-type]

    assert calls == [(1234, signal.SIGTERM)]
