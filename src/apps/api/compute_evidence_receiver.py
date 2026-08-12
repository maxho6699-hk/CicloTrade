"""Disabled-by-default receiver for generic Compute Gate shadow evidence."""

from __future__ import annotations

from datetime import datetime
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.compute_evidence_contracts import (
    AUTHORITY,
    ComputeEvidenceError,
    canonical_json,
    delivery_signature,
    parse_timestamp,
    sha256_bytes,
    stamp,
    validate_package,
)


MAX_PACKAGE_BYTES = 512 * 1024
MAX_EXPIRY_SECONDS = 300
DELIVERY_EPOCH = re.compile(r"^[1-9][0-9]{0,9}$")
INTEGRATION_CHECKLIST = (
    "Apply 0010_compute_evidence_acceptance.sql to isolated spool and website ledgers.",
    "Configure distinct absolute spool and receiver database paths and a 32-byte secret.",
    "Wire one fixed internal POST route only after independent security review.",
    "Keep every accepted package quarantined, research-only, non-actionable, and hidden.",
    "Do not connect this ledger to recommendations, orders, Telegram, official, or live state.",
)


class ComputeEvidenceReceiverError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class ComputeEvidenceReceiver:
    """Authenticate and append one immutable package in a single SQLite transaction."""

    def __init__(
        self,
        database: BacktestQueueDatabase,
        *,
        shared_secret: str | bytes,
        site_id: str,
        publisher_id: str,
        enabled: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        secret = shared_secret.encode("utf-8") if isinstance(shared_secret, str) else shared_secret
        if not isinstance(database, BacktestQueueDatabase):
            raise TypeError("database must be BacktestQueueDatabase")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("compute evidence shared secret must contain at least 32 bytes")
        self.database = database
        self._secret = secret
        self.site_id = _identity(site_id, "site_id")
        self.publisher_id = _identity(publisher_id, "publisher_id")
        self.enabled = bool(enabled)
        self.clock = clock or (lambda: datetime.now(UTC))

    def accept(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        if not self.enabled:
            raise ComputeEvidenceReceiverError("compute evidence receiver is disabled", 404)
        if not isinstance(raw, bytes) or not raw:
            raise ComputeEvidenceReceiverError("compute evidence body is empty", 400)
        if len(raw) > MAX_PACKAGE_BYTES:
            raise ComputeEvidenceReceiverError("compute evidence body is too large", 413)
        normalized_headers = _headers(headers)
        if normalized_headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise ComputeEvidenceReceiverError("compute evidence content type is invalid", 415)
        try:
            identity = self._authenticate(raw, normalized_headers)
            package = validate_package(_json(raw))
            if canonical_json(package) != raw:
                raise ComputeEvidenceError("compute evidence body must use canonical JSON")
            if (
                package["site_id"] != identity["site_id"]
                or package["worker_id"] != identity["source_worker_id"]
                or package["package_id"] != identity["receipt_key"]
            ):
                raise ComputeEvidenceReceiverError("compute evidence identity is not authorized", 401)
            return self._store(package, identity, raw)
        except ComputeEvidenceReceiverError:
            raise
        except ComputeEvidenceError as exc:
            raise ComputeEvidenceReceiverError(str(exc), 400) from exc

    def _authenticate(
        self,
        raw: bytes,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        site_id = headers.get("x-ciclotrade-site-id", "")
        publisher_id = headers.get("x-ciclotrade-publisher-id", "")
        source_worker_id = headers.get("x-ciclotrade-source-worker-id", "")
        epoch_text = headers.get("x-ciclotrade-fencing-epoch", "")
        receipt_key = headers.get("idempotency-key", "")
        nonce = headers.get("x-ciclotrade-nonce", "")
        expires_at = headers.get("x-ciclotrade-expires-at", "")
        package_sha = headers.get("x-ciclotrade-package-sha256", "")
        supplied = headers.get("x-ciclotrade-evidence-signature", "")
        if not DELIVERY_EPOCH.fullmatch(epoch_text):
            raise ComputeEvidenceReceiverError("compute evidence identity headers are invalid", 401)
        epoch = int(epoch_text)
        actual_sha = sha256_bytes(raw)
        try:
            expiry = parse_timestamp(expires_at, "expires_at")
            expected = delivery_signature(
                self._secret,
                site_id=site_id,
                publisher_id=publisher_id,
                source_worker_id=source_worker_id,
                fencing_epoch=epoch,
                idempotency_key=receipt_key,
                nonce=nonce,
                expires_at=expires_at,
                package_sha256=package_sha,
            )
        except ComputeEvidenceError as exc:
            raise ComputeEvidenceReceiverError("compute evidence identity headers are invalid", 401) from exc
        now = self._now()
        remaining = (expiry - now).total_seconds()
        if remaining < 0 or remaining > MAX_EXPIRY_SECONDS:
            raise ComputeEvidenceReceiverError("compute evidence delivery has expired", 401)
        if site_id != self.site_id or publisher_id != self.publisher_id:
            raise ComputeEvidenceReceiverError("compute evidence identity is not authorized", 401)
        if not hmac.compare_digest(actual_sha, package_sha):
            raise ComputeEvidenceReceiverError("compute evidence package hash does not match", 409)
        if not supplied or not hmac.compare_digest(expected, supplied):
            raise ComputeEvidenceReceiverError("compute evidence signature is invalid", 401)
        return {
            "site_id": site_id,
            "publisher_id": publisher_id,
            "source_worker_id": source_worker_id,
            "delivery_fencing_epoch": epoch,
            "receipt_key": receipt_key,
            "nonce": nonce,
            "expires_at": stamp(expiry),
            "package_sha256": actual_sha,
        }

    def _store(
        self,
        package: Mapping[str, Any],
        identity: Mapping[str, Any],
        raw: bytes,
    ) -> dict[str, Any]:
        now = stamp(self._now())
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM compute_evidence_receiver_nonces WHERE nonce=?",
                (identity["nonce"],),
            ).fetchone():
                raise ComputeEvidenceReceiverError("compute evidence nonce was replayed", 409)
            existing = connection.execute(
                "SELECT * FROM compute_evidence_receipts WHERE receipt_key=? OR package_id=?",
                (identity["receipt_key"], package["package_id"]),
            ).fetchone()
            fence = connection.execute(
                """SELECT highest_epoch FROM compute_evidence_receiver_fences
                   WHERE site_id=? AND publisher_id=?""",
                (identity["site_id"], identity["publisher_id"]),
            ).fetchone()
            highest = int(fence["highest_epoch"]) if fence else 0
            epoch = int(identity["delivery_fencing_epoch"])
            if epoch < highest or (epoch == highest and existing is None):
                raise ComputeEvidenceReceiverError("compute evidence delivery fence is stale", 409)
            if existing is not None:
                immutable = {
                    "receipt_key": identity["receipt_key"],
                    "package_id": package["package_id"],
                    "site_id": identity["site_id"],
                    "publisher_id": identity["publisher_id"],
                    "source_worker_id": identity["source_worker_id"],
                    "package_sha256": identity["package_sha256"],
                }
                if any(existing[key] != value for key, value in immutable.items()):
                    raise ComputeEvidenceReceiverError("compute evidence package identity changed", 409)
                if existing["payload_json"].encode("utf-8") != raw:
                    raise ComputeEvidenceReceiverError("compute evidence package content changed", 409)
                created = False
            else:
                connection.execute(
                    """INSERT INTO compute_evidence_receipts(
                           receipt_key,package_id,site_id,publisher_id,source_worker_id,
                           delivery_fencing_epoch,compute_attempt_no,compute_fencing_epoch,
                           manifest_sha256,result_sha256,package_sha256,payload_json,
                           publication_state,received_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'quarantine',?)""",
                    (
                        identity["receipt_key"],
                        package["package_id"],
                        identity["site_id"],
                        identity["publisher_id"],
                        identity["source_worker_id"],
                        epoch,
                        package["attempt_no"],
                        package["fencing_epoch"],
                        package["manifest_sha256"],
                        package["result_sha256"],
                        identity["package_sha256"],
                        raw.decode("utf-8"),
                        now,
                    ),
                )
                created = True
            if epoch > highest:
                connection.execute(
                    """INSERT INTO compute_evidence_receiver_fences(site_id,publisher_id,highest_epoch,updated_at)
                       VALUES(?,?,?,?) ON CONFLICT(site_id,publisher_id) DO UPDATE SET
                       highest_epoch=excluded.highest_epoch,updated_at=excluded.updated_at""",
                    (identity["site_id"], identity["publisher_id"], epoch, now),
                )
            connection.execute(
                """INSERT INTO compute_evidence_receiver_nonces(
                       nonce,receipt_key,package_sha256,expires_at,received_at
                   ) VALUES(?,?,?,?,?)""",
                (
                    identity["nonce"],
                    identity["receipt_key"],
                    identity["package_sha256"],
                    identity["expires_at"],
                    now,
                ),
            )
        return {
            "accepted": True,
            "created": created,
            "receipt_key": identity["receipt_key"],
            "package_id": package["package_id"],
            "package_sha256": identity["package_sha256"],
            "publication_state": "quarantine",
            "research_only": AUTHORITY["research_only"],
            "actionable": AUTHORITY["actionable"],
            "user_visible": AUTHORITY["user_visible"],
        }

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ComputeEvidenceReceiverError("compute evidence receiver clock is invalid", 503)
        return value.astimezone(UTC)


def build_compute_evidence_receiver(
    env: Mapping[str, str] | None = None,
) -> ComputeEvidenceReceiver | None:
    values = os.environ if env is None else env
    if not _boolean(values.get("TRADEAI_COMPUTE_EVIDENCE_RECEIVER_ENABLED", "false")):
        return None
    database_path = Path(values.get("TRADEAI_COMPUTE_EVIDENCE_RECEIVER_DATABASE", "")).expanduser()
    if not database_path.is_absolute():
        raise RuntimeError("TRADEAI_COMPUTE_EVIDENCE_RECEIVER_DATABASE must be absolute")
    database_path = database_path.resolve()
    for name in (
        "DATABASE_URL",
        "TRADEAI_BACKTEST_DATABASE_URL",
        "TRADEAI_COMPUTE_EVIDENCE_SPOOL_DATABASE",
        "TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE",
    ):
        protected = _sqlite_path(values.get(name, ""))
        if protected is not None and protected == database_path:
            raise RuntimeError(f"compute evidence receiver database must be isolated from {name}")
    return ComputeEvidenceReceiver(
        BacktestQueueDatabase(database_path),
        shared_secret=values.get("TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET", ""),
        site_id=values.get("TRADEAI_COMPUTE_EVIDENCE_SITE_ID", ""),
        publisher_id=values.get("TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID", ""),
        enabled=True,
    )


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ComputeEvidenceError) as exc:
        raise ComputeEvidenceError("compute evidence body is invalid JSON") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ComputeEvidenceError("compute evidence JSON contains duplicate keys")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ComputeEvidenceError(f"non-finite JSON constant {value} is forbidden")


def _headers(value: Mapping[str, str]) -> dict[str, str]:
    return {str(key).strip().lower(): str(item).strip() for key, item in value.items()}


def _identity(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", str(value)):
        raise ValueError(f"{label} is invalid")
    return str(value)


def _boolean(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError("compute evidence receiver enabled flag is invalid")


def _sqlite_path(value: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("sqlite:///"):
        raw = raw[10:]
    elif "://" in raw:
        return None
    return Path(raw).expanduser().resolve()
