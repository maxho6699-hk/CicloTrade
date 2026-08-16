"""Authenticated, owner-scoped HTTP adapter for the auto-live control plane."""

from __future__ import annotations

import json
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core.auto_live_control import AutoLiveConflict, AutoLiveControlError, AutoLiveControlPlane
from core.database import DatabaseManager


class AutoLiveApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _response(value: Any, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        value,
        status_code=status,
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


async def _body(request: Request, *, allow_empty: bool = False) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 16_384:
        raise AutoLiveApiError("请求内容过大。", 413)
    if not raw and allow_empty:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutoLiveApiError("请求必须是 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise AutoLiveApiError("请求必须是 JSON 对象。")
    return value


def _idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key", "").strip()
    if not 8 <= len(value) <= 128:
        raise AutoLiveApiError("缺少有效的 Idempotency-Key。")
    return value


def _owner_id(request: Request) -> int:
    authenticate = getattr(request.app.state, "auto_live_authenticate", None)
    if not callable(authenticate):
        raise AutoLiveApiError("自动实盘身份服务尚未接入。", 503)
    identity = authenticate(request)
    owner_id = getattr(identity, "id", None)
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        raise AutoLiveApiError("账户身份无效。", 401)
    return owner_id


def _service(request: Request) -> AutoLiveControlPlane:
    factory: Callable[[Request], AutoLiveControlPlane] | None = getattr(
        request.app.state, "auto_live_service_factory", None
    )
    if factory is None:
        raise AutoLiveApiError("自动实盘控制服务尚未接入。", 503)
    service = factory(request)
    if not isinstance(service, AutoLiveControlPlane):
        raise AutoLiveApiError("自动实盘控制服务配置无效。", 503)
    return service


async def _invoke(callable_: Callable[..., Any], *args: Any, status: int = 200, **kwargs: Any) -> JSONResponse:
    try:
        return _response(await run_in_threadpool(callable_, *args, **kwargs), status=status)
    except AutoLiveConflict as exc:
        raise AutoLiveApiError(str(exc), 409) from exc
    except AutoLiveControlError as exc:
        raise AutoLiveApiError(str(exc), exc.status_code) from exc


async def auto_live_snapshot(request: Request) -> JSONResponse:
    service, owner_id = _service(request), _owner_id(request)
    if isinstance(service.db, DatabaseManager):
        accounts = service.db.fetch_all(
            "SELECT id FROM broker_accounts WHERE user_id=? ORDER BY id", (owner_id,)
        )
        for account in accounts:
            await run_in_threadpool(service.broker_account_public_ref, owner_id, int(account["id"]))
    return await _invoke(service.list_snapshot, owner_id)


async def auto_live_create_mandate(request: Request) -> JSONResponse:
    return await _invoke(
        _service(request).create_mandate_from_public_ref,
        _owner_id(request),
        await _body(request),
        idempotency_key=_idempotency(request),
        status=201,
    )


async def auto_live_confirmation(request: Request) -> JSONResponse:
    payload = await _body(request, allow_empty=True)
    if payload:
        raise AutoLiveApiError("请求确认不接受额外字段。")
    return await _invoke(
        _service(request).submit_confirmation,
        _owner_id(request),
        request.path_params["mandate_public_id"],
    )


async def auto_live_confirm(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"snapshot_sha256", "confirmation_phrase"}:
        raise AutoLiveApiError("mandate 确认字段无效。")
    mandate_id = request.path_params["mandate_public_id"]
    return await _invoke(
        _service(request).confirm_mandate,
        _owner_id(request),
        mandate_id,
        {
            "mandate_public_id": mandate_id,
            "snapshot_sha256": payload["snapshot_sha256"],
            "confirmation_phrase": payload["confirmation_phrase"],
        },
    )


async def auto_live_start(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"expected_fencing_epoch"}:
        raise AutoLiveApiError("启动字段无效。")
    return await _invoke(
        _service(request).start_mandate,
        _owner_id(request),
        request.path_params["mandate_public_id"],
        expected_fencing_epoch=payload["expected_fencing_epoch"],
        idempotency_key=_idempotency(request),
    )


async def auto_live_resume(request: Request) -> JSONResponse:
    payload = await _body(request, allow_empty=True)
    if payload:
        raise AutoLiveApiError("恢复请求不接受额外字段。")
    return await _invoke(
        _service(request).resume_mandate,
        _owner_id(request),
        request.path_params["mandate_public_id"],
    )


async def auto_live_revoke(request: Request) -> JSONResponse:
    payload = await _body(request)
    if set(payload) != {"reason"}:
        raise AutoLiveApiError("撤销字段无效。")
    return await _invoke(
        _service(request).revoke_mandate,
        _owner_id(request),
        request.path_params["mandate_public_id"],
        reason=payload["reason"],
    )


async def auto_live_pause(request: Request) -> JSONResponse:
    payload = await _body(request)
    service, owner_id, key = _service(request), _owner_id(request), _idempotency(request)
    if payload == {"scope": "aggregate"}:
        return await _invoke(
            service.request_pause,
            owner_id,
            payload,
            idempotency_key=key,
        )
    if set(payload) == {"scope", "mandate_public_id"} and payload.get("scope") == "mandate":
        return await _invoke(
            service.request_pause,
            owner_id,
            payload,
            idempotency_key=key,
        )
    if set(payload) == {"scope", "broker_account_public_id"} and payload.get("scope") == "broker":
        return await _invoke(
            service.request_pause_public_ref,
            owner_id,
            payload["broker_account_public_id"],
            idempotency_key=key,
        )
    raise AutoLiveApiError("暂停范围字段无效。")


AUTO_LIVE_ROUTES = [
    Route("/api/rewrite/v1/auto-live", auto_live_snapshot, methods=["GET"]),
    Route("/api/rewrite/v1/auto-live/mandates", auto_live_create_mandate, methods=["POST"]),
    Route("/api/rewrite/v1/auto-live/mandates/{mandate_public_id:str}/confirmation", auto_live_confirmation, methods=["POST"]),
    Route("/api/rewrite/v1/auto-live/mandates/{mandate_public_id:str}/confirm", auto_live_confirm, methods=["POST"]),
    Route("/api/rewrite/v1/auto-live/mandates/{mandate_public_id:str}/start", auto_live_start, methods=["POST"]),
    Route("/api/rewrite/v1/auto-live/mandates/{mandate_public_id:str}/resume", auto_live_resume, methods=["POST"]),
    Route("/api/rewrite/v1/auto-live/mandates/{mandate_public_id:str}/revoke", auto_live_revoke, methods=["POST"]),
    Route("/api/rewrite/v1/auto-live/pause", auto_live_pause, methods=["POST"]),
]


async def auto_live_error_handler(_: Request, exc: AutoLiveApiError) -> JSONResponse:
    return JSONResponse(
        {"code": "auto_live_error", "error": str(exc)},
        status_code=exc.status,
        headers={"Cache-Control": "private, no-store", "Vary": "Authorization"},
    )


__all__ = ["AUTO_LIVE_ROUTES", "AutoLiveApiError", "auto_live_error_handler"]
