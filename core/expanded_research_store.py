"""Append-only website ledger for the isolated 97-symbol research chain."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any, Callable, Mapping

from core.compat import UTC
from core.backtest_queue_database import BacktestQueueDatabase
from core.expanded_research_contracts import (
    ExpandedResearchConflict,
    ExpandedResearchError,
    ExpandedResearchStaleFence,
    UNIVERSE_SHA256,
    UNIVERSE_VERSION,
    canonical_json,
    parse_timestamp,
    sha256_bytes,
    validate_invalidation,
    validate_result,
)

ACTIVE_RESULT_TTL = timedelta(hours=12)


class ExpandedResearchStore:
    def __init__(self, database: BacktestQueueDatabase, *, clock: Callable[[], datetime] | None = None) -> None:
        if not isinstance(database, BacktestQueueDatabase):
            raise TypeError("database must BacktestQueueDatabase")
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def record(self, value: Mapping[str, Any], *, receipt_key: str, worker_id: str, fencing_epoch: int, payload_sha256: str) -> dict[str, Any]:
        result = validate_result(value)
        body = canonical_json(result)
        digest = sha256_bytes(body)
        if digest != payload_sha256:
            raise ExpandedResearchError("expanded research payload hash does not match")
        if result["universe_sha256"] != UNIVERSE_SHA256:
            raise ExpandedResearchError("expanded research universe is not current")
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM expanded_research_receipts WHERE receipt_key=? OR result_id=?",
                (receipt_key, result["result_id"]),
            ).fetchone()
            if existing is not None:
                if (
                    existing["receipt_key"] != receipt_key
                    or existing["result_id"] != result["result_id"]
                    or existing["payload_sha256"] != digest
                    or existing["payload_json"].encode("utf-8") != body
                ):
                    raise ExpandedResearchConflict("expanded research idempotency identity was reused with different content")
                return {**dict(existing), "created": False}
            if connection.execute(
                "SELECT 1 FROM expanded_research_invalidations WHERE invalidation_key=?", (receipt_key,)
            ).fetchone() is not None:
                raise ExpandedResearchConflict("expanded research idempotency identity was reused by an invalidation")
            fence = connection.execute(
                "SELECT highest_epoch FROM expanded_research_worker_fences WHERE worker_id=?",
                (worker_id,),
            ).fetchone()
            highest = int(fence["highest_epoch"]) if fence else 0
            if fencing_epoch < highest:
                raise ExpandedResearchStaleFence("expanded research fencing epoch is stale")
            if fence is None:
                connection.execute(
                    "INSERT INTO expanded_research_worker_fences(worker_id,highest_epoch,updated_at) VALUES(?,?,?)",
                    (worker_id, fencing_epoch, now),
                )
            elif fencing_epoch > highest:
                connection.execute(
                    "UPDATE expanded_research_worker_fences SET highest_epoch=?,updated_at=? WHERE worker_id=?",
                    (fencing_epoch, now, worker_id),
                )
            connection.execute(
                """INSERT INTO expanded_research_receipts(
                   receipt_key,result_id,worker_id,fencing_epoch,universe_version,
                   universe_sha256,symbol,tier,source_sha256,payload_sha256,
                   payload_json,received_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_key, result["result_id"], worker_id, fencing_epoch, UNIVERSE_VERSION,
                    result["universe_sha256"], result["symbol"], result["tier"], result["source_sha256"],
                    digest, body.decode("utf-8"), now,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM expanded_research_receipts WHERE receipt_key=?", (receipt_key,)
            ).fetchone()
            return {**dict(stored), "created": True}

    def latest(self) -> dict[str, Any] | None:
        rows = self.latest_by_symbol()
        return max(rows, key=lambda row: (row["received_at"], row["receipt_key"]), default=None)

    def latest_by_symbol(self) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            """SELECT * FROM expanded_research_receipts
               ORDER BY received_at DESC,receipt_key DESC"""
        )
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            decoded = self._decode(row)
            if decoded is not None and decoded["symbol"] not in selected:
                selected[decoded["symbol"]] = decoded
        invalidated = self._invalidated_result_ids(selected.values())
        return sorted(
            (
                row for row in selected.values()
                if row["result_id"] not in invalidated and self._is_active(row["received_at"])
            ),
            key=lambda row: row["symbol"],
        )

    def invalidate(
        self,
        value: Mapping[str, Any],
        *,
        receipt_key: str,
        worker_id: str,
        fencing_epoch: int,
        payload_sha256: str,
    ) -> dict[str, Any]:
        invalidation = validate_invalidation(value)
        body = canonical_json(invalidation)
        digest = sha256_bytes(body)
        if digest != payload_sha256:
            raise ExpandedResearchError("expanded research invalidation payload hash does not match")
        if invalidation["universe_sha256"] != UNIVERSE_SHA256:
            raise ExpandedResearchError("expanded research invalidation universe is not current")
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM expanded_research_invalidations WHERE invalidation_key=? OR invalidation_id=?",
                (receipt_key, invalidation["invalidation_id"]),
            ).fetchone()
            if existing is not None:
                if (
                    existing["invalidation_key"] != receipt_key
                    or existing["invalidation_id"] != invalidation["invalidation_id"]
                    or existing["payload_sha256"] != digest
                    or existing["payload_json"].encode("utf-8") != body
                ):
                    raise ExpandedResearchConflict("expanded research invalidation identity was reused with different content")
                return {**dict(existing), "created": False}
            if connection.execute(
                "SELECT 1 FROM expanded_research_receipts WHERE receipt_key=?", (receipt_key,)
            ).fetchone() is not None:
                raise ExpandedResearchConflict("expanded research idempotency identity was reused by a result")
            self._advance_fence(connection, worker_id, fencing_epoch, now)
            connection.execute(
                """INSERT INTO expanded_research_invalidations(
                   invalidation_key,invalidation_id,worker_id,fencing_epoch,
                   target_result_id,symbol,reason,universe_version,universe_sha256,
                   payload_sha256,payload_json,invalidated_at,received_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_key, invalidation["invalidation_id"], worker_id, fencing_epoch,
                    invalidation["target_result_id"], invalidation["symbol"], invalidation["reason"],
                    UNIVERSE_VERSION, invalidation["universe_sha256"], digest, body.decode("utf-8"),
                    invalidation["invalidated_at"], now,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM expanded_research_invalidations WHERE invalidation_key=?", (receipt_key,)
            ).fetchone()
            return {**dict(stored), "created": True}

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("expanded research history limit must be between 1 and 100")
        decoded = [decoded for row in self.database.fetch_all(
            "SELECT * FROM expanded_research_receipts ORDER BY received_at DESC,receipt_key DESC LIMIT ?", (limit,)
        ) if (decoded := self._decode(row)) is not None]
        latest_ids: dict[str, str] = {}
        for row in decoded:
            latest_ids.setdefault(row["symbol"], row["result_id"])
        invalidated_ids = self._invalidated_result_ids(decoded)
        result: list[dict[str, Any]] = []
        for row in decoded:
            invalidated = row["result_id"] in invalidated_ids
            expired = not self._is_active(row["received_at"])
            active = latest_ids[row["symbol"]] == row["result_id"] and not invalidated and not expired
            state = "active" if active else "invalidated" if invalidated else "expired" if expired else "superseded"
            result.append({**row, "projection_state": state})
        return result

    def status(self) -> dict[str, Any]:
        active = self.latest_by_symbol()
        counts = {tier: sum(row["tier"] == tier for row in active) for tier in ("A", "C")}
        latest = max((row["received_at"] for row in active), default=None)
        return {
            "available": bool(active), "state": "shadow" if active else "waiting", "research_only": True,
            "shadow": True, "actionable": False, "outbound": False, "user_visible": False,
            "execution": False, "official": False, "live": False, "universe_version": UNIVERSE_VERSION,
            "universe_sha256": UNIVERSE_SHA256, "universe_size": 97,
            "sealed_count": len(active), "tier_a_count": counts["A"], "tier_c_count": counts["C"],
            "symbol_count": len(active), "last_received_at": latest,
        }

    def _invalidated_result_ids(self, rows: Any) -> set[str]:
        result_ids = [str(row["result_id"]) for row in rows]
        if not result_ids:
            return set()
        placeholders = ",".join("?" for _ in result_ids)
        return {
            str(row["target_result_id"])
            for row in self.database.fetch_all(
                f"SELECT target_result_id FROM expanded_research_invalidations WHERE target_result_id IN ({placeholders})",
                tuple(result_ids),
            )
        }

    def _is_active(self, received_at: str) -> bool:
        try:
            received = parse_timestamp(received_at, "received_at")
            return self._now_datetime() - received <= ACTIVE_RESULT_TTL
        except ExpandedResearchError:
            return False

    def _now_datetime(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExpandedResearchError("expanded research store clock must include a timezone")
        return value.astimezone(UTC)

    def _advance_fence(self, connection: Any, worker_id: str, fencing_epoch: int, now: str) -> None:
        fence = connection.execute(
            "SELECT highest_epoch FROM expanded_research_worker_fences WHERE worker_id=?", (worker_id,)
        ).fetchone()
        highest = int(fence["highest_epoch"]) if fence else 0
        if fencing_epoch < highest:
            raise ExpandedResearchStaleFence("expanded research fencing epoch is stale")
        if fence is None:
            connection.execute(
                "INSERT INTO expanded_research_worker_fences(worker_id,highest_epoch,updated_at) VALUES(?,?,?)",
                (worker_id, fencing_epoch, now),
            )
        elif fencing_epoch > highest:
            connection.execute(
                "UPDATE expanded_research_worker_fences SET highest_epoch=?,updated_at=? WHERE worker_id=?",
                (fencing_epoch, now, worker_id),
            )

    @staticmethod
    def _decode(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            payload = validate_result(json.loads(str(row["payload_json"])))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ExpandedResearchError):
            return None
        return {
            "receipt_key": str(row["receipt_key"]), "result_id": payload["result_id"], "symbol": payload["symbol"],
            "tier": payload["tier"], "dataset_end": payload["dataset_end"], "received_at": str(row["received_at"]),
            "source_sha256": payload["source_sha256"], "universe_version": UNIVERSE_VERSION,
            "universe_sha256": payload["universe_sha256"], "equity": payload["equity"],
            "option_proxy": payload["option_proxy"], "research_only": True, "shadow": True,
            "actionable": False, "outbound": False, "user_visible": False, "execution": False,
            "official": False, "live": False, "evidence_sha256": str(row["payload_sha256"]),
        }

    def _now(self) -> str:
        return self._now_datetime().replace(microsecond=0).isoformat().replace("+00:00", "Z")
