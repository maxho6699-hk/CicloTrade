"""Persistent one-at-a-time spool for signed system-cycle research delivery."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets
from typing import Any, Callable, Mapping

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import (
    RECEIVER_ENDPOINT_HEARTBEAT,
    RECEIVER_ENDPOINT_RESULT,
    SAFE_ID,
    SystemCycleResearchConflict,
    WORKER_ID,
    canonical_json,
    receiver_signature,
    sha256_bytes,
    stamp,
    validate_system_cycle_heartbeat,
    validate_system_cycle_result,
)


class SystemCycleSpoolError(RuntimeError):
    """Raised when a local spool lease or state transition is invalid."""


class PersistentSystemCycleSpool:
    def __init__(self, database: BacktestQueueDatabase, *, clock: Callable[[], datetime] | None = None):
        if not isinstance(database, BacktestQueueDatabase):
            raise TypeError("database must be BacktestQueueDatabase")
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def enqueue(self, result: Mapping[str, Any], *, idempotency_key: str) -> tuple[dict[str, Any], bool]:
        if not isinstance(idempotency_key, str) or not SAFE_ID.fullmatch(idempotency_key):
            raise SystemCycleSpoolError("idempotency_key is invalid")
        validated = validate_system_cycle_result(result)
        body = canonical_json(validated)
        digest = sha256_bytes(body)
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM system_cycle_research_spool WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                row = dict(existing)
                if row["result_sha256"] != digest or row["payload_json"].encode("utf-8") != body:
                    raise SystemCycleResearchConflict("spool idempotency key was reused with different content")
                return self._row(row), False
            connection.execute(
                """INSERT INTO system_cycle_research_spool
                   (idempotency_key,payload_json,result_sha256,state,attempts,retry_at,created_at,updated_at)
                   VALUES (?,?,?,'pending',0,?,?,?)""",
                (idempotency_key, body.decode("utf-8"), digest, now, now, now),
            )
            row = connection.execute(
                "SELECT * FROM system_cycle_research_spool WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return self._row(dict(row)), True

    def allocate_fencing_epoch(self, worker_id: str) -> int:
        """Reserve one monotonic receiver epoch before sealing a new result."""
        self._worker(worker_id)
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT highest_epoch FROM system_cycle_research_spool_workers WHERE worker_id=?", (worker_id,)
            ).fetchone()
            epoch = (int(row["highest_epoch"]) if row else 0) + 1
            connection.execute(
                """INSERT INTO system_cycle_research_spool_workers(worker_id,highest_epoch,updated_at)
                   VALUES (?,?,?) ON CONFLICT(worker_id) DO UPDATE SET
                   highest_epoch=excluded.highest_epoch,updated_at=excluded.updated_at""",
                (worker_id, epoch, now),
            )
            return epoch

    def claim(self, worker_id: str, *, lease_seconds: int = 60) -> dict[str, Any] | None:
        self._worker(worker_id)
        self._lease_seconds(lease_seconds)
        now_dt = self._clock()
        now = stamp(now_dt)
        expires = stamp(now_dt + timedelta(seconds=lease_seconds))
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM system_cycle_research_spool
                   WHERE (state IN ('pending','failed') AND retry_at<=?)
                      OR (state='claimed' AND lease_expires_at<=?)
                   ORDER BY id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            epoch = int(row["fencing_epoch"] or 0) + 1
            connection.execute(
                """UPDATE system_cycle_research_spool SET state='claimed',attempts=attempts+1,
                   worker_id=?,fencing_epoch=?,lease_token_sha256=?,lease_expires_at=?,heartbeat_at=?,
                   last_error=NULL,updated_at=? WHERE id=?""",
                (worker_id, epoch, token_hash, expires, now, now, row["id"]),
            )
            claimed = dict(connection.execute(
                "SELECT * FROM system_cycle_research_spool WHERE id=?", (row["id"],)
            ).fetchone())
        value = self._row(claimed)
        value["lease_token"] = raw_token
        return value

    def heartbeat(self, spool_id: int, worker_id: str, lease_token: str, fencing_epoch: int, *, lease_seconds: int = 60) -> dict[str, Any]:
        self._worker(worker_id)
        self._lease_seconds(lease_seconds)
        now_dt = self._clock()
        now = stamp(now_dt)
        expires = stamp(now_dt + timedelta(seconds=lease_seconds))
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._leased(connection, spool_id, worker_id, lease_token, fencing_epoch, now)
            connection.execute(
                "UPDATE system_cycle_research_spool SET heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE id=?",
                (now, expires, now, spool_id),
            )
            return {"spool_id": spool_id, "result_sha256": row["result_sha256"], "lease_expires_at": expires}

    def fail(
        self,
        spool_id: int,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
        *,
        error: str,
        retry_delay_seconds: int,
    ) -> dict[str, Any]:
        if not isinstance(retry_delay_seconds, int) or isinstance(retry_delay_seconds, bool) or not 0 <= retry_delay_seconds <= 86_400:
            raise SystemCycleSpoolError("retry_delay_seconds is invalid")
        if not isinstance(error, str) or not error.strip():
            raise SystemCycleSpoolError("error is required")
        now_dt = self._clock()
        now = stamp(now_dt)
        retry_at = stamp(now_dt + timedelta(seconds=retry_delay_seconds))
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._leased(connection, spool_id, worker_id, lease_token, fencing_epoch, now)
            connection.execute(
                """UPDATE system_cycle_research_spool SET state='failed',retry_at=?,last_error=?,
                   lease_token_sha256=NULL,lease_expires_at=NULL,heartbeat_at=NULL,updated_at=? WHERE id=?""",
                (retry_at, error.strip()[:500], now, spool_id),
            )
            row = connection.execute("SELECT * FROM system_cycle_research_spool WHERE id=?", (spool_id,)).fetchone()
            return self._row(dict(row))

    def complete(
        self,
        spool_id: int,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = canonical_json(dict(receipt))
        if set(receipt) != {"accepted", "created", "receipt_key", "result_sha256", "state", "outbound", "user_visible"}:
            raise SystemCycleSpoolError("delivery receipt fields do not match the receiver contract")
        if receipt.get("accepted") is not True or receipt.get("outbound") is not False or receipt.get("user_visible") is not False:
            raise SystemCycleSpoolError("delivery receipt authority is invalid")
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM system_cycle_research_spool WHERE id=?", (spool_id,)).fetchone()
            if existing is None:
                raise SystemCycleSpoolError("spool item does not exist")
            if existing["state"] == "delivered":
                if existing["delivery_receipt_sha256"] != sha256_bytes(body):
                    raise SystemCycleSpoolError("delivered spool receipt changed")
                return self._row(dict(existing))
            row = self._leased(connection, spool_id, worker_id, lease_token, fencing_epoch, now)
            if (
                receipt.get("receipt_key") != row["idempotency_key"]
                or receipt.get("result_sha256") != row["result_sha256"]
                or receipt.get("state") != "shadow"
            ):
                raise SystemCycleSpoolError("delivery receipt is not bound to the shadow result")
            connection.execute(
                """UPDATE system_cycle_research_spool SET state='delivered',delivery_receipt_json=?,
                   delivery_receipt_sha256=?,delivered_at=?,lease_token_sha256=NULL,lease_expires_at=NULL,
                   heartbeat_at=NULL,updated_at=? WHERE id=?""",
                (body.decode("utf-8"), sha256_bytes(body), now, now, spool_id),
            )
            stored = connection.execute("SELECT * FROM system_cycle_research_spool WHERE id=?", (spool_id,)).fetchone()
            return self._row(dict(stored))

    def counts(self) -> dict[str, int]:
        rows = self.database.fetch_all(
            "SELECT state,COUNT(*) count FROM system_cycle_research_spool GROUP BY state"
        )
        values = {str(row["state"]): int(row["count"]) for row in rows}
        return {
            "pending": values.get("pending", 0),
            "claimed": values.get("claimed", 0),
            "retryable": values.get("failed", 0),
            "delivered": values.get("delivered", 0),
        }

    def last_delivered_sha256(self) -> str | None:
        row = self.database.fetch_one(
            "SELECT result_sha256 FROM system_cycle_research_spool WHERE state='delivered' ORDER BY delivered_at DESC,id DESC LIMIT 1"
        )
        return str(row["result_sha256"]) if row else None

    def _leased(self, connection: Any, spool_id: int, worker_id: str, lease_token: str, fencing_epoch: int, now: str) -> Mapping[str, Any]:
        row = connection.execute("SELECT * FROM system_cycle_research_spool WHERE id=?", (spool_id,)).fetchone()
        supplied = hashlib.sha256(str(lease_token).encode("utf-8")).hexdigest()
        if (
            row is None or row["state"] != "claimed" or row["worker_id"] != worker_id
            or int(row["fencing_epoch"] or 0) != fencing_epoch
            or not hmac.compare_digest(str(row["lease_token_sha256"] or ""), supplied)
            or not row["lease_expires_at"] or row["lease_expires_at"] <= now
        ):
            raise SystemCycleSpoolError("stale or invalid spool lease")
        return row

    def _clock(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise SystemCycleSpoolError("spool clock must include a timezone")
        return value.astimezone(UTC)

    @staticmethod
    def _worker(worker_id: str) -> None:
        if not isinstance(worker_id, str) or not WORKER_ID.fullmatch(worker_id):
            raise SystemCycleSpoolError("worker_id is invalid")

    @staticmethod
    def _lease_seconds(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or not 10 <= value <= 600:
            raise SystemCycleSpoolError("lease_seconds must be between 10 and 600")

    def _now(self) -> str:
        return stamp(self._clock())

    @staticmethod
    def _row(row: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        if value.get("delivery_receipt_json"):
            value["delivery_receipt"] = json.loads(value.pop("delivery_receipt_json"))
        else:
            value.pop("delivery_receipt_json", None)
            value["delivery_receipt"] = None
        return value


def signed_result_headers(claim: Mapping[str, Any], secret: str | bytes, *, sent_at: datetime) -> dict[str, str]:
    result = validate_system_cycle_result(claim["payload"])
    body = canonical_json(result)
    body_sha = sha256_bytes(body)
    if body_sha != claim.get("result_sha256"):
        raise SystemCycleSpoolError("claimed result hash changed")
    identity = {
        "worker_id": result["worker_id"], "fencing_epoch": result["fencing_epoch"],
        "idempotency_key": claim["idempotency_key"],
    }
    return _headers(identity, secret, sent_at=sent_at, endpoint=RECEIVER_ENDPOINT_RESULT, body_sha=body_sha)


def signed_heartbeat_headers(
    heartbeat: Mapping[str, Any], secret: str | bytes, *, idempotency_key: str, sent_at: datetime
) -> dict[str, str]:
    value = validate_system_cycle_heartbeat(heartbeat)
    body_sha = sha256_bytes(canonical_json(value))
    claim = {
        "worker_id": value["worker_id"], "fencing_epoch": value["fencing_epoch"],
        "idempotency_key": idempotency_key,
    }
    return _headers(claim, secret, sent_at=sent_at, endpoint=RECEIVER_ENDPOINT_HEARTBEAT, body_sha=body_sha)


def _headers(
    claim: Mapping[str, Any], secret: str | bytes, *, sent_at: datetime, endpoint: str, body_sha: str
) -> dict[str, str]:
    sent = stamp(sent_at)
    worker_id = str(claim["worker_id"])
    epoch = int(claim["fencing_epoch"])
    key = str(claim["idempotency_key"])
    signature = receiver_signature(
        secret, endpoint=endpoint, worker_id=worker_id, fencing_epoch=epoch,
        idempotency_key=key, sent_at=sent, body_sha256=body_sha,
    )
    return {
        "x-ciclotrade-worker-id": worker_id,
        "x-ciclotrade-fencing-epoch": str(epoch),
        "idempotency-key": key,
        "x-ciclotrade-sent-at": sent,
        "x-ciclotrade-result-sha256": body_sha,
        "x-ciclotrade-research-signature": signature,
        "content-type": "application/json",
    }
