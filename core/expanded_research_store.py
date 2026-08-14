"""Append-only website ledger for the isolated 97-symbol research chain."""

from __future__ import annotations

from datetime import datetime
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
    sha256_bytes,
    validate_result,
)


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
        row = self.database.fetch_one(
            "SELECT * FROM expanded_research_receipts ORDER BY received_at DESC,receipt_key DESC LIMIT 1"
        )
        return self._decode(row)

    def latest_by_symbol(self) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            """SELECT r.* FROM expanded_research_receipts r
               WHERE r.received_at=(SELECT MAX(r2.received_at) FROM expanded_research_receipts r2 WHERE r2.symbol=r.symbol)
               ORDER BY r.symbol ASC,r.received_at DESC,r.receipt_key DESC"""
        )
        return [decoded for row in rows if (decoded := self._decode(row)) is not None]

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("expanded research history limit must be between 1 and 100")
        return [decoded for row in self.database.fetch_all(
            "SELECT * FROM expanded_research_receipts ORDER BY received_at DESC,receipt_key DESC LIMIT ?", (limit,)
        ) if (decoded := self._decode(row)) is not None]

    def status(self) -> dict[str, Any]:
        counts = self.database.fetch_all("SELECT tier,COUNT(*) AS total FROM expanded_research_receipts GROUP BY tier")
        latest = self.database.fetch_one("SELECT received_at FROM expanded_research_receipts ORDER BY received_at DESC,receipt_key DESC LIMIT 1")
        symbols = self.database.fetch_one("SELECT COUNT(DISTINCT symbol) AS total FROM expanded_research_receipts")
        return {
            "available": bool(latest), "state": "shadow" if latest else "waiting", "research_only": True,
            "shadow": True, "actionable": False, "outbound": False, "user_visible": False,
            "execution": False, "official": False, "live": False, "universe_version": UNIVERSE_VERSION,
            "universe_sha256": UNIVERSE_SHA256, "universe_size": 97,
            "sealed_count": sum(int(row["total"]) for row in counts),
            "tier_a_count": next((int(row["total"]) for row in counts if row["tier"] == "A"), 0),
            "tier_c_count": next((int(row["total"]) for row in counts if row["tier"] == "C"), 0),
            "symbol_count": int(symbols["total"]) if symbols else 0,
            "last_received_at": latest["received_at"] if latest else None,
        }

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
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExpandedResearchError("expanded research store clock must include a timezone")
        return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
