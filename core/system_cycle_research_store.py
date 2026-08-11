"""Append-only website-side storage for shadow system-cycle research receipts."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable, Mapping

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import (
    SystemCycleResearchConflict,
    SystemCycleResearchError,
    SystemCycleResearchStaleFence,
    canonical_json,
    sha256_bytes,
    stamp,
    validate_system_cycle_heartbeat,
    validate_system_cycle_result,
)


class SystemCycleResearchStore:
    """Writes only dedicated research tables in the isolated backtest database."""

    def __init__(self, database: BacktestQueueDatabase, *, clock: Callable[[], datetime] | None = None):
        if not isinstance(database, BacktestQueueDatabase):
            raise TypeError("database must be BacktestQueueDatabase")
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def record_result(
        self,
        value: Mapping[str, Any],
        *,
        receipt_key: str,
        worker_id: str,
        fencing_epoch: int,
        result_sha256: str,
    ) -> dict[str, Any]:
        result = validate_system_cycle_result(value)
        body = canonical_json(result)
        digest = sha256_bytes(body)
        if digest != result_sha256 or result["worker_id"] != worker_id or result["fencing_epoch"] != fencing_epoch:
            raise SystemCycleResearchError("result headers are not bound to the sealed payload")
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM system_cycle_research_receipts WHERE receipt_key=?", (receipt_key,)
            ).fetchone()
            if existing is not None:
                if existing["result_sha256"] != digest or existing["payload_json"].encode("utf-8") != body:
                    raise SystemCycleResearchConflict("receipt idempotency key was reused with different content")
                return {**dict(existing), "created": False}
            existing_cycle = connection.execute(
                """SELECT * FROM system_cycle_research_receipts
                   WHERE worker_id=? AND cycle_id=?""",
                (worker_id, result["cycle_id"]),
            ).fetchone()
            if existing_cycle is not None:
                if (
                    existing_cycle["result_sha256"] == digest
                    and existing_cycle["payload_json"].encode("utf-8") == body
                ):
                    raise SystemCycleResearchConflict(
                        "system cycle result was already recorded under a different idempotency key"
                    )
                raise SystemCycleResearchConflict(
                    "system cycle result is immutable and cannot be revised for the same worker and cycle"
                )
            self._advance_fence(connection, worker_id, fencing_epoch, now)
            connection.execute(
                """INSERT INTO system_cycle_research_receipts
                   (receipt_key,worker_id,fencing_epoch,result_sha256,payload_json,cycle_id,universe_sha256,received_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    receipt_key, worker_id, fencing_epoch, digest, body.decode("utf-8"), result["cycle_id"],
                    result["universe"]["sha256"], now,
                ),
            )
            connection.execute(
                """UPDATE system_cycle_research_worker_fences SET last_result_sha256=?,updated_at=?
                   WHERE worker_id=?""",
                (digest, now, worker_id),
            )
            stored = connection.execute(
                "SELECT * FROM system_cycle_research_receipts WHERE receipt_key=?", (receipt_key,)
            ).fetchone()
            return {**dict(stored), "created": True}

    def record_heartbeat(
        self,
        value: Mapping[str, Any],
        *,
        heartbeat_key: str,
        worker_id: str,
        fencing_epoch: int,
        payload_sha256: str,
    ) -> dict[str, Any]:
        heartbeat = validate_system_cycle_heartbeat(value)
        body = canonical_json(heartbeat)
        digest = sha256_bytes(body)
        if digest != payload_sha256 or heartbeat["worker_id"] != worker_id or heartbeat["fencing_epoch"] != fencing_epoch:
            raise SystemCycleResearchError("heartbeat headers are not bound to the sealed payload")
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM system_cycle_research_heartbeats WHERE heartbeat_key=?", (heartbeat_key,)
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] != digest or existing["payload_json"].encode("utf-8") != body:
                    raise SystemCycleResearchConflict("heartbeat idempotency key was reused with different content")
                return {**dict(existing), "created": False}
            self._advance_fence(connection, worker_id, fencing_epoch, now)
            connection.execute(
                """INSERT INTO system_cycle_research_heartbeats
                   (heartbeat_key,worker_id,fencing_epoch,payload_sha256,payload_json,heartbeat_at,received_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    heartbeat_key, worker_id, fencing_epoch, digest, body.decode("utf-8"),
                    heartbeat["heartbeat_at"], now,
                ),
            )
            connection.execute(
                """UPDATE system_cycle_research_worker_fences SET last_heartbeat_at=?,updated_at=?
                   WHERE worker_id=?""",
                (heartbeat["heartbeat_at"], now, worker_id),
            )
            stored = connection.execute(
                "SELECT * FROM system_cycle_research_heartbeats WHERE heartbeat_key=?", (heartbeat_key,)
            ).fetchone()
            return {**dict(stored), "created": True}

    @staticmethod
    def _advance_fence(connection: Any, worker_id: str, fencing_epoch: int, now: str) -> None:
        row = connection.execute(
            "SELECT highest_epoch FROM system_cycle_research_worker_fences WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if row is not None and fencing_epoch < int(row["highest_epoch"]):
            raise SystemCycleResearchStaleFence("stale system-cycle fencing epoch")
        if row is None:
            connection.execute(
                """INSERT INTO system_cycle_research_worker_fences
                   (worker_id,highest_epoch,last_heartbeat_at,last_result_sha256,updated_at)
                   VALUES (?,?,NULL,NULL,?)""",
                (worker_id, fencing_epoch, now),
            )
        elif fencing_epoch > int(row["highest_epoch"]):
            connection.execute(
                "UPDATE system_cycle_research_worker_fences SET highest_epoch=?,updated_at=? WHERE worker_id=?",
                (fencing_epoch, now, worker_id),
            )

    def fence(self, worker_id: str) -> dict[str, Any] | None:
        return self.database.fetch_one(
            "SELECT * FROM system_cycle_research_worker_fences WHERE worker_id=?", (worker_id,)
        )

    def receipt(self, receipt_key: str) -> dict[str, Any] | None:
        row = self.database.fetch_one(
            "SELECT * FROM system_cycle_research_receipts WHERE receipt_key=?", (receipt_key,)
        )
        if row:
            row["payload"] = json.loads(row.pop("payload_json"))
        return row

    def _now(self) -> str:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise SystemCycleResearchError("research store clock must include a timezone")
        return stamp(value)
