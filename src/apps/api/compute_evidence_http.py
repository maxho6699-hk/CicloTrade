"""Starlette adapter for quarantined compute evidence acceptance."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.apps.api.compute_evidence_receiver import (
    MAX_PACKAGE_BYTES,
    ComputeEvidenceReceiver,
    ComputeEvidenceReceiverError,
)


INTERNAL_PATH = "/api/rewrite/internal/v1/compute-evidence/equity-shadow"
HTTP_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


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


async def compute_evidence_error_handler(
    _request: Request,
    exc: ComputeEvidenceHttpError | ComputeEvidenceReceiverError,
) -> Response:
    return JSONResponse(
        {"error": str(exc)},
        status_code=exc.status,
        headers=HTTP_HEADERS,
    )


def _receiver(request: Request) -> ComputeEvidenceReceiver:
    value = getattr(request.app.state, "compute_evidence_receiver", None)
    if not isinstance(value, ComputeEvidenceReceiver) or not value.enabled:
        raise ComputeEvidenceHttpError("compute evidence receiver is unavailable", 404)
    return value


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
