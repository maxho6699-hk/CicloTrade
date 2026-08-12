"""Starlette adapters for compute evidence acceptance and sanitized admin reads."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.compute_evidence_contracts import RECEIVER_HTTP_PATH
from src.apps.api.compute_evidence_read_model import ComputeEvidenceReadModel
from src.apps.api.compute_evidence_receiver import (
    MAX_PACKAGE_BYTES,
    ComputeEvidenceReceiver,
    ComputeEvidenceReceiverError,
)


INTERNAL_PATH = RECEIVER_HTTP_PATH
ADMIN_STATUS_PATH = "/api/rewrite/v1/admin/compute-evidence/status"
ADMIN_LATEST_PATH = "/api/rewrite/v1/admin/compute-evidence/latest"
ADMIN_HISTORY_PATH = "/api/rewrite/v1/admin/compute-evidence/history"
HTTP_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
ADMIN_HTTP_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Authorization, Cookie",
    "X-Content-Type-Options": "nosniff",
}
_PUBLIC_EVIDENCE_FIELDS = (
    "publication_state",
    "received_at",
    "completed_at",
    "candidate_id",
    "candidate_version",
    "market",
    "instrument_family",
    "symbols",
    "candidate_status",
    "manifest_sha256",
    "result_sha256",
    "package_sha256",
    "artifact_count",
)


class ComputeEvidenceHttpError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


async def compute_evidence_accept(request: Request) -> JSONResponse:
    receiver = _receiver(request)
    raw = await _limited_body(request, MAX_PACKAGE_BYTES)
    receipt = await run_in_threadpool(receiver.accept, raw, request.headers)
    return JSONResponse(
        receipt,
        status_code=201 if receipt["created"] else 200,
        headers=HTTP_HEADERS,
    )


async def compute_evidence_status(request: Request) -> JSONResponse:
    model = _read_model(request)
    if model is None:
        payload = _unavailable_status()
    else:
        raw = await run_in_threadpool(model.status)
        payload = {
            "available": bool(raw.get("available")),
            "publication_ceiling": "shadow",
            "research_only": True,
            "actionable": False,
            "user_visible": False,
            "counts": {
                "quarantine": int(raw.get("counts", {}).get("quarantine", 0)),
                "shadow": int(raw.get("counts", {}).get("shadow", 0)),
            },
            "last_received_at": raw.get("last_received_at"),
        }
    return JSONResponse(payload, headers=ADMIN_HTTP_HEADERS)


async def compute_evidence_latest(request: Request) -> JSONResponse:
    model = _read_model(request)
    evidence = None
    if model is not None:
        raw = await run_in_threadpool(model.latest)
        evidence = _public_evidence(raw.get("evidence"))
    return JSONResponse(
        {
            **_authority(bool(evidence)),
            "evidence": evidence,
        },
        headers=ADMIN_HTTP_HEADERS,
    )


async def compute_evidence_history(request: Request) -> JSONResponse:
    limit = _history_limit(request)
    model = _read_model(request)
    items = []
    if model is not None:
        raw = await run_in_threadpool(model.history, limit)
        items = [item for value in raw.get("items", []) if (item := _public_evidence(value)) is not None]
    return JSONResponse(
        {
            **_authority(bool(items)),
            "limit": limit,
            "items": items,
        },
        headers=ADMIN_HTTP_HEADERS,
    )


async def compute_evidence_error_handler(
    request: Request,
    exc: ComputeEvidenceHttpError | ComputeEvidenceReceiverError,
) -> Response:
    return JSONResponse(
        {"error": str(exc)},
        status_code=exc.status,
        headers=ADMIN_HTTP_HEADERS if request.url.path.startswith("/api/rewrite/v1/admin/") else HTTP_HEADERS,
    )


def _receiver(request: Request) -> ComputeEvidenceReceiver:
    value = getattr(request.app.state, "compute_evidence_receiver", None)
    if not isinstance(value, ComputeEvidenceReceiver) or not value.enabled:
        raise ComputeEvidenceHttpError("compute evidence receiver is unavailable", 404)
    return value


def _read_model(request: Request) -> ComputeEvidenceReadModel | None:
    value = getattr(request.app.state, "compute_evidence_receiver", None)
    if not isinstance(value, ComputeEvidenceReceiver) or not value.enabled:
        return None
    return ComputeEvidenceReadModel(value.database)


def _history_limit(request: Request) -> int:
    raw = request.query_params.get("limit", "20")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ComputeEvidenceHttpError("compute evidence history limit must be between 1 and 100", 400) from exc
    if not 1 <= value <= 100:
        raise ComputeEvidenceHttpError("compute evidence history limit must be between 1 and 100", 400)
    return value


def _authority(available: bool) -> dict[str, object]:
    return {
        "available": available,
        "publication_ceiling": "shadow",
        "research_only": True,
        "actionable": False,
        "user_visible": False,
    }


def _unavailable_status() -> dict[str, object]:
    return {
        **_authority(False),
        "counts": {"quarantine": 0, "shadow": 0},
        "last_received_at": None,
    }


def _public_evidence(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or any(field not in value for field in _PUBLIC_EVIDENCE_FIELDS):
        return None
    return {
        **{field: value[field] for field in _PUBLIC_EVIDENCE_FIELDS},
        "research_only": True,
        "actionable": False,
        "user_visible": False,
    }


async def _limited_body(request: Request, maximum: int) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            declared = int(length)
        except ValueError as exc:
            raise ComputeEvidenceHttpError("compute evidence content length is invalid", 400) from exc
        if declared < 0 or declared > maximum:
            raise ComputeEvidenceHttpError("compute evidence body is too large", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise ComputeEvidenceHttpError("compute evidence body is too large", 413)
    if not body:
        raise ComputeEvidenceHttpError("compute evidence body is empty", 400)
    return bytes(body)
