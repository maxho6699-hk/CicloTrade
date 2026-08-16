"""Public owner-scoped Workflow Registry HTTP surface."""
from __future__ import annotations

import json
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.workflow_registry import WorkflowError, WorkflowNotFound, WorkflowRegistry


class WorkflowApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _service(request: Request) -> WorkflowRegistry:
    factory: Callable[[Request], WorkflowRegistry] | None = getattr(request.app.state, "workflow_service_factory", None)
    service = factory(request) if callable(factory) else getattr(request.app.state, "workflow_service", None)
    if not isinstance(service, WorkflowRegistry):
        raise WorkflowApiError("Workflow 服务尚未接入。", 503)
    return service


def _owner(request: Request) -> int:
    authenticate = getattr(request.app.state, "workflow_authenticate", None)
    if not callable(authenticate):
        raise WorkflowApiError("账户身份服务尚未接入。", 503)
    else:
        identity = authenticate(request)
    owner_id = getattr(identity, "id", None)
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        raise WorkflowApiError("账户身份无效。", 401)
    return owner_id


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise WorkflowApiError("Workflow 请求过大。", 413)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowApiError("Workflow 请求必须是 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise WorkflowApiError("Workflow 请求必须是 JSON 对象。")
    return value


def _response(value: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "private, no-store", "Vary": "Authorization"})


async def workflow_create(request: Request) -> JSONResponse:
    try:
        if not getattr(request.app.state, "workflow_internal_enabled", False):
            raise WorkflowApiError("Workflow 创建仅允许受信服务调用。", 404)
        payload = await _body(request)
        if set(payload) - {"source_kind", "source_public_id", "context", "provenance"}:
            raise WorkflowApiError("Workflow 请求包含未知字段。")
        result = await run_in_threadpool(_service(request).create, _owner(request), source_kind=payload.get("source_kind", ""), source_public_id=payload.get("source_public_id", ""), context=payload.get("context"), provenance=payload.get("provenance"))
        return _response(result, 202)
    except WorkflowApiError:
        raise
    except WorkflowError as exc:
        raise WorkflowApiError(str(exc), 400) from exc


async def workflow_list(request: Request) -> JSONResponse:
    try:
        limit = int(request.query_params.get("limit", "50"))
        return _response({"items": await run_in_threadpool(_service(request).list, _owner(request), limit=limit)})
    except (ValueError, WorkflowError) as exc:
        raise WorkflowApiError(str(exc), 400) from exc


async def workflow_item(request: Request) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(_service(request).get, _owner(request), str(request.path_params["task_id"])))
    except WorkflowNotFound as exc:
        raise WorkflowApiError(str(exc), 404) from exc
    except WorkflowError as exc:
        raise WorkflowApiError(str(exc), 400) from exc


async def workflow_cancel(request: Request) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(_service(request).cancel, _owner(request), str(request.path_params["task_id"])))
    except WorkflowNotFound as exc:
        raise WorkflowApiError(str(exc), 404) from exc
    except WorkflowError as exc:
        raise WorkflowApiError(str(exc), 409) from exc


async def workflow_retry(request: Request) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(_service(request).retry, _owner(request), str(request.path_params["task_id"])), 202)
    except WorkflowNotFound as exc:
        raise WorkflowApiError(str(exc), 404) from exc
    except WorkflowError as exc:
        raise WorkflowApiError(str(exc), 409) from exc


WORKFLOW_ROUTES = [
    Route("/api/rewrite/v1/workflows", workflow_list, methods=["GET"]),
    Route("/api/rewrite/v1/workflows/{task_id:str}", workflow_item, methods=["GET"]),
    Route("/api/rewrite/v1/workflows/{task_id:str}/cancel", workflow_cancel, methods=["POST"]),
    Route("/api/rewrite/v1/workflows/{task_id:str}/retry", workflow_retry, methods=["POST"]),
]


async def workflow_error_handler(_: Request, exc: WorkflowApiError) -> JSONResponse:
    return _response({"code": "workflow_request_failed", "error": str(exc)}, exc.status)


__all__ = ["WORKFLOW_ROUTES", "WorkflowApiError", "WorkflowRegistry", "workflow_error_handler"]
