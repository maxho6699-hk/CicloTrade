"""Authenticated HTTP adapter for the owner-scoped CSV signal portal."""

from __future__ import annotations

from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from core.signal_import_portal import (
    SignalImportPortalError,
    SignalImportPortalService,
)


class SignalImportsApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _response(value: Any, *, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "private, no-store", "Vary": "Authorization"})


def _owner_id(request: Request) -> int:
    authenticate = getattr(request.app.state, "signal_import_authenticate", None)
    if not callable(authenticate):
        authenticate = getattr(request.app.state, "authenticate", None)
    if not callable(authenticate):
        raise SignalImportsApiError("CSV 导入身份服务尚未接入。", 503)
    try:
        identity = authenticate(request)
    except Exception as exc:
        raise SignalImportsApiError("Bearer 身份验证失败。", 401) from exc
    owner_id = getattr(identity, "id", None)
    if owner_id is None and isinstance(identity, dict):
        owner_id = identity.get("id")
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        raise SignalImportsApiError("账户身份无效。", 401)
    return owner_id


def _service(request: Request) -> SignalImportPortalService:
    factory: Callable[[Request], SignalImportPortalService] | None = getattr(request.app.state, "signal_import_service_factory", None)
    if not callable(factory):
        factory = getattr(request.app.state, "service_factory", None)
    if not callable(factory):
        raise SignalImportsApiError("CSV 导入服务尚未接入。", 503)
    service = factory(request)
    if not isinstance(service, SignalImportPortalService):
        raise SignalImportsApiError("CSV 导入服务配置无效。", 503)
    return service


def _idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key", "").strip()
    if not 8 <= len(value) <= 128:
        raise SignalImportsApiError("缺少有效的 Idempotency-Key。")
    return value


def _query_limit(request: Request, default: int = 50) -> int:
    raw = request.query_params.get("limit", str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SignalImportsApiError("查询数量无效。") from exc
    if value < 1 or value > 100:
        raise SignalImportsApiError("查询数量必须介于 1 与 100。")
    return value


async def _invoke(callable_: Callable[..., Any], *args: Any, status: int = 200, **kwargs: Any) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(callable_, *args, **kwargs), status=status)
    except SignalImportPortalError as exc:
        raise SignalImportsApiError(str(exc), getattr(exc, "status_code", 400)) from exc


async def _call(callable_: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return await run_in_threadpool(callable_, *args, **kwargs)
    except SignalImportPortalError as exc:
        raise SignalImportsApiError(str(exc), getattr(exc, "status_code", 400)) from exc


async def _upload(request: Request) -> tuple[bytes, str]:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception as exc:
            raise SignalImportsApiError("无法读取 CSV 上传内容。", 415) from exc
        upload = form.get("file") or form.get("csv")
        if not isinstance(upload, UploadFile):
            raise SignalImportsApiError("请上传名为 file 的 CSV 文件。")
        filename = upload.filename or ""
        content = await upload.read(262_145)
        await upload.close()
        return content, filename
    if not content_type.startswith("text/csv"):
        raise SignalImportsApiError("CSV 上传必须使用 multipart/form-data 或 text/csv。", 415)
    filename = request.headers.get("x-filename", "").strip()
    if not filename:
        raise SignalImportsApiError("text/csv 上传必须提供 UTF-8 X-Filename。")
    content = await request.body()
    if len(content) > 262_144:
        raise SignalImportsApiError("CSV 文件必须介于 1 byte 与 256 KB。", 413)
    return content, filename


async def signal_import_readiness(request: Request) -> JSONResponse:
    return await _invoke(_service(request).readiness, _owner_id(request))


async def signal_import_jobs(request: Request) -> Response:
    service, owner_id = _service(request), _owner_id(request)
    if request.method == "GET":
        return _response({"items": await _call(service.list_jobs, owner_id, limit=_query_limit(request))})
    content, filename = await _upload(request)
    result = await _call(service.import_csv, owner_id, content, filename, _idempotency(request), request_sha256=None)
    return _response(result, status=200 if result.get("replayed") else 201)


async def signal_import_job(request: Request) -> JSONResponse:
    return await _invoke(_service(request).get_job, _owner_id(request), request.path_params["job_public_id"])


async def signal_import_job_signals(request: Request) -> JSONResponse:
    return _response({"items": await _call(_service(request).list_signals, _owner_id(request), request.path_params["job_public_id"], limit=_query_limit(request, 500))})


async def signal_import_export(request: Request) -> Response:
    content = await _call(_service(request).export_csv, _owner_id(request), request.path_params.get("job_public_id"))
    suffix = request.path_params.get("job_public_id") or "all"
    return Response(content, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="ciclo-signal-import-{suffix}.csv"', "Cache-Control": "private, no-store", "Vary": "Authorization", "X-Content-Type-Options": "nosniff"})


SIGNAL_IMPORT_ROUTES = [
    Route("/api/rewrite/v1/signal-imports/readiness", signal_import_readiness, methods=["GET"]),
    Route("/api/rewrite/v1/signal-imports", signal_import_jobs, methods=["GET", "POST"]),
    Route("/api/rewrite/v1/signal-imports/export.csv", signal_import_export, methods=["GET"]),
    Route("/api/rewrite/v1/signal-imports/{job_public_id:str}", signal_import_job, methods=["GET"]),
    Route("/api/rewrite/v1/signal-imports/{job_public_id:str}/signals", signal_import_job_signals, methods=["GET"]),
    Route("/api/rewrite/v1/signal-imports/{job_public_id:str}/export.csv", signal_import_export, methods=["GET"]),
]


async def signal_import_error_handler(_: Request, exc: SignalImportsApiError) -> JSONResponse:
    return _response({"code": "signal_import_error", "error": str(exc)}, status=exc.status)


async def signal_import_portal_error_handler(_: Request, exc: SignalImportPortalError) -> JSONResponse:
    return _response({"code": "signal_import_error", "error": str(exc)}, status=getattr(exc, "status_code", 400))


__all__ = ["SIGNAL_IMPORT_ROUTES", "SignalImportsApiError", "signal_import_error_handler", "signal_import_portal_error_handler"]
