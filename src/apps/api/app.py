"""Browser compatibility API for the rewritten TradeAI client."""

from __future__ import annotations

from pathlib import Path
import json
import os
import re
import threading
import time
from typing import Any

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.datastructures import UploadFile
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool

from src.apps.api.read_model import (
    BrowserIdentity,
    ReadModelAuthError,
    ReadModelError,
    ReadOnlyLegacyRepository,
)
from src.apps.api.write_service import BrowserWriteService
from core.auth import AuthError, AuthService
from core.plans import effective_plan, plan_display_name
from data.datasource import DataSourceError, get_resilient_data_source, public_market_status


load_dotenv(Path(__file__).resolve().parents[3] / ".env")


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class RewriteSecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        async def send_with_security_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                names = {name.lower() for name, _ in headers}
                additions = (
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                )
                headers.extend((name, value) for name, value in additions if name not in names)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class ProofBodyTooLarge(RuntimeError):
    pass


class PaymentProofBodyLimit:
    """Enforce a total upload limit while ASGI request chunks are still streaming."""

    def __init__(self, app, max_bytes: int = 5 * 1024 * 1024):
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope, receive, send) -> None:
        path = str(scope.get("path") or "")
        protected = (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and path.startswith("/api/rewrite/v1/membership/orders/")
            and path.endswith("/proof")
        )
        if not protected:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await JSONResponse({"error": "付款凭证请求过大。"}, status_code=413)(scope, receive, send)
                    return
            except ValueError:
                pass
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    raise ProofBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except ProofBodyTooLarge:
            await JSONResponse({"error": "付款凭证请求过大。"}, status_code=413)(scope, receive, send)


REFRESH_COOKIE = "tradeai_refresh"


def _auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    return service if service is not None else AuthService()


def _write_service(request: Request) -> BrowserWriteService:
    service = getattr(request.app.state, "write_service", None)
    return service if service is not None else BrowserWriteService()


async def _json_body(request: Request, limit: int = 16_384) -> dict[str, Any]:
    length = request.headers.get("content-length")
    try:
        if length and (int(length) < 0 or int(length) > limit):
            raise ApiError("请求内容过大。", 413)
    except ValueError as exc:
        raise ApiError("Content-Length 无效。") from exc
    body = await request.body()
    if len(body) > limit:
        raise ApiError("请求内容过大。", 413)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("请求内容必须是 JSON 对象。") from exc
    if not isinstance(payload, dict):
        raise ApiError("请求内容必须是 JSON 对象。")
    return payload


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max(1, int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))) * 86_400,
        httponly=True,
        secure=os.getenv("APP_ENV", "development").lower() == "production",
        samesite="strict",
        path="/api/rewrite/v1/session",
    )


def _session_user(user: dict[str, Any]) -> dict[str, Any]:
    plan = effective_plan(user)
    return {
        "id": user.get("id"),
        "display_name": user.get("display_name") or "TradeAI 用户",
        "plan": plan,
        "plan_display_name": plan_display_name(plan),
        "subscription_expire": user.get("subscription_expire"),
    }


def _repository(request: Request | None) -> ReadOnlyLegacyRepository:
    if request is not None and hasattr(request.app.state, "repository"):
        return request.app.state.repository
    return app.state.repository


def _identity(request: Request) -> BrowserIdentity:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 4096:
        raise ApiError("缺少 Bearer Access Token。", 401)
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return _repository(request).authenticate(token)
    except ReadModelAuthError as exc:
        raise ApiError(str(exc), 401) from exc


def _bounded_int(request: Request, name: str, default: int, maximum: int) -> int:
    value = request.query_params.get(name, str(default))
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(f"{name} 必须是整数。") from exc
    if not 1 <= parsed <= maximum:
        raise ApiError(f"{name} 必须介于 1 与 {maximum}。")
    return parsed


async def health(request: Request | None) -> JSONResponse:
    repository = _repository(request)
    return JSONResponse(
        {
            "status": "ok" if repository.db_path.is_file() else "degraded",
            "app": "tradeai-rewrite-api",
            "mode": "compatibility-protected-writes",
            "canonical_data": "legacy-sqlite",
            "database_available": repository.db_path.is_file(),
        }
    )


async def capabilities(_: Request | None) -> JSONResponse:
    return JSONResponse(
        {
            "markets": ["US", "CN"],
            "reserved_markets": ["HK", "CRYPTO"],
            "recommendations": {
                "official_actions": ["BUY", "ADD", "HOLD", "REDUCE", "EXIT", "WAIT"],
                "shadow_learning": True,
                "self_promotion": False,
                "mystic_isolated": True,
            },
            "compatibility_reads": [
                "identity", "membership", "recommendations", "quant_timeline",
                "quant_performance", "paper_portfolio", "telegram_status",
            ],
            "external_side_effects": {"payments": False, "telegram": False, "live_trading": False},
            "protected_writes": ["risk_settings", "telegram_preferences", "ui_locale", "watchlist", "price_alerts", "paper_orders", "pending_membership_orders"],
        }
    )


async def session_login(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    unknown = set(payload) - {"email", "password"}
    if unknown:
        raise ApiError("登录请求包含未知字段。")
    email, password = payload.get("email"), payload.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        raise ApiError("邮箱和密码不能为空。")
    client_ip = request.client.host if request.client else "unknown"
    try:
        result = _auth_service(request).login(
            email, password, client_ip, request.headers.get("user-agent", "browser")
        )
    except AuthError as exc:
        raise ApiError(str(exc), 401) from exc
    response = JSONResponse(
        {"access_token": result.access_token, "user": _session_user(result.user), "new_ip": result.new_ip}
    )
    _set_refresh_cookie(response, result.refresh_token)
    return response


async def session_refresh(request: Request) -> JSONResponse:
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        return JSONResponse({"authenticated": False})
    try:
        access_token, rotated_refresh = _auth_service(request).refresh(refresh_token)
    except AuthError as exc:
        raise ApiError(str(exc), 401) from exc
    response = JSONResponse({"authenticated": True, "access_token": access_token})
    _set_refresh_cookie(response, rotated_refresh)
    return response


async def session_logout(request: Request) -> JSONResponse:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        _auth_service(request).logout(authorization.removeprefix("Bearer ").strip())
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(REFRESH_COOKIE, path="/api/rewrite/v1/session")
    return response


async def me(request: Request) -> JSONResponse:
    identity = _identity(request)
    return JSONResponse(_repository(request).me(identity))


async def membership(request: Request) -> JSONResponse:
    identity = _identity(request)
    return JSONResponse(_repository(request).membership(identity))


async def telegram_status(request: Request) -> JSONResponse:
    identity = _identity(request)
    return JSONResponse(_repository(request).telegram_status(identity))


async def recommendations(request: Request) -> JSONResponse:
    identity = _identity(request)
    return JSONResponse(
        _repository(request).recommendations(
            identity, limit=_bounded_int(request, "limit", 20, 100)
        )
    )


async def quant_timeline(request: Request) -> JSONResponse:
    identity = _identity(request)
    cursor_value = request.query_params.get("cursor")
    cursor = None
    if cursor_value is not None:
        try:
            cursor = int(cursor_value)
        except ValueError as exc:
            raise ApiError("cursor 必须是整数。") from exc
        if cursor <= 0:
            raise ApiError("cursor 必须是正整数。")
    return JSONResponse(
        _repository(request).timeline(
            identity, limit=_bounded_int(request, "limit", 30, 100), cursor=cursor
        )
    )


async def quant_performance(request: Request) -> JSONResponse:
    identity = _identity(request)
    return JSONResponse(
        _repository(request).performance(
            identity, limit=_bounded_int(request, "limit", 200, 500)
        )
    )


async def portfolio(request: Request) -> JSONResponse:
    identity = _identity(request)
    return JSONResponse(_repository(request).portfolio(identity))


MARKET_TIMEFRAMES = {
    "1分": ("1d", "1m"),
    "5分": ("5d", "5m"),
    "15分": ("1mo", "15m"),
    "1小时": ("3mo", "60m"),
    "日线": ("6mo", "1d"),
}

_MARKET_SEARCH_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}
_MARKET_SEARCH_RATE: dict[int, list[float]] = {}
_MARKET_SEARCH_LOCK = threading.Lock()
_MARKET_SEARCH_TTL_SECONDS = 300
_MARKET_SEARCH_LIMIT_PER_MINUTE = 30


def _is_yahoo_source(source: Any) -> bool:
    """Recognize Yahoo adapters without depending on one display-name spelling."""
    return str(getattr(source, "name", "")).strip().casefold() in {"yahoo finance", "yfinance"}


def _prune_market_search_state_locked(now: float) -> None:
    """Discard expired cache and limiter entries while the search lock is held."""
    for key, (created_at, _) in list(_MARKET_SEARCH_CACHE.items()):
        if now - created_at >= _MARKET_SEARCH_TTL_SECONDS:
            _MARKET_SEARCH_CACHE.pop(key, None)
    for user_id, stamps in list(_MARKET_SEARCH_RATE.items()):
        active_stamps = [stamp for stamp in stamps if now - stamp < 60]
        if active_stamps:
            _MARKET_SEARCH_RATE[user_id] = active_stamps
        else:
            _MARKET_SEARCH_RATE.pop(user_id, None)


def _market_symbol(request: Request) -> str:
    symbol = request.query_params.get("symbol", "").strip().upper()
    if not re.fullmatch(r"(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})", symbol):
        raise ApiError("标的代码无效。")
    return symbol


async def market_candles(request: Request) -> JSONResponse:
    _identity(request)
    symbol = _market_symbol(request)
    timeframe = request.query_params.get("timeframe", "日线")
    if timeframe not in MARKET_TIMEFRAMES:
        raise ApiError("K线周期无效。")
    period, interval = MARKET_TIMEFRAMES[timeframe]
    market_name = "A股" if symbol.isdigit() else "美股"
    source = get_resilient_data_source("yfinance") if market_name == "A股" else get_resilient_data_source()
    try:
        frame = await run_in_threadpool(source.bars, symbol, period, interval)
    except DataSourceError as exc:
        raise ApiError(str(exc), 503) from exc
    items = []
    for index, row in frame.tail(600).iterrows():
        timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
        if not hasattr(timestamp, "timestamp"):
            continue
        time_value = int(timestamp.timestamp())
        items.append({
            "time": time_value,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    if not items:
        raise ApiError("行情服务没有返回可用 K 线。", 503)
    return JSONResponse({
        "symbol": symbol,
        "timeframe": timeframe,
        "items": items,
        "status": public_market_status(market=market_name),
    })


async def market_search(request: Request) -> JSONResponse:
    identity = _identity(request)
    query = request.query_params.get("q", "").strip()
    if not 1 <= len(query) <= 40:
        raise ApiError("搜索内容必须为 1 至 40 个字符。")
    market = request.query_params.get("market", "美股")
    if market not in {"美股", "A股", "全部"}:
        raise ApiError("市场范围无效。")
    now = time.monotonic()
    cache_key = (market, query.casefold())
    with _MARKET_SEARCH_LOCK:
        _prune_market_search_state_locked(now)
        recent = [stamp for stamp in _MARKET_SEARCH_RATE.get(identity.id, []) if now - stamp < 60]
        if len(recent) >= _MARKET_SEARCH_LIMIT_PER_MINUTE:
            raise ApiError("搜索过于频繁，请稍后再试。", 429)
        recent.append(now)
        _MARKET_SEARCH_RATE[identity.id] = recent
        cached = _MARKET_SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] < _MARKET_SEARCH_TTL_SECONDS:
        return JSONResponse({"items": cached[1], "market": market, "cached": True})

    def search_one(scope: str) -> list[dict[str, str]]:
        source = get_resilient_data_source("yfinance") if scope == "A股" else get_resilient_data_source()
        is_yahoo = _is_yahoo_source(source)
        try:
            results = source.search(query, scope, 8)
        except DataSourceError:
            if scope == "美股" and not is_yahoo:
                return get_resilient_data_source("yfinance").search(query, scope, 8)
            raise
        if scope == "美股" and not results and not is_yahoo:
            return get_resilient_data_source("yfinance").search(query, scope, 8)
        return results

    try:
        scopes = ("美股", "A股") if market == "全部" else (market,)
        raw_items: list[dict[str, str]] = []
        for scope in scopes:
            raw_items.extend(await run_in_threadpool(search_one, scope))
    except DataSourceError as exc:
        raise ApiError(str(exc), 503) from exc
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        symbol = str(item.get("symbol", "")).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        items.append({
            "symbol": symbol,
            "name": str(item.get("name") or symbol)[:120],
            "exchange": str(item.get("exchange") or "")[:80],
            "type": str(item.get("type") or "股票")[:40],
            "market": "CN" if re.fullmatch(r"\d{6}(?:\.(?:SS|SZ))?", symbol) else "US",
        })
        if len(items) >= 12:
            break
    with _MARKET_SEARCH_LOCK:
        _MARKET_SEARCH_CACHE[cache_key] = (now, items)
    return JSONResponse({"items": items, "market": market})


async def bootstrap(request: Request) -> JSONResponse:
    identity = _identity(request)
    repository = _repository(request)
    payload: dict[str, Any] = {
        "me": repository.me(identity),
        "membership": repository.membership(identity),
        "telegram": repository.telegram_status(identity),
        "portfolio": repository.portfolio(identity),
        "recommendations": repository.recommendations(identity, limit=20),
        "performance": repository.performance(identity, limit=200),
        "settings": repository.settings(identity),
        "alerts": {"items": repository.alerts(identity)},
        "market_data": public_market_status(market="美股"),
        "mode": "compatibility",
    }
    return JSONResponse(payload)


async def settings(request: Request) -> JSONResponse:
    identity = _identity(request)
    return JSONResponse(_write_service(request).settings(identity))


async def risk_settings(request: Request) -> JSONResponse:
    identity = _identity(request)
    try:
        value = _write_service(request).update_risk(identity, await _json_body(request))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"risk": value})


async def telegram_preferences(request: Request) -> JSONResponse:
    identity = _identity(request)
    try:
        value = _write_service(request).update_telegram_events(identity, await _json_body(request))
    except PermissionError as exc:
        raise ApiError(str(exc), 403) from exc
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"events": value})


async def watchlist(request: Request) -> JSONResponse:
    identity = _identity(request)
    service = _write_service(request)
    if request.method in {"GET", "HEAD"}:
        return JSONResponse({"watchlists": service.settings(identity)["watchlists"]})
    try:
        value = service.update_watchlist(
            identity, await _json_body(request), remove=request.method == "DELETE"
        )
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"watchlists": value})


async def locale_preference(request: Request) -> JSONResponse:
    identity = _identity(request)
    try:
        value = _write_service(request).update_locale(identity, await _json_body(request))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"locale": value})


async def alerts(request: Request) -> JSONResponse:
    identity = _identity(request)
    service = _write_service(request)
    if request.method == "GET":
        return JSONResponse({"items": service.list_alerts(identity)})
    try:
        items = service.create_alert(identity, await _json_body(request))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"items": items}, status_code=201)


async def paper_orders(request: Request) -> JSONResponse:
    identity = _identity(request)
    try:
        order = _write_service(request).create_paper_order(identity, await _json_body(request))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse(order, status_code=201)


async def membership_orders(request: Request) -> JSONResponse:
    identity = _identity(request)
    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if not 8 <= len(idempotency_key) <= 128:
        raise ApiError("会员订单必须提供 8 至 128 字符的幂等键。")
    try:
        order = _write_service(request).create_membership_order(
            identity, await _json_body(request), idempotency_key
        )
    except (PermissionError, ValueError) as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse(order, status_code=201)


async def membership_order_proof(request: Request) -> JSONResponse:
    identity = _identity(request)
    order_no = str(request.path_params.get("order_no", "")).strip()
    if not order_no or len(order_no) > 64:
        raise ApiError("订单编号无效。")
    content_length = request.headers.get("content-length")
    try:
        if content_length and (int(content_length) < 0 or int(content_length) > 5 * 1024 * 1024):
            raise ApiError("付款凭证请求过大。", 413)
    except ValueError as exc:
        raise ApiError("Content-Length 无效。") from exc
    try:
        async with request.form(max_files=1, max_fields=1, max_part_size=4 * 1024 * 1024 + 1) as form:
            upload = form.get("proof")
            if not isinstance(upload, UploadFile):
                raise ApiError("请上传付款凭证图片。")
            content = await upload.read(4 * 1024 * 1024 + 1)
            content_type = upload.content_type or ""
            await upload.close()
        claim = await run_in_threadpool(
            _write_service(request).submit_membership_proof,
            identity,
            order_no,
            content,
            content_type,
        )
    except ApiError:
        raise
    except (PermissionError, ValueError) as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse(claim, status_code=201)


async def membership_order_payment_qr(request: Request) -> Response:
    identity = _identity(request)
    order_no = str(request.path_params.get("order_no", "")).strip()
    if not order_no or len(order_no) > 64:
        raise ApiError("订单编号无效。")
    try:
        content = await run_in_threadpool(
            _write_service(request).membership_payment_qr,
            identity,
            order_no,
        )
    except (PermissionError, ValueError) as exc:
        raise ApiError(str(exc), 404 if isinstance(exc, PermissionError) else 409) from exc
    return Response(
        content,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'inline; filename="payment-qr.jpg"',
        },
    )


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


async def read_model_error_handler(_: Request, exc: ReadModelError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=503)


routes = [
    Route("/api/rewrite/health", health, methods=["GET"]),
    Route("/api/rewrite/v1/capabilities", capabilities, methods=["GET"]),
    Route("/api/rewrite/v1/session", session_login, methods=["POST"]),
    Route("/api/rewrite/v1/session/refresh", session_refresh, methods=["POST"]),
    Route("/api/rewrite/v1/session", session_logout, methods=["DELETE"]),
    Route("/api/rewrite/v1/me", me, methods=["GET"]),
    Route("/api/rewrite/v1/bootstrap", bootstrap, methods=["GET"]),
    Route("/api/rewrite/v1/recommendations", recommendations, methods=["GET"]),
    Route("/api/rewrite/v1/quant/timeline", quant_timeline, methods=["GET"]),
    Route("/api/rewrite/v1/quant/performance", quant_performance, methods=["GET"]),
    Route("/api/rewrite/v1/portfolio", portfolio, methods=["GET"]),
    Route("/api/rewrite/v1/market/candles", market_candles, methods=["GET"]),
    Route("/api/rewrite/v1/market/search", market_search, methods=["GET"]),
    Route("/api/rewrite/v1/membership", membership, methods=["GET"]),
    Route("/api/rewrite/v1/telegram/status", telegram_status, methods=["GET"]),
    Route("/api/rewrite/v1/settings", settings, methods=["GET"]),
    Route("/api/rewrite/v1/settings/risk", risk_settings, methods=["PUT"]),
    Route("/api/rewrite/v1/settings/telegram", telegram_preferences, methods=["PUT"]),
    Route("/api/rewrite/v1/watchlist", watchlist, methods=["GET", "POST", "DELETE"]),
    Route("/api/rewrite/v1/settings/locale", locale_preference, methods=["PUT"]),
    Route("/api/rewrite/v1/alerts", alerts, methods=["GET", "POST"]),
    Route("/api/rewrite/v1/paper/orders", paper_orders, methods=["POST"]),
    Route("/api/rewrite/v1/membership/orders", membership_orders, methods=["POST"]),
    Route("/api/rewrite/v1/membership/orders/{order_no:str}/payment-qr", membership_order_payment_qr, methods=["GET"]),
    Route("/api/rewrite/v1/membership/orders/{order_no:str}/proof", membership_order_proof, methods=["POST"]),
]

app = Starlette(
    debug=False,
    routes=routes,
    middleware=[Middleware(PaymentProofBodyLimit), Middleware(RewriteSecurityHeaders)],
    exception_handlers={ApiError: api_error_handler, ReadModelError: read_model_error_handler},
)
app.state.repository = ReadOnlyLegacyRepository()
