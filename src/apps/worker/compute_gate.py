"""Fail-closed local Compute Gate for frozen, research-only strategy jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.backtest_contracts import BacktestQueueError, sha256_json
from core.backtest_operations import BacktestOperations
from core.backtest_queue import BacktestQueue
from src.apps.worker.backtest_runtime import ResourceProbe, ResourceSnapshot, WorkerSettings
from src.apps.worker.candidate_input_contracts import (
    CandidateInputError,
    approved_universe_sha256,
    validate_candidate_spec,
)
from src.apps.worker.compute_gate_config import (
    ComputeGateError,
    absolute as _absolute,
    as_bool as _as_bool,
    clock_time as _clock_time,
    integer as _integer,
    number as _float,
    reject_constant as _reject_constant,
    unique_object as _unique_object,
)
from src.apps.worker.local_csv_snapshot import LocalCsvSnapshot, LocalSnapshotError, import_local_csv_snapshot
from src.apps.worker.research_executor import EQUITY_TEMPLATES, research_code_bundle_sha256


REQUEST_FIELDS = {
    "schema_version",
    "request_id",
    "symbol",
    "evaluation_date",
    "template_key",
    "source_file",
    "source_sha256",
    "source_bytes",
}
AUTONOMOUS_REQUEST_FIELDS = REQUEST_FIELDS | {"evaluation_at", "universe_sha256", "candidate_spec"}
REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$")
SOURCE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.csv$")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MINIMUM_COMPUTE_ROWS = 252
EXECUTABLE_GATE_STATES = frozenset({"idle", "produced", "reused", "budget_gated"})
_LOOKBACK = {
    "equity.trend.long_flat.v1": 5,
    "equity.mean_reversion.long_flat.v1": 10,
    "equity.breakout.long_flat.v1": 10,
}


@dataclass(frozen=True)
class ComputeGateSettings:
    drop_dir: Path
    queue_db: Path
    artifact_dir: Path
    allowed_symbols: frozenset[str]
    timezone_name: str = "Asia/Hong_Kong"
    offpeak_start: time = time(0, 30)
    offpeak_end: time = time(6, 30)
    max_cpu_percent: float = 60.0
    max_memory_percent: float = 75.0
    minimum_free_bytes: int = 2 * 1024 * 1024 * 1024
    max_input_bytes: int = 8 * 1024 * 1024
    max_daily_jobs: int = 12
    max_daily_runs: int = 12
    max_pending_jobs: int = 4
    max_requests_per_run: int = 1
    orphan_minimum_age_seconds: int = 3_600
    worker_id: str = "hk-strategy-worker"
    worker_lease_seconds: int = 60
    worker_hard_timeout_seconds: float = 900.0

    def __post_init__(self) -> None:
        for field in ("drop_dir", "queue_db", "artifact_dir"):
            path = Path(getattr(self, field))
            if not path.is_absolute():
                raise ComputeGateError(f"{field} must be an absolute local path")
            object.__setattr__(self, field, path.resolve())
        if self.drop_dir == self.artifact_dir or self.queue_db == self.artifact_dir:
            raise ComputeGateError("compute paths must be isolated")
        if not self.allowed_symbols or any(not SYMBOL.fullmatch(symbol) for symbol in self.allowed_symbols):
            raise ComputeGateError("allowed symbols must be an explicit non-empty US equity allow-list")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ComputeGateError("compute timezone is unavailable") from exc
        if self.offpeak_start == self.offpeak_end:
            raise ComputeGateError("off-peak window must not cover the full day")
        for label, value, maximum in (
            ("max_cpu_percent", self.max_cpu_percent, 70.0),
            ("max_memory_percent", self.max_memory_percent, 80.0),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= maximum:
                raise ComputeGateError(f"{label} is outside the Compute Gate ceiling")
        for label, value, minimum, maximum in (
            ("minimum_free_bytes", self.minimum_free_bytes, 1, 1024**4),
            ("max_input_bytes", self.max_input_bytes, 1024, 64 * 1024 * 1024),
            ("max_daily_jobs", self.max_daily_jobs, 1, 10_000),
            ("max_daily_runs", self.max_daily_runs, 1, 10_000),
            ("max_pending_jobs", self.max_pending_jobs, 1, 1_000),
            ("max_requests_per_run", self.max_requests_per_run, 1, 64),
            ("orphan_minimum_age_seconds", self.orphan_minimum_age_seconds, 60, 30 * 24 * 3600),
            ("worker_lease_seconds", self.worker_lease_seconds, 10, 600),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise ComputeGateError(f"{label} is invalid")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.worker_id):
            raise ComputeGateError("worker_id is invalid")
        if (
            isinstance(self.worker_hard_timeout_seconds, bool)
            or not isinstance(self.worker_hard_timeout_seconds, (int, float))
            or not math.isfinite(self.worker_hard_timeout_seconds)
            or not 1 <= self.worker_hard_timeout_seconds <= 14_400
        ):
            raise ComputeGateError("worker_hard_timeout_seconds is invalid")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "ComputeGateSettings":
        env = os.environ if environment is None else environment
        if _as_bool(env.get("TRADEAI_STRATEGY_WORKER_OUTBOUND_PUBLISH_ENABLED", "false")) or _as_bool(env.get("TRADEAI_STRATEGY_WORKER_PUBLISH", "false")):
            raise ComputeGateError("outbound publication is unavailable in the Compute Gate")
        raw_symbols = env.get("TRADEAI_COMPUTE_ALLOWED_SYMBOLS", "")
        symbols = frozenset(item.strip().upper() for item in raw_symbols.split(",") if item.strip())
        return cls(
            drop_dir=_absolute(env.get("TRADEAI_COMPUTE_DROP_DIR", "/var/lib/ciclotrade-worker/inbox"), "drop_dir"),
            queue_db=_absolute(env.get("TRADEAI_STRATEGY_WORKER_QUEUE_DB", "/var/lib/ciclotrade-worker/backtest-queue.db"), "queue_db"),
            artifact_dir=_absolute(env.get("TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR", "/var/lib/ciclotrade-worker/artifacts"), "artifact_dir"),
            allowed_symbols=symbols,
            timezone_name=env.get("TRADEAI_COMPUTE_TIMEZONE", "Asia/Hong_Kong"),
            offpeak_start=_clock_time(env.get("TRADEAI_COMPUTE_OFFPEAK_START", "00:30"), "offpeak start"),
            offpeak_end=_clock_time(env.get("TRADEAI_COMPUTE_OFFPEAK_END", "06:30"), "offpeak end"),
            max_cpu_percent=_float(env.get("TRADEAI_COMPUTE_MAX_CPU_PERCENT", "60"), "max CPU"),
            max_memory_percent=_float(env.get("TRADEAI_COMPUTE_MAX_MEMORY_PERCENT", "75"), "max memory"),
            minimum_free_bytes=_integer(env.get("TRADEAI_COMPUTE_MIN_FREE_BYTES", str(2 * 1024**3)), "minimum free bytes"),
            max_input_bytes=_integer(env.get("TRADEAI_COMPUTE_MAX_INPUT_BYTES", str(8 * 1024**2)), "max input bytes"),
            max_daily_jobs=_integer(env.get("TRADEAI_COMPUTE_MAX_DAILY_JOBS", "12"), "max daily jobs"),
            max_daily_runs=_integer(env.get("TRADEAI_COMPUTE_MAX_DAILY_RUNS", "12"), "max daily runs"),
            max_pending_jobs=_integer(env.get("TRADEAI_COMPUTE_MAX_PENDING_JOBS", "4"), "max pending jobs"),
            max_requests_per_run=_integer(env.get("TRADEAI_COMPUTE_MAX_REQUESTS_PER_RUN", "1"), "max requests per run"),
            orphan_minimum_age_seconds=_integer(env.get("TRADEAI_COMPUTE_ORPHAN_MIN_AGE_SECONDS", "3600"), "orphan minimum age"),
            worker_id=env.get("TRADEAI_STRATEGY_WORKER_ID", "hk-strategy-worker"),
            worker_lease_seconds=_integer(env.get("TRADEAI_STRATEGY_WORKER_LEASE_SECONDS", "60"), "worker lease seconds"),
            worker_hard_timeout_seconds=_float(env.get("TRADEAI_STRATEGY_WORKER_HARD_TIMEOUT_SECONDS", "900"), "worker hard timeout"),
        )

    def worker_settings(self) -> WorkerSettings:
        return WorkerSettings(
            queue_db=self.queue_db,
            artifact_dir=self.artifact_dir,
            worker_id=self.worker_id,
            lease_seconds=self.worker_lease_seconds,
            hard_timeout_seconds=self.worker_hard_timeout_seconds,
        )


class ComputeGate:
    def __init__(
        self,
        queue: BacktestQueue,
        settings: ComputeGateSettings,
        *,
        resource_probe: ResourceProbe | Any | None = None,
        disk_probe: Callable[[Path], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.queue = queue
        self.settings = settings
        self.resource_probe = resource_probe or ResourceProbe()
        self.disk_probe = disk_probe or shutil.disk_usage
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> dict[str, Any]:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ComputeGateError("Compute Gate clock must include a timezone")
        if not self._inside_window(now):
            return self._result("outside_window")
        resources = self.resource_probe.snapshot()
        if not self._resources_available(resources):
            return self._result("resource_gated")
        disk = self.disk_probe(self._disk_probe_path())
        if int(getattr(disk, "free", -1)) < self.settings.minimum_free_bytes:
            return self._result("disk_gated")
        self._validate_drop_root()
        audit = BacktestOperations(self.queue).audit_artifacts()
        if audit["missing"] or audit["mismatched"]:
            return self._result("artifact_integrity_gated", artifact_audit=audit)
        cleanup = self._cleanup_orphans()
        created, reused, job_ids, request_ids = 0, 0, [], []
        state = "idle"
        for request_path in self._request_paths():
            try:
                job, was_created, request_id = self._produce(request_path, now)
            except BacktestQueueError as exc:
                if exc.status != 429:
                    raise
                state = "budget_gated"
                break
            self._archive_request(request_path, request_id)
            created += int(was_created)
            reused += int(not was_created)
            job_ids.append(job["id"])
            request_ids.append(request_id)
        else:
            state = "produced" if created else "reused" if reused else "idle"
        return self._result(state, created, reused, job_ids, request_ids, cleanup, audit)

    def execution_gate_state(self) -> str:
        """Recheck mutable safety conditions immediately before claiming work."""
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ComputeGateError("Compute Gate clock must include a timezone")
        if not self._inside_window(now):
            return "outside_window"
        if not self._resources_available(self.resource_probe.snapshot()):
            return "resource_gated"
        disk = self.disk_probe(self._disk_probe_path())
        if int(getattr(disk, "free", -1)) < self.settings.minimum_free_bytes:
            return "disk_gated"
        self._validate_drop_root()
        audit = BacktestOperations(self.queue).audit_artifacts()
        if audit["missing"] or audit["mismatched"]:
            return "artifact_integrity_gated"
        return "ready"

    def _produce(self, request_path: Path, now: datetime) -> tuple[dict[str, Any], bool, str]:
        request = self._request(request_path, now)
        evaluation = date.fromisoformat(request["evaluation_date"])
        as_of = _evaluation_at(request) or datetime.combine(evaluation, time.max, tzinfo=timezone.utc)
        try:
            snapshot = import_local_csv_snapshot(
                self.settings.drop_dir,
                request["source_file"],
                as_of=as_of,
                allowed_symbols={request["symbol"]},
                imported_at=now,
                maximum_bytes=self.settings.max_input_bytes,
            )
        except LocalSnapshotError as exc:
            raise ComputeGateError(str(exc)) from exc
        if (
            snapshot.receipt["source_sha256"] != request["source_sha256"]
            or len(snapshot.raw_csv) != request["source_bytes"]
        ):
            raise ComputeGateError("source CSV does not match the request checksum and size")
        if snapshot.frozen.row_count < MINIMUM_COMPUTE_ROWS:
            raise ComputeGateError(f"Compute Gate requires at least {MINIMUM_COMPUTE_ROWS} frozen daily rows")
        canonical_receipt = BacktestOperations(self.queue).register_source_snapshot(snapshot.receipt)
        snapshot = snapshot.with_receipt(canonical_receipt)
        manifest = _candidate_manifest(request, snapshot)
        idempotency_key = f"compute:{request['request_id']}"
        day_start = datetime.combine(now.astimezone(ZoneInfo(self.settings.timezone_name)).date(), time.min, tzinfo=ZoneInfo(self.settings.timezone_name)).astimezone(timezone.utc)
        job, created = self.queue.enqueue(
            None,
            {"type": "candidate.evaluate.v1", "manifest": manifest, "max_attempts": 1},
            idempotency_scope="system:compute-gate",
            idempotency_key=idempotency_key,
            internal=True,
            system_daily_limit=self.settings.max_daily_jobs,
            system_daily_runs_limit=self.settings.max_daily_runs,
            system_pending_limit=self.settings.max_pending_jobs,
            system_day_start=day_start.isoformat(),
            system_budget_timezone=self.settings.timezone_name,
        )
        descriptors = {item["artifact_key"]: item for item in snapshot.manifest_inputs()}
        for artifact_key, body in snapshot.artifact_bodies().items():
            descriptor = descriptors[artifact_key]
            self.queue.register_input(
                job["id"],
                artifact_key,
                body,
                str(descriptor["sha256"]),
                row_count=int(descriptor["rows"]),
                media_type="application/json" if artifact_key == "source-snapshot.json" else "text/csv",
            )
        return job, created, request["request_id"]

    def _request(self, path: Path, now: datetime) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ComputeGateError("request files must be regular files")
        try:
            if path.stat().st_size > 16_384:
                raise ComputeGateError("request JSON exceeds 16 KiB")
            value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ComputeGateError("request JSON is unreadable") from exc
        fields = frozenset(value) if isinstance(value, dict) else frozenset()
        if not isinstance(value, dict) or fields not in {frozenset(REQUEST_FIELDS), frozenset(AUTONOMOUS_REQUEST_FIELDS)}:
            raise ComputeGateError("request fields do not match the Compute Gate contract")
        autonomous = fields == frozenset(AUTONOMOUS_REQUEST_FIELDS)
        if value.get("schema_version") != (2 if autonomous else 1):
            raise ComputeGateError("request schema version does not match its field set")
        if not isinstance(value["request_id"], str) or not REQUEST_ID.fullmatch(value["request_id"]):
            raise ComputeGateError("request_id is invalid")
        if value["symbol"] not in self.settings.allowed_symbols:
            raise ComputeGateError("symbol is outside the Compute Gate allow-list")
        if value["template_key"] not in EQUITY_TEMPLATES:
            raise ComputeGateError("template is outside the executable equity allow-list")
        if not isinstance(value["source_file"], str) or not SOURCE_FILE.fullmatch(value["source_file"]):
            raise ComputeGateError("source_file must be a direct CSV child of the drop directory")
        if not isinstance(value["source_sha256"], str) or not SHA256.fullmatch(value["source_sha256"]):
            raise ComputeGateError("source_sha256 must be a lowercase SHA-256 digest")
        if (
            not isinstance(value["source_bytes"], int)
            or isinstance(value["source_bytes"], bool)
            or not 1 <= value["source_bytes"] <= self.settings.max_input_bytes
        ):
            raise ComputeGateError("source_bytes is outside the Compute Gate input ceiling")
        try:
            evaluation = date.fromisoformat(value["evaluation_date"])
        except (TypeError, ValueError) as exc:
            raise ComputeGateError("evaluation_date must be YYYY-MM-DD") from exc
        if evaluation > now.astimezone(timezone.utc).date():
            raise ComputeGateError("evaluation_date must not be in the future")
        if autonomous:
            try:
                evaluated_at = datetime.fromisoformat(str(value["evaluation_at"]).replace("Z", "+00:00"))
            except (TypeError, ValueError) as exc:
                raise ComputeGateError("evaluation_at must be an aware ISO timestamp") from exc
            if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
                raise ComputeGateError("evaluation_at must include a timezone")
            evaluated_at = evaluated_at.astimezone(timezone.utc)
            if evaluated_at > now.astimezone(timezone.utc) or evaluated_at.date() != evaluation:
                raise ComputeGateError("evaluation_at must be observed and match evaluation_date")
            expected_universe = approved_universe_sha256(self.settings.allowed_symbols)
            if value["universe_sha256"] != expected_universe:
                raise ComputeGateError("candidate request universe hash does not match the deployed allow-list")
            try:
                value["candidate_spec"] = validate_candidate_spec(value["candidate_spec"])
            except CandidateInputError as exc:
                raise ComputeGateError(str(exc)) from exc
        return value

    def _request_paths(self) -> list[Path]:
        return sorted(self.settings.drop_dir.glob("*.json"), key=lambda path: path.name)[:self.settings.max_requests_per_run]

    def _archive_request(self, request_path: Path, request_id: str) -> None:
        processed = self.settings.drop_dir / ".processed"
        if processed.exists() and (processed.is_symlink() or not processed.is_dir()):
            raise ComputeGateError("processed request archive must be a local directory")
        processed.mkdir(mode=0o700, exist_ok=True)
        if processed.is_symlink() or not processed.is_dir():
            raise ComputeGateError("processed request archive must remain a local directory")
        suffix = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:16]
        destination = processed / f"{request_path.stem}.{suffix}.json"
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise ComputeGateError("processed request archive entry is unsafe")
            try:
                if destination.read_bytes() != request_path.read_bytes():
                    raise ComputeGateError("processed request archive contains conflicting content")
                request_path.unlink()
                return
            except OSError as exc:
                raise ComputeGateError("processed request archive cannot be verified") from exc
        try:
            request_path.replace(destination)
        except OSError as exc:
            raise ComputeGateError("processed request cannot be archived") from exc

    def _validate_drop_root(self) -> None:
        if self.settings.drop_dir.is_symlink() or not self.settings.drop_dir.is_dir():
            raise ComputeGateError("drop directory must exist and must not be a symlink")

    def _cleanup_orphans(self) -> dict[str, object]:
        registered = self.queue.db.fetch_all("SELECT storage_key FROM backtest_job_artifacts")
        return self.queue.artifacts.reconcile_orphans(
            {row["storage_key"] for row in registered},
            minimum_age_seconds=self.settings.orphan_minimum_age_seconds,
        )

    def _inside_window(self, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(self.settings.timezone_name)).time().replace(tzinfo=None)
        start, end = self.settings.offpeak_start, self.settings.offpeak_end
        return start <= local < end if start < end else local >= start or local < end

    def _resources_available(self, value: ResourceSnapshot) -> bool:
        return (
            math.isfinite(value.cpu_percent)
            and math.isfinite(value.memory_percent)
            and value.cpu_percent <= self.settings.max_cpu_percent
            and value.memory_percent <= self.settings.max_memory_percent
        )

    def _disk_probe_path(self) -> Path:
        for path in (self.settings.artifact_dir, self.settings.artifact_dir.parent, self.settings.drop_dir):
            if path.exists():
                return path
        raise ComputeGateError("no local path is available for disk capacity probing")

    @staticmethod
    def _result(
        state: str,
        created: int = 0,
        reused: int = 0,
        job_ids: list[str] | None = None,
        request_ids: list[str] | None = None,
        cleanup: dict[str, object] | None = None,
        artifact_audit: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "created": created,
            "reused": reused,
            "job_ids": job_ids or [],
            "request_ids": request_ids or [],
            "orphan_cleanup": cleanup or {"removed": [], "removed_count": 0},
            "artifact_audit": artifact_audit or {"verified": [], "missing": [], "mismatched": []},
            "publication": "disabled",
        }


def _candidate_manifest(request: Mapping[str, Any], snapshot: LocalCsvSnapshot) -> dict[str, Any]:
    template = str(request["template_key"])
    symbol = str(request["symbol"])
    evaluation = str(request["evaluation_date"])
    frozen = snapshot.frozen
    spec = request.get("candidate_spec")
    if spec is not None:
        try:
            candidate = validate_candidate_spec(spec)
        except CandidateInputError as exc:  # pragma: no cover - validated while opening request
            raise ComputeGateError(str(exc)) from exc
        provenance_source = str(candidate["provenance_source"])
        candidate_id = str(candidate["candidate_id"])
        candidate_version = str(candidate["candidate_version"])
        hypothesis = str(candidate["hypothesis"])
        parent_version = candidate["parent_version"]
        parent_job_id = candidate["parent_job_id"]
        parent_manifest_sha256 = candidate["parent_manifest_sha256"]
        parent_result_sha256 = candidate["parent_result_sha256"]
        search_space = candidate["search_space"]
        experiment_budget = candidate["experiment_budget"]
        parameters = candidate["parameters"]
    else:
        provenance_source = "approved_seed"
        candidate_id = f"{symbol}.{template}"
        candidate_version = f"{evaluation.replace('-', '')}.{snapshot.snapshot_id[:12]}"
        hypothesis = f"Bounded point-in-time long-flat research for {symbol} using {template}."
        parent_version = parent_job_id = parent_manifest_sha256 = parent_result_sha256 = None
        search_space = {"lookback": [_LOOKBACK[template]]}
        experiment_budget = {"runs": 1, "folds": 3}
        parameters = {"lookback": _LOOKBACK[template]}
    return {
        "schema_version": 1,
        "template_key": template,
        "evaluation_date": evaluation,
        "dataset_end": frozen.dataset_end.isoformat(),
        "code_bundle_sha256": research_code_bundle_sha256(),
        "inputs": snapshot.manifest_inputs(),
        "candidate_id": candidate_id,
        "candidate_version": candidate_version,
        "provenance": {
            "source": provenance_source,
            "generated_by": "compute-gate",
            "request_id": str(request["request_id"]),
            "request_sha256": sha256_json(dict(request)),
        },
        "hypothesis": hypothesis,
        "parent_version": parent_version,
        "parent_job_id": parent_job_id,
        "parent_manifest_sha256": parent_manifest_sha256,
        "parent_result_sha256": parent_result_sha256,
        "asset_universe": {
            "market": "US",
            "instrument_family": "equity",
            "symbols": [symbol],
            "direction": "long_flat",
            "research_proxy": False,
            "data_mode": "point_in_time_prices",
        },
        "search_space": search_space,
        "experiment_budget": experiment_budget,
        "parameters": parameters,
        "evidence_hashes": snapshot.evidence_hashes(),
        "authority": {
            "origin_site": "hk-strategy-worker",
            "deployment_role": "strategy_worker",
            "publication_ceiling": "shadow",
            "outbound_publish_enabled": False,
            "user_visible": False,
            "execution_eligible": False,
            "recommendations_published": False,
        },
        "risk_contract": {
            "defined_risk": True,
            "max_loss_amount": 500.0,
            "max_loss_pct_model_equity": 0.005,
            "currency": "USD",
            "risk_basis_equity": 100_000.0,
            "risk_basis_captured_at": f"{evaluation}T00:00:00Z",
            "portfolio_open_risk_cap_pct": 0.03,
            "daily_new_risk_pause_pct": 0.015,
            "quarantine_drawdown_pct": 0.08,
            "invalidation_condition": "frozen long-flat risk or point-in-time contract breached",
        },
        "validation_plan": {
            "oos_method": "point_in_time",
            "walk_forward": True,
            "cost_multipliers": [1.0, 2.0],
            "stress_tests": ["gap", "liquidity", "volatility"],
            "minimum_trades": 30,
            "minimum_coverage_days": MINIMUM_COMPUTE_ROWS,
            "market_regimes": ["bull", "bear", "sideways"],
        },
    }


def _evaluation_at(request: Mapping[str, Any]) -> datetime | None:
    raw = request.get("evaluation_at")
    if raw is None:
        return None
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    from src.apps.worker.compute_gate_cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
