"""Produce one bounded candidate request for the existing local Compute Gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from core.backtest_artifacts import ArtifactStore
from core.backtest_contracts import _json, _stamp, sha256_json
from core.backtest_operations import BacktestOperations
from core.backtest_queue import BacktestQueue
from core.backtest_queue_database import BacktestQueueDatabase
from src.apps.worker.backtest_runtime import BacktestRuntime, ResourceProbe, ResourceSnapshot
from src.apps.worker.candidate_input_contracts import (
    CandidateInputError,
    approved_universe_sha256,
    canonical_candidate_id,
    validate_candidate_binding,
    validate_candidate_spec,
)
from src.apps.worker.candidate_producer_config import (
    CandidateProducerError,
    CandidateProducerSettings,
)
from src.apps.worker.compute_gate import ComputeGate, ComputeGateSettings, EXECUTABLE_GATE_STATES
from src.apps.worker.point_in_time_freezer import PointInTimeError, freeze_daily_ohlcv
from src.apps.worker.research_executor import EQUITY_TEMPLATES


SOURCE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.csv$")
REQUEST_FILE_LIMIT = 16_384
MINIMUM_RESEARCH_ROWS = 252
TEMPLATE_LOOKBACKS = {
    "equity.trend.long_flat.v1": (5, 10, 20, 50),
    "equity.mean_reversion.long_flat.v1": (5, 10, 20),
    "equity.breakout.long_flat.v1": (10, 20, 55),
}


class AutonomousCandidateProducer:
    def __init__(
        self,
        queue: BacktestQueue,
        settings: CandidateProducerSettings,
        *,
        resource_probe: ResourceProbe | Any | None = None,
        disk_probe: Callable[[Path], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not settings.enabled:
            raise CandidateProducerError("disabled producer cannot be constructed")
        self.queue = queue
        self.settings = settings
        self.resource_probe = resource_probe or ResourceProbe()
        self.disk_probe = disk_probe or shutil.disk_usage
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def produce(self, source_name: str, candidate_spec: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        moment = now or self.clock()
        self._admission(moment)
        spec = validate_candidate_spec(candidate_spec)
        return self._produce_admitted(source_name, spec, moment)

    def produce_next(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Select one allowlisted root or child candidate from real local data."""
        moment = now or self.clock()
        self._admission(moment)
        sources = self._approved_sources()
        for children_only in (True, False):
            for source_name in sources:
                symbol = source_name[:-4].upper()
                for template in sorted(EQUITY_TEMPLATES):
                    candidate_id = _candidate_id(symbol, template)
                    pending = self.queue.db.fetch_one(
                        """SELECT id FROM backtest_jobs WHERE owner_scope='system'
                           AND job_type='candidate.evaluate.v1'
                           AND json_extract(manifest_json,'$.candidate_id')=?
                           AND status IN ('queued','preparing','running') LIMIT 1""",
                        (candidate_id,),
                    )
                    if pending is not None:
                        continue
                    parent = BacktestOperations(self.queue).latest_completed_candidate(candidate_id, template)
                    if children_only and parent is None:
                        continue
                    if not children_only and parent is not None:
                        continue
                    spec = self._automatic_spec(symbol, template, parent, moment)
                    if parent is not None:
                        existing_child = self.queue.db.fetch_one(
                            """SELECT id FROM backtest_jobs WHERE owner_scope='system'
                               AND job_type='candidate.evaluate.v1'
                               AND json_extract(manifest_json,'$.parent_job_id')=? LIMIT 1""",
                            (parent["id"],),
                        )
                        if existing_child is not None:
                            continue
                    return self._produce_admitted(source_name, spec, moment)
        return {"state": "idle", "publication": "disabled"}

    def _produce_admitted(self, source_name: str, spec: dict[str, Any], moment: datetime) -> dict[str, Any]:
        source_path = self._source_path(source_name)
        body = self._source_bytes(source_path)
        symbol = source_name[:-4].upper()
        try:
            frozen = freeze_daily_ohlcv(body, as_of=moment.astimezone(timezone.utc), allowed_symbols={symbol})
        except PointInTimeError as exc:
            raise CandidateProducerError(f"candidate source is not a valid point-in-time dataset: {exc}") from exc
        if frozen.row_count < MINIMUM_RESEARCH_ROWS:
            raise CandidateProducerError(f"candidate source requires at least {MINIMUM_RESEARCH_ROWS} frozen daily rows")
        source_hash = hashlib.sha256(body).hexdigest()
        existing = self.queue.db.fetch_one(
            "SELECT request_sha256,source_sha256,request_json,state FROM backtest_candidate_production_receipts WHERE candidate_id=? AND candidate_version=?",
            (spec["candidate_id"], spec["candidate_version"]),
        )
        if existing is not None:
            prior = json.loads(existing["request_json"])
            if existing["source_sha256"] != source_hash or prior.get("candidate_spec") != spec:
                raise CandidateProducerError("candidate version is already bound to different frozen content")
            self._publish_drop(source_name, body, prior, str(existing["request_sha256"]))
            self._mark_delivered(str(prior["request_id"]), moment)
            return {
                "state": "reused",
                "request_id": prior["request_id"],
                "request_sha256": existing["request_sha256"],
                "candidate_id": spec["candidate_id"],
                "candidate_version": spec["candidate_version"],
                "publication": "disabled",
            }
        request = self._request(source_name, body, spec, moment)
        request_id = str(request["request_id"])
        request_hash = sha256_json(request)
        created = self._reserve(request, request_hash, moment)
        self._publish_drop(source_name, body, request, request_hash)
        self._mark_delivered(request_id, moment)
        return {
            "state": "produced" if created else "reused",
            "request_id": request_id,
            "request_sha256": request_hash,
            "candidate_id": spec["candidate_id"],
            "candidate_version": spec["candidate_version"],
            "publication": "disabled",
        }

    def _approved_sources(self) -> list[str]:
        assert self.settings.source_dir is not None
        sources: list[str] = []
        for path in sorted(self.settings.source_dir.glob("*.csv"), key=lambda item: item.name.lower()):
            if path.is_symlink() or not path.is_file():
                raise CandidateProducerError("candidate source directory contains an unsafe CSV entry")
            symbol = path.stem.upper()
            if path.name.lower() != f"{symbol.lower()}.csv" or symbol not in self.settings.allowed_symbols:
                raise CandidateProducerError("candidate source directory contains a file outside the deployed allow-list")
            sources.append(path.name)
        return sources

    @staticmethod
    def _automatic_spec(symbol: str, template: str, parent: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
        lookbacks = TEMPLATE_LOOKBACKS[template]
        parent_version = parent_job_id = parent_manifest = parent_result = None
        provenance = "approved_seed"
        lookback = lookbacks[0]
        if parent is not None:
            manifest = parent.get("manifest")
            if not isinstance(manifest, dict):
                raise CandidateProducerError("completed candidate parent manifest is unavailable")
            parameters = manifest.get("parameters")
            previous = parameters.get("lookback") if isinstance(parameters, dict) else None
            if previous not in lookbacks:
                raise CandidateProducerError("completed candidate parent has an unsupported frozen parameter")
            lookback = lookbacks[(lookbacks.index(previous) + 1) % len(lookbacks)]
            parent_version = manifest.get("candidate_version")
            parent_job_id = parent.get("id")
            parent_manifest = parent.get("manifest_sha256")
            parent_result = parent.get("result_sha256")
            if not all(isinstance(item, str) and item for item in (parent_version, parent_job_id, parent_manifest, parent_result)):
                raise CandidateProducerError("completed candidate parent is missing immutable lineage hashes")
            provenance = "derived_candidate"
        seed = {
            "evaluation_date": now.astimezone(timezone.utc).date().isoformat(),
            "parent_result_sha256": parent_result,
            "symbol": symbol,
            "template_key": template,
            "lookback": lookback,
        }
        suffix = hashlib.sha256(_json(seed).encode("utf-8")).hexdigest()[:12]
        version = f"{now.astimezone(timezone.utc).date().strftime('%Y%m%d')}.lb{lookback}.{suffix}"
        return validate_candidate_spec({
            "candidate_id": _candidate_id(symbol, template),
            "candidate_version": version,
            "template_key": template,
            "provenance_source": provenance,
            "hypothesis": f"Test whether the bounded {template} lookback={lookback} variant remains robust for {symbol} after point-in-time OOS, Walk-Forward, 1x/2x costs, stress and regime gates.",
            "parent_version": parent_version,
            "parent_job_id": parent_job_id,
            "parent_manifest_sha256": parent_manifest,
            "parent_result_sha256": parent_result,
            "search_space": {"lookback": [lookback]},
            "experiment_budget": {"runs": 1, "folds": 3},
            "parameters": {"lookback": lookback},
        })

    def _admission(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise CandidateProducerError("candidate producer clock must include a timezone")
        local = now.astimezone(ZoneInfo(self.settings.timezone_name)).time().replace(tzinfo=None)
        start, end = self.settings.offpeak_start, self.settings.offpeak_end
        inside = start <= local < end if start < end else local >= start or local < end
        if not inside:
            raise CandidateProducerError("candidate production is outside the Hong Kong off-peak window")
        resources: ResourceSnapshot = self.resource_probe.snapshot()
        if (
            not math.isfinite(resources.cpu_percent)
            or not math.isfinite(resources.memory_percent)
            or resources.cpu_percent > self.settings.max_cpu_percent
            or resources.memory_percent > self.settings.max_memory_percent
        ):
            raise CandidateProducerError("candidate production is resource gated")
        assert self.settings.drop_dir is not None and self.settings.source_dir is not None
        for path, label in ((self.settings.source_dir, "source"), (self.settings.drop_dir, "drop")):
            if path.is_symlink() or not path.is_dir():
                raise CandidateProducerError(f"candidate {label} directory must exist and must not be a symlink")
        disk = self.disk_probe(self.settings.drop_dir)
        if int(getattr(disk, "free", -1)) < self.settings.minimum_free_bytes:
            raise CandidateProducerError("candidate production is disk gated")
        audit = BacktestOperations(self.queue).audit_artifacts()
        if audit["missing"] or audit["mismatched"]:
            raise CandidateProducerError("candidate production is artifact-integrity gated")
        pending = self.queue.db.fetch_one(
            "SELECT count(*) count FROM backtest_jobs WHERE owner_scope='system' AND status IN ('queued','preparing','running')"
        )
        if pending and int(pending["count"]) >= self.settings.max_pending_jobs:
            raise CandidateProducerError("candidate production is pending-budget gated")

    def _source_path(self, source_name: str) -> Path:
        if not isinstance(source_name, str) or not SOURCE_FILE.fullmatch(source_name):
            raise CandidateProducerError("candidate source must be a direct CSV filename")
        assert self.settings.source_dir is not None
        path = self.settings.source_dir / source_name
        if path.is_symlink() or path.parent.resolve() != self.settings.source_dir:
            raise CandidateProducerError("candidate source must remain inside the approved source directory")
        return path

    @staticmethod
    def _source_bytes(path: Path) -> bytes:
        try:
            before = path.stat()
            if not path.is_file() or before.st_size <= 0 or before.st_size > 8 * 1024 * 1024:
                raise CandidateProducerError("candidate source is missing or outside the input ceiling")
            body = path.read_bytes()
            after = path.stat()
        except OSError as exc:
            raise CandidateProducerError("candidate source could not be read") from exc
        if len(body) != before.st_size or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
            raise CandidateProducerError("candidate source changed during read")
        return body

    def _request(self, source_name: str, body: bytes, spec: dict[str, Any], now: datetime) -> dict[str, Any]:
        symbol = source_name[:-4].upper()
        if symbol not in self.settings.allowed_symbols:
            raise CandidateProducerError("candidate source symbol is outside the deployed allow-list")
        template = str(spec["template_key"])
        if template not in EQUITY_TEMPLATES:
            raise CandidateProducerError("candidate template is outside the executable equity allow-list")
        try:
            validate_candidate_binding(spec, symbol=symbol, template_key=template)
        except CandidateInputError as exc:
            raise CandidateProducerError(str(exc)) from exc
        evaluation = now.astimezone(timezone.utc)
        seed = {
            "candidate_id": spec["candidate_id"],
            "candidate_version": spec["candidate_version"],
            "symbol": symbol,
            "template_key": template,
        }
        request_id = f"cand-{hashlib.sha256(_json(seed).encode('utf-8')).hexdigest()[:32]}"
        return {
            "schema_version": 2,
            "request_id": request_id,
            "symbol": symbol,
            "evaluation_date": evaluation.date().isoformat(),
            "evaluation_at": evaluation.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "template_key": template,
            "source_file": source_name,
            "source_sha256": hashlib.sha256(body).hexdigest(),
            "source_bytes": len(body),
            "universe_sha256": approved_universe_sha256(self.settings.allowed_symbols),
            "candidate_spec": spec,
        }

    def _reserve(self, request: dict[str, Any], request_hash: str, now: datetime) -> bool:
        budget_day = now.astimezone(ZoneInfo(self.settings.timezone_name)).date().isoformat()
        candidate = request["candidate_spec"]
        with self.queue.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT request_sha256 FROM backtest_candidate_production_receipts WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            if current:
                if current["request_sha256"] != request_hash:
                    raise CandidateProducerError("candidate request id is already bound to different frozen content")
                return False
            duplicate = conn.execute(
                "SELECT request_sha256 FROM backtest_candidate_production_receipts WHERE candidate_id=? AND candidate_version=?",
                (candidate["candidate_id"], candidate["candidate_version"]),
            ).fetchone()
            if duplicate:
                raise CandidateProducerError("candidate version is already bound to another request")
            count = conn.execute(
                "SELECT count(*) FROM backtest_candidate_production_receipts WHERE budget_day=?",
                (budget_day,),
            ).fetchone()[0]
            if int(count) >= self.settings.max_daily_candidates:
                raise CandidateProducerError("candidate production reached the daily budget")
            conn.execute(
                """INSERT INTO backtest_candidate_production_receipts(
                    request_id,request_sha256,candidate_id,candidate_version,budget_day,symbol,
                    template_key,universe_sha256,source_file,source_sha256,source_bytes,request_json,state,created_at,delivered_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request["request_id"], request_hash, candidate["candidate_id"], candidate["candidate_version"],
                    budget_day, request["symbol"], request["template_key"], request["universe_sha256"],
                    request["source_file"], request["source_sha256"], request["source_bytes"], _json(request), "reserved", _stamp(now), None,
                ),
            )
        return True

    def _mark_delivered(self, request_id: str, now: datetime) -> None:
        self.queue.db.execute(
            """UPDATE backtest_candidate_production_receipts
               SET state='delivered',delivered_at=?
               WHERE request_id=? AND state='reserved'""",
            (_stamp(now), request_id),
        )

    def _publish_drop(self, source_name: str, body: bytes, request: dict[str, Any], request_hash: str) -> None:
        assert self.settings.drop_dir is not None
        request_name = f"{request['request_id']}.json"
        destination_csv = self.settings.drop_dir / source_name
        destination_request = self.settings.drop_dir / request_name
        encoded = _json(request).encode("utf-8")
        if len(encoded) > REQUEST_FILE_LIMIT:
            raise CandidateProducerError("candidate request exceeds the Compute Gate request ceiling")
        if destination_request.exists():
            if destination_request.is_symlink() or hashlib.sha256(destination_request.read_bytes()).hexdigest() != hashlib.sha256(encoded).hexdigest():
                raise CandidateProducerError("Compute Gate inbox contains a conflicting candidate request")
            if destination_csv.is_symlink() or hashlib.sha256(destination_csv.read_bytes()).hexdigest() != request["source_sha256"]:
                raise CandidateProducerError("Compute Gate inbox contains a conflicting candidate source")
            return
        if destination_csv.exists() and destination_csv.is_symlink():
            raise CandidateProducerError("Compute Gate inbox contains an unsafe candidate source")
        if destination_csv.exists() and hashlib.sha256(destination_csv.read_bytes()).hexdigest() != request["source_sha256"]:
            if self._source_is_referenced_by_pending_request(source_name):
                raise CandidateProducerError("Compute Gate inbox still references the prior candidate source")
        if not destination_csv.exists() or hashlib.sha256(destination_csv.read_bytes()).hexdigest() != request["source_sha256"]:
            _atomic_write(destination_csv, body)
        _atomic_write(destination_request, encoded)
        if sha256_json(json.loads(destination_request.read_text(encoding="utf-8"))) != request_hash:
            raise CandidateProducerError("candidate request failed its post-write hash verification")

    def _source_is_referenced_by_pending_request(self, source_name: str) -> bool:
        assert self.settings.drop_dir is not None
        for path in self.settings.drop_dir.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                raise CandidateProducerError("Compute Gate inbox contains an unsafe request entry")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise CandidateProducerError("Compute Gate inbox contains an unreadable request") from exc
            if isinstance(value, dict) and value.get("source_file") == source_name:
                return True
        return False


def _atomic_write(destination: Path, body: bytes) -> None:
    temporary = destination.with_name(f".{destination.name}.{hashlib.sha256(body).hexdigest()[:12]}.part")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except FileExistsError as exc:
        raise CandidateProducerError("candidate staging file already exists") from exc
    except OSError as exc:
        raise CandidateProducerError("candidate drop could not be committed") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _candidate_id(symbol: str, template: str) -> str:
    return canonical_candidate_id(symbol, template)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded local autonomous candidate producer")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--execute-one", action="store_true")
    parser.add_argument("--source-file")
    parser.add_argument("--candidate-spec")
    args = parser.parse_args(argv)
    if not args.once or bool(args.source_file) != bool(args.candidate_spec):
        parser.error("--once is required; manual source/spec must be supplied together")
    try:
        settings = CandidateProducerSettings.from_environment()
        if not settings.enabled:
            print(json.dumps({"state": "disabled", "publication": "disabled"}, sort_keys=True, separators=(",", ":")))
            return 0
        assert settings.queue_db is not None and settings.artifact_dir is not None
        compute = ComputeGateSettings.from_environment()
        queue = BacktestQueue(BacktestQueueDatabase(settings.queue_db), ArtifactStore(settings.artifact_dir))
        producer = AutonomousCandidateProducer(queue, settings)
        if compute.allowed_symbols != settings.allowed_symbols or compute.drop_dir != settings.drop_dir:
            raise CandidateProducerError("producer and Compute Gate environment contracts have drifted")
        if args.source_file:
            spec_path = Path(args.candidate_spec)
            if not spec_path.is_absolute() or spec_path.is_symlink() or not spec_path.is_file():
                raise CandidateProducerError("candidate spec must be an absolute regular file")
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            result = producer.produce(args.source_file, spec)
        else:
            result = producer.produce_next()
        if args.execute_one:
            gate = ComputeGate(queue, compute)
            imported = gate.run_once()
            execution = {"state": "not_ready", "job_id": None}
            if imported["state"] in EXECUTABLE_GATE_STATES:
                gate_state = gate.execution_gate_state()
                if gate_state == "ready":
                    outcome = BacktestRuntime(queue, compute.worker_settings()).run_once()
                    execution = {"state": outcome.state, "job_id": outcome.job_id}
                else:
                    execution = {"state": gate_state, "job_id": None}
            result = {**result, "compute_gate": imported, "execution": execution}
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except (CandidateInputError, CandidateProducerError, OSError, ValueError) as exc:
        print(f"candidate producer refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
