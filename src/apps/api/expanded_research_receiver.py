"""HMAC receiver for the isolated 97-symbol research result ledger.

The module is intentionally standalone.  The integration owner may mount its
handlers later; this file does not modify the shared Starlette route table.
"""

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

from core.compat import UTC
from core.backtest_queue_database import BacktestQueueDatabase
from core.expanded_research_contracts import (
    ExpandedResearchConflict,
    ExpandedResearchError,
    ExpandedResearchStaleFence,
    INVALIDATION_KIND,
    canonical_json,
    parse_timestamp,
    receiver_signature,
    sha256_bytes,
    validate_invalidation,
    validate_result,
)
from core.expanded_research_store import ExpandedResearchStore


MAX_RESULT_BYTES = 1 * 1024 * 1024
MIN_FRESHNESS_SECONDS = 30
MAX_FRESHNESS_SECONDS = 900
EPOCH_PATTERN = re.compile(r"^[1-9][0-9]{0,9}$")


class ExpandedResearchReceiverError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class ExpandedResearchReceiver:
    def __init__(
        self,
        store: ExpandedResearchStore,
        *,
        shared_secret: str | bytes,
        enabled: bool = False,
        freshness_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        secret = shared_secret.encode("utf-8") if isinstance(shared_secret, str) else shared_secret
        if not isinstance(store, ExpandedResearchStore):
            raise TypeError("store must be ExpandedResearchStore")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("expanded research shared secret must contain at least 32 bytes")
        if not isinstance(freshness_seconds, int) or not MIN_FRESHNESS_SECONDS <= freshness_seconds <= MAX_FRESHNESS_SECONDS:
            raise ValueError("expanded research freshness_seconds is invalid")
        self.store = store
        self._secret = secret
        self.enabled = bool(enabled)
        self.freshness_seconds = freshness_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    def accept(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        if not self.enabled:
            raise ExpandedResearchReceiverError("expanded research receiver is disabled", 404)
        if not isinstance(raw, bytes) or not raw:
            raise ExpandedResearchReceiverError("expanded research body is empty", 400)
        if len(raw) > MAX_RESULT_BYTES:
            raise ExpandedResearchReceiverError("expanded research body is too large", 413)
        normalized = _headers(headers)
        if normalized.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise ExpandedResearchReceiverError("expanded research content type is invalid", 415)
        try:
            identity = self._authenticate(raw, normalized)
            payload = _json(raw)
            if isinstance(payload, Mapping) and payload.get("kind") == INVALIDATION_KIND:
                result = validate_invalidation(payload)
                if canonical_json(result) != raw:
                    raise ExpandedResearchError("expanded research body must use canonical JSON")
                stored = self.store.invalidate(
                    result,
                    receipt_key=identity["idempotency_key"],
                    worker_id=identity["worker_id"],
                    fencing_epoch=identity["fencing_epoch"],
                    payload_sha256=identity["body_sha256"],
                )
                return {
                    "accepted": True, "created": bool(stored["created"]),
                    "receipt_key": stored["invalidation_key"], "invalidation_id": stored["invalidation_id"],
                    "target_result_id": stored["target_result_id"], "payload_sha256": stored["payload_sha256"],
                    "result_sha256": stored["payload_sha256"], "state": "invalidated",
                    "research_only": True, "shadow": True, "actionable": False, "outbound": False,
                    "user_visible": False, "execution": False, "official": False, "live": False,
                }
            result = validate_result(payload)
            if canonical_json(result) != raw:
                raise ExpandedResearchError("expanded research body must use canonical JSON")
            stored = self.store.record(
                result,
                receipt_key=identity["idempotency_key"],
                worker_id=identity["worker_id"],
                fencing_epoch=identity["fencing_epoch"],
                payload_sha256=identity["body_sha256"],
            )
        except ExpandedResearchReceiverError:
            raise
        except (ExpandedResearchConflict, ExpandedResearchStaleFence) as exc:
            raise ExpandedResearchReceiverError(str(exc), 409) from exc
        except ExpandedResearchError as exc:
            raise ExpandedResearchReceiverError(str(exc), 400) from exc
        return {
            "accepted": True, "created": bool(stored["created"]), "receipt_key": stored["receipt_key"],
            "result_id": stored["result_id"], "payload_sha256": stored["payload_sha256"],
            "result_sha256": stored["payload_sha256"], "state": "shadow",
            "research_only": True, "shadow": True, "actionable": False, "outbound": False,
            "user_visible": False, "execution": False, "official": False, "live": False,
        }

    def _authenticate(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        worker_id = headers.get("x-ciclotrade-research-worker-id", "")
        epoch_text = headers.get("x-ciclotrade-research-fencing-epoch", "")
        idempotency_key = headers.get("idempotency-key", "")
        sent_at = headers.get("x-ciclotrade-research-sent-at", "")
        body_sha = headers.get("x-ciclotrade-research-sha256", "")
        supplied = headers.get("x-ciclotrade-research-signature", "")
        if not EPOCH_PATTERN.fullmatch(epoch_text):
            raise ExpandedResearchReceiverError("expanded research identity headers are invalid", 401)
        try:
            epoch = int(epoch_text)
            sent = parse_timestamp(sent_at, "sent_at")
            expected = receiver_signature(
                self._secret, worker_id=worker_id, fencing_epoch=epoch, idempotency_key=idempotency_key,
                sent_at=sent_at, body_sha256=body_sha,
            )
        except ExpandedResearchError as exc:
            raise ExpandedResearchReceiverError("expanded research identity headers are invalid", 401) from exc
        now = self._now()
        if abs((now - sent).total_seconds()) > self.freshness_seconds:
            raise ExpandedResearchReceiverError("expanded research signature has expired", 401)
        actual_sha = sha256_bytes(raw)
        if not hmac.compare_digest(actual_sha, body_sha):
            raise ExpandedResearchReceiverError("expanded research body hash does not match", 409)
        if not supplied or not hmac.compare_digest(expected, supplied):
            raise ExpandedResearchReceiverError("expanded research signature is invalid", 401)
        return {"worker_id": worker_id, "fencing_epoch": epoch, "idempotency_key": idempotency_key, "body_sha256": actual_sha}

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExpandedResearchReceiverError("expanded research receiver clock is invalid", 503)
        return value.astimezone(UTC)


def build_expanded_research_receiver(env: Mapping[str, str] | None = None) -> ExpandedResearchReceiver | None:
    values = os.environ if env is None else env
    if not _boolean(values.get("TRADEAI_EXPANDED_RESEARCH_RECEIVER_ENABLED", "false")):
        return None
    database_path = Path(values.get("TRADEAI_EXPANDED_RESEARCH_RECEIVER_DATABASE", "")).expanduser()
    if not database_path.is_absolute():
        raise RuntimeError("TRADEAI_EXPANDED_RESEARCH_RECEIVER_DATABASE must be absolute")
    database_path = database_path.resolve()
    for name in ("DATABASE_URL", "TRADEAI_BACKTEST_DATABASE_URL", "TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE", "TRADEAI_COMPUTE_EVIDENCE_RECEIVER_DATABASE"):
        protected = _sqlite_path(values.get(name, ""))
        if protected is not None and protected == database_path:
            raise RuntimeError(f"expanded research receiver database must be isolated from {name}")
    return ExpandedResearchReceiver(
        ExpandedResearchStore(BacktestQueueDatabase(database_path)),
        shared_secret=values.get("TRADEAI_EXPANDED_RESEARCH_SHARED_SECRET", ""),
        enabled=True,
        freshness_seconds=int(values.get("TRADEAI_EXPANDED_RESEARCH_SIGNATURE_FRESHNESS_SECONDS", "300")),
    )


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ExpandedResearchError) as exc:
        raise ExpandedResearchError("expanded research body is invalid JSON") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ExpandedResearchError("expanded research JSON contains duplicate keys")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ExpandedResearchError(f"non-finite JSON constant {value} is forbidden")


def _headers(value: Mapping[str, str]) -> dict[str, str]:
    return {str(key).strip().lower(): str(item).strip() for key, item in value.items()}


def _boolean(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError("expanded research receiver enabled flag is invalid")


def _sqlite_path(value: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("sqlite:///"):
        raw = raw[10:]
    elif "://" in raw:
        return None
    return Path(raw).expanduser().resolve()


async def expanded_research_result(request: Request) -> Response:
    receiver = getattr(request.app.state, "expanded_research_receiver", None)
    if not isinstance(receiver, ExpandedResearchReceiver):
        raise ExpandedResearchReceiverError("expanded research receiver is unavailable", 404)
    raw = await _limited_body(request, MAX_RESULT_BYTES)
    result = await run_in_threadpool(receiver.accept, raw, request.headers)
    return JSONResponse(result, status_code=201 if result["created"] else 200, headers=_response_headers())


async def expanded_research_receiver_error(_: Request, exc: ExpandedResearchReceiverError) -> Response:
    return JSONResponse({"error": str(exc)}, status_code=exc.status, headers=_response_headers())


async def _limited_body(request: Request, maximum: int) -> bytes:
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > maximum):
        raise ExpandedResearchReceiverError("expanded research body is too large", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise ExpandedResearchReceiverError("expanded research body is too large", 413)
    if not body:
        raise ExpandedResearchReceiverError("expanded research body is empty", 400)
    return bytes(body)


def _response_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
