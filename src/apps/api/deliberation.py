"""Thin authenticated HTTP surface for four-seat evidence deliberation."""
from __future__ import annotations

import json
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.deliberation import DeliberationError, DeliberationService


class DeliberationApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _service(request: Request) -> DeliberationService:
    factory: Callable[[Request], DeliberationService] | None = getattr(request.app.state, "deliberation_service_factory", None)
    service = factory(request) if callable(factory) else getattr(request.app.state, "deliberation_service", None)
    if not isinstance(service, DeliberationService):
        raise DeliberationApiError("审议服务尚未接入。", 503)
    return service


def _owner(request: Request) -> int:
    authenticate = getattr(request.app.state, "deliberation_authenticate", None)
    if not callable(authenticate):
        raise DeliberationApiError("账户身份服务尚未接入。", 503)
    else:
        identity = authenticate(request)
    owner_id = getattr(identity, "id", None)
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        raise DeliberationApiError("账户身份无效。", 401)
    return owner_id


def _binding(request: Request, owner_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    loader = getattr(request.app.state, "deliberation_binding_loader", None)
    if not callable(loader):
        return payload
    value = loader(request, owner_id, dict(payload))
    if not isinstance(value, dict):
        raise DeliberationApiError("审议来源绑定服务返回无效数据。", 503)
    return value


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise DeliberationApiError("审议请求过大。", 413)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliberationApiError("审议请求必须是 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise DeliberationApiError("审议请求必须是 JSON 对象。")
    return value


def _response(value: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "private, no-store", "Vary": "Authorization"})


async def deliberation_readiness(request: Request) -> JSONResponse:
    payload = {key: request.query_params.get(key) for key in ("market", "symbol", "timeframe", "question", "source_event_id", "source_event_version", "source_event_sha256")}
    try:
        if payload["source_event_version"] is not None:
            payload["source_event_version"] = int(payload["source_event_version"])
        owner_id = _owner(request)
        payload = _binding(request, owner_id, {key: value for key, value in payload.items() if value is not None})
        return _response(await run_in_threadpool(_service(request).readiness, owner_id, payload))
    except DeliberationError as exc:
        raise DeliberationApiError(str(exc), getattr(exc, "status", 400)) from exc


async def deliberation_create(request: Request) -> JSONResponse:
    try:
        owner_id = _owner(request)
        payload = _binding(request, owner_id, await _body(request))
        result = await run_in_threadpool(_service(request).create, owner_id, payload)
        return _response(result, 202)
    except DeliberationError as exc:
        raise DeliberationApiError(str(exc), getattr(exc, "status", 400)) from exc


async def deliberation_list(request: Request) -> JSONResponse:
    try:
        limit = int(request.query_params.get("limit", "50"))
        return _response({"items": await run_in_threadpool(_service(request).list, _owner(request), limit=limit)})
    except (ValueError, DeliberationError) as exc:
        raise DeliberationApiError(str(exc), getattr(exc, "status", 400)) from exc


async def deliberation_item(request: Request) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(_service(request).get, _owner(request), str(request.path_params["deliberation_id"])))
    except DeliberationError as exc:
        raise DeliberationApiError(str(exc), getattr(exc, "status", 400)) from exc


async def deliberation_cancel(request: Request) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(_service(request).cancel, _owner(request), str(request.path_params["deliberation_id"])))
    except DeliberationError as exc:
        raise DeliberationApiError(str(exc), getattr(exc, "status", 400)) from exc


async def deliberation_retry(request: Request) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(_service(request).retry, _owner(request), str(request.path_params["deliberation_id"])), 202)
    except DeliberationError as exc:
        raise DeliberationApiError(str(exc), getattr(exc, "status", 400)) from exc


DELIBERATION_ROUTES = [
    Route("/api/rewrite/v1/deliberations/readiness", deliberation_readiness, methods=["GET"]),
    Route("/api/rewrite/v1/deliberations", deliberation_list, methods=["GET"]),
    Route("/api/rewrite/v1/deliberations", deliberation_create, methods=["POST"]),
    Route("/api/rewrite/v1/deliberations/{deliberation_id:str}", deliberation_item, methods=["GET"]),
    Route("/api/rewrite/v1/deliberations/{deliberation_id:str}/cancel", deliberation_cancel, methods=["POST"]),
    Route("/api/rewrite/v1/deliberations/{deliberation_id:str}/retry", deliberation_retry, methods=["POST"]),
]


async def deliberation_error_handler(_: Request, exc: DeliberationApiError) -> JSONResponse:
    return _response({"code": "deliberation_request_failed", "error": str(exc)}, exc.status)


__all__ = ["DELIBERATION_ROUTES", "DeliberationApiError", "DeliberationService", "deliberation_error_handler"]
