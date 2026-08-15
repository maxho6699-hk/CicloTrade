"""Artifact operations mixed into the canonical backtest queue."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.backtest_artifacts import ArtifactError
from core.backtest_contracts import BacktestQueueError, _stamp


class BacktestQueueArtifactMixin:
    """Keep artifact persistence isolated from queue scheduling semantics."""

    def register_input(
        self,
        job_id: str,
        artifact_key: str,
        body: bytes,
        sha256: str,
        row_count: int | None = None,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise BacktestQueueError("找不到回测任务。", 404)
            if job["status"] not in {"queued", "preparing"}:
                raise BacktestQueueError("输入只能在任务准备阶段冻结。", 409)
            manifest = json.loads(job["manifest_json"])
            declared = next(
                (item for item in manifest["inputs"] if item["artifact_key"] == artifact_key),
                None,
            )
            if not declared or declared["sha256"] != sha256:
                raise BacktestQueueError("输入未在 manifest 中冻结或哈希不匹配。", 409)
            existing = conn.execute(
                """SELECT * FROM backtest_job_artifacts
                   WHERE job_id=? AND attempt_no=0 AND direction='input' AND artifact_key=?""",
                (job_id, artifact_key),
            ).fetchone()
            if existing:
                same_input = (
                    existing["state"] == "verified"
                    and existing["sha256"] == sha256
                    and existing["bytes"] == len(body)
                    and existing["row_count"] == row_count
                    and existing["media_type"] == media_type
                )
                if not same_input:
                    raise BacktestQueueError("输入 artifact 已以不同内容冻结。", 409)
                try:
                    self.artifacts.read(existing["storage_key"], sha256)
                except ArtifactError as exc:
                    raise BacktestQueueError("已冻结输入 artifact 完整性检查失败。", 409) from exc
                return {
                    "artifact_key": artifact_key,
                    "sha256": sha256,
                    "bytes": existing["bytes"],
                    "storage_key": existing["storage_key"],
                }
            if "bytes" in declared and declared["bytes"] != len(body):
                raise BacktestQueueError("输入 artifact 大小不匹配。", 409)
            if "rows" in declared and declared["rows"] != row_count:
                raise BacktestQueueError("输入 artifact 行数不匹配。", 409)
            usage = conn.execute(
                "SELECT COALESCE(sum(bytes),0) AS total_bytes FROM backtest_job_artifacts WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if int(usage["total_bytes"]) + len(body) > self.max_job_bytes:
                raise BacktestQueueError("任务 artifact 总大小超过限制。", 413)
        try:
            storage_key, size = self.artifacts.write(
                job_id, "input", artifact_key, body, sha256, 0
            )
        except ArtifactError as exc:
            raise BacktestQueueError(str(exc), 409) from exc
        if "bytes" in declared and declared["bytes"] != size:
            raise BacktestQueueError("输入 artifact 大小不匹配。", 409)
        if "rows" in declared and declared["rows"] != row_count:
            raise BacktestQueueError("输入 artifact 行数不匹配。", 409)
        now = _stamp()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute("SELECT status FROM backtest_jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] not in {"queued", "preparing"}:
                raise BacktestQueueError("输入冻结已过期。", 409)
            usage = conn.execute(
                "SELECT COALESCE(sum(bytes),0) AS total_bytes FROM backtest_job_artifacts WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if int(usage["total_bytes"]) + size > self.max_job_bytes:
                raise BacktestQueueError("任务 artifact 总大小超过限制。", 413)
            inserted = conn.execute(
                """INSERT OR IGNORE INTO backtest_job_artifacts(
                       job_id,attempt_no,direction,artifact_key,sha256,bytes,row_count,media_type,
                       state,storage_key,verified_at,created_at
                   ) VALUES(?,0,'input',?,?,?,?,?,'verified',?,?,?)""",
                (job_id, artifact_key, sha256, size, row_count, media_type, storage_key, now, now),
            )
            if inserted.rowcount == 0:
                existing = conn.execute(
                    """SELECT * FROM backtest_job_artifacts
                       WHERE job_id=? AND attempt_no=0 AND direction='input' AND artifact_key=?""",
                    (job_id, artifact_key),
                ).fetchone()
                if not existing or not (
                    existing["state"] == "verified"
                    and existing["sha256"] == sha256
                    and existing["bytes"] == size
                    and existing["row_count"] == row_count
                    and existing["media_type"] == media_type
                    and existing["storage_key"] == storage_key
                ):
                    raise BacktestQueueError("输入 artifact 并发冻结发生冲突。", 409)
        return {
            "artifact_key": artifact_key,
            "sha256": sha256,
            "bytes": size,
            "storage_key": storage_key,
        }

    def input_artifact(
        self,
        job_id: str,
        artifact_key: str,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
    ) -> tuple[bytes, dict[str, Any]]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._lease(conn, job_id, worker_id, lease_token, fencing_epoch)
            manifest = json.loads(row["manifest_json"])
            declared = next(
                (item for item in manifest["inputs"] if item["artifact_key"] == artifact_key),
                None,
            )
            if not declared:
                raise BacktestQueueError("该输入未在 manifest 中冻结。", 403)
            artifact = conn.execute(
                """SELECT * FROM backtest_job_artifacts
                   WHERE job_id=? AND attempt_no=0 AND direction='input'
                     AND artifact_key=? AND state='verified'""",
                (job_id, artifact_key),
            ).fetchone()
            if not artifact or artifact["sha256"] != declared["sha256"]:
                raise BacktestQueueError("输入 artifact 未验证。", 409)
        try:
            return self.artifacts.read(artifact["storage_key"], artifact["sha256"]), dict(artifact)
        except ArtifactError as exc:
            raise BacktestQueueError("输入 artifact 完整性失败。", 409) from exc

    def input_bytes(
        self,
        job_id: str,
        artifact_key: str,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
    ) -> bytes:
        return self.input_artifact(
            job_id, artifact_key, worker_id, lease_token, fencing_epoch
        )[0]

    def _record_output(
        self,
        job_id: str,
        artifact_key: str,
        storage_key: str,
        size: int,
        sha256: str,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
        row_count: int | None,
        media_type: str,
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._output_lease(
                conn, job_id, artifact_key, worker_id, lease_token, fencing_epoch
            )
            existing = conn.execute(
                """SELECT * FROM backtest_job_artifacts
                   WHERE job_id=? AND attempt_no=? AND direction='output' AND artifact_key=?""",
                (job_id, job["attempt_count"], artifact_key),
            ).fetchone()
            if existing:
                if existing["sha256"] == sha256:
                    return dict(existing)
                raise BacktestQueueError("artifact_key 已用于不同内容。", 409)
            usage = conn.execute(
                """SELECT count(*) AS item_count,COALESCE(sum(bytes),0) AS total_bytes
                   FROM backtest_job_artifacts WHERE job_id=?""",
                (job_id,),
            ).fetchone()
            output_count = conn.execute(
                """SELECT count(*) FROM backtest_job_artifacts
                   WHERE job_id=? AND attempt_no=? AND direction='output'""",
                (job_id, job["attempt_count"]),
            ).fetchone()[0]
            if output_count >= self.max_output_artifacts:
                raise BacktestQueueError("输出 artifact 数量超过限制。", 413)
            if int(usage["total_bytes"]) + size > self.max_job_bytes:
                raise BacktestQueueError("任务 artifact 总大小超过限制。", 413)
            now = _stamp()
            conn.execute(
                """INSERT INTO backtest_job_artifacts(
                       job_id,attempt_no,direction,artifact_key,sha256,bytes,row_count,media_type,
                       state,storage_key,verified_at,created_at
                   ) VALUES(?,?,'output',?,?,?,?,?,'verified',?,?,?)""",
                (
                    job_id,
                    job["attempt_count"],
                    artifact_key,
                    sha256,
                    size,
                    row_count,
                    media_type,
                    storage_key,
                    now,
                    now,
                ),
            )
            return dict(
                conn.execute(
                    """SELECT * FROM backtest_job_artifacts
                       WHERE job_id=? AND attempt_no=? AND direction='output' AND artifact_key=?""",
                    (job_id, job["attempt_count"], artifact_key),
                ).fetchone()
            )

    def upload_output(
        self,
        job_id: str,
        artifact_key: str,
        body: bytes,
        sha256: str,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
        row_count: int | None = None,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._output_lease(
                conn, job_id, artifact_key, worker_id, lease_token, fencing_epoch
            )
        try:
            storage_key, size = self.artifacts.write(
                job_id, "output", artifact_key, body, sha256, job["attempt_count"]
            )
        except ArtifactError as exc:
            raise BacktestQueueError(str(exc), 409) from exc
        return self._record_output(
            job_id,
            artifact_key,
            storage_key,
            size,
            sha256,
            worker_id,
            lease_token,
            fencing_epoch,
            row_count,
            media_type,
        )

    def upload_output_temp(
        self,
        job_id: str,
        artifact_key: str,
        temporary: str | Path,
        sha256: str,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
        row_count: int | None = None,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._output_lease(
                conn, job_id, artifact_key, worker_id, lease_token, fencing_epoch
            )
        try:
            storage_key, size = self.artifacts.finalize_temp(
                temporary,
                job_id,
                "output",
                artifact_key,
                sha256,
                job["attempt_count"],
            )
        except ArtifactError as exc:
            raise BacktestQueueError(str(exc), 409) from exc
        return self._record_output(
            job_id,
            artifact_key,
            storage_key,
            size,
            sha256,
            worker_id,
            lease_token,
            fencing_epoch,
            row_count,
            media_type,
        )

    def owner_artifact(
        self, job_id: str, artifact_key: str, owner_id: int
    ) -> tuple[bytes, dict[str, Any]]:
        job = self.get(job_id, owner_id)
        if job["status"] != "completed":
            raise BacktestQueueError("任务尚未完成。", 409)
        row = self.db.fetch_one(
            """SELECT * FROM backtest_job_artifacts
               WHERE job_id=? AND attempt_no=? AND direction='output'
                 AND artifact_key=? AND state='verified'""",
            (job_id, job["attempt_count"], artifact_key),
        )
        if not row:
            raise BacktestQueueError("找不到输出 artifact。", 404)
        try:
            return self.artifacts.read(row["storage_key"], row["sha256"]), row
        except ArtifactError as exc:
            raise BacktestQueueError("输出 artifact 完整性失败。", 409) from exc

    def owner_output_metadata(
        self, job_id: str, owner_id: int
    ) -> list[dict[str, Any]]:
        job = self.get(job_id, owner_id)
        if job["status"] != "completed":
            return []
        rows = self.db.fetch_all(
            """SELECT artifact_key,sha256,bytes FROM backtest_job_artifacts
               WHERE job_id=? AND attempt_no=? AND direction='output'
                 AND state='verified' ORDER BY artifact_key""",
            (job_id, job["attempt_count"]),
        )
        return [
            {
                "artifact_key": row["artifact_key"],
                "sha256": row["sha256"],
                "bytes": row["bytes"],
                "verified": True,
            }
            for row in rows
        ]
