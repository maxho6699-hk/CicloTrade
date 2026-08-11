"""Local operator and maintenance operations for the isolated research queue."""
from __future__ import annotations

import json
import re
from typing import Any

from core.backtest_artifacts import ArtifactError
from core.backtest_contracts import SHA, BacktestQueueError, _stamp, _timestamp
from core.backtest_queue import BacktestQueue


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
OPERATOR_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,70}$")


class BacktestOperations:
    def __init__(self, queue: BacktestQueue):
        self.queue = queue

    def cancel_system(
        self,
        job_id: str,
        *,
        operator_subject: str,
        request_id: str,
        reason_code: str,
        expected_manifest_sha256: str,
    ) -> dict[str, Any]:
        if not OPERATOR_SUBJECT.fullmatch(operator_subject) or not SAFE_ID.fullmatch(reason_code):
            raise BacktestQueueError("operator cancel 身份或原因代码无效。")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", request_id):
            raise BacktestQueueError("operator cancel request_id 无效。")
        if not isinstance(expected_manifest_sha256, str) or not SHA.fullmatch(expected_manifest_sha256):
            raise BacktestQueueError("operator cancel manifest SHA-256 无效。")
        with self.queue.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous_action = conn.execute(
                "SELECT * FROM backtest_operator_actions WHERE request_id=?", (request_id,),
            ).fetchone()
            if previous_action:
                if (
                    previous_action["job_id"] != job_id
                    or previous_action["operator_subject"] != operator_subject
                    or previous_action["reason_code"] != reason_code
                    or previous_action["manifest_sha256"] != expected_manifest_sha256
                ):
                    raise BacktestQueueError("operator cancel request_id 已用于不同请求。", 409)
                row = conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone()
                return self.queue._row(dict(row)) if row else {}
            row = conn.execute(
                "SELECT * FROM backtest_jobs WHERE id=? AND owner_scope='system'", (job_id,),
            ).fetchone()
            if not row:
                raise BacktestQueueError("找不到系统研究任务。", 404)
            if row["manifest_sha256"] != expected_manifest_sha256:
                raise BacktestQueueError("operator cancel manifest 已变化。", 409)
            if row["cancel_requested"]:
                raise BacktestQueueError("系统研究任务已存在取消请求。", 409)
            previous_status = row["status"]
            now = _stamp()
            if previous_status in {"queued", "preparing"}:
                resulting_status = "cancelled"
                conn.execute(
                    """UPDATE backtest_jobs SET status='cancelled',cancel_requested=1,
                       cancel_source=?,cancel_reason=?,completed_at=?,updated_at=? WHERE id=?""",
                    (f"operator:{operator_subject}", reason_code, now, now, job_id),
                )
            elif previous_status == "running":
                resulting_status = "running"
                conn.execute(
                    """UPDATE backtest_jobs SET cancel_requested=1,cancel_source=?,cancel_reason=?,updated_at=?
                       WHERE id=?""",
                    (f"operator:{operator_subject}", reason_code, now, job_id),
                )
            else:
                raise BacktestQueueError("终态系统任务不能再请求取消。", 409)
            conn.execute(
                """INSERT INTO backtest_operator_actions(
                    request_id,job_id,action,operator_subject,reason_code,manifest_sha256,
                    previous_status,resulting_status,created_at
                ) VALUES(?,?,'cancel_system_job',?,?,?,?,?,?)""",
                (
                    request_id, job_id, operator_subject, reason_code, expected_manifest_sha256,
                    previous_status, resulting_status, now,
                ),
            )
            updated = conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone()
            return self.queue._row(dict(updated)) if updated else {}

    def system_usage(self, window_start: str, window_end: str) -> dict[str, int]:
        start = _timestamp(window_start, "budget_window_start")
        end = _timestamp(window_end, "budget_window_end")
        if end <= start:
            raise BacktestQueueError("budget window 无效。")
        rows = self.queue.db.fetch_all(
            """SELECT manifest_json,status FROM backtest_jobs
               WHERE owner_scope='system' AND job_type='candidate.evaluate.v1'
                 AND created_at>=? AND created_at<? AND status<>'superseded'""",
            (start, end),
        )
        runs = 0
        pending = 0
        for row in rows:
            manifest = json.loads(row["manifest_json"])
            runs += int(manifest.get("experiment_budget", {}).get("runs", 0))
            pending += int(row["status"] in {"queued", "preparing", "running"})
        return {"jobs": len(rows), "declared_runs": runs, "pending": pending}

    def register_source_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version", "snapshot_id", "source_kind", "source_name", "imported_at", "as_of",
            "source_sha256", "prices_sha256", "canonical_bytes", "canonical_rows", "dataset_end", "symbol",
        }
        if not isinstance(snapshot, dict) or set(snapshot) != required:
            raise BacktestQueueError("source snapshot 持久化字段无效。")
        now = _stamp()
        with self.queue.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            natural = conn.execute(
                """SELECT * FROM backtest_source_snapshots
                   WHERE source_name=? AND source_sha256=? AND as_of=?""",
                (snapshot["source_name"], snapshot["source_sha256"], snapshot["as_of"]),
            ).fetchone()
            if natural:
                canonical = {key: natural[key] for key in required}
                stable = required - {"snapshot_id", "imported_at"}
                if any(canonical[key] != snapshot[key] for key in stable):
                    raise BacktestQueueError("source snapshot 自然键已绑定不同内容。", 409)
                return canonical
            collision = conn.execute(
                "SELECT * FROM backtest_source_snapshots WHERE snapshot_id=?", (snapshot["snapshot_id"],),
            ).fetchone()
            if collision:
                canonical = {key: collision[key] for key in required}
                if canonical != snapshot:
                    raise BacktestQueueError("source snapshot 标识已绑定不同内容。", 409)
                return canonical
            conn.execute(
                """INSERT INTO backtest_source_snapshots(
                    snapshot_id,schema_version,source_kind,source_name,source_sha256,prices_sha256,
                    imported_at,as_of,dataset_end,symbol,canonical_rows,canonical_bytes,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot["snapshot_id"], snapshot["schema_version"], snapshot["source_kind"],
                    snapshot["source_name"], snapshot["source_sha256"], snapshot["prices_sha256"],
                    snapshot["imported_at"], snapshot["as_of"], snapshot["dataset_end"], snapshot["symbol"],
                    snapshot["canonical_rows"], snapshot["canonical_bytes"], now,
                ),
            )
            stored = conn.execute(
                "SELECT * FROM backtest_source_snapshots WHERE snapshot_id=?", (snapshot["snapshot_id"],),
            ).fetchone()
            return {key: stored[key] for key in required}

    def cleanup_orphans(self, *, minimum_age_seconds: int = 3_600, now: float | None = None) -> dict[str, object]:
        rows = self.queue.db.fetch_all("SELECT storage_key FROM backtest_job_artifacts")
        return self.queue.artifacts.reconcile_orphans(
            {row["storage_key"] for row in rows},
            minimum_age_seconds=minimum_age_seconds,
            now=now,
        )

    def audit_artifacts(self) -> dict[str, list[str]]:
        rows = self.queue.db.fetch_all(
            "SELECT storage_key,sha256 FROM backtest_job_artifacts ORDER BY storage_key"
        )
        report: dict[str, list[str]] = {"verified": [], "missing": [], "mismatched": []}
        for row in rows:
            key = row["storage_key"]
            path = self.queue.artifacts._path(key)
            if not path.exists():
                report["missing"].append(key)
                continue
            try:
                self.queue.artifacts.read(key, row["sha256"])
                report["verified"].append(key)
            except ArtifactError:
                report["mismatched"].append(key)
        return report

    def latest_completed_candidate(self, candidate_id: str, template_key: str) -> dict[str, Any] | None:
        row = self.queue.db.fetch_one(
            """SELECT * FROM backtest_jobs
               WHERE owner_scope='system' AND job_type='candidate.evaluate.v1' AND status='completed'
                 AND json_extract(manifest_json,'$.candidate_id')=?
                 AND json_extract(manifest_json,'$.template_key')=?
               ORDER BY completed_at DESC LIMIT 1""",
            (candidate_id, template_key),
        )
        return self.queue._row(row) if row else None
