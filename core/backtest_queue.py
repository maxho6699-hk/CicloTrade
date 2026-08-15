"""Canonical SQLite queue for bounded, evidence-only backtest research."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.backtest_artifacts import ArtifactStore
from core.backtest_contracts import (
    INTERNAL_TYPES,
    MAX_ATTEMPT_SECONDS,
    PLAN_QUEUE_LIMITS,
    PUBLIC_TYPES,
    SHA,
    STAGES,
    BacktestQueueError,
    _json,
    _now,
    _stamp,
    _timestamp,
    sha256_json,
    validate_candidate_manifest,
    validate_failure,
    validate_manifest,
    validate_result_shape,
)
from core.backtest_queue_database import BacktestQueueDatabase
from core.backtest_queue_artifacts import BacktestQueueArtifactMixin


class BacktestQueue(BacktestQueueArtifactMixin):
    def __init__(
        self,
        database: Any | None = None,
        artifacts: ArtifactStore | None = None,
        *,
        max_output_artifacts: int = 32,
        max_job_bytes: int = 128 * 1024 * 1024,
    ):
        self.db = database or BacktestQueueDatabase()
        self.artifacts = artifacts or ArtifactStore()
        if not isinstance(max_output_artifacts, int) or isinstance(max_output_artifacts, bool) or not 1 <= max_output_artifacts <= 128:
            raise ValueError("max_output_artifacts must be between 1 and 128")
        if not isinstance(max_job_bytes, int) or isinstance(max_job_bytes, bool) or not 1024 <= max_job_bytes <= 1024 * 1024 * 1024:
            raise ValueError("max_job_bytes must be between 1 MiB and 1 GiB")
        self.max_output_artifacts = max_output_artifacts
        self.max_job_bytes = max_job_bytes

    @staticmethod
    def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in ("manifest_json", "result_json"):
            if result.get(field):
                result[field.removesuffix("_json")] = json.loads(result[field])
        if "cancel_requested" in result:
            result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    @staticmethod
    def _limits(plan: str | None) -> tuple[int, int]:
        return PLAN_QUEUE_LIMITS.get(str(plan), PLAN_QUEUE_LIMITS["免费版"])

    @staticmethod
    def _require_candidate_parent(conn: Any, manifest: dict[str, Any]) -> None:
        if manifest["parent_version"] is None:
            return
        parent = conn.execute(
            """SELECT id,manifest_json,manifest_sha256,result_sha256 FROM backtest_jobs
               WHERE id=? AND owner_scope='system' AND job_type='candidate.evaluate.v1'
                 AND status='completed'""",
            (manifest["parent_job_id"],),
        ).fetchone()
        if not parent:
            raise BacktestQueueError("候选父版本不存在或尚未完成证据评估。", 409)
        parent_manifest = json.loads(parent["manifest_json"])
        if (
            parent_manifest.get("candidate_id") != manifest["candidate_id"]
            or parent_manifest.get("candidate_version") != manifest["parent_version"]
            or parent["manifest_sha256"] != manifest["parent_manifest_sha256"]
            or parent["result_sha256"] != manifest["parent_result_sha256"]
        ):
            raise BacktestQueueError("候选父任务、版本、manifest 或结果哈希不匹配。", 409)

    def enqueue(
        self,
        owner_id: int | None,
        request: dict[str, Any],
        *,
        idempotency_scope: str,
        idempotency_key: str,
        plan: str | None = None,
        internal: bool = False,
        preparing: bool = False,
        system_daily_limit: int | None = None,
        system_daily_runs_limit: int | None = None,
        system_pending_limit: int | None = None,
        system_day_start: str | None = None,
        system_budget_timezone: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(request, dict) or set(request) - ({"type", "manifest", "available_at", "deadline_at", "priority", "max_attempts"} if internal else {"type", "manifest"}):
            raise BacktestQueueError("回测任务请求字段无效。")
        if not isinstance(preparing, bool) or internal and preparing:
            raise BacktestQueueError("任务准备状态无效。")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
            raise BacktestQueueError("Idempotency-Key 必须为 8 至 128 个安全字符。")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_scope):
            raise BacktestQueueError("幂等范围无效。")
        job_type = request.get("type")
        if job_type not in (INTERNAL_TYPES if internal else PUBLIC_TYPES):
            raise BacktestQueueError("不支持的回测任务类型。", 403)
        if not internal and owner_id is None:
            raise BacktestQueueError("公共任务必须有真实所有者。", 401)
        if internal and owner_id is not None:
            raise BacktestQueueError("系统研究任务不得伪造用户所有者。")
        if not internal and any(value is not None for value in (system_daily_limit, system_daily_runs_limit, system_pending_limit, system_day_start, system_budget_timezone)):
            raise BacktestQueueError("公共任务不得声明系统计算预算。", 403)
        for label, value in (("system_daily_limit", system_daily_limit), ("system_daily_runs_limit", system_daily_runs_limit), ("system_pending_limit", system_pending_limit)):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 10_000):
                raise BacktestQueueError(f"{label} 无效。")
        if (system_daily_limit is not None or system_daily_runs_limit is not None) and system_day_start is None:
            raise BacktestQueueError("系统每日预算必须绑定 UTC 日界。")
        if system_daily_runs_limit is not None:
            try:
                ZoneInfo(str(system_budget_timezone))
            except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
                raise BacktestQueueError("系统实际运行预算必须绑定有效时区。") from exc
        elif system_budget_timezone is not None:
            raise BacktestQueueError("系统预算时区不得脱离实际运行预算。")
        if system_day_start is not None:
            system_day_start = _timestamp(system_day_start, "system_day_start")
        if not internal and idempotency_scope != f"user:{owner_id}":
            raise BacktestQueueError("公共任务幂等范围必须由真实所有者派生。", 403)
        manifest = validate_manifest(request.get("manifest"))
        if job_type == "candidate.evaluate.v1":
            validate_candidate_manifest(manifest)
        elif "promotion_proposal" in manifest:
            raise BacktestQueueError("promotion_proposal 仅允许用于系统候选评估。", 403)
        request_hash, manifest_hash, now = sha256_json(request), sha256_json(manifest), _stamp()
        available_at = now
        deadline_at = None
        priority = 0
        max_attempts = 3
        if internal:
            if not idempotency_scope.startswith("system:"):
                raise BacktestQueueError("系统任务必须使用 system: 幂等范围。")
            if "available_at" in request:
                available_at = _timestamp(request["available_at"], "available_at")
            if "deadline_at" in request:
                deadline_at = _timestamp(request["deadline_at"], "deadline_at")
                if deadline_at <= available_at:
                    raise BacktestQueueError("deadline_at 必须晚于 available_at。")
            priority = request.get("priority", 0)
            max_attempts = request.get("max_attempts", 3)
            if not isinstance(priority, int) or isinstance(priority, bool) or not -100 <= priority <= 100:
                raise BacktestQueueError("priority 无效。")
            if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 5:
                raise BacktestQueueError("max_attempts 无效。")
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM backtest_jobs WHERE idempotency_scope=? AND idempotency_key=?", (idempotency_scope, idempotency_key)).fetchone()
            if existing:
                row = self._row(dict(existing))
                if row["request_sha256"] != request_hash:
                    raise BacktestQueueError("Idempotency-Key 已用于不同请求。", 409)
                if (
                    row["owner_scope"] == "system"
                    and row["idempotency_scope"] == "system:compute-gate"
                    and row["system_daily_attempt_limit"] is None
                    and system_daily_runs_limit is not None
                ):
                    conn.execute(
                        """UPDATE backtest_jobs SET system_daily_attempt_limit=?,system_budget_timezone=?,updated_at=?
                           WHERE id=?""",
                        (system_daily_runs_limit, system_budget_timezone, now, row["id"]),
                    )
                    row = self._row(
                        dict(conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (row["id"],)).fetchone())
                    )
                return row, False
            if job_type == "candidate.evaluate.v1":
                self._require_candidate_parent(conn, manifest)
            if owner_id is None:
                if system_pending_limit is not None:
                    pending = conn.execute(
                        "SELECT count(*) FROM backtest_jobs WHERE owner_scope='system' AND status IN ('queued','preparing','running')"
                    ).fetchone()[0]
                    if pending >= system_pending_limit:
                        raise BacktestQueueError("系统研究队列已达到待处理预算。", 429)
                if system_daily_limit is not None:
                    daily = conn.execute(
                        "SELECT count(*) FROM backtest_jobs WHERE owner_scope='system' AND created_at>=?",
                        (system_day_start,),
                    ).fetchone()[0]
                    if daily >= system_daily_limit:
                        raise BacktestQueueError("系统研究任务已达到每日预算。", 429)
                if system_daily_runs_limit is not None:
                    rows = conn.execute(
                        "SELECT manifest_json FROM backtest_jobs WHERE owner_scope='system' AND created_at>=?",
                        (system_day_start,),
                    ).fetchall()
                    reserved = sum(int(json.loads(item["manifest_json"]).get("experiment_budget", {}).get("runs", 0)) for item in rows)
                    incoming = int(manifest.get("experiment_budget", {}).get("runs", 0))
                    if reserved + incoming > system_daily_runs_limit:
                        raise BacktestQueueError("系统研究实验运行数已达到每日预算。", 429)
            if owner_id is not None:
                waiting_limit, daily_limit = self._limits(plan)
                queued = conn.execute("SELECT count(*) FROM backtest_jobs WHERE owner_id=? AND status IN ('queued','preparing')", (owner_id,)).fetchone()[0]
                daily = conn.execute("SELECT count(*) FROM backtest_jobs WHERE owner_id=? AND created_at >= ?", (owner_id, now[:10])).fetchone()[0]
                if queued >= waiting_limit or daily >= daily_limit:
                    raise BacktestQueueError("回测队列已达到个人上限。", 429)
            job_id = uuid.uuid4().hex
            conn.execute("""INSERT INTO backtest_jobs(id,owner_id,owner_scope,job_type,status,idempotency_scope,idempotency_key,request_sha256,manifest_json,manifest_sha256,max_attempts,available_at,deadline_at,priority,created_at,updated_at,system_daily_attempt_limit,system_budget_timezone)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (job_id, owner_id, "system" if owner_id is None else "user", job_type, "preparing" if preparing else "queued", idempotency_scope, idempotency_key, request_hash, _json(manifest), manifest_hash, max_attempts, available_at, deadline_at, priority, now, now, system_daily_runs_limit, system_budget_timezone))
            return self._row(dict(conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone())), True

    @staticmethod
    def _attempt_budget_available(conn: Any, row: Any, now: datetime) -> bool:
        limit = row["system_daily_attempt_limit"]
        if limit is None:
            return row["idempotency_scope"] != "system:compute-gate"
        try:
            zone = ZoneInfo(str(row["system_budget_timezone"]))
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise BacktestQueueError("系统研究任务预算时区无效。", 409) from exc
        local_day = now.astimezone(zone).date()
        start = datetime.combine(local_day, time.min, tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
        attempts = conn.execute(
            """SELECT count(*) FROM backtest_job_attempts a
               JOIN backtest_jobs j ON j.id=a.job_id
               WHERE j.owner_scope='system' AND a.claimed_at>=? AND a.claimed_at<?""",
            (_stamp(start), _stamp(end)),
        ).fetchone()[0]
        return int(attempts) < int(limit)

    def get(self, job_id: str, owner_id: int | None = None) -> dict[str, Any]:
        query, params = "SELECT * FROM backtest_jobs WHERE id=?", [job_id]
        if owner_id is not None:
            query += " AND owner_id=? AND owner_scope='user'"
            params.append(owner_id)
        row = self.db.fetch_one(query, tuple(params))
        if not row:
            raise BacktestQueueError("找不到回测任务。", 404)
        return self._row(row) or {}

    def find_idempotent(self, owner_id: int, idempotency_key: str) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            """SELECT * FROM backtest_jobs
               WHERE owner_id=? AND owner_scope='user' AND idempotency_scope=?
                 AND idempotency_key=?""",
            (owner_id, f"user:{owner_id}", idempotency_key),
        )
        return self._row(row)

    def inputs_ready(self, job_id: str, owner_id: int) -> bool:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM backtest_jobs WHERE id=? AND owner_id=? AND owner_scope='user'",
                (job_id, owner_id),
            ).fetchone()
            if not row:
                raise BacktestQueueError("找不到回测任务。", 404)
            return self._inputs_ready(conn, row)

    def release_prepared(self, job_id: str, owner_id: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM backtest_jobs WHERE id=? AND owner_id=? AND owner_scope='user'",
                (job_id, owner_id),
            ).fetchone()
            if not row:
                raise BacktestQueueError("找不到回测任务。", 404)
            if row["status"] == "queued":
                if not self._inputs_ready(conn, row):
                    raise BacktestQueueError("冻结输入完整性验证失败。", 409)
                return self._row(dict(row)) or {}
            if row["status"] != "preparing" or row["cancel_requested"]:
                raise BacktestQueueError("任务不在可完成准备的状态。", 409)
            if not self._inputs_ready(conn, row):
                raise BacktestQueueError("冻结输入完整性验证失败。", 409)
            now = _stamp()
            conn.execute(
                "UPDATE backtest_jobs SET status='queued',updated_at=? WHERE id=? AND status='preparing'",
                (now, job_id),
            )
            return self._row(
                dict(conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone())
            ) or {}

    def list(self, owner_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.fetch_all("SELECT * FROM backtest_jobs WHERE owner_id=? AND owner_scope='user' ORDER BY created_at DESC LIMIT ?", (owner_id, min(max(limit, 1), 100)))
        return [self._row(row) or {} for row in rows]

    def owner_failure(self, job_id: str, owner_id: int) -> dict[str, Any] | None:
        job = self.get(job_id, owner_id)
        if job["status"] != "failed":
            return None
        row = self.db.fetch_one(
            """SELECT error_json FROM backtest_job_attempts
               WHERE job_id=? AND error_json IS NOT NULL
               ORDER BY attempt_no DESC LIMIT 1""",
            (job_id,),
        )
        if not row:
            return {
                "error_code": "UNKNOWN",
                "summary": "任务执行失败。",
                "retryable": False,
            }
        error = json.loads(row["error_json"])
        return {
            "error_code": str(error.get("error_code") or "UNKNOWN"),
            "summary": "任务执行失败。",
            "retryable": False,
        }

    def cancel(self, job_id: str, owner_id: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM backtest_jobs WHERE id=? AND owner_id=? AND owner_scope='user'", (job_id, owner_id)).fetchone()
            if not row:
                raise BacktestQueueError("找不到回测任务。", 404)
            if row["status"] in {"queued", "preparing"}:
                conn.execute("""UPDATE backtest_jobs SET status='cancelled',cancel_requested=1,
                    cancel_source=?,cancel_reason=?,completed_at=?,updated_at=? WHERE id=?""",
                    (f"user:{owner_id}", "user requested cancellation", _stamp(), _stamp(), job_id))
            elif row["status"] == "running":
                conn.execute("""UPDATE backtest_jobs SET cancel_requested=1,cancel_source=?,cancel_reason=?,updated_at=?
                    WHERE id=?""", (f"user:{owner_id}", "user requested cancellation", _stamp(), job_id))
            return self._row(dict(conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone())) or {}

    def _inputs_ready(self, conn: Any, row: Any) -> bool:
        manifest = json.loads(row["manifest_json"])
        for item in manifest["inputs"]:
            artifact = conn.execute("SELECT sha256,bytes,row_count,state FROM backtest_job_artifacts WHERE job_id=? AND attempt_no=0 AND direction='input' AND artifact_key=?", (row["id"], item["artifact_key"])).fetchone()
            if not artifact or artifact["state"] != "verified" or artifact["sha256"] != item["sha256"]:
                return False
            if "bytes" in item and artifact["bytes"] != item["bytes"]:
                return False
            if "rows" in item and artifact["row_count"] != item["rows"]:
                return False
        return True

    def _expire(self, conn: Any, now: str) -> None:
        rows = conn.execute("SELECT * FROM backtest_jobs WHERE status='running' AND lease_expires_at <= ?", (now,)).fetchall()
        for row in rows:
            terminal = "cancelled" if row["cancel_requested"] else ("failed" if row["attempt_count"] >= row["max_attempts"] else "queued")
            finished = now if terminal in {"cancelled", "failed"} else None
            conn.execute("""UPDATE backtest_jobs SET status=?,worker_id=NULL,lease_token_sha256=NULL,
                lease_expires_at=NULL,attempt_deadline_at=NULL,heartbeat_at=NULL,
                completed_at=COALESCE(?,completed_at),updated_at=? WHERE id=?""", (terminal, finished, now, row["id"]))
            conn.execute("UPDATE backtest_job_attempts SET status='expired',finished_at=?,updated_at=? WHERE job_id=? AND attempt_no=?", (now, now, row["id"], row["attempt_count"]))

    def claim(self, worker_id: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", worker_id):
            raise BacktestQueueError("Worker ID 无效。", 401)
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 10 <= lease_seconds <= 600:
            raise BacktestQueueError("lease_seconds 必须为 10 至 600 的整数。")
        now_dt = _now()
        now, raw = _stamp(now_dt), secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._expire(conn, now)
            conn.execute("""UPDATE backtest_jobs SET status='failed',completed_at=?,updated_at=?
                WHERE status IN ('queued','preparing') AND deadline_at IS NOT NULL AND deadline_at <= ?""", (now, now, now))
            if conn.execute("SELECT 1 FROM backtest_jobs WHERE status='running' AND lease_expires_at > ?", (now,)).fetchone():
                return None
            candidates = conn.execute("""SELECT * FROM backtest_jobs WHERE status='queued' AND cancel_requested=0
                AND available_at <= ? AND (deadline_at IS NULL OR deadline_at > ?) AND attempt_count < max_attempts
                ORDER BY priority DESC,available_at,created_at,id""", (now, now)).fetchall()
            row = next(
                (
                    item
                    for item in candidates
                    if self._inputs_ready(conn, item) and self._attempt_budget_available(conn, item, now_dt)
                ),
                None,
            )
            if row is None:
                return None
            attempt_deadline_dt = now_dt + timedelta(seconds=MAX_ATTEMPT_SECONDS)
            if row["deadline_at"]:
                job_deadline = datetime.fromisoformat(row["deadline_at"].replace("Z", "+00:00"))
                attempt_deadline_dt = min(attempt_deadline_dt, job_deadline)
            attempt_deadline = _stamp(attempt_deadline_dt)
            expires = _stamp(min(now_dt + timedelta(seconds=lease_seconds), attempt_deadline_dt))
            attempt, fence = row["attempt_count"] + 1, row["fencing_epoch"] + 1
            conn.execute("""UPDATE backtest_jobs SET status='running',attempt_count=?,worker_id=?,lease_token_sha256=?,lease_seconds=?,lease_expires_at=?,attempt_deadline_at=?,heartbeat_at=?,fencing_epoch=?,progress=0,progress_stage='queued',updated_at=? WHERE id=?""", (attempt, worker_id, token_hash, lease_seconds, expires, attempt_deadline, now, fence, now, row["id"]))
            conn.execute("""INSERT INTO backtest_job_attempts(job_id,attempt_no,worker_id,fencing_epoch,lease_token_sha256,status,claimed_at,lease_expires_at,attempt_deadline_at,heartbeat_at,created_at,updated_at)
                VALUES(?,?,?,?,?,'claimed',?,?,?,?,?,?)""", (row["id"], attempt, worker_id, fence, token_hash, now, expires, attempt_deadline, now, now, now))
            claimed = self._row(dict(conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (row["id"],)).fetchone())) or {}
            claimed["lease_token"] = raw
            return claimed

    def _lease(self, conn: Any, job_id: str, worker_id: str, lease_token: str, fencing_epoch: int, *, allow_completed: bool = False) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise BacktestQueueError("找不到回测任务。", 404)
        digest = hashlib.sha256(lease_token.encode()).hexdigest()
        if row["worker_id"] != worker_id or not secrets.compare_digest(row["lease_token_sha256"] or "", digest) or int(row["fencing_epoch"]) != int(fencing_epoch):
            raise BacktestQueueError("租约已失效。", 409)
        if allow_completed and row["status"] == "completed":
            return dict(row)
        now = _stamp()
        if row["status"] != "running" or not row["lease_expires_at"] or row["lease_expires_at"] <= now:
            raise BacktestQueueError("租约已失效。", 409)
        if not row["attempt_deadline_at"] or row["attempt_deadline_at"] <= now:
            raise BacktestQueueError("租约已失效。", 409)
        return dict(row)

    def _output_lease(self, conn: Any, job_id: str, artifact_key: str, worker_id: str, lease_token: str, fencing_epoch: int) -> dict[str, Any]:
        if not ArtifactStore.valid_key(artifact_key):
            raise BacktestQueueError("artifact_key 无效。")
        row = self._lease(conn, job_id, worker_id, lease_token, fencing_epoch)
        if row["cancel_requested"]:
            raise BacktestQueueError("任务已请求取消，不能继续上传输出。", 409)
        return row

    def verify_output_lease(self, job_id: str, artifact_key: str, worker_id: str, lease_token: str, fencing_epoch: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._output_lease(conn, job_id, artifact_key, worker_id, lease_token, fencing_epoch)
            return {"attempt_no": row["attempt_count"], "lease_expires_at": row["lease_expires_at"]}

    def heartbeat(self, job_id: str, worker_id: str, lease_token: str, fencing_epoch: int, progress: float, stage: str) -> dict[str, Any]:
        if not isinstance(progress, (int, float)) or isinstance(progress, bool) or not 0 <= progress <= 1:
            raise BacktestQueueError("progress 必须在 0 到 1 之间。")
        if stage not in STAGES:
            raise BacktestQueueError("progress_stage 无效。")
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._lease(conn, job_id, worker_id, lease_token, fencing_epoch)
            if row["cancel_requested"]:
                return {
                    "progress": row["progress"],
                    "stage": row["progress_stage"],
                    "cancel_requested": True,
                    "lease_expires_at": row["lease_expires_at"],
                }
            if progress < row["progress"]:
                raise BacktestQueueError("progress 不得倒退。", 409)
            stage_order = {name: index for index, name in enumerate(("queued", "loading", "executing", "finalizing"))}
            if stage_order[stage] < stage_order[row["progress_stage"]]:
                raise BacktestQueueError("progress_stage 不得倒退。", 409)
            now = _stamp()
            candidate_expiry = _now() + timedelta(seconds=row["lease_seconds"])
            attempt_deadline = datetime.fromisoformat(row["attempt_deadline_at"].replace("Z", "+00:00"))
            expires = _stamp(min(candidate_expiry, attempt_deadline))
            conn.execute("UPDATE backtest_jobs SET progress=?,progress_stage=?,heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE id=?", (progress, stage, now, expires, now, job_id))
            conn.execute("UPDATE backtest_job_attempts SET heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE job_id=? AND attempt_no=?", (now, expires, now, job_id, row["attempt_count"]))
            return {"progress": progress, "stage": stage, "cancel_requested": bool(row["cancel_requested"]), "lease_expires_at": expires}

    def _validate_output_hashes(self, conn: Any, row: dict[str, Any], result: dict[str, Any]) -> None:
        hashes = result["output_hashes"]
        if not isinstance(hashes, dict) or not all(isinstance(key, str) and SHA.fullmatch(value) for key, value in hashes.items()):
            raise BacktestQueueError("output_hashes 无效。")
        rows = conn.execute("""SELECT artifact_key,sha256 FROM backtest_job_artifacts
            WHERE job_id=? AND attempt_no=? AND direction='output' AND state='verified'""", (row["id"], row["attempt_count"])).fetchall()
        if hashes != {item["artifact_key"]: item["sha256"] for item in rows}:
            raise BacktestQueueError("结果输出哈希不匹配。", 409)

    def complete(self, job_id: str, worker_id: str, lease_token: str, fencing_epoch: int, result: Any) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._lease(conn, job_id, worker_id, lease_token, fencing_epoch, allow_completed=True)
            validated = validate_result_shape(result, row)
            result_hash = sha256_json(validated)
            if row["status"] == "completed":
                if row["result_sha256"] == result_hash:
                    return self._row(row) or {}
                raise BacktestQueueError("任务已用不同结果完成。", 409)
            if row["cancel_requested"]:
                raise BacktestQueueError("任务已请求取消。", 409)
            self._validate_output_hashes(conn, row, validated)
            now = _stamp()
            conn.execute("UPDATE backtest_jobs SET status='completed',result_json=?,result_sha256=?,progress=1,progress_stage='finalizing',completed_at=?,updated_at=? WHERE id=?", (_json(validated), result_hash, now, now, job_id))
            conn.execute("UPDATE backtest_job_attempts SET status='completed',finished_at=?,updated_at=? WHERE job_id=? AND attempt_no=?", (now, now, job_id, row["attempt_count"]))
            return self._row(dict(conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone())) or {}

    def fail(self, job_id: str, worker_id: str, lease_token: str, fencing_epoch: int, error: Any) -> dict[str, Any]:
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._lease(conn, job_id, worker_id, lease_token, fencing_epoch)
            error_obj = validate_failure(error)
            now = _stamp()
            deadline_open = row["deadline_at"] is None or row["deadline_at"] > now
            retry = bool(error_obj["retryable"] and row["attempt_count"] < row["max_attempts"] and deadline_open and not row["cancel_requested"])
            status = "queued" if retry else ("cancelled" if row["cancel_requested"] else "failed")
            completed_at = None if retry else now
            available_at = _stamp(_now() + timedelta(seconds=min(300, 5 * (2 ** row["attempt_count"])))) if retry else row["available_at"]
            conn.execute("""UPDATE backtest_jobs SET status=?,worker_id=NULL,lease_token_sha256=NULL,
                lease_expires_at=NULL,attempt_deadline_at=NULL,heartbeat_at=NULL,progress_stage='queued',
                available_at=?,completed_at=?,updated_at=? WHERE id=?""",
                (status, available_at, completed_at, now, job_id))
            conn.execute("UPDATE backtest_job_attempts SET status='failed',error_json=?,finished_at=?,updated_at=? WHERE job_id=? AND attempt_no=?", (_json(error_obj), now, now, job_id, row["attempt_count"]))
            return self._row(dict(conn.execute("SELECT * FROM backtest_jobs WHERE id=?", (job_id,)).fetchone())) or {}
