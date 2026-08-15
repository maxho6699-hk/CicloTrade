"""Opt-in browser and M2M endpoints for canonical research backtest jobs."""
from __future__ import annotations

import hmac
import os
import re
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.backtest_artifacts import ArtifactError
from core.backtest_queue import BacktestQueue, BacktestQueueError
from src.apps.api.backtest_preparation import BacktestPreparationService


_WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _queue(request: Request) -> BacktestQueue:
    queue = getattr(request.app.state, "backtest_queue", None)
    if queue is not None:
        return queue
    return BacktestQueue()


def _identity(request: Request):
    from src.apps.api.app import _identity as browser_identity
    return browser_identity(request)


async def _body(request: Request, maximum: int = 65_536) -> dict[str, Any]:
    from src.apps.api.app import _json_body
    return await _json_body(request, maximum)


def _gate(name: str) -> None:
    if not _enabled(name):
        raise BacktestQueueError("回测队列功能尚未启用。", 404)


def _worker(request: Request, *, claims: bool = False) -> str:
    _gate("TRADEAI_BACKTEST_QUEUE_ENABLED")
    _gate("TRADEAI_BACKTEST_WORKER_API_ENABLED")
    if claims:
        _gate("TRADEAI_BACKTEST_CLAIMS_ENABLED")
    expected = os.getenv("TRADEAI_BACKTEST_WORKER_TOKEN", "")
    expected_worker_id = os.getenv("TRADEAI_BACKTEST_WORKER_ID", "").strip()
    authorization = request.headers.get("authorization", "")
    if len(expected) < 32 or len(expected) > 512 or not _WORKER_ID.fullmatch(expected_worker_id) or len(authorization) > 600 or not authorization.startswith("Bearer "):
        raise BacktestQueueError("Worker 身份验证失败。", 401)
    token = authorization.removeprefix("Bearer ")
    worker_id = request.headers.get("x-ciclotrade-worker-id", "").strip()
    if not token or len(token) > 512 or not _WORKER_ID.fullmatch(worker_id) or not hmac.compare_digest(expected, token) or not hmac.compare_digest(expected_worker_id, worker_id):
        raise BacktestQueueError("Worker 身份验证失败。", 401)
    return worker_id


def _lease(request: Request) -> tuple[str, int]:
    token = request.headers.get("x-ciclotrade-lease-token", "")
    raw_epoch = request.headers.get("x-ciclotrade-fencing-epoch", "")
    if not token or len(token) > 256 or not re.fullmatch(r"[1-9][0-9]{0,18}", raw_epoch):
        raise BacktestQueueError("缺少有效租约。", 409)
    return token, int(raw_epoch)


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    allowed = ("id", "job_type", "status", "manifest", "result", "attempt_count", "max_attempts", "progress", "progress_stage", "cancel_requested", "created_at", "updated_at", "completed_at")
    return {key: job.get(key) for key in allowed if key in job}


def _browser_job_view(
    queue: BacktestQueue, job: dict[str, Any], owner_id: int
) -> dict[str, Any]:
    # Re-read once before projection. A running job may have completed after a
    # list query; returning the older running snapshot without terminal fields
    # is safe, while mixing it with newly completed artifacts is not.
    current = queue.get(job["id"], owner_id)
    return {
        **_job_view(current),
        "artifacts": queue.owner_output_metadata(current["id"], owner_id)
        if current["status"] == "completed"
        else [],
        "failure": queue.owner_failure(current["id"], owner_id)
        if current["status"] == "failed"
        else None,
    }


def _preparation(request: Request, queue: BacktestQueue) -> BacktestPreparationService:
    service = getattr(request.app.state, "backtest_preparation", None)
    return service if service is not None else BacktestPreparationService(queue)


def _worker_job_view(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if job is None:
        return None
    allowed = (
        "id", "job_type", "manifest", "manifest_sha256", "attempt_count", "fencing_epoch",
        "lease_token", "lease_expires_at", "attempt_deadline_at", "cancel_requested",
    )
    return {key: job.get(key) for key in allowed}


def _artifact_view(artifact: dict[str, Any]) -> dict[str, Any]:
    allowed = ("artifact_key", "sha256", "bytes", "row_count", "media_type", "attempt_no")
    return {key: artifact.get(key) for key in allowed if key in artifact}


async def backtests(request: Request) -> Response:
    _gate("TRADEAI_BACKTEST_QUEUE_ENABLED")
    identity = _identity(request)
    queue = _queue(request)
    if request.method == "GET":
        items = await run_in_threadpool(queue.list, identity.id)
        views = await run_in_threadpool(
            lambda: [_browser_job_view(queue, item, identity.id) for item in items]
        )
        return JSONResponse({"items": views})
    payload = await _body(request)
    key = request.headers.get("idempotency-key", "")
    job, created = await run_in_threadpool(
        _preparation(request, queue).prepare,
        identity.id,
        identity.effective_plan,
        payload,
        key,
    )
    view = await run_in_threadpool(_browser_job_view, queue, job, identity.id)
    return JSONResponse({"created": created, "job": view}, status_code=202)


async def backtest_item(request: Request) -> Response:
    _gate("TRADEAI_BACKTEST_QUEUE_ENABLED")
    identity = _identity(request)
    queue = _queue(request)
    job = await run_in_threadpool(queue.get, str(request.path_params["job_id"]), identity.id)
    return JSONResponse(await run_in_threadpool(_browser_job_view, queue, job, identity.id))


async def backtest_cancel(request: Request) -> Response:
    _gate("TRADEAI_BACKTEST_QUEUE_ENABLED")
    identity = _identity(request)
    queue = _queue(request)
    job = await run_in_threadpool(queue.cancel, str(request.path_params["job_id"]), identity.id)
    return JSONResponse(await run_in_threadpool(_browser_job_view, queue, job, identity.id))


async def backtest_artifact(request: Request) -> Response:
    _gate("TRADEAI_BACKTEST_QUEUE_ENABLED")
    identity = _identity(request)
    body, metadata = await run_in_threadpool(_queue(request).owner_artifact, str(request.path_params["job_id"]), str(request.path_params["artifact_key"]), identity.id)
    filename = str(metadata["artifact_key"])
    return Response(
        body,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(body)),
            "ETag": metadata["sha256"],
            "X-Content-Type-Options": "nosniff",
        },
    )


async def worker_claim(request: Request) -> Response:
    worker_id = _worker(request, claims=True)
    payload = await _body(request)
    if set(payload) - {"lease_seconds"}:
        raise BacktestQueueError("claim 请求包含未知字段。")
    job = await run_in_threadpool(_queue(request).claim, worker_id, payload.get("lease_seconds", 60))
    return JSONResponse({"job": _worker_job_view(job)})


async def worker_heartbeat(request: Request) -> Response:
    worker_id = _worker(request)
    token, epoch = _lease(request)
    payload = await _body(request)
    if set(payload) != {"progress", "stage"}:
        raise BacktestQueueError("heartbeat 请求字段无效。")
    value = await run_in_threadpool(_queue(request).heartbeat, str(request.path_params["job_id"]), worker_id, token, epoch, payload["progress"], payload["stage"])
    return JSONResponse(value)


async def worker_input(request: Request) -> Response:
    worker_id = _worker(request)
    token, epoch = _lease(request)
    body, metadata = await run_in_threadpool(_queue(request).input_artifact, str(request.path_params["job_id"]), str(request.path_params["artifact_key"]), worker_id, token, epoch)
    return Response(
        body,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "ETag": metadata["sha256"],
            "X-CicloTrade-Artifact-SHA256": metadata["sha256"],
            "X-CicloTrade-Artifact-Bytes": str(metadata["bytes"]),
        },
    )


def _output_metadata(request: Request) -> tuple[str, int | None, str]:
    sha = request.headers.get("x-ciclotrade-artifact-sha256", "")
    if not _SHA.fullmatch(sha):
        raise BacktestQueueError("artifact SHA-256 无效。")
    raw_rows = request.headers.get("x-ciclotrade-artifact-row-count")
    if raw_rows is None:
        rows = None
    elif not re.fullmatch(r"[0-9]{1,12}", raw_rows):
        raise BacktestQueueError("artifact row count 无效。")
    else:
        rows = int(raw_rows)
    media_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip()
    if len(media_type) > 128 or not _MEDIA_TYPE.fullmatch(media_type):
        raise BacktestQueueError("artifact media type 无效。")
    return sha, rows, media_type


async def worker_output(request: Request) -> Response:
    worker_id = _worker(request)
    token, epoch = _lease(request)
    queue = _queue(request)
    job_id = str(request.path_params["job_id"])
    artifact_key = str(request.path_params["artifact_key"])
    sha, rows, media_type = _output_metadata(request)
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > queue.artifacts.max_bytes):
        raise BacktestQueueError("artifact 超过大小限制。", 413)
    await run_in_threadpool(queue.verify_output_lease, job_id, artifact_key, worker_id, token, epoch)
    fd, temporary = queue.artifacts.create_temp()
    total = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > queue.artifacts.max_bytes:
                    raise BacktestQueueError("artifact 超过大小限制。", 413)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        artifact = await run_in_threadpool(queue.upload_output_temp, job_id, artifact_key, temporary, sha, worker_id, token, epoch, rows, media_type)
        return JSONResponse(_artifact_view(artifact), status_code=201)
    except BacktestQueueError:
        raise
    except ArtifactError as exc:
        raise BacktestQueueError(str(exc), 409) from exc
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


async def worker_complete(request: Request) -> Response:
    worker_id = _worker(request)
    token, epoch = _lease(request)
    payload = await _body(request)
    if set(payload) != {"result"} or not isinstance(payload["result"], dict):
        raise BacktestQueueError("complete 请求字段无效。")
    job = await run_in_threadpool(_queue(request).complete, str(request.path_params["job_id"]), worker_id, token, epoch, payload["result"])
    return JSONResponse(_job_view(job))


async def worker_fail(request: Request) -> Response:
    worker_id = _worker(request)
    token, epoch = _lease(request)
    payload = await _body(request)
    job = await run_in_threadpool(_queue(request).fail, str(request.path_params["job_id"]), worker_id, token, epoch, payload)
    return JSONResponse(_job_view(job))
