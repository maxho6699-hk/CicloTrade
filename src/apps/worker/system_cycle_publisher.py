"""Single-shot HTTPS publisher for fenced system-cycle shadow research."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
import http.client
import json
import math
import os
from pathlib import Path
import ssl
import sys
import time
from typing import Any, Callable, Mapping
from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import (
    SystemCycleResearchError, canonical_json, sha256_bytes, validate_system_cycle_result,
)
from src.apps.worker.system_cycle_research import build_system_cycle_heartbeat
from src.apps.worker.system_cycle_spool import (
    PersistentSystemCycleSpool, SystemCycleSpoolError, signed_heartbeat_headers, signed_result_headers,
)
PUBLISH_ORIGIN = "https://ciclotrade.com"
PUBLISH_HOST = "ciclotrade.com"
PUBLISH_PORT = 443
RESULT_PATH = "/api/rewrite/internal/v1/system-cycle-research/results"
HEARTBEAT_PATH = "/api/rewrite/internal/v1/system-cycle-research/heartbeat"
PUBLISH_PATHS = frozenset({RESULT_PATH, HEARTBEAT_PATH})
PUBLISHER_WORKER_ID = "system-cycle-publisher"
HEARTBEAT_WORKER_ID = "system-cycle-publisher-heartbeat"
RESULT_REQUEST_LIMIT = 512 * 1024
HEARTBEAT_REQUEST_LIMIT = 16 * 1024
RETRYABLE_HTTP_STATUSES = frozenset({408, 429})
class SystemCyclePublisherError(RuntimeError):
    """Raised when the bounded publisher cannot safely continue."""
class SystemCyclePublisherConfigurationError(SystemCyclePublisherError):
    """Raised for disabled-by-default deployment configuration violations."""
class PublisherTransportError(SystemCyclePublisherError):
    def __init__(
        self, message: str, *, status: int | None = None, headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.headers = _headers(headers or {})
class PublisherRetryableTransportError(PublisherTransportError):
    """The connection failed before any HTTP request could be sent."""
class PublisherUncertainTransportError(PublisherTransportError):
    """The request may have reached the receiver, so blind retry is unsafe."""
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
class SystemCyclePublisherSettings:
    enabled: bool
    database_path: Path | None = None
    shared_secret: bytes = field(default=b"", repr=False)
    connect_timeout_seconds: float = 5.0
    total_timeout_seconds: float = 20.0
    max_response_bytes: int = 64 * 1024
    lease_seconds: int = 90
    max_retry_after_seconds: int = 3_600
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SystemCyclePublisherSettings:
        values = os.environ if env is None else env
        enabled = _boolean(values.get("TRADEAI_SYSTEM_CYCLE_PUBLISHER_ENABLED", "false"))
        if not enabled:
            return cls(enabled=False)
        path = Path(values.get("TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE", "")).expanduser()
        if not path.is_absolute():
            raise SystemCyclePublisherConfigurationError("TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE must be absolute")
        secret = values.get("TRADEAI_SYSTEM_CYCLE_RESEARCH_SHARED_SECRET", "").encode("utf-8")
        if len(secret) < 32:
            raise SystemCyclePublisherConfigurationError("publisher shared secret must contain at least 32 bytes")
        connect = _number(values.get("TRADEAI_SYSTEM_CYCLE_PUBLISHER_CONNECT_TIMEOUT", "5"), 1, 30)
        total = _number(values.get("TRADEAI_SYSTEM_CYCLE_PUBLISHER_TOTAL_TIMEOUT", "20"), 2, 60)
        maximum = _integer(values.get("TRADEAI_SYSTEM_CYCLE_PUBLISHER_MAX_RESPONSE_BYTES", "65536"), 1024, 262_144)
        lease = _integer(values.get("TRADEAI_SYSTEM_CYCLE_PUBLISHER_LEASE_SECONDS", "90"), 30, 600)
        retry = _integer(values.get("TRADEAI_SYSTEM_CYCLE_PUBLISHER_MAX_RETRY_AFTER", "3600"), 30, 86_400)
        if connect > total:
            raise SystemCyclePublisherConfigurationError("publisher connect timeout cannot exceed total timeout")
        if lease < math.ceil(total) + 10:
            raise SystemCyclePublisherConfigurationError("publisher lease must exceed total timeout by at least 10 seconds")
        return cls(
            True, path.resolve(), secret, connect, total, maximum, lease, retry,
        )
class HttpsPublisherTransport:
    """Verified TLS transport pinned to the one approved website origin."""
    def __init__(self, *, monotonic: Callable[[], float] | None = None) -> None:
        self.monotonic = monotonic or time.monotonic
    def post(
        self, path: str, headers: Mapping[str, str], body: bytes, *,
        connect_timeout_seconds: float,
        total_timeout_seconds: float,
        max_response_bytes: int,
    ) -> PublisherResponse:
        if path not in PUBLISH_PATHS:
            raise SystemCyclePublisherConfigurationError("publisher path is outside the fixed receiver contract")
        request_limit = RESULT_REQUEST_LIMIT if path == RESULT_PATH else HEARTBEAT_REQUEST_LIMIT
        if not isinstance(body, bytes) or not 1 <= len(body) <= request_limit:
            raise SystemCyclePublisherConfigurationError("publisher request body exceeds its fixed limit")
        outbound_headers = {str(key).lower(): str(value) for key, value in headers.items()}
        if "host" in outbound_headers or outbound_headers.get("content-type") != "application/json":
            raise SystemCyclePublisherConfigurationError("publisher headers are invalid")
        outbound_headers["accept"] = "application/json"
        deadline = self.monotonic() + total_timeout_seconds
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(
            PUBLISH_HOST, PUBLISH_PORT, timeout=min(connect_timeout_seconds, total_timeout_seconds), context=context,
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
                connection.request("POST", path, body=body, headers=outbound_headers)
                self._set_timeout(connection, deadline, request_started=True, status=None)
                response = connection.getresponse()
                status = int(response.status)
                response_headers = _headers(dict(response.getheaders()))
                content_length = response_headers.get("content-length", "")
                if content_length.isdigit() and int(content_length) > max_response_bytes:
                    raise PublisherUncertainTransportError(
                        "publisher response exceeded its size limit",
                        status=status,
                        headers=response_headers,
                    )
                chunks: list[bytes] = []
                received = 0
                while received <= max_response_bytes:
                    self._set_timeout(
                        connection, deadline, request_started=True,
                        status=status, headers=response_headers,
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
                return PublisherResponse(status=status, headers=response_headers, body=b"".join(chunks))
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
            error_type = PublisherUncertainTransportError if request_started else PublisherRetryableTransportError
            raise error_type(
                "publisher total timeout expired", status=status, headers=headers
            )
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
class SystemCyclePublisher:
    def __init__(
        self,
        spool: PersistentSystemCycleSpool,
        settings: SystemCyclePublisherSettings,
        transport: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(spool, PersistentSystemCycleSpool):
            raise TypeError("spool must be PersistentSystemCycleSpool")
        if not settings.enabled:
            raise SystemCyclePublisherConfigurationError("publisher settings are disabled")
        self.spool = spool
        self.settings = settings
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))
    def run_once(self) -> dict[str, Any]:
        quarantined = self.spool.quarantine_expired_deliveries()
        claim = self.spool.claim(PUBLISHER_WORKER_ID, lease_seconds=self.settings.lease_seconds)
        if claim is None:
            result = self._publish_heartbeat()
        else:
            result = self._publish_result(claim)
        result["quarantined_expired_deliveries"] = quarantined
        result["origin"] = PUBLISH_ORIGIN
        return result
    def _publish_result(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        try:
            result = validate_system_cycle_result(claim["payload"])
            body = canonical_json(result)
            if len(body) > RESULT_REQUEST_LIMIT:
                return self._dead(claim, "sealed result exceeds receiver request limit")
            headers = signed_result_headers(claim, self.settings.shared_secret, sent_at=self._now())
        except (KeyError, TypeError, SystemCycleResearchError, SystemCycleSpoolError) as exc:
            return self._dead(claim, f"local sealed result is invalid: {type(exc).__name__}")
        self.spool.begin_delivery(*self._lease(claim))
        try:
            response = self._post(RESULT_PATH, headers, body)
        except PublisherTransportError as exc:
            return self._handle_result_transport_error(claim, exc)
        return self._handle_result_response(claim, response)
    def _handle_result_transport_error(
        self, claim: Mapping[str, Any], exc: PublisherTransportError
    ) -> dict[str, Any]:
        if isinstance(exc, PublisherRetryableTransportError):
            return self._retry(claim, str(exc), headers=exc.headers, http_status=exc.status)
        if exc.status is not None and _retryable_status(exc.status):
            return self._retry(claim, str(exc), headers=exc.headers, http_status=exc.status)
        if exc.status is not None and not 200 <= exc.status <= 299:
            return self._dead(claim, str(exc), http_status=exc.status)
        return self._uncertain(claim, str(exc), http_status=exc.status)
    def _handle_result_response(
        self, claim: Mapping[str, Any], response: PublisherResponse
    ) -> dict[str, Any]:
        if 200 <= response.status <= 299:
            try:
                receipt = _json_receipt(response)
                stored = self.spool.complete(*self._lease(claim), receipt, http_status=response.status)
            except (ValueError, SystemCycleResearchError, SystemCycleSpoolError) as exc:
                return self._uncertain(
                    claim, f"successful response receipt is invalid: {type(exc).__name__}",
                    http_status=response.status,
                )
            return _spool_result("delivered", stored, response.status)
        if _retryable_status(response.status):
            return self._retry(
                claim, f"receiver returned retryable HTTP {response.status}",
                headers=response.headers, http_status=response.status,
            )
        return self._dead(
            claim, f"receiver returned terminal HTTP {response.status}", http_status=response.status
        )
    def _retry(
        self,
        claim: Mapping[str, Any],
        error: str,
        *,
        headers: Mapping[str, str],
        http_status: int | None,
    ) -> dict[str, Any]:
        delay = _retry_delay(
            headers, now=self._now(), attempts=int(claim["attempts"]),
            maximum=self.settings.max_retry_after_seconds,
        )
        stored = self.spool.fail(
            *self._lease(claim), error=error, retry_delay_seconds=delay, http_status=http_status
        )
        result = _spool_result("retryable", stored, http_status)
        result["retry_after_seconds"] = delay
        return result
    def _dead(
        self, claim: Mapping[str, Any], error: str, *, http_status: int | None = None
    ) -> dict[str, Any]:
        stored = self.spool.dead(*self._lease(claim), error=error, http_status=http_status)
        return _spool_result("dead", stored, http_status)
    def _uncertain(
        self, claim: Mapping[str, Any], error: str, *, http_status: int | None = None
    ) -> dict[str, Any]:
        try:
            stored = self.spool.uncertain(*self._lease(claim), error=error, http_status=http_status)
        except SystemCycleSpoolError:
            return {"state": "uncertain", "persisted": False, "http_status": http_status}
        return _spool_result("uncertain", stored, http_status)
    def _publish_heartbeat(self) -> dict[str, Any]:
        gate = self.spool.heartbeat_delivery(HEARTBEAT_WORKER_ID)
        gate_state = str(gate["heartbeat_delivery_state"])
        if gate_state != "ready":
            return {
                "state": f"heartbeat_{'deferred' if gate_state == 'retryable' else gate_state}",
                "retry_at": gate.get("heartbeat_retry_at"),
                "http_status": gate.get("heartbeat_last_http_status"),
            }
        epoch = self.spool.allocate_fencing_epoch(HEARTBEAT_WORKER_ID)
        heartbeat = build_system_cycle_heartbeat(
            worker_id=HEARTBEAT_WORKER_ID,
            fencing_epoch=epoch,
            counts=self.spool.counts(),
            last_result_sha256=self.spool.last_delivered_sha256(),
            heartbeat_at=self._now(),
        )
        body = canonical_json(heartbeat)
        key = f"system-heartbeat-{epoch:010d}"
        headers = signed_heartbeat_headers(
            heartbeat, self.settings.shared_secret, idempotency_key=key, sent_at=self._now()
        )
        try:
            response = self._post(HEARTBEAT_PATH, headers, body)
        except PublisherTransportError as exc:
            return self._handle_heartbeat_error(exc)
        if 200 <= response.status <= 299:
            try:
                receipt = _json_receipt(response)
                expected = {
                    "accepted", "created", "heartbeat_key", "payload_sha256", "state",
                }
                if (
                    set(receipt) != expected
                    or receipt.get("accepted") is not True
                    or not isinstance(receipt.get("created"), bool)
                    or receipt.get("heartbeat_key") != key
                    or receipt.get("payload_sha256") != sha256_bytes(body)
                    or receipt.get("state") != "shadow"
                ):
                    raise ValueError("heartbeat receipt is not bound")
            except (ValueError, SystemCycleResearchError) as exc:
                return self._record_heartbeat(
                    "uncertain", f"successful heartbeat receipt is invalid: {type(exc).__name__}",
                    response.status,
                )
            self.spool.record_heartbeat_delivery(
                HEARTBEAT_WORKER_ID, state="ready", http_status=response.status
            )
            return {"state": "heartbeat_delivered", "http_status": response.status, "fencing_epoch": epoch}
        if _retryable_status(response.status):
            return self._record_heartbeat(
                "retryable", f"receiver returned retryable HTTP {response.status}",
                response.status, response.headers,
            )
        return self._record_heartbeat(
            "dead", f"receiver returned terminal HTTP {response.status}", response.status
        )
    def _handle_heartbeat_error(self, exc: PublisherTransportError) -> dict[str, Any]:
        if isinstance(exc, PublisherRetryableTransportError) or (
            exc.status is not None and _retryable_status(exc.status)
        ):
            return self._record_heartbeat("retryable", str(exc), exc.status, exc.headers)
        if exc.status is not None and not 200 <= exc.status <= 299:
            return self._record_heartbeat("dead", str(exc), exc.status)
        return self._record_heartbeat("uncertain", str(exc), exc.status)
    def _record_heartbeat(
        self,
        state: str,
        error: str,
        http_status: int | None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        delay = None
        if state == "retryable":
            delay = _retry_delay(
                headers or {}, now=self._now(), attempts=1,
                maximum=self.settings.max_retry_after_seconds,
            )
        stored = self.spool.record_heartbeat_delivery(
            HEARTBEAT_WORKER_ID,
            state=state,
            error=error,
            http_status=http_status,
            retry_delay_seconds=delay,
        )
        return {
            "state": f"heartbeat_{state}",
            "http_status": http_status,
            "retry_at": stored["heartbeat_retry_at"],
        }
    def _post(self, path: str, headers: Mapping[str, str], body: bytes) -> PublisherResponse:
        return self.transport.post(
            path,
            headers,
            body,
            connect_timeout_seconds=self.settings.connect_timeout_seconds,
            total_timeout_seconds=self.settings.total_timeout_seconds,
            max_response_bytes=self.settings.max_response_bytes,
        )
    @staticmethod
    def _lease(claim: Mapping[str, Any]) -> tuple[int, str, str, int]:
        return (
            int(claim["id"]),
            PUBLISHER_WORKER_ID,
            str(claim["lease_token"]),
            int(claim["fencing_epoch"]),
        )
    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise SystemCyclePublisherConfigurationError("publisher clock must include a timezone")
        return value.astimezone(UTC)
def run_system_cycle_publisher(
    *,
    env: Mapping[str, str] | None = None,
    transport: Any = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    settings = SystemCyclePublisherSettings.from_env(env)
    if not settings.enabled:
        return {"state": "disabled", "origin": PUBLISH_ORIGIN}
    database = BacktestQueueDatabase(settings.database_path)
    spool = PersistentSystemCycleSpool(database, clock=clock)
    return SystemCyclePublisher(
        spool, settings, transport or HttpsPublisherTransport(), clock=clock
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
    headers: Mapping[str, str], *, now: datetime, attempts: int, maximum: int
) -> int:
    raw = _headers(headers).get("retry-after", "").strip()
    if raw.isdigit():
        return min(int(raw), maximum)
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return min(max(0, math.ceil((parsed.astimezone(UTC) - now).total_seconds())), maximum)
        except (TypeError, ValueError, OverflowError):
            pass
    return min(15 * (2 ** min(max(attempts - 1, 0), 8)), maximum)
def _retryable_status(status: int) -> bool:
    return status in RETRYABLE_HTTP_STATUSES or 500 <= status <= 599
def _headers(value: Mapping[str, str]) -> dict[str, str]:
    return {str(key).strip().lower(): str(item).strip() for key, item in value.items()}
def _spool_result(state: str, row: Mapping[str, Any], http_status: int | None) -> dict[str, Any]:
    return {
        "state": state,
        "spool_id": int(row["id"]),
        "attempts": int(row["attempts"]),
        "http_status": http_status,
        "retry_at": row.get("retry_at") if state == "retryable" else None,
    }
def _boolean(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise SystemCyclePublisherConfigurationError("publisher enabled flag is invalid")
def _number(value: Any, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SystemCyclePublisherConfigurationError("publisher numeric setting is invalid") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise SystemCyclePublisherConfigurationError("publisher numeric setting is outside its bounds")
    return parsed
def _integer(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise SystemCyclePublisherConfigurationError("publisher integer setting is invalid")
    parsed = int(str(value).strip())
    if not minimum <= parsed <= maximum:
        raise SystemCyclePublisherConfigurationError("publisher integer setting is outside its bounds")
    return parsed
def main() -> int:
    try:
        result = run_system_cycle_publisher()
    except (SystemCyclePublisherError, SystemCycleSpoolError, SystemCycleResearchError) as exc:
        print(json.dumps({"state": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 1 if result["state"] in {"dead", "uncertain", "heartbeat_dead", "heartbeat_uncertain"} else 0
if __name__ == "__main__":
    raise SystemExit(main())
