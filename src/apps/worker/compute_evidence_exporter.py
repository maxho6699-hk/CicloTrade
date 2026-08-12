"""Export completed system candidate evidence into an isolated local spool."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from core.backtest_artifacts import ArtifactStore
from core.backtest_queue import BacktestQueue
from core.backtest_queue_database import (
    BacktestQueueDatabase,
    BacktestQueueDatabaseError,
    ReadOnlyBacktestQueueDatabase,
)
from core.compute_evidence_contracts import ComputeEvidenceError, package_id
from src.apps.worker.compute_evidence_package import build_completed_equity_package
from src.apps.worker.compute_evidence_spool import (
    ComputeEvidenceSpoolError,
    PersistentComputeEvidenceSpool,
)


SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ComputeEvidenceExporterError(RuntimeError):
    """Raised when the local exporter cannot safely continue."""


@dataclass(frozen=True)
class ComputeEvidenceExporterSettings:
    enabled: bool
    queue_database: Path | None = None
    artifact_directory: Path | None = None
    spool_database: Path | None = None
    site_id: str = ""
    max_packages_per_run: int = 4

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "ComputeEvidenceExporterSettings":
        values = os.environ if env is None else env
        enabled = _boolean(values.get("TRADEAI_COMPUTE_EVIDENCE_EXPORTER_ENABLED", "false"))
        if not enabled:
            return cls(enabled=False)
        queue_database = _absolute_path(
            values.get("TRADEAI_STRATEGY_WORKER_QUEUE_DB", ""),
            "TRADEAI_STRATEGY_WORKER_QUEUE_DB",
        )
        artifact_directory = _absolute_path(
            values.get("TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR", ""),
            "TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR",
        )
        spool_database = _absolute_path(
            values.get("TRADEAI_COMPUTE_EVIDENCE_SPOOL_DATABASE", ""),
            "TRADEAI_COMPUTE_EVIDENCE_SPOOL_DATABASE",
        )
        if queue_database == spool_database:
            raise ComputeEvidenceExporterError("compute evidence spool database must be isolated from the queue")
        site_id = str(values.get("TRADEAI_COMPUTE_EVIDENCE_SITE_ID", "")).strip()
        if not SAFE_IDENTITY.fullmatch(site_id):
            raise ComputeEvidenceExporterError("compute evidence site identity is invalid")
        maximum = _integer(
            values.get("TRADEAI_COMPUTE_EVIDENCE_EXPORT_MAX_PER_RUN", "4"),
            minimum=1,
            maximum=100,
        )
        return cls(True, queue_database, artifact_directory, spool_database, site_id, maximum)


class ComputeEvidenceExporter:
    """Copy verified, completed system evidence into the network publisher spool."""

    def __init__(
        self,
        queue: BacktestQueue,
        spool: PersistentComputeEvidenceSpool,
        *,
        site_id: str,
        max_packages_per_run: int = 4,
    ) -> None:
        if not isinstance(queue, BacktestQueue):
            raise TypeError("queue must be a BacktestQueue")
        if not isinstance(spool, PersistentComputeEvidenceSpool):
            raise TypeError("spool must be PersistentComputeEvidenceSpool")
        if not SAFE_IDENTITY.fullmatch(str(site_id)):
            raise ComputeEvidenceExporterError("compute evidence site identity is invalid")
        if (
            not isinstance(max_packages_per_run, int)
            or isinstance(max_packages_per_run, bool)
            or not 1 <= max_packages_per_run <= 100
        ):
            raise ComputeEvidenceExporterError("compute evidence export limit is invalid")
        if Path(queue.db._db_path).resolve() == Path(spool.database._db_path).resolve():
            raise ComputeEvidenceExporterError("compute evidence spool database must be isolated from the queue")
        self.queue = queue
        self.spool = spool
        self.site_id = str(site_id)
        self.max_packages_per_run = max_packages_per_run

    def run_once(self) -> dict[str, Any]:
        rows = self.queue.db.fetch_all(
            """SELECT id,manifest_sha256,result_sha256 FROM backtest_jobs
               WHERE owner_scope='system' AND owner_id IS NULL
                 AND job_type='candidate.evaluate.v1' AND status='completed'
                 AND result_sha256 IS NOT NULL
               ORDER BY completed_at,id"""
        )
        exported = 0
        already_present = 0
        inspected = 0
        for row in rows:
            expected_package_id = package_id(row["id"], row["manifest_sha256"], row["result_sha256"])
            if self.spool.database.fetch_one(
                "SELECT package_id FROM compute_evidence_spool WHERE package_id=?",
                (expected_package_id,),
            ):
                already_present += 1
                continue
            package = build_completed_equity_package(self.queue, row["id"], site_id=self.site_id)
            _stored, created = self.spool.enqueue(package)
            inspected += 1
            if created:
                exported += 1
            else:
                already_present += 1
            if exported >= self.max_packages_per_run:
                break
        return {
            "state": "exported" if exported else "idle",
            "exported": exported,
            "already_present": already_present,
            "inspected": inspected,
            "remaining_completed": max(0, len(rows) - already_present - exported),
        }


def run_compute_evidence_exporter(
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    settings = ComputeEvidenceExporterSettings.from_env(env)
    if not settings.enabled:
        return {"state": "disabled", "exported": 0}
    if settings.queue_database is None or settings.artifact_directory is None or settings.spool_database is None:
        raise ComputeEvidenceExporterError("enabled exporter paths are incomplete")
    if not settings.artifact_directory.exists() or not settings.artifact_directory.is_dir():
        raise ComputeEvidenceExporterError("compute evidence artifact directory does not exist")
    try:
        source_database = ReadOnlyBacktestQueueDatabase(settings.queue_database)
    except BacktestQueueDatabaseError as exc:
        raise ComputeEvidenceExporterError(str(exc)) from exc
    queue = BacktestQueue(
        source_database,
        ArtifactStore(settings.artifact_directory),
    )
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(settings.spool_database))
    return ComputeEvidenceExporter(
        queue,
        spool,
        site_id=settings.site_id,
        max_packages_per_run=settings.max_packages_per_run,
    ).run_once()


def main() -> int:
    try:
        result = run_compute_evidence_exporter()
    except (
        OSError,
        ValueError,
        BacktestQueueDatabaseError,
        ComputeEvidenceError,
        ComputeEvidenceExporterError,
        ComputeEvidenceSpoolError,
    ) as exc:
        print(json.dumps({"state": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


def _absolute_path(value: Any, label: str) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise ComputeEvidenceExporterError(f"{label} must be absolute")
    return path.resolve()


def _boolean(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ComputeEvidenceExporterError("exporter enabled flag is invalid")


def _integer(value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise ComputeEvidenceExporterError("exporter integer setting is invalid")
    parsed = int(str(value).strip())
    if not minimum <= parsed <= maximum:
        raise ComputeEvidenceExporterError("exporter integer setting is outside its bounds")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
