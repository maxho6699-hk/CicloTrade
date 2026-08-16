"""Authenticated HTTP boundary for Account Center and the canonical inbox."""

from __future__ import annotations

import json
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.account_center import (
    AccountCenterError,
    AccountCenterNotFound,
    AccountCenterService,
    IdempotencyConflict,
)


class AccountCenterApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _response(value: Any, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        value,
        status_code=status,
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


async def _body(request: Request, *, limit: int = 16_384) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > limit:
        raise AccountCenterApiError("请求内容过大。", 413)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AccountCenterApiError("请求必须是 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise AccountCenterApiError("请求必须是 JSON 对象。")
    return value


def _idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key", "").strip()
    if not 8 <= len(value) <= 128:
        raise AccountCenterApiError("缺少有效的 Idempotency-Key。")
    return value


def _service(request: Request) -> AccountCenterService:
    factory: Callable[[Request], AccountCenterService] | None = getattr(
        request.app.state, "account_center_service_factory", None
    )
    if factory is None:
        raise AccountCenterApiError("账户中心尚未接入。", 503)
    service = factory(request)
    if not isinstance(service, AccountCenterService):
        raise AccountCenterApiError("账户中心配置无效。", 503)
    return service


def _owner_id(request: Request) -> int:
    authenticate = getattr(request.app.state, "account_center_authenticate", None)
    if not callable(authenticate):
        raise AccountCenterApiError("账户身份服务尚未接入。", 503)
    identity = authenticate(request)
    owner_id = getattr(identity, "id", None)
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        raise AccountCenterApiError("账户身份无效。", 401)
    return owner_id


async def _invoke(callable_: Callable[..., Any], *args: Any, status: int = 200, **kwargs: Any) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(callable_, *args, **kwargs), status=status)
    except AccountCenterNotFound as exc:
        raise AccountCenterApiError(str(exc), 404) from exc
    except IdempotencyConflict as exc:
        raise AccountCenterApiError(str(exc), 409) from exc
    except AccountCenterError as exc:
        raise AccountCenterApiError(str(exc), 400) from exc


async def account_overview(request: Request) -> JSONResponse:
    return await _invoke(_service(request).account_overview, _owner_id(request))


async def account_appearances(request: Request) -> JSONResponse:
    items = await run_in_threadpool(_service(request).list_appearances, _owner_id(request))
    return _response({"items": items})


async def account_appearance(request: Request) -> JSONResponse:
    return await _invoke(_service(request).current_appearance, _owner_id(request))


async def account_appearance_select(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"manifest_public_id"} or not isinstance(payload["manifest_public_id"], str):
        raise AccountCenterApiError("外观选择字段无效。")
    return await _invoke(
        _service(request).select_appearance,
        _owner_id(request),
        payload["manifest_public_id"],
        _idempotency(request),
    )


async def account_content(request: Request) -> JSONResponse:
    items = await run_in_threadpool(_service(request).list_content, _owner_id(request))
    return _response({"items": items})


async def account_memory(request: Request) -> JSONResponse:
    service, owner_id = _service(request), _owner_id(request)
    if request.method == "GET":
        return _response({"items": await run_in_threadpool(service.list_memories, owner_id)})
    payload = await _body(request)
    if set(payload) - {"memory_key", "value", "expires_at"} or not {"memory_key", "value"} <= set(payload):
        raise AccountCenterApiError("记忆字段无效。")
    return await _invoke(
        service.put_memory,
        owner_id,
        payload["memory_key"],
        payload["value"],
        _idempotency(request),
        payload.get("expires_at"),
        status=201,
    )


async def account_memory_delete(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"reason"} or not isinstance(payload["reason"], str):
        raise AccountCenterApiError("记忆删除字段无效。")
    return await _invoke(
        _service(request).tombstone_memory,
        _owner_id(request),
        request.path_params["memory_public_id"],
        payload["reason"],
        _idempotency(request),
    )


async def account_authorization_status(request: Request) -> JSONResponse:
    return await _invoke(
        _service(request).authorization_status,
        _owner_id(request),
        request.path_params["data_kind"],
    )


async def account_authorization(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"data_kind", "scope", "action"}:
        raise AccountCenterApiError("数据授权字段无效。")
    return await _invoke(
        _service(request).authorize_data,
        _owner_id(request),
        payload["data_kind"],
        payload["scope"],
        payload["action"],
        _idempotency(request),
        status=201,
    )


async def notifications(request: Request) -> JSONResponse:
    items = await run_in_threadpool(_service(request).list_notifications, _owner_id(request))
    return _response({"items": items})


async def notification_read(request: Request) -> JSONResponse:
    return await _invoke(
        _service(request).mark_read,
        _owner_id(request),
        request.path_params["item_public_id"],
        _idempotency(request),
    )


async def notification_resolve(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"notification_public_id"} or not isinstance(payload["notification_public_id"], str):
        raise AccountCenterApiError("通知 deep link 字段无效。")
    return await _invoke(
        _service(request).resolve_notification,
        _owner_id(request),
        payload["notification_public_id"],
    )


ACCOUNT_CENTER_ROUTES = [
    Route("/api/rewrite/v1/account", account_overview, methods=["GET"]),
    Route("/api/rewrite/v1/account/appearances", account_appearances, methods=["GET"]),
    Route("/api/rewrite/v1/account/appearance", account_appearance, methods=["GET"]),
    Route("/api/rewrite/v1/account/appearance/select", account_appearance_select, methods=["POST"]),
    Route("/api/rewrite/v1/account/content", account_content, methods=["GET"]),
    Route("/api/rewrite/v1/account/memory", account_memory, methods=["GET", "POST"]),
    Route("/api/rewrite/v1/account/memory/{memory_public_id:str}/delete", account_memory_delete, methods=["POST"]),
    Route("/api/rewrite/v1/account/authorizations/{data_kind:str}", account_authorization_status, methods=["GET"]),
    Route("/api/rewrite/v1/account/authorizations", account_authorization, methods=["POST"]),
    Route("/api/rewrite/v1/notifications", notifications, methods=["GET"]),
    Route("/api/rewrite/v1/notifications/{item_public_id:str}/read", notification_read, methods=["POST"]),
    Route("/api/rewrite/v1/notifications/resolve", notification_resolve, methods=["POST"]),
]


async def account_center_error_handler(_: Request, exc: AccountCenterApiError) -> JSONResponse:
    return JSONResponse(
        {"code": "account_center_error", "error": str(exc)},
        status_code=exc.status,
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


__all__ = ["ACCOUNT_CENTER_ROUTES", "AccountCenterApiError", "account_center_error_handler"]
