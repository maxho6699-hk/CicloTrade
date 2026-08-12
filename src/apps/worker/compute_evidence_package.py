"""Build immutable equity shadow packages from completed local queue evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core.backtest_artifacts import ArtifactError
from core.backtest_contracts import BacktestQueueError
from core.backtest_queue import BacktestQueue
from core.compute_evidence_contracts import AUTHORITY, ComputeEvidenceError, package_id, validate_package


def build_completed_equity_package(queue: BacktestQueue, job_id: str, *, site_id: str) -> dict[str, Any]:
    if not isinstance(queue, BacktestQueue):
        raise TypeError("queue must be a BacktestQueue")
    with queue.db.transaction() as connection:
        connection.execute("BEGIN")
        job = connection.execute(
            """SELECT * FROM backtest_jobs
               WHERE id=? AND owner_scope='system' AND owner_id IS NULL
                 AND job_type='candidate.evaluate.v1' AND status='completed'""",
            (job_id,),
        ).fetchone()
        if job is None:
            raise ComputeEvidenceError("completed system candidate evidence does not exist")
        attempt = connection.execute(
            """SELECT * FROM backtest_job_attempts
               WHERE job_id=? AND attempt_no=? AND status='completed'""",
            (job_id, job["attempt_count"]),
        ).fetchone()
        artifacts = connection.execute(
            """SELECT * FROM backtest_job_artifacts
               WHERE job_id=? AND state='verified'
                 AND ((direction='input' AND attempt_no=0)
                   OR (direction='output' AND attempt_no=?))
               ORDER BY direction,artifact_key""",
            (job_id, job["attempt_count"]),
        ).fetchall()
    if attempt is None or not job["result_json"] or not job["result_sha256"]:
        raise ComputeEvidenceError("completed evidence is missing its final attempt or result")
    if (
        int(attempt["attempt_no"]) != int(job["attempt_count"])
        or int(attempt["fencing_epoch"]) != int(job["fencing_epoch"])
        or attempt["worker_id"] != job["worker_id"]
    ):
        raise ComputeEvidenceError("completed attempt identity does not match the queue result")
    manifest = json.loads(job["manifest_json"])
    result = json.loads(job["result_json"])
    descriptors: list[dict[str, Any]] = []
    for row in artifacts:
        try:
            body = queue.artifacts.read(row["storage_key"], row["sha256"])
        except ArtifactError as exc:
            raise ComputeEvidenceError("completed artifact integrity verification failed") from exc
        if len(body) != int(row["bytes"]) or hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise ComputeEvidenceError("completed artifact bytes do not match queue metadata")
        descriptors.append(
            {
                "direction": row["direction"],
                "artifact_key": row["artifact_key"],
                "attempt_no": int(row["attempt_no"]),
                "sha256": row["sha256"],
                "bytes": int(row["bytes"]),
                "row_count": row["row_count"],
                "media_type": row["media_type"],
            }
        )
    package = {
        "schema_version": 1,
        "kind": "compute.equity-shadow.package.v1",
        "package_id": package_id(job["id"], job["manifest_sha256"], job["result_sha256"]),
        "site_id": site_id,
        "worker_id": job["worker_id"],
        "job_id": job["id"],
        "job_type": job["job_type"],
        "attempt_no": int(job["attempt_count"]),
        "fencing_epoch": int(job["fencing_epoch"]),
        "completed_at": job["completed_at"],
        "manifest_sha256": job["manifest_sha256"],
        "result_sha256": job["result_sha256"],
        "manifest": manifest,
        "result": result,
        "artifacts": descriptors,
        "authority": dict(AUTHORITY),
    }
    try:
        return validate_package(package)
    except BacktestQueueError as exc:  # defensive compatibility with queue validators
        raise ComputeEvidenceError(str(exc)) from exc
