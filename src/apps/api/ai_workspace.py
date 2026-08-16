"""Authenticated HTTP adapter for the owner-scoped AI workspace."""

from __future__ import annotations

import json
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.ai_workspace import (
    AIWorkspaceError,
    AIWorkspaceService,
)


class AIWorkspaceApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _response(value: Any, *, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "private, no-store", "Vary": "Authorization"})


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 16_384:
        raise AIWorkspaceApiError("请求内容过大。", 413)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIWorkspaceApiError("请求必须是 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise AIWorkspaceApiError("请求必须是 JSON 对象。")
    return value


def _idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key", "").strip()
    if not 8 <= len(value) <= 128:
        raise AIWorkspaceApiError("缺少有效的 Idempotency-Key。")
    return value


def _owner_id(request: Request) -> int:
    authenticate = getattr(request.app.state, "ai_workspace_authenticate", None)
    if not callable(authenticate):
        authenticate = getattr(request.app.state, "authenticate", None)
    if not callable(authenticate):
        raise AIWorkspaceApiError("AI 工作台身份服务尚未接入。", 503)
    try:
        identity = authenticate(request)
    except Exception as exc:
        raise AIWorkspaceApiError("Bearer 身份验证失败。", 401) from exc
    owner_id = getattr(identity, "id", None)
    if owner_id is None and isinstance(identity, dict):
        owner_id = identity.get("id")
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        raise AIWorkspaceApiError("账户身份无效。", 401)
    authorize = getattr(request.app.state, "ai_workspace_authorize", None)
    if not callable(authorize):
        raise AIWorkspaceApiError("AI 工作台会员授权服务尚未接入。", 503)
    try:
        allowed = authorize(request, owner_id)
    except Exception as exc:
        raise AIWorkspaceApiError("AI 工作台会员授权校验失败。", 503) from exc
    if allowed is not True:
        raise AIWorkspaceApiError("当前会员未开放 AI 工作台。", 403)
    return owner_id


def _service(request: Request) -> AIWorkspaceService:
    factory: Callable[[Request], AIWorkspaceService] | None = getattr(request.app.state, "ai_workspace_service_factory", None)
    if not callable(factory):
        factory = getattr(request.app.state, "service_factory", None)
    if not callable(factory):
        raise AIWorkspaceApiError("AI 工作台服务尚未接入。", 503)
    service = factory(request)
    if not isinstance(service, AIWorkspaceService):
        raise AIWorkspaceApiError("AI 工作台服务配置无效。", 503)
    return service


async def _invoke(callable_: Callable[..., Any], *args: Any, status: int = 200, **kwargs: Any) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(callable_, *args, **kwargs), status=status)
    except AIWorkspaceError as exc:
        raise AIWorkspaceApiError(str(exc), exc.status_code) from exc


async def ai_readiness(request: Request) -> JSONResponse:
    _owner_id(request)
    return await _invoke(_service(request).readiness)


async def ai_sessions(request: Request) -> JSONResponse:
    service, owner_id = _service(request), _owner_id(request)
    if request.method == "GET":
        return _response({"items": await run_in_threadpool(service.list_sessions, owner_id)})
    payload = await _body(request)
    allowed = {"title", "route", "market", "symbol", "timeframe", "question"}
    if set(payload) - allowed:
        raise AIWorkspaceApiError("会话字段无效；引用与来源只能由服务端生成。")
    # route/market/symbol/timeframe/question are accepted as a selector only.
    # The integration owner injects a server context loader; raw citation fields
    # are intentionally rejected above and never reach the service.
    result = await _invoke(
        service.create_session,
        owner_id,
        title=payload.get("title", "新研究会话"),
        selectors={key: payload[key] for key in ("route", "market", "symbol", "timeframe", "question") if key in payload} or None,
        idempotency_key=_idempotency(request),
        status=201,
    )
    return result


async def ai_session(request: Request) -> JSONResponse:
    return await _invoke(_service(request).get_session, _owner_id(request), request.path_params["session_public_id"])


async def ai_session_archive(request: Request) -> JSONResponse:
    payload = await _body(request)
    if payload:
        raise AIWorkspaceApiError("归档请求不接受额外字段。")
    return await _invoke(_service(request).archive_session, _owner_id(request), request.path_params["session_public_id"], _idempotency(request))


async def ai_messages(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"content"} or not isinstance(payload["content"], str):
        raise AIWorkspaceApiError("消息字段无效。")
    result = await _invoke(_service(request).submit_message, _owner_id(request), request.path_params["session_public_id"], payload["content"], _idempotency(request))
    if json.loads(result.body).get("blocked"):
        result.status_code = 503
    return result


async def ai_task(request: Request) -> JSONResponse:
    return await _invoke(_service(request).get_task, _owner_id(request), request.path_params["task_public_id"])


async def ai_task_events(request: Request) -> JSONResponse:
    return _response({"items": await run_in_threadpool(_service(request).list_task_events, _owner_id(request), request.path_params["task_public_id"])})


async def ai_task_cancel(request: Request) -> JSONResponse:
    payload = await _body(request)
    if payload:
        raise AIWorkspaceApiError("取消请求不接受额外字段。")
    return await _invoke(_service(request).cancel_task, _owner_id(request), request.path_params["task_public_id"], _idempotency(request))


AI_WORKSPACE_ROUTES = [
    Route("/api/rewrite/v1/ai/workspace/readiness", ai_readiness, methods=["GET"]),
    Route("/api/rewrite/v1/ai/workspace/sessions", ai_sessions, methods=["GET", "POST"]),
    Route("/api/rewrite/v1/ai/workspace/sessions/{session_public_id:str}", ai_session, methods=["GET"]),
    Route("/api/rewrite/v1/ai/workspace/sessions/{session_public_id:str}/archive", ai_session_archive, methods=["POST"]),
    Route("/api/rewrite/v1/ai/workspace/sessions/{session_public_id:str}/messages", ai_messages, methods=["POST"]),
    Route("/api/rewrite/v1/ai/workspace/tasks/{task_public_id:str}", ai_task, methods=["GET"]),
    Route("/api/rewrite/v1/ai/workspace/tasks/{task_public_id:str}/events", ai_task_events, methods=["GET"]),
    Route("/api/rewrite/v1/ai/workspace/tasks/{task_public_id:str}/cancel", ai_task_cancel, methods=["POST"]),
]


async def ai_workspace_error_handler(_: Request, exc: AIWorkspaceApiError) -> JSONResponse:
    return JSONResponse({"code": "ai_workspace_error", "error": str(exc)}, status_code=exc.status, headers={"Cache-Control": "private, no-store", "Vary": "Authorization"})


__all__ = ["AI_WORKSPACE_ROUTES", "AIWorkspaceApiError", "ai_workspace_error_handler"]
