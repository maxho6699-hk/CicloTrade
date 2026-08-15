"""Bounded local runner for research-only backtest queue jobs.

This module deliberately owns no network client.  It consumes an explicitly
configured local queue database and writes only its paired artifact directory.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Protocol


DEFAULT_QUEUE_DB = "/var/lib/ciclotrade-worker/backtest-queue.db"
DEFAULT_ARTIFACT_DIR = "/var/lib/ciclotrade-worker/artifacts"
SUPPORTED_JOB_TYPES = frozenset({"backtest.run.v1", "candidate.evaluate.v1"})
MAX_EXECUTOR_REQUEST_BYTES = 32 * 1024 * 1024
MAX_EXECUTOR_RAW_INPUT_BYTES = 22 * 1024 * 1024
MAX_EXECUTOR_OUTPUT_BYTES = 1024 * 1024
MAX_EXECUTOR_STDERR_BYTES = 64 * 1024


class WorkerConfigurationError(ValueError):
    """Raised when a local-only worker configuration is unsafe."""


class PublishDenied(WorkerConfigurationError):
    """Raised when a P0 configuration attempts to enable publication."""


class WorkerTimedOut(RuntimeError):
    """Raised after the child process exceeds its hard deadline."""


class WorkerCancelled(RuntimeError):
    """Raised when queue cancellation is observed while a child is running."""


class WorkerExecutionError(RuntimeError):
    """Raised when a child exits without a valid research receipt."""


@dataclass(frozen=True)
class ResourceSnapshot:
    cpu_percent: float
    memory_percent: float


@dataclass(frozen=True)
class WorkerSettings:
    queue_db: Path
    artifact_dir: Path
    worker_id: str = "hk-strategy-worker"
    lease_seconds: int = 60
    poll_seconds: float = 2.0
    hard_timeout_seconds: float = 900.0

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        queue_db: str | None = None,
        artifact_dir: str | None = None,
        worker_id: str | None = None,
        poll_seconds: float | None = None,
        hard_timeout_seconds: float | None = None,
    ) -> "WorkerSettings":
        env = os.environ if environment is None else environment
        if _as_bool(env.get("TRADEAI_STRATEGY_WORKER_OUTBOUND_PUBLISH_ENABLED", "false")) or _as_bool(env.get("TRADEAI_STRATEGY_WORKER_PUBLISH", "false")):
            raise PublishDenied("outbound publication is unavailable in the P0 worker")
        settings = cls(
            queue_db=_local_path(queue_db or env.get("TRADEAI_STRATEGY_WORKER_QUEUE_DB", DEFAULT_QUEUE_DB), "queue database"),
            artifact_dir=_local_path(artifact_dir or env.get("TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR), "artifact directory"),
            worker_id=worker_id or env.get("TRADEAI_STRATEGY_WORKER_ID", "hk-strategy-worker"),
            lease_seconds=_positive_int(env.get("TRADEAI_STRATEGY_WORKER_LEASE_SECONDS", "60"), "lease seconds", 10, 600),
            poll_seconds=poll_seconds if poll_seconds is not None else _positive_float(env.get("TRADEAI_STRATEGY_WORKER_POLL_SECONDS", "2"), "poll seconds"),
            hard_timeout_seconds=hard_timeout_seconds if hard_timeout_seconds is not None else _positive_float(env.get("TRADEAI_STRATEGY_WORKER_HARD_TIMEOUT_SECONDS", "900"), "hard timeout"),
        )
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", settings.worker_id):
            raise WorkerConfigurationError("worker id must be 1 to 128 characters")
        if not 0 < settings.poll_seconds <= 60 or not 1 <= settings.hard_timeout_seconds <= 14_400:
            raise WorkerConfigurationError("poll or hard timeout value is outside its allowed range")
        if settings.queue_db == settings.artifact_dir or settings.queue_db in settings.artifact_dir.parents or settings.artifact_dir in settings.queue_db.parents:
            raise WorkerConfigurationError("queue database and artifact directory must be separate local paths")
        return settings


class BacktestQueueProtocol(Protocol):
    def claim(self, worker_id: str, lease_seconds: int = 60) -> dict[str, Any] | None: ...

    def heartbeat(self, job_id: str, worker_id: str, lease_token: str, fencing_epoch: int, progress: float, stage: str) -> Mapping[str, Any]: ...

    def input_bytes(self, job_id: str, artifact_key: str, worker_id: str, lease_token: str, fencing_epoch: int) -> bytes: ...

    def upload_output(self, job_id: str, artifact_key: str, body: bytes, sha256: str, worker_id: str, lease_token: str, fencing_epoch: int, **kwargs: Any) -> Mapping[str, Any]: ...

    def complete(self, job_id: str, worker_id: str, lease_token: str, fencing_epoch: int, result: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def fail(self, job_id: str, worker_id: str, lease_token: str, fencing_epoch: int, error: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ResourceProbe:
    """Small Linux-only probe with no optional monitoring dependency."""

    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(cpu_percent=self._cpu_percent(), memory_percent=self._memory_percent())

    @staticmethod
    def _cpu_percent() -> float:
        first = _cpu_ticks()
        time.sleep(0.05)
        second = _cpu_ticks()
        total = second[0] - first[0]
        idle = second[1] - first[1]
        return 0.0 if total <= 0 else max(0.0, min(100.0, (total - idle) * 100.0 / total))

    @staticmethod
    def _memory_percent() -> float:
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.split()[0])
        except (OSError, ValueError, IndexError):
            return 100.0
        total, available = values.get("MemTotal", 0), values.get("MemAvailable", 0)
        return 100.0 if total <= 0 else max(0.0, min(100.0, (total - available) * 100.0 / total))


class BoundedChildProcess:
    """Execute only a fixed local child program with timeout and cancellation."""

    def __init__(
        self,
        *,
        poll_interval: float = 0.1,
        posix: bool | None = None,
        command: tuple[str, ...] | None = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.posix = os.name == "posix" if posix is None else posix
        self.command = command or (sys.executable, "-m", "src.apps.worker.research_executor")

    def run(self, payload: bytes, *, timeout_seconds: float, cancellation_probe: Callable[[], bool]) -> bytes:
        if not isinstance(payload, bytes) or len(payload) > MAX_EXECUTOR_REQUEST_BYTES:
            raise WorkerExecutionError("executor request exceeds the local IPC limit")
        with tempfile.TemporaryDirectory(prefix="tradeai-executor-") as directory:
            root = Path(directory)
            request_path = root / "request.json"
            output_path = root / "result.json"
            stderr_path = root / "stderr.log"
            with request_path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            deadline = time.monotonic() + timeout_seconds
            with stderr_path.open("wb") as stderr_handle:
                process = subprocess.Popen(
                    (*self.command, "--request-file", str(request_path), "--output-file", str(output_path)),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_handle,
                    start_new_session=self.posix,
                )
                try:
                    while process.poll() is None:
                        if cancellation_probe():
                            self._terminate_process_group(process)
                            raise WorkerCancelled("queue cancellation observed")
                        if time.monotonic() >= deadline:
                            self._terminate_process_group(process)
                            raise WorkerTimedOut("child exceeded hard timeout")
                        time.sleep(self.poll_interval)
                except BaseException:
                    if process.poll() is None:
                        self._terminate_process_group(process)
                    raise
            if stderr_path.stat().st_size > MAX_EXECUTOR_STDERR_BYTES:
                raise WorkerExecutionError("local child exceeded the stderr limit")
            if process.returncode != 0:
                raise WorkerExecutionError("local child exited unsuccessfully")
            try:
                size = output_path.stat().st_size
            except OSError as exc:
                raise WorkerExecutionError("local child did not produce a receipt") from exc
            if size <= 0 or size > MAX_EXECUTOR_OUTPUT_BYTES:
                raise WorkerExecutionError("local child receipt exceeds the output limit")
            return output_path.read_bytes()

    def _terminate_process_group(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if self.posix:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        else:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if self.posix:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
            else:
                process.kill()
            process.wait(timeout=2)


@dataclass(frozen=True)
class RunOutcome:
    state: str
    job_id: str | None = None


class BacktestRuntime:
    """One-at-a-time local queue consumer; all publication is intentionally absent."""

    def __init__(self, queue: BacktestQueueProtocol, settings: WorkerSettings, *, resource_probe: ResourceProbe | Any | None = None, executor: BoundedChildProcess | Any | None = None) -> None:
        self.queue = queue
        self.settings = settings
        self.resource_probe = resource_probe or ResourceProbe()
        self.executor = executor or BoundedChildProcess()
        self._run_lock = threading.Lock()
        self._next_heartbeat_at = 0.0

    def run_once(self) -> RunOutcome:
        with self._run_lock:
            resources = self.resource_probe.snapshot()
            if not math.isfinite(resources.cpu_percent) or not math.isfinite(resources.memory_percent) or resources.cpu_percent > 70.0 or resources.memory_percent > 80.0:
                return RunOutcome("resource_gated")
            job = self.queue.claim(self.settings.worker_id, self.settings.lease_seconds)
            if job is None:
                return RunOutcome("idle")
            return self._run_claimed(job)

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.settings.poll_seconds)

    def _run_claimed(self, job: Mapping[str, Any]) -> RunOutcome:
        job_id = str(job["id"])
        try:
            if job.get("job_type") not in SUPPORTED_JOB_TYPES:
                raise WorkerExecutionError("job type is outside the local runner scope")
            self._heartbeat(job, 0.1, "loading")
            input_hashes, input_bodies = self._verified_inputs(job)
            manifest = _manifest(job)
            _verify_code_bundle(manifest)
            payload = _executor_payload(manifest, input_bodies)
            child_output = self.executor.run(payload, timeout_seconds=self.settings.hard_timeout_seconds, cancellation_probe=lambda: self._cancel_requested(job))
            local_receipt = _decode_evidence(child_output, manifest, input_hashes)
            self._heartbeat(job, 0.9, "finalizing")
            digest = hashlib.sha256(child_output).hexdigest()
            artifact_key = "research-evidence.json"
            self.queue.upload_output(job_id, artifact_key, child_output, digest, self.settings.worker_id, str(job["lease_token"]), int(job["fencing_epoch"]), media_type="application/json")
            evidence = _completion_evidence(str(job["job_type"]), manifest, local_receipt, artifact_key, digest)
            self.queue.complete(job_id, self.settings.worker_id, str(job["lease_token"]), int(job["fencing_epoch"]), {
                "job_id": job_id,
                "manifest_sha256": job["manifest_sha256"],
                "code_bundle_sha256": manifest["code_bundle_sha256"],
                "fencing_epoch": job["fencing_epoch"],
                "input_hashes": input_hashes,
                "output_hashes": {artifact_key: digest},
                "evidence": evidence,
            })
            return RunOutcome("completed", job_id)
        except WorkerCancelled:
            self._fail(job, "CANCELLED", retryable=False)
            return RunOutcome("cancelled", job_id)
        except WorkerTimedOut:
            self._fail(job, "TIMEOUT", retryable=True)
            return RunOutcome("timed_out", job_id)
        except Exception:
            self._fail(job, "LOCAL_RUN_FAILED", retryable=False)
            return RunOutcome("failed", job_id)

    def _verified_inputs(self, job: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, bytes]]:
        hashes: dict[str, str] = {}
        bodies: dict[str, bytes] = {}
        for item in _manifest(job)["inputs"]:
            key, expected = item["artifact_key"], item["sha256"]
            body = self.queue.input_bytes(str(job["id"]), key, self.settings.worker_id, str(job["lease_token"]), int(job["fencing_epoch"]))
            if hashlib.sha256(body).hexdigest() != expected:
                raise WorkerExecutionError("frozen input integrity check failed")
            hashes[key], bodies[key] = expected, body
        return hashes, bodies

    def _heartbeat(self, job: Mapping[str, Any], progress: float, stage: str) -> Mapping[str, Any]:
        result = self.queue.heartbeat(str(job["id"]), self.settings.worker_id, str(job["lease_token"]), int(job["fencing_epoch"]), progress, stage)
        self._next_heartbeat_at = time.monotonic() + max(1.0, min(10.0, self.settings.lease_seconds / 3))
        if result.get("cancel_requested"):
            raise WorkerCancelled("queue cancellation observed")
        return result

    def _cancel_requested(self, job: Mapping[str, Any]) -> bool:
        if time.monotonic() < self._next_heartbeat_at:
            return False
        self._heartbeat(job, 0.5, "executing")
        return False

    def _fail(self, job: Mapping[str, Any], error_code: str, *, retryable: bool) -> None:
        try:
            self.queue.fail(str(job["id"]), self.settings.worker_id, str(job["lease_token"]), int(job["fencing_epoch"]), {
                "error_code": error_code,
                "message": "local research worker did not complete",
                "retryable": retryable,
            })
        except Exception:
            # The queue may already have terminally recorded cancellation or a stale lease.
            pass


def build_local_queue(settings: WorkerSettings) -> BacktestQueueProtocol:
    """Construct a queue only from the explicit local paths in ``settings``."""
    from core.backtest_artifacts import ArtifactStore
    from core.backtest_queue import BacktestQueue
    from core.backtest_queue_database import BacktestQueueDatabase

    settings.queue_db.parent.mkdir(parents=True, exist_ok=True)
    return BacktestQueue(BacktestQueueDatabase(settings.queue_db), ArtifactStore(settings.artifact_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local research backtest worker")
    parser.add_argument("--once", action="store_true", help="claim and process at most one job")
    parser.add_argument("--queue-db")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--worker-id")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--hard-timeout-seconds", type=float)
    args = parser.parse_args(argv)
    settings = WorkerSettings.from_environment(
        queue_db=args.queue_db,
        artifact_dir=args.artifact_dir,
        worker_id=args.worker_id,
        poll_seconds=args.poll_seconds,
        hard_timeout_seconds=args.hard_timeout_seconds,
    )
    runtime = BacktestRuntime(build_local_queue(settings), settings)
    if args.once:
        runtime.run_once()
        return 0
    try:
        runtime.run_forever()
    except KeyboardInterrupt:
        return 0
    return 0


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _local_path(value: str, label: str) -> Path:
    if "://" in value:
        raise WorkerConfigurationError(f"{label} must be an absolute local filesystem path")
    path = Path(value)
    if not path.is_absolute():
        raise WorkerConfigurationError(f"{label} must be an absolute local filesystem path")
    return path.resolve()


def _positive_int(value: str, label: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise WorkerConfigurationError(f"{label} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise WorkerConfigurationError(f"{label} is outside its allowed range")
    return parsed


def _positive_float(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise WorkerConfigurationError(f"{label} must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise WorkerConfigurationError(f"{label} must be positive")
    return parsed


def _cpu_ticks() -> tuple[int, int]:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        values = [int(field) for field in fields]
    except (OSError, ValueError, IndexError):
        return (1, 0)
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def _manifest(job: Mapping[str, Any]) -> Mapping[str, Any]:
    manifest = job.get("manifest")
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("inputs"), list):
        raise WorkerExecutionError("claimed job does not contain a frozen manifest")
    return manifest


def _executor_payload(manifest: Mapping[str, Any], input_bodies: Mapping[str, bytes]) -> bytes:
    if sum(len(value) for value in input_bodies.values()) > MAX_EXECUTOR_RAW_INPUT_BYTES:
        raise WorkerExecutionError("frozen inputs exceed the local executor memory budget")
    payload = {
        "manifest": manifest,
        "inputs": {key: base64.b64encode(value).decode("ascii") for key, value in sorted(input_bodies.items())},
    }
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WorkerExecutionError("executor request is not canonical JSON") from exc
    if len(encoded) > MAX_EXECUTOR_REQUEST_BYTES:
        raise WorkerExecutionError("executor request exceeds the local IPC limit")
    return encoded


def _verify_code_bundle(manifest: Mapping[str, Any]) -> None:
    from src.apps.worker.research_executor import research_code_bundle_sha256

    if manifest.get("code_bundle_sha256") != research_code_bundle_sha256():
        raise WorkerExecutionError("manifest code bundle does not match the allowlisted executor")


def _decode_evidence(body: bytes, manifest: Mapping[str, Any], input_hashes: Mapping[str, str]) -> Mapping[str, Any]:
    try:
        value = json.loads(body, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (TypeError, ValueError) as exc:
        raise WorkerExecutionError("child returned invalid JSON") from exc
    required = {"schema_version", "runner", "code_bundle_sha256", "template_key", "input_hashes", "metrics", "validation", "risk", "limitations"}
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1 or value.get("runner") != "equity-research-v1":
        raise WorkerExecutionError("child returned an invalid local receipt")
    if value.get("template_key") != manifest.get("template_key") or value.get("input_hashes") != dict(input_hashes):
        raise WorkerExecutionError("child receipt is not bound to the frozen manifest inputs")
    if value.get("code_bundle_sha256") != manifest.get("code_bundle_sha256"):
        raise WorkerExecutionError("child receipt code bundle does not match the frozen manifest")
    if not isinstance(value.get("metrics"), dict) or not isinstance(value.get("validation"), dict) or not isinstance(value.get("risk"), dict):
        raise WorkerExecutionError("child receipt evidence sections are invalid")
    if not isinstance(value.get("limitations"), list) or not all(isinstance(item, str) and 1 <= len(item) <= 500 for item in value["limitations"]):
        raise WorkerExecutionError("child receipt limitations are invalid")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkerExecutionError("child receipt contains non-finite or non-JSON values") from exc
    return value


def _completion_evidence(job_type: str, manifest: Mapping[str, Any], receipt: Mapping[str, Any], artifact_key: str, digest: str) -> dict[str, Any]:
    if job_type == "candidate.evaluate.v1":
        return {
            "kind": "research",
            "hashes": {artifact_key: digest},
            "authority": manifest["authority"],
            "data_contract": {
                "research_proxy": manifest["asset_universe"]["research_proxy"],
                "actionable": False,
            },
            "validation": receipt["validation"],
            "risk": receipt["risk"],
        }
    metrics = receipt["metrics"]
    validation = receipt["validation"]
    public_metrics = {
        "total_return_pct": _percentage_points(metrics["costs"]["1x"]["return_pct"]),
        "cost_adjusted_return_pct": _percentage_points(metrics["costs"]["2x"]["return_pct"]),
        "max_drawdown_pct": _percentage_points(metrics["costs"]["1x"]["max_drawdown"]),
        "oos_return_pct": _percentage_points(metrics["oos"]["metrics"]["return_pct"]),
        "stress_tail_loss_pct": _percentage_points(
            metrics["stress"]["volatility"]["tail_stress_loss_pct"]
        ),
        "trade_count": validation["trade_count"],
        "coverage_days": validation["coverage_days"],
        "walk_forward_passed": validation["walk_forward_passed"],
        "stress_passed": validation["stress_passed"],
    }
    return {
        "kind": "research",
        "metrics": public_metrics,
        "limitations": receipt["limitations"],
        "local_receipt": {
            "runner": receipt["runner"],
            "template_key": receipt["template_key"],
            "input_hashes": receipt["input_hashes"],
        },
    }


def _percentage_points(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise WorkerExecutionError("child receipt percentage metric is invalid")
    return round(float(value) * 100.0, 8)


if __name__ == "__main__":
    raise SystemExit(main())
