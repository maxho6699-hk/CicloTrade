"""Owner-scoped public workflow registry with append-only event history."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from typing import Any, Iterator, Mapping

from core.database import DatabaseManager

WORKFLOW_STATUSES = frozenset({"queued", "running", "succeeded", "partial", "failed", "cancelled", "blocked", "timed_out"})
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class WorkflowError(ValueError):
    pass


class WorkflowNotFound(WorkflowError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Workflow 数据无法规范化。") from exc


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _reject_urls(value: Any, *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for name, item in value.items():
            lowered = str(name).casefold()
            if lowered in {"url", "uri", "href", "download_url", "artifact_url"}:
                raise WorkflowError("Workflow 不接受任意 URL。")
            _reject_urls(item, key=lowered)
    elif isinstance(value, list):
        for item in value:
            _reject_urls(item, key=key)
    elif isinstance(value, str) and re.match(r"^https?://", value.strip(), re.I):
        raise WorkflowError("Workflow 不接受任意 URL。")


class WorkflowRegistry:
    def __init__(self, database: DatabaseManager | Any):
        self.db = database

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        if isinstance(self.db, DatabaseManager):
            with self.db.transaction() as conn:
                yield conn
        else:
            try:
                yield self.db
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def _fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if isinstance(self.db, DatabaseManager):
            return self.db.fetch_all(sql, params)
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    @staticmethod
    def _view(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result.pop("owner_id", None)
        context_raw = result.pop("context_json", None)
        result["context"] = json.loads(context_raw) if context_raw else None
        # Provenance content remains server-side.  The public owner-scoped DTO
        # exposes its immutable digest without returning internal source detail.
        result.pop("provenance_json", None)
        result_raw = result.pop("result_json", None)
        result["result"] = json.loads(result_raw) if result_raw else None
        result["cancel_requested"] = bool(result.get("cancel_requested"))
        result["artifacts"] = []
        return result

    def create(self, owner_id: int, *, source_kind: str, source_public_id: str, context: Mapping[str, Any] | None = None, provenance: Mapping[str, Any] | None = None, attempt: int = 1) -> dict[str, Any]:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
            raise WorkflowError("owner_id 无效。")
        source_kind, source_public_id = str(source_kind).strip(), str(source_public_id).strip()
        if not 2 <= len(source_kind) <= 64 or not 1 <= len(source_public_id) <= 160 or not _ID.fullmatch(source_public_id):
            raise WorkflowError("Workflow 来源字段无效。")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise WorkflowError("Workflow attempt 无效。")
        context = dict(context or {})
        provenance = dict(provenance or {})
        _reject_urls(context)
        _reject_urls(provenance)
        context_raw, provenance_raw = canonical_json(context), canonical_json(provenance)
        now, task_id = _now(), _public_id("wfl")
        with self._transaction() as conn:
            conn.execute("INSERT INTO workflow_tasks(task_public_id,owner_id,source_kind,source_public_id,attempt,status,context_json,context_sha256,provenance_json,provenance_sha256,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (task_id, owner_id, source_kind, source_public_id, attempt, "queued", context_raw, hashlib.sha256(context_raw.encode()).hexdigest(), provenance_raw, hashlib.sha256(provenance_raw.encode()).hexdigest(), now, now))
            conn.execute("INSERT INTO workflow_public_events(task_public_id,seq,event_type,status,payload_json,created_at) VALUES (?,?,?,?,?,?)", (task_id, 1, "created", "queued", canonical_json({"attempt": attempt}), now))
            row = conn.execute("SELECT * FROM workflow_tasks WHERE task_public_id=?", (task_id,)).fetchone()
        return self._view(dict(row))

    def _row(self, conn: Any, owner_id: int, task_public_id: str) -> Any:
        row = conn.execute("SELECT * FROM workflow_tasks WHERE task_public_id=? AND owner_id=?", (task_public_id, owner_id)).fetchone()
        if not row:
            raise WorkflowNotFound("Workflow 不存在。")
        return row

    def get(self, owner_id: int, task_public_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            row = self._row(conn, owner_id, task_public_id)
            result = self._view(dict(row))
            events = conn.execute("SELECT seq,event_type,status,payload_json,created_at FROM workflow_public_events WHERE task_public_id=? ORDER BY seq", (task_public_id,)).fetchall()
            result["events"] = [
                {
                    "seq": event["seq"],
                    "event_type": event["event_type"],
                    "status": event["status"],
                    "payload": json.loads(event["payload_json"]),
                    "created_at": event["created_at"],
                }
                for event in events
            ]
            has_deliberation = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deliberation_jobs'").fetchone()
            if has_deliberation:
                projection = conn.execute("SELECT result_json FROM deliberation_jobs WHERE task_public_id=? AND owner_id=?", (task_public_id, owner_id)).fetchone()
                result["deliberation"] = json.loads(projection["result_json"]) if projection else None
        return result

    def list(self, owner_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = min(max(int(limit), 1), 100)
        return [self._view(row) for row in self._fetch("SELECT * FROM workflow_tasks WHERE owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_id, bounded))]

    def transition(self, task_public_id: str, owner_id: int, status: str, *, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if status not in WORKFLOW_STATUSES:
            raise WorkflowError("Workflow 状态无效。")
        payload = dict(payload or {})
        _reject_urls(payload)
        with self._transaction() as conn:
            row = self._row(conn, owner_id, task_public_id)
            current = str(row["status"])
            if current in {"succeeded", "partial", "failed", "cancelled", "blocked", "timed_out"} and status != current:
                raise WorkflowError("终态 Workflow 不可改写，请创建 retry。")
            now = _now()
            conn.execute("UPDATE workflow_tasks SET status=?,updated_at=?,completed_at=? WHERE task_public_id=?", (status, now, now if status in WORKFLOW_STATUSES - {"queued", "running"} else row["completed_at"], task_public_id))
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM workflow_public_events WHERE task_public_id=?", (task_public_id,)).fetchone()[0]
            conn.execute("INSERT INTO workflow_public_events(task_public_id,seq,event_type,status,payload_json,created_at) VALUES (?,?,?,?,?,?)", (task_public_id, seq, "status", status, canonical_json(payload), now))
            fresh = conn.execute("SELECT * FROM workflow_tasks WHERE task_public_id=?", (task_public_id,)).fetchone()
        return self._view(dict(fresh))

    def set_result(self, task_public_id: str, owner_id: int, result: Mapping[str, Any], *, status: str) -> dict[str, Any]:
        if status not in WORKFLOW_STATUSES:
            raise WorkflowError("Workflow 状态无效。")
        _reject_urls(result)
        raw = canonical_json(dict(result))
        with self._transaction() as conn:
            self._row(conn, owner_id, task_public_id)
            now = _now()
            conn.execute("UPDATE workflow_tasks SET status=?,result_json=?,result_sha256=?,updated_at=?,completed_at=? WHERE task_public_id=?", (status, raw, hashlib.sha256(raw.encode()).hexdigest(), now, now, task_public_id))
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM workflow_public_events WHERE task_public_id=?", (task_public_id,)).fetchone()[0]
            conn.execute("INSERT INTO workflow_public_events(task_public_id,seq,event_type,status,payload_json,created_at) VALUES (?,?,?,?,?,?)", (task_public_id, seq, "result", status, raw, now))
            row = conn.execute("SELECT * FROM workflow_tasks WHERE task_public_id=?", (task_public_id,)).fetchone()
        return self._view(dict(row))

    def cancel(self, owner_id: int, task_public_id: str) -> dict[str, Any]:
        return self.transition(task_public_id, owner_id, "cancelled", payload={"reason": "user_requested"})

    def retry(self, owner_id: int, task_public_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            row = self._row(conn, owner_id, task_public_id)
            context, provenance = json.loads(row["context_json"]), json.loads(row["provenance_json"])
        return self.create(owner_id, source_kind=row["source_kind"], source_public_id=row["source_public_id"], context=context, provenance=provenance, attempt=int(row["attempt"]) + 1)

    create_task = create
    get_task = get
    list_tasks = list
    cancel_task = cancel
    retry_task = retry


__all__ = ["WORKFLOW_STATUSES", "WorkflowError", "WorkflowNotFound", "WorkflowRegistry", "canonical_json", "sha256"]
