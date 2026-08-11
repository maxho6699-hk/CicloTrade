"""HMAC-authenticated receiver for shadow-only canonical system-cycle research."""

from __future__ import annotations

from datetime import datetime
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import (
    RECEIVER_ENDPOINT_HEARTBEAT,
    RECEIVER_ENDPOINT_RESULT,
    SystemCycleResearchConflict,
    SystemCycleResearchError,
    SystemCycleResearchStaleFence,
    canonical_json,
    parse_timestamp,
    receiver_signature,
    sha256_bytes,
    validate_system_cycle_heartbeat,
    validate_system_cycle_result,
)
from core.system_cycle_research_store import SystemCycleResearchStore


class SystemCycleResearchReceiverError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class SystemCycleResearchReceiver:
    def __init__(
        self,
        store: SystemCycleResearchStore,
        *,
        shared_secret: str | bytes,
        enabled: bool = False,
        freshness_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        raw = shared_secret.encode("utf-8") if isinstance(shared_secret, str) else shared_secret
        if not isinstance(store, SystemCycleResearchStore):
            raise TypeError("store must be SystemCycleResearchStore")
        if not isinstance(raw, bytes) or len(raw) < 32:
            raise ValueError("system cycle research shared secret must contain at least 32 bytes")
        if not isinstance(freshness_seconds, int) or not 30 <= freshness_seconds <= 900:
            raise ValueError("freshness_seconds is invalid")
        self.store = store
        self._secret = raw
        self.enabled = bool(enabled)
        self.freshness_seconds = freshness_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    def accept_result(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        identity = self._authenticate(raw, headers, RECEIVER_ENDPOINT_RESULT)
        try:
            value = _json(raw)
            result = validate_system_cycle_result(value)
            if canonical_json(result) != raw:
                raise SystemCycleResearchError("result body must use canonical JSON")
            stored = self.store.record_result(
                result,
                receipt_key=identity["idempotency_key"],
                worker_id=identity["worker_id"],
                fencing_epoch=identity["fencing_epoch"],
                result_sha256=identity["body_sha256"],
            )
        except (SystemCycleResearchConflict, SystemCycleResearchStaleFence) as exc:
            raise SystemCycleResearchReceiverError(str(exc), 409) from exc
        except SystemCycleResearchError as exc:
            raise SystemCycleResearchReceiverError(str(exc), 400) from exc
        return {
            "accepted": True,
            "created": bool(stored["created"]),
            "receipt_key": stored["receipt_key"],
            "result_sha256": stored["result_sha256"],
            "state": "shadow",
            "outbound": False,
            "user_visible": False,
        }

    def accept_heartbeat(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        identity = self._authenticate(raw, headers, RECEIVER_ENDPOINT_HEARTBEAT)
        try:
            value = _json(raw)
            heartbeat = validate_system_cycle_heartbeat(value)
            if canonical_json(heartbeat) != raw:
                raise SystemCycleResearchError("heartbeat body must use canonical JSON")
            stored = self.store.record_heartbeat(
                heartbeat,
                heartbeat_key=identity["idempotency_key"],
                worker_id=identity["worker_id"],
                fencing_epoch=identity["fencing_epoch"],
                payload_sha256=identity["body_sha256"],
            )
        except (SystemCycleResearchConflict, SystemCycleResearchStaleFence) as exc:
            raise SystemCycleResearchReceiverError(str(exc), 409) from exc
        except SystemCycleResearchError as exc:
            raise SystemCycleResearchReceiverError(str(exc), 400) from exc
        return {
            "accepted": True,
            "created": bool(stored["created"]),
            "heartbeat_key": stored["heartbeat_key"],
            "payload_sha256": stored["payload_sha256"],
            "state": "shadow",
        }

    def _authenticate(self, raw: bytes, headers: Mapping[str, str], endpoint: str) -> dict[str, Any]:
        if not self.enabled:
            raise SystemCycleResearchReceiverError("system cycle research receiver is disabled", 404)
        worker_id = str(headers.get("x-ciclotrade-worker-id", "")).strip()
        epoch_text = str(headers.get("x-ciclotrade-fencing-epoch", "")).strip()
        key = str(headers.get("idempotency-key", "")).strip()
        sent_at = str(headers.get("x-ciclotrade-sent-at", "")).strip()
        body_sha = str(headers.get("x-ciclotrade-result-sha256", "")).strip()
        supplied = str(headers.get("x-ciclotrade-research-signature", "")).strip()
        if not re.fullmatch(r"[1-9][0-9]{0,9}", epoch_text):
            raise SystemCycleResearchReceiverError("system cycle receiver identity headers are invalid", 401)
        epoch = int(epoch_text)
        try:
            sent = parse_timestamp(sent_at, "sent_at")
            expected = receiver_signature(
                self._secret, endpoint=endpoint, worker_id=worker_id, fencing_epoch=epoch,
                idempotency_key=key, sent_at=sent_at, body_sha256=body_sha,
            )
        except SystemCycleResearchError as exc:
            raise SystemCycleResearchReceiverError("system cycle receiver identity headers are invalid", 401) from exc
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise SystemCycleResearchReceiverError("system cycle receiver clock is invalid", 503)
        if abs((now.astimezone(UTC) - sent).total_seconds()) > self.freshness_seconds:
            raise SystemCycleResearchReceiverError("system cycle receiver signature has expired", 401)
        if not supplied or not hmac.compare_digest(expected, supplied):
            raise SystemCycleResearchReceiverError("system cycle receiver signature is invalid", 401)
        actual_sha = sha256_bytes(raw)
        if not hmac.compare_digest(actual_sha, body_sha):
            raise SystemCycleResearchReceiverError("system cycle result hash does not match the body", 409)
        return {
            "worker_id": worker_id,
            "fencing_epoch": epoch,
            "idempotency_key": key,
            "body_sha256": actual_sha,
        }


def build_system_cycle_research_receiver() -> SystemCycleResearchReceiver | None:
    enabled = os.getenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_RECEIVER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    database_path = Path(os.getenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE", "")).expanduser()
    if not database_path.is_absolute():
        raise RuntimeError("TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE must be an absolute path")
    database_path = database_path.resolve()
    for name in ("DATABASE_URL", "TRADEAI_BACKTEST_DATABASE_URL"):
        protected = _sqlite_path(os.getenv(name, ""))
        if protected is not None and protected == database_path:
            raise RuntimeError(
                f"TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE must be isolated from {name}"
            )
    secret = os.getenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_SHARED_SECRET", "")
    store = SystemCycleResearchStore(BacktestQueueDatabase(database_path))
    return SystemCycleResearchReceiver(store, shared_secret=secret, enabled=True)


def _sqlite_path(value: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("sqlite:///"):
        raw = raw[10:]
    elif "://" in raw:
        return None
    return Path(raw).expanduser().resolve()


async def system_cycle_research_result(request: Request) -> Response:
    raw = await _limited_body(request, 512 * 1024)
    result = await run_in_threadpool(_receiver(request).accept_result, raw, request.headers)
    return JSONResponse(result, status_code=201 if result["created"] else 200, headers=_headers())


async def system_cycle_research_heartbeat(request: Request) -> Response:
    raw = await _limited_body(request, 16 * 1024)
    result = await run_in_threadpool(_receiver(request).accept_heartbeat, raw, request.headers)
    return JSONResponse(result, status_code=201 if result["created"] else 200, headers=_headers())


async def system_cycle_research_receiver_error(_: Request, exc: SystemCycleResearchReceiverError) -> Response:
    return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=_headers())


def _receiver(request: Request) -> SystemCycleResearchReceiver:
    value = getattr(request.app.state, "system_cycle_research_receiver", None)
    if not isinstance(value, SystemCycleResearchReceiver):
        raise SystemCycleResearchReceiverError("system cycle research receiver is unavailable", 404)
    return value


async def _limited_body(request: Request, maximum: int) -> bytes:
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > maximum):
        raise SystemCycleResearchReceiverError("system cycle research body is too large", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise SystemCycleResearchReceiverError("system cycle research body is too large", 413)
    if not body:
        raise SystemCycleResearchReceiverError("system cycle research body is empty", 400)
    return bytes(body)


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, SystemCycleResearchError) as exc:
        raise SystemCycleResearchError("system cycle research body is invalid JSON") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SystemCycleResearchError("system cycle research JSON contains duplicate keys")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise SystemCycleResearchError(f"non-finite JSON constant {value} is forbidden")


def _headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
