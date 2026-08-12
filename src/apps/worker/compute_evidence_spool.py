"""Persistent fenced spool for generic Compute Gate evidence packages."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import re
import secrets
from typing import Any, Callable, Mapping

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.compute_evidence_contracts import (
    ComputeEvidenceConflict,
    canonical_json,
    delivery_signature,
    sha256_bytes,
    stamp,
    validate_package,
)


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
PUBLISHER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ComputeEvidenceSpoolError(RuntimeError):
    pass


class PersistentComputeEvidenceSpool:
    def __init__(self, database: BacktestQueueDatabase, *, clock: Callable[[], datetime] | None = None):
        if not isinstance(database, BacktestQueueDatabase):
            raise TypeError("database must be BacktestQueueDatabase")
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def enqueue(self, package: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        value = validate_package(package)
        body = canonical_json(value)
        digest = sha256_bytes(body)
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM compute_evidence_spool WHERE package_id=?", (value["package_id"],)
            ).fetchone()
            if existing is not None:
                row = dict(existing)
                if row["package_sha256"] != digest or row["payload_json"].encode("utf-8") != body:
                    raise ComputeEvidenceConflict("compute package identity was reused with different content")
                return self._row(row), False
            connection.execute(
                """INSERT INTO compute_evidence_spool
                   (package_id,payload_json,package_sha256,state,attempts,retry_at,created_at,updated_at)
                   VALUES (?,?,?,'pending',0,?,?,?)""",
                (value["package_id"], body.decode("utf-8"), digest, now, now, now),
            )
            stored = connection.execute(
                "SELECT * FROM compute_evidence_spool WHERE package_id=?", (value["package_id"],)
            ).fetchone()
            return self._row(dict(stored)), True

    def claim(self, publisher_id: str, *, lease_seconds: int = 90) -> dict[str, Any] | None:
        self._publisher(publisher_id)
        self._lease_seconds(lease_seconds)
        now_dt = self._clock()
        now = stamp(now_dt)
        expires = stamp(now_dt + timedelta(seconds=lease_seconds))
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE compute_evidence_spool SET state='uncertain',
                   last_error='publisher exited after delivery began; remote acceptance is unknown',
                   lease_token_sha256=NULL,lease_expires_at=NULL,uncertain_at=?,updated_at=?
                   WHERE state='sending' AND lease_expires_at<=?""",
                (now, now, now),
            )
            row = connection.execute(
                """SELECT * FROM compute_evidence_spool
                   WHERE (state IN ('pending','failed') AND retry_at<=?)
                      OR (state='claimed' AND lease_expires_at<=?)
                   ORDER BY id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            worker = connection.execute(
                "SELECT highest_epoch FROM compute_evidence_spool_workers WHERE publisher_id=?", (publisher_id,)
            ).fetchone()
            epoch = (int(worker["highest_epoch"]) if worker else 0) + 1
            connection.execute(
                """INSERT INTO compute_evidence_spool_workers(publisher_id,highest_epoch,updated_at)
                   VALUES (?,?,?) ON CONFLICT(publisher_id) DO UPDATE SET
                   highest_epoch=excluded.highest_epoch,updated_at=excluded.updated_at""",
                (publisher_id, epoch, now),
            )
            connection.execute(
                """UPDATE compute_evidence_spool SET state='claimed',attempts=attempts+1,
                   publisher_id=?,fencing_epoch=?,lease_token_sha256=?,lease_expires_at=?,
                   last_error=NULL,last_http_status=NULL,updated_at=? WHERE id=?""",
                (publisher_id, epoch, token_hash, expires, now, row["id"]),
            )
            claimed = dict(
                connection.execute("SELECT * FROM compute_evidence_spool WHERE id=?", (row["id"],)).fetchone()
            )
        result = self._row(claimed)
        result["lease_token"] = token
        return result

    def quarantine_expired_deliveries(self) -> int:
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE compute_evidence_spool SET state='uncertain',
                   last_error='publisher exited after delivery began; remote acceptance is unknown',
                   lease_token_sha256=NULL,lease_expires_at=NULL,uncertain_at=?,updated_at=?
                   WHERE state='sending' AND lease_expires_at<=?""",
                (now, now, now),
            )
            return int(changed.rowcount)

    def begin_delivery(self, spool_id: int, publisher_id: str, lease_token: str, fencing_epoch: int) -> dict[str, Any]:
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._leased(connection, spool_id, publisher_id, lease_token, fencing_epoch, now, {"claimed"})
            connection.execute(
                "UPDATE compute_evidence_spool SET state='sending',updated_at=? WHERE id=?", (now, spool_id)
            )
            return self._row(
                dict(connection.execute("SELECT * FROM compute_evidence_spool WHERE id=?", (spool_id,)).fetchone())
            )

    def fail(
        self,
        spool_id: int,
        publisher_id: str,
        lease_token: str,
        fencing_epoch: int,
        *,
        error: str,
        retry_delay_seconds: int,
        http_status: int | None = None,
    ) -> dict[str, Any]:
        if (
            not isinstance(retry_delay_seconds, int)
            or isinstance(retry_delay_seconds, bool)
            or not 0 <= retry_delay_seconds <= 86_400
        ):
            raise ComputeEvidenceSpoolError("retry delay is invalid")
        now_dt = self._clock()
        now, retry_at = stamp(now_dt), stamp(now_dt + timedelta(seconds=retry_delay_seconds))
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._leased(connection, spool_id, publisher_id, lease_token, fencing_epoch, now, {"claimed", "sending"})
            connection.execute(
                """UPDATE compute_evidence_spool SET state='failed',retry_at=?,last_error=?,last_http_status=?,
                   lease_token_sha256=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?""",
                (retry_at, self._error(error), self._status(http_status), now, spool_id),
            )
            return self._row(
                dict(connection.execute("SELECT * FROM compute_evidence_spool WHERE id=?", (spool_id,)).fetchone())
            )

    def complete(
        self,
        spool_id: int,
        publisher_id: str,
        lease_token: str,
        fencing_epoch: int,
        receipt: Mapping[str, Any],
        *,
        http_status: int | None = None,
    ) -> dict[str, Any]:
        expected = {
            "accepted",
            "created",
            "receipt_key",
            "package_id",
            "package_sha256",
            "publication_state",
            "research_only",
            "actionable",
            "user_visible",
        }
        if (
            set(receipt) != expected
            or receipt.get("accepted") is not True
            or not isinstance(receipt.get("created"), bool)
        ):
            raise ComputeEvidenceSpoolError("delivery receipt fields are invalid")
        if (
            receipt.get("publication_state") != "quarantine"
            or receipt.get("research_only") is not True
            or receipt.get("actionable") is not False
            or receipt.get("user_visible") is not False
        ):
            raise ComputeEvidenceSpoolError("delivery receipt authority is invalid")
        body = canonical_json(dict(receipt))
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM compute_evidence_spool WHERE id=?", (spool_id,)).fetchone()
            if existing is None:
                raise ComputeEvidenceSpoolError("spool item does not exist")
            if existing["state"] == "delivered":
                if existing["delivery_receipt_sha256"] != sha256_bytes(body):
                    raise ComputeEvidenceSpoolError("delivered receipt changed")
                return self._row(dict(existing))
            row = self._leased(
                connection, spool_id, publisher_id, lease_token, fencing_epoch, now, {"claimed", "sending"}
            )
            if (
                receipt.get("receipt_key") != row["package_id"]
                or receipt.get("package_id") != row["package_id"]
                or receipt.get("package_sha256") != row["package_sha256"]
            ):
                raise ComputeEvidenceSpoolError("delivery receipt is not bound to the package")
            connection.execute(
                """UPDATE compute_evidence_spool SET state='delivered',delivery_receipt_json=?,
                   delivery_receipt_sha256=?,delivered_at=?,lease_token_sha256=NULL,lease_expires_at=NULL,
                   last_http_status=?,updated_at=? WHERE id=?""",
                (body.decode("utf-8"), sha256_bytes(body), now, self._status(http_status), now, spool_id),
            )
            return self._row(
                dict(connection.execute("SELECT * FROM compute_evidence_spool WHERE id=?", (spool_id,)).fetchone())
            )

    def stop(
        self,
        spool_id: int,
        publisher_id: str,
        lease_token: str,
        fencing_epoch: int,
        *,
        state: str,
        error: str,
        http_status: int | None = None,
    ) -> dict[str, Any]:
        if state not in {"dead", "uncertain"}:
            raise ComputeEvidenceSpoolError("terminal spool state is invalid")
        now = self._now()
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._leased(connection, spool_id, publisher_id, lease_token, fencing_epoch, now, {"claimed", "sending"})
            column = "uncertain_at" if state == "uncertain" else "terminal_at"
            connection.execute(
                f"""UPDATE compute_evidence_spool SET state=?,last_error=?,last_http_status=?,
                    lease_token_sha256=NULL,lease_expires_at=NULL,{column}=?,updated_at=? WHERE id=?""",
                (state, self._error(error), self._status(http_status), now, now, spool_id),
            )
            return self._row(
                dict(connection.execute("SELECT * FROM compute_evidence_spool WHERE id=?", (spool_id,)).fetchone())
            )

    @staticmethod
    def signed_headers(
        claim: Mapping[str, Any], secret: str | bytes, *, nonce: str, expires_at: datetime
    ) -> dict[str, str]:
        package = validate_package(claim["payload"])
        body = canonical_json(package)
        digest = sha256_bytes(body)
        if digest != claim.get("package_sha256"):
            raise ComputeEvidenceSpoolError("claimed package hash changed")
        expiry = stamp(expires_at)
        signature = delivery_signature(
            secret,
            site_id=package["site_id"],
            publisher_id=str(claim["publisher_id"]),
            source_worker_id=package["worker_id"],
            fencing_epoch=int(claim["fencing_epoch"]),
            idempotency_key=package["package_id"],
            nonce=nonce,
            expires_at=expiry,
            package_sha256=digest,
        )
        return {
            "content-type": "application/json",
            "x-ciclotrade-site-id": package["site_id"],
            "x-ciclotrade-publisher-id": str(claim["publisher_id"]),
            "x-ciclotrade-source-worker-id": package["worker_id"],
            "x-ciclotrade-fencing-epoch": str(claim["fencing_epoch"]),
            "idempotency-key": package["package_id"],
            "x-ciclotrade-nonce": nonce,
            "x-ciclotrade-expires-at": expiry,
            "x-ciclotrade-package-sha256": digest,
            "x-ciclotrade-evidence-signature": signature,
        }

    @staticmethod
    def _leased(
        connection: Any,
        spool_id: int,
        publisher_id: str,
        lease_token: str,
        fencing_epoch: int,
        now: str,
        states: set[str],
    ) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM compute_evidence_spool WHERE id=?", (spool_id,)).fetchone()
        digest = hashlib.sha256(str(lease_token).encode("utf-8")).hexdigest()
        if (
            row is None
            or row["state"] not in states
            or row["publisher_id"] != publisher_id
            or row["fencing_epoch"] != fencing_epoch
            or not secrets.compare_digest(row["lease_token_sha256"] or "", digest)
            or not row["lease_expires_at"]
            or row["lease_expires_at"] <= now
        ):
            raise ComputeEvidenceSpoolError("compute evidence lease is stale")
        return dict(row)

    def _clock(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ComputeEvidenceSpoolError("spool clock must include a timezone")
        return value.astimezone(UTC)

    def _now(self) -> str:
        return stamp(self._clock())

    @staticmethod
    def _publisher(value: str) -> None:
        if not isinstance(value, str) or not PUBLISHER_ID.fullmatch(value):
            raise ComputeEvidenceSpoolError("publisher_id is invalid")

    @staticmethod
    def _lease_seconds(value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or not 30 <= value <= 600:
            raise ComputeEvidenceSpoolError("lease_seconds is invalid")

    @staticmethod
    def _error(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ComputeEvidenceSpoolError("delivery error is required")
        return value.strip()[:500]

    @staticmethod
    def _status(value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599):
            raise ComputeEvidenceSpoolError("HTTP status is invalid")
        return value

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
