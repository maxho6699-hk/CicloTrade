"""Deterministic four-seat evidence deliberation over immutable server snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import inspect
import json
import re
import secrets
from typing import Any, Callable, Iterator, Mapping

from core.database import DatabaseManager
from core.workflow_registry import WorkflowRegistry, canonical_json

DELIBERATION_METHOD_VERSION = "deliberation.v1"
SEATS = ("market_structure", "fundamentals", "news_macro", "risk")
SEAT_WEIGHTS_BPS = {"market_structure": 3000, "fundamentals": 2500, "news_macro": 2000, "risk": 2500}
_SHA = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset({"queued", "running", "succeeded", "partial", "failed", "cancelled", "blocked", "timed_out"})


class DeliberationError(ValueError):
    status = 400


class DeliberationForbidden(DeliberationError):
    status = 403


class DeliberationNotFound(DeliberationError):
    status = 404


class DeliberationConflict(DeliberationError):
    status = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise DeliberationError(f"{name} 无效。")
    return value.strip()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if 0 <= number <= 100 else None


def _score(seat: Mapping[str, Any], direct: str, collection: str) -> float | None:
    direct_value = _number(seat.get(direct))
    if direct_value is not None:
        return direct_value
    values: list[float] = []
    entries = seat.get(collection)
    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, Mapping):
                value = _number(item.get("strength"))
                if value is not None:
                    values.append(value)
    return round(sum(values) / len(values), 6) if values else None


def _snapshot_payload(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        return None, None
    if isinstance(value.get("snapshot"), Mapping):
        supplied_hash = value.get("snapshot_sha256") or value["snapshot"].get("snapshot_sha256")
        snapshot = dict(value["snapshot"])
    else:
        supplied_hash = value.get("snapshot_sha256")
        snapshot = dict(value)
    declared = str(supplied_hash or "").lower() or None
    material = dict(snapshot)
    material.pop("snapshot_sha256", None)
    computed = _sha(material)
    if declared is not None and (not _SHA.fullmatch(declared) or declared != computed):
        raise DeliberationConflict("证据快照 SHA-256 与内容不一致。")
    return snapshot, computed


def _callback(callback: Callable[..., Any] | None, *args: Any) -> Any:
    if callback is None:
        return None
    try:
        signature = inspect.signature(callback)
        if len(signature.parameters) == len(args):
            return callback(*args)
    except (TypeError, ValueError):
        pass
    return callback(args[0]) if args else callback()


class DeliberationService:
    def __init__(self, database: DatabaseManager | Any, *, authorize: Callable[..., bool] | None = None, evidence_loader: Callable[..., Any] | None = None, workflow_registry: WorkflowRegistry | None = None, clock: Callable[[], str] | None = None):
        self.db = database
        self.authorize = authorize
        self.evidence_loader = evidence_loader
        self.workflow = workflow_registry or WorkflowRegistry(database)
        self.clock = clock or _now

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

    def _allowed(self, owner_id: int) -> bool:
        result = _callback(self.authorize, owner_id, "multi_agent_deliberation")
        return bool(result)

    def _require_allowed(self, owner_id: int) -> None:
        if not self._allowed(owner_id):
            raise DeliberationForbidden("当前会员或权限未开放多智能体审议。")

    @staticmethod
    def _binding(payload: Mapping[str, Any]) -> dict[str, Any]:
        market = _text(payload.get("market"), "market", 32).upper()
        symbol = _text(payload.get("symbol"), "symbol", 32).upper()
        timeframe = _text(payload.get("timeframe"), "timeframe", 32)
        question = _text(payload.get("question", "资料审阅"), "question", 4000)
        source_event_id = _text(payload.get("source_event_id"), "source_event_id", 160)
        version = payload.get("source_event_version")
        digest = str(payload.get("source_event_sha256") or "").lower()
        if isinstance(version, bool) or not isinstance(version, int) or version < 1 or not _SHA.fullmatch(digest):
            raise DeliberationError("source event 绑定字段无效。")
        return {"market": market, "symbol": symbol, "timeframe": timeframe, "question": question, "source_event_id": source_event_id, "source_event_version": version, "source_event_sha256": digest}

    @staticmethod
    def _validate_snapshot_binding(
        binding: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> None:
        source_id = snapshot.get("source_event_id")
        source_version = snapshot.get("source_event_version")
        source_sha256 = str(snapshot.get("source_event_sha256") or "").lower()
        if (
            source_id is None
            or isinstance(source_version, bool)
            or not isinstance(source_version, int)
            or source_version < 1
            or not _SHA.fullmatch(source_sha256)
        ):
            raise DeliberationConflict("证据快照缺少原始事件绑定。")
        if str(source_id) != str(binding["source_event_id"]):
            raise DeliberationConflict("证据快照来源事件不匹配。")
        if source_version != int(binding["source_event_version"]):
            raise DeliberationConflict("证据快照来源版本不匹配。")
        if source_sha256 != binding["source_event_sha256"]:
            raise DeliberationConflict("证据快照来源哈希不匹配。")

    def readiness(self, owner_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_allowed(owner_id)
        binding = self._binding(payload)
        try:
            snapshot = _callback(self.evidence_loader, owner_id, binding)
        except Exception:
            return {**binding, "ready": False, "status": "blocked", "missing": list(SEATS), "reason": "evidence_loader_unavailable"}
        normalized, digest = _snapshot_payload(snapshot)
        if normalized is None:
            return {**binding, "ready": False, "status": "blocked", "missing": list(SEATS), "reason": "evidence_snapshot_missing"}
        self._validate_snapshot_binding(binding, normalized)
        seats = normalized.get("seats", {})
        missing = []
        for seat_name in SEATS:
            seat = seats.get(seat_name) if isinstance(seats, Mapping) else None
            if (
                not isinstance(seat, Mapping)
                or _score(seat, "support_strength", "support") is None
                or _score(seat, "counter_evidence_strength", "counter_evidence") is None
                or not seat.get("source")
                or not seat.get("citation")
            ):
                missing.append(seat_name)
        return {**binding, "ready": not missing, "status": "succeeded" if not missing else "partial", "missing": missing, "snapshot_sha256": digest, "evidence_version": normalized.get("evidence_version"), "research_version": normalized.get("research_version")}

    def _compute(self, binding: Mapping[str, Any], snapshot_value: Any) -> dict[str, Any]:
        snapshot, snapshot_hash = _snapshot_payload(snapshot_value)
        if snapshot is not None:
            self._validate_snapshot_binding(binding, snapshot)
        calculated_at = (
            str(snapshot.get("calculated_at") or snapshot.get("as_of"))
            if snapshot
            else self.clock()
        )
        seats_raw = snapshot.get("seats", {}) if snapshot else {}
        missing: list[str] = []
        seats: dict[str, dict[str, Any]] = {}
        support_total = counter_total = 0.0
        observed = available = as_of = None
        evidence_version = research_version = None
        if snapshot:
            observed, available, as_of = snapshot.get("observed_at"), snapshot.get("available_at"), snapshot.get("as_of")
            evidence_version, research_version = snapshot.get("evidence_version"), snapshot.get("research_version")
        snapshot_invalidated = bool(snapshot and isinstance(snapshot.get("invalidated_reason"), str) and snapshot.get("invalidated_reason").strip())
        for seat_name in SEATS:
            raw = seats_raw.get(seat_name) if isinstance(seats_raw, Mapping) else None
            raw = raw if isinstance(raw, Mapping) else {}
            support = _score(raw, "support_strength", "support")
            counter = _score(raw, "counter_evidence_strength", "counter_evidence")
            seat_missing = []
            if snapshot_invalidated:
                support = counter = None
                seat_missing.append("invalidated")
            if support is None:
                seat_missing.append("support_strength")
            if counter is None:
                seat_missing.append("counter_evidence_strength")
            if seat_missing:
                missing.append(seat_name)
            weight = SEAT_WEIGHTS_BPS[seat_name]
            contribution = {"support": round(support * weight / 10000, 6) if support is not None else None, "counter": round(counter * weight / 10000, 6) if counter is not None else None}
            source_value = raw.get("source", raw.get("sources"))
            source = source_value if isinstance(source_value, (str, list, dict)) and source_value else None
            citation = raw.get("citation") if isinstance(raw.get("citation"), (str, list, dict)) else None
            if source is None:
                seat_missing.append("source")
            if citation is None:
                seat_missing.append("citation")
            if seat_missing and seat_name not in missing:
                missing.append(seat_name)
            if not seat_missing:
                if support is not None:
                    support_total += contribution["support"]
                if counter is not None:
                    counter_total += contribution["counter"]
            seats[seat_name] = {"seat": seat_name, "status": "ready" if not seat_missing else "missing", "support_strength": support, "counter_evidence_strength": counter, "weight_bps": weight, "contribution": contribution, "coverage": _number(raw.get("coverage")), "source": source, "citation": citation, "missing": seat_missing, "invalidated_reason": raw.get("invalidated_reason") if isinstance(raw.get("invalidated_reason"), str) else None}
        ready_count = len(SEATS) - len(missing)
        status = "blocked" if not snapshot or snapshot_invalidated or ready_count == 0 else "partial" if missing else "succeeded"
        result = {"deliberation_public_id": None, "task_public_id": None, **dict(binding), "status": status, "method_version": DELIBERATION_METHOD_VERSION, "evidence_version": evidence_version, "research_version": research_version, "support_strength": round(support_total, 6) if ready_count else None, "counter_evidence_strength": round(counter_total, 6) if ready_count else None, "coverage": round(ready_count / len(SEATS), 6), "missing": missing if snapshot else list(SEATS), "seats": seats, "observed_at": observed, "available_at": available, "as_of": as_of, "calculated_at": calculated_at, "invalidated_reason": snapshot.get("invalidated_reason") if snapshot else "evidence_snapshot_missing", "evidence_snapshot_sha256": snapshot_hash, "result_sha256": None}
        result["result_sha256"] = _sha({k: v for k, v in result.items() if k != "result_sha256"})
        return result

    def create(self, owner_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_allowed(owner_id)
        if not isinstance(payload, Mapping):
            raise DeliberationError("审议请求必须是对象。")
        binding = self._binding(payload)
        deliberation_id = _public_id("dlb")
        source = {"source_event_id": binding["source_event_id"], "source_event_version": binding["source_event_version"], "source_event_sha256": binding["source_event_sha256"]}
        try:
            snapshot_value = _callback(self.evidence_loader, owner_id, binding)
            result = self._compute(binding, snapshot_value)
        except DeliberationConflict:
            raise
        except Exception:
            snapshot_value = None
            result = self._compute(binding, None)
        task = self.workflow.create(owner_id, source_kind="deliberation", source_public_id=deliberation_id, context={k: binding[k] for k in ("market", "symbol", "timeframe", "question")}, provenance=source)
        self.workflow.transition(task["task_public_id"], owner_id, "running")
        result["deliberation_public_id"], result["task_public_id"] = deliberation_id, task["task_public_id"]
        result["result_sha256"] = _sha({k: v for k, v in result.items() if k != "result_sha256"})
        raw = canonical_json(result)
        snapshot, _ = _snapshot_payload(snapshot_value)
        snapshot_raw = canonical_json(snapshot) if snapshot is not None else None
        now = self.clock()
        with self._transaction() as conn:
            conn.execute("INSERT INTO deliberation_jobs(deliberation_public_id,task_public_id,owner_id,market,symbol,timeframe,question,source_event_id,source_event_version,source_event_sha256,status,evidence_snapshot_json,evidence_snapshot_sha256,method_version,evidence_version,research_version,support_strength,counter_evidence_strength,coverage,missing_json,seats_json,invalidated_reason,observed_at,available_at,as_of,calculated_at,result_json,result_sha256,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (deliberation_id, task["task_public_id"], owner_id, binding["market"], binding["symbol"], binding["timeframe"], binding["question"], binding["source_event_id"], binding["source_event_version"], binding["source_event_sha256"], result["status"], snapshot_raw, result["evidence_snapshot_sha256"], DELIBERATION_METHOD_VERSION, result["evidence_version"], result["research_version"], result["support_strength"], result["counter_evidence_strength"], result["coverage"], canonical_json(result["missing"]), canonical_json(result["seats"]), result["invalidated_reason"], result["observed_at"], result["available_at"], result["as_of"], result["calculated_at"], raw, result["result_sha256"], now, now))
        self.workflow.set_result(task["task_public_id"], owner_id, result, status=result["status"])
        return result

    def get(self, owner_id: int, deliberation_public_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            row = conn.execute("SELECT * FROM deliberation_jobs WHERE deliberation_public_id=? AND owner_id=?", (deliberation_public_id, owner_id)).fetchone()
            if not row:
                raise DeliberationNotFound("审议不存在。")
            result = json.loads(row["result_json"])
        return result

    def list(self, owner_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._fetch("SELECT result_json FROM deliberation_jobs WHERE owner_id=? ORDER BY created_at DESC LIMIT ?", (owner_id, min(max(int(limit), 1), 100)))
        return [json.loads(row["result_json"]) for row in rows]

    def _fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if isinstance(self.db, DatabaseManager):
            return self.db.fetch_all(sql, params)
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def cancel(self, owner_id: int, deliberation_public_id: str) -> dict[str, Any]:
        current = self.get(owner_id, deliberation_public_id)
        if current["status"] in _STATUSES - {"queued", "running"}:
            return current
        self.workflow.cancel(owner_id, current["task_public_id"])
        with self._transaction() as conn:
            current["status"] = "cancelled"
            raw = canonical_json(current)
            conn.execute("UPDATE deliberation_jobs SET status='cancelled',result_json=?,result_sha256=?,updated_at=? WHERE deliberation_public_id=? AND owner_id=?", (raw, _sha({k: v for k, v in current.items() if k != "result_sha256"}), self.clock(), deliberation_public_id, owner_id))
        return current

    def retry(self, owner_id: int, deliberation_public_id: str) -> dict[str, Any]:
        current = self.get(owner_id, deliberation_public_id)
        payload = {k: current[k] for k in ("market", "symbol", "timeframe", "question", "source_event_id", "source_event_version", "source_event_sha256")}
        return self.create(owner_id, payload)

    create_deliberation = create
    create_job = create
    list_jobs = list
    get_job = get
    cancel_job = cancel
    retry_job = retry


__all__ = ["DELIBERATION_METHOD_VERSION", "SEATS", "SEAT_WEIGHTS_BPS", "DeliberationConflict", "DeliberationError", "DeliberationForbidden", "DeliberationNotFound", "DeliberationService"]
