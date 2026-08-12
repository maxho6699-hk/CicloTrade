"""Single-shot fixed-origin HTTPS publisher for compute evidence packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import http.client
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
import time
from typing import Any, Callable, Mapping

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.compute_evidence_contracts import (
    RECEIVER_HTTP_PATH,
    ComputeEvidenceError,
    canonical_json,
    validate_package,
)
from src.apps.worker.compute_evidence_spool import (
    ComputeEvidenceSpoolError,
    PersistentComputeEvidenceSpool,
)


PUBLISH_ORIGIN = "https://ciclotrade.com"
PUBLISH_HOST = "ciclotrade.com"
PUBLISH_PORT = 443
PUBLISH_PATH = RECEIVER_HTTP_PATH
PUBLISHER_ID = "compute-evidence-publisher"
REQUEST_LIMIT = 512 * 1024
RETRYABLE_HTTP_STATUSES = frozenset({408, 429})


class ComputeEvidencePublisherError(RuntimeError):
    pass


class ComputeEvidencePublisherConfigurationError(ComputeEvidencePublisherError):
    pass


class PublisherTransportError(ComputeEvidencePublisherError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.headers = _headers(headers or {})


class PublisherRetryableTransportError(PublisherTransportError):
    """The connection failed before the request could be sent."""


class PublisherUncertainTransportError(PublisherTransportError):
    """The request may have reached the receiver."""


@dataclass(frozen=True)
class PublisherResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int) or not 100 <= self.status <= 599:
            raise ValueError("publisher response status is invalid")
        if not isinstance(self.body, bytes):
            raise TypeError("publisher response body must be bytes")
        object.__setattr__(self, "headers", _headers(self.headers))


@dataclass(frozen=True)
class ComputeEvidencePublisherSettings:
    enabled: bool
    database_path: Path | None = None
    shared_secret: bytes = field(default=b"", repr=False)
    publisher_id: str = PUBLISHER_ID
    connect_timeout_seconds: float = 5.0
    total_timeout_seconds: float = 20.0
    max_response_bytes: int = 64 * 1024
    lease_seconds: int = 90
    delivery_expiry_seconds: int = 120
    max_retry_after_seconds: int = 3_600

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> ComputeEvidencePublisherSettings:
        values = os.environ if env is None else env
        enabled = _boolean(values.get("TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ENABLED", "false"))
        if not enabled:
            return cls(enabled=False)
        path = Path(values.get("TRADEAI_COMPUTE_EVIDENCE_SPOOL_DATABASE", "")).expanduser()
        if not path.is_absolute():
            raise ComputeEvidencePublisherConfigurationError("TRADEAI_COMPUTE_EVIDENCE_SPOOL_DATABASE must be absolute")
        secret = values.get("TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET", "").encode("utf-8")
        if len(secret) < 32:
            raise ComputeEvidencePublisherConfigurationError(
                "compute evidence shared secret must contain at least 32 bytes"
            )
        publisher_id = values.get("TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID", PUBLISHER_ID)
        if not _safe_identity(publisher_id):
            raise ComputeEvidencePublisherConfigurationError("publisher_id is invalid")
        connect = _number(values.get("TRADEAI_COMPUTE_EVIDENCE_CONNECT_TIMEOUT", "5"), 1, 30)
        total = _number(values.get("TRADEAI_COMPUTE_EVIDENCE_TOTAL_TIMEOUT", "20"), 2, 60)
        maximum = _integer(values.get("TRADEAI_COMPUTE_EVIDENCE_MAX_RESPONSE_BYTES", "65536"), 1024, 262_144)
        lease = _integer(values.get("TRADEAI_COMPUTE_EVIDENCE_LEASE_SECONDS", "90"), 30, 600)
        expiry = _integer(values.get("TRADEAI_COMPUTE_EVIDENCE_EXPIRY_SECONDS", "120"), 30, 300)
        retry = _integer(values.get("TRADEAI_COMPUTE_EVIDENCE_MAX_RETRY_AFTER", "3600"), 30, 86_400)
        if connect > total:
            raise ComputeEvidencePublisherConfigurationError("publisher connect timeout cannot exceed total timeout")
        if lease < math.ceil(total) + 10:
            raise ComputeEvidencePublisherConfigurationError(
                "publisher lease must exceed total timeout by at least 10 seconds"
            )
        return cls(
            True,
            path.resolve(),
            secret,
            publisher_id,
            connect,
            total,
            maximum,
            lease,
            expiry,
            retry,
        )


class HttpsPublisherTransport:
    """Verified TLS transport pinned to the one approved website receiver path."""

    def __init__(self, *, monotonic: Callable[[], float] | None = None) -> None:
        self.monotonic = monotonic or time.monotonic

    def post(
        self,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        connect_timeout_seconds: float,
        total_timeout_seconds: float,
        max_response_bytes: int,
    ) -> PublisherResponse:
        if path != PUBLISH_PATH:
            raise ComputeEvidencePublisherConfigurationError("publisher path is outside the fixed receiver contract")
        if not isinstance(body, bytes) or not 1 <= len(body) <= REQUEST_LIMIT:
            raise ComputeEvidencePublisherConfigurationError("publisher request body exceeds its fixed limit")
        outbound = _headers(headers)
        if "host" in outbound or outbound.get("content-type") != "application/json":
            raise ComputeEvidencePublisherConfigurationError("publisher headers are invalid")
        outbound["accept"] = "application/json"
        deadline = self.monotonic() + total_timeout_seconds
        connection = http.client.HTTPSConnection(
            PUBLISH_HOST,
            PUBLISH_PORT,
            timeout=min(connect_timeout_seconds, total_timeout_seconds),
            context=ssl.create_default_context(),
        )
        status: int | None = None
        response_headers: dict[str, str] = {}
        try:
            try:
                connection.connect()
            except (OSError, TimeoutError, http.client.HTTPException) as exc:
                raise PublisherRetryableTransportError(
                    f"publisher connection failed before request: {type(exc).__name__}"
                ) from exc
            try:
                self._set_timeout(connection, deadline, request_started=False, status=None)
                connection.request("POST", path, body=body, headers=outbound)
                self._set_timeout(connection, deadline, request_started=True, status=None)
                response = connection.getresponse()
                status = int(response.status)
                response_headers = _headers(dict(response.getheaders()))
                declared = response_headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_response_bytes:
                    raise PublisherUncertainTransportError(
                        "publisher response exceeded its size limit",
                        status=status,
                        headers=response_headers,
                    )
                chunks: list[bytes] = []
                received = 0
                while received <= max_response_bytes:
                    self._set_timeout(
                        connection,
                        deadline,
                        request_started=True,
                        status=status,
                        headers=response_headers,
                    )
                    chunk = response.read(min(65_536, max_response_bytes + 1 - received))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                if received > max_response_bytes:
                    raise PublisherUncertainTransportError(
                        "publisher response exceeded its size limit",
                        status=status,
                        headers=response_headers,
                    )
                return PublisherResponse(status, response_headers, b"".join(chunks))
            except PublisherTransportError:
                raise
            except (OSError, TimeoutError, http.client.HTTPException) as exc:
                raise PublisherUncertainTransportError(
                    f"publisher response is uncertain: {type(exc).__name__}",
                    status=status,
                    headers=response_headers,
                ) from exc
        finally:
            connection.close()

    def _set_timeout(
        self,
        connection: http.client.HTTPSConnection,
        deadline: float,
        *,
        request_started: bool,
        status: int | None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            error = PublisherUncertainTransportError if request_started else PublisherRetryableTransportError
            raise error("publisher total timeout expired", status=status, headers=headers)
        if connection.sock is not None:
            connection.sock.settimeout(remaining)


class ComputeEvidencePublisher:
    def __init__(
        self,
        spool: PersistentComputeEvidenceSpool,
        settings: ComputeEvidencePublisherSettings,
        transport: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(spool, PersistentComputeEvidenceSpool):
            raise TypeError("spool must be PersistentComputeEvidenceSpool")
        if not settings.enabled:
            raise ComputeEvidencePublisherConfigurationError("publisher settings are disabled")
        self.spool = spool
        self.settings = settings
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))

    def run_once(self) -> dict[str, Any]:
        quarantined = self.spool.quarantine_expired_deliveries()
        claim = self.spool.claim(
            self.settings.publisher_id,
            lease_seconds=self.settings.lease_seconds,
        )
        if claim is None:
            return {
                "state": "idle",
                "origin": PUBLISH_ORIGIN,
                "quarantined_expired_deliveries": quarantined,
            }
        result = self._publish(claim)
        result["origin"] = PUBLISH_ORIGIN
        result["quarantined_expired_deliveries"] = quarantined
        return result

    def _publish(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        try:
            package = validate_package(claim["payload"])
            body = canonical_json(package)
            if len(body) > REQUEST_LIMIT:
                return self._stop(claim, "dead", "sealed package exceeds receiver request limit")
            now = self._now()
            headers = self.spool.signed_headers(
                claim,
                self.settings.shared_secret,
                nonce=secrets.token_urlsafe(32),
                expires_at=now + timedelta(seconds=self.settings.delivery_expiry_seconds),
            )
        except (KeyError, TypeError, ComputeEvidenceError, ComputeEvidenceSpoolError) as exc:
            return self._stop(claim, "dead", f"local sealed package is invalid: {type(exc).__name__}")
        self.spool.begin_delivery(*self._lease(claim))
        try:
            response = self.transport.post(
                PUBLISH_PATH,
                headers,
                body,
                connect_timeout_seconds=self.settings.connect_timeout_seconds,
                total_timeout_seconds=self.settings.total_timeout_seconds,
                max_response_bytes=self.settings.max_response_bytes,
            )
        except PublisherTransportError as exc:
            return self._transport_error(claim, exc)
        return self._response(claim, response)

    def _transport_error(
        self,
        claim: Mapping[str, Any],
        exc: PublisherTransportError,
    ) -> dict[str, Any]:
        if isinstance(exc, PublisherUncertainTransportError):
            return self._stop(claim, "uncertain", str(exc), exc.status)
        if isinstance(exc, PublisherRetryableTransportError):
            return self._retry(claim, str(exc), exc.headers, exc.status)
        if exc.status is not None and _retryable_status(exc.status):
            return self._retry(claim, str(exc), exc.headers, exc.status)
        if exc.status is not None and not 200 <= exc.status <= 299:
            return self._stop(claim, "dead", str(exc), exc.status)
        return self._stop(claim, "uncertain", str(exc), exc.status)

    def _response(
        self,
        claim: Mapping[str, Any],
        response: PublisherResponse,
    ) -> dict[str, Any]:
        if 200 <= response.status <= 299:
            try:
                receipt = _json_receipt(response)
                stored = self.spool.complete(*self._lease(claim), receipt, http_status=response.status)
            except (ValueError, ComputeEvidenceError, ComputeEvidenceSpoolError) as exc:
                return self._stop(
                    claim,
                    "uncertain",
                    f"successful response receipt is invalid: {type(exc).__name__}",
                    response.status,
                )
            return _spool_result("delivered", stored, response.status)
        if _retryable_status(response.status):
            return self._retry(
                claim,
                f"receiver returned retryable HTTP {response.status}",
                response.headers,
                response.status,
            )
        return self._stop(
            claim,
            "dead",
            f"receiver returned terminal HTTP {response.status}",
            response.status,
        )

    def _retry(
        self,
        claim: Mapping[str, Any],
        error: str,
        headers: Mapping[str, str],
        http_status: int | None,
    ) -> dict[str, Any]:
        delay = _retry_delay(
            headers,
            now=self._now(),
            attempts=int(claim["attempts"]),
            maximum=self.settings.max_retry_after_seconds,
        )
        stored = self.spool.fail(
            *self._lease(claim),
            error=error,
            retry_delay_seconds=delay,
            http_status=http_status,
        )
        result = _spool_result("retryable", stored, http_status)
        result["retry_after_seconds"] = delay
        return result

    def _stop(
        self,
        claim: Mapping[str, Any],
        state: str,
        error: str,
        http_status: int | None = None,
    ) -> dict[str, Any]:
        try:
            stored = self.spool.stop(*self._lease(claim), state=state, error=error, http_status=http_status)
        except ComputeEvidenceSpoolError:
            return {"state": state, "persisted": False, "http_status": http_status}
        return _spool_result(state, stored, http_status)

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ComputeEvidencePublisherConfigurationError("publisher clock must include a timezone")
        return value.astimezone(UTC)

    def _lease(self, claim: Mapping[str, Any]) -> tuple[int, str, str, int]:
        return (
            int(claim["id"]),
            self.settings.publisher_id,
            str(claim["lease_token"]),
            int(claim["fencing_epoch"]),
        )


def run_compute_evidence_publisher(
    *,
    env: Mapping[str, str] | None = None,
    transport: Any = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    settings = ComputeEvidencePublisherSettings.from_env(env)
    if not settings.enabled:
        return {"state": "disabled", "origin": PUBLISH_ORIGIN}
    spool = PersistentComputeEvidenceSpool(
        BacktestQueueDatabase(settings.database_path),
        clock=clock,
    )
    return ComputeEvidencePublisher(
        spool,
        settings,
        transport or HttpsPublisherTransport(),
        clock=clock,
    ).run_once()


def _json_receipt(response: PublisherResponse) -> dict[str, Any]:
    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ValueError("publisher response content type is invalid")
    try:
        value = json.loads(
            response.body,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("publisher response is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("publisher response must be an object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("publisher response contains duplicate keys")
        value[key] = item
    return value


def _retry_delay(
    headers: Mapping[str, str],
    *,
    now: datetime,
    attempts: int,
    maximum: int,
) -> int:
    raw = _headers(headers).get("retry-after", "").strip()
    if raw.isdigit():
        return min(int(raw), maximum)
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return min(
                    max(0, math.ceil((parsed.astimezone(UTC) - now).total_seconds())),
                    maximum,
                )
        except (TypeError, ValueError, OverflowError):
            pass
    return min(15 * (2 ** min(max(attempts - 1, 0), 8)), maximum)


def _retryable_status(status: int) -> bool:
    return status in RETRYABLE_HTTP_STATUSES or 500 <= status <= 599


def _headers(value: Mapping[str, str]) -> dict[str, str]:
    return {str(key).strip().lower(): str(item).strip() for key, item in value.items()}


def _spool_result(
    state: str,
    row: Mapping[str, Any],
    http_status: int | None,
) -> dict[str, Any]:
    return {
        "state": state,
        "spool_id": int(row["id"]),
        "attempts": int(row["attempts"]),
        "http_status": http_status,
        "retry_at": row.get("retry_at") if state == "retryable" else None,
    }


def _safe_identity(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", str(value)))


def _boolean(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ComputeEvidencePublisherConfigurationError("publisher enabled flag is invalid")


def _number(value: Any, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ComputeEvidencePublisherConfigurationError("publisher numeric setting is invalid") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ComputeEvidencePublisherConfigurationError("publisher numeric setting is outside its bounds")
    return parsed


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise ComputeEvidencePublisherConfigurationError("publisher integer setting is invalid")
    parsed = int(str(value).strip())
    if not minimum <= parsed <= maximum:
        raise ComputeEvidencePublisherConfigurationError("publisher integer setting is outside its bounds")
    return parsed
