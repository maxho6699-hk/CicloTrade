"""Browser compatibility API for the rewritten TradeAI client."""

from __future__ import annotations

from pathlib import Path
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import os
import re
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

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
    legacy_database_path,
)
from src.apps.api.write_service import BrowserWriteService
from src.apps.api.chart_drawings import ChartDrawingConflict, ChartDrawingError, ChartDrawingService
from src.apps.api.backtest_jobs import (
    backtest_artifact, backtest_cancel, backtest_item, backtests, worker_claim,
    worker_complete, worker_fail, worker_heartbeat, worker_input, worker_output,
)
from src.apps.api.earnings_forecasts import (
    EarningsForecastApi,
    EarningsForecastUnavailable,
    earnings_forecast_detail,
    earnings_forecast_history,
    earnings_forecast_overview,
    earnings_forecast_statistics,
    earnings_option_detail,
)
from src.apps.api.earnings_read_model import EarningsForecastReadModel, OpaqueIdCodec
from src.apps.api.official_option_simulation import (
    OfficialOptionSimulationApi,
    OfficialOptionSimulationUnavailable,
    official_option_sim_detail,
    official_option_sim_overview,
    official_option_sim_unavailable_handler,
)
from src.apps.api.official_option_sim_read_model import OfficialOptionSimulationReadModel
from src.apps.api.official_option_sim_receiver import (
    OfficialOptionSimulationReceiver,
    OfficialOptionSimulationReceiverError,
    official_option_sim_receipt,
    official_option_sim_receiver_error,
)
from core.backtest_queue import BacktestQueueError
from core.database import DatabaseManager
from core.official_option_sim_journal import OfficialOptionSimulationJournal
from core.auth import AuthError, AuthService, email_verification_required
from core.compat import UTC
from core.plans import can, effective_plan, plan_display_name, web_market_data_visibility
from notification.email_sender import send_email, smtp_configured
from notification.templates import auth_email
from payment.order_service import MembershipPlanConflict
from data.datasource import DataSourceError, get_resilient_data_source, public_market_status
from data.opend_adapter import OpenDAdapter, OptionExpiryUnavailableError
from data.yfinance_adapter import YahooOptionExpiryUnavailableError


load_dotenv(Path(__file__).resolve().parents[3] / ".env")


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _build_earnings_forecast_api() -> EarningsForecastApi | None:
    """Configure the private read surface without weakening API startup."""
    secret = (
        os.getenv("EARNINGS_OPAQUE_ID_SECRET")
        or os.getenv("JWT_SECRET_KEY")
        or os.getenv("SECRET_KEY")
        or ""
    )
    if len(secret.encode("utf-8")) < 32:
        return None
    codec_key = hashlib.sha256(
        b"ciclotrade:earnings-opaque-id:v1\0" + secret.encode("utf-8")
    ).digest()
    return EarningsForecastApi(
        EarningsForecastReadModel(legacy_database_path(), OpaqueIdCodec(codec_key)),
        authenticate=_identity,
        has_capability=lambda identity, capability: can(
            identity.effective_plan, capability
        ),
        clock=lambda: datetime.now().astimezone(),
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _official_option_sim_codec() -> OpaqueIdCodec | None:
    secret = os.getenv("OFFICIAL_OPTION_SIM_OPAQUE_ID_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        return None
    key = hashlib.sha256(
        b"ciclotrade:official-option-sim-opaque-id:v1\0" + secret.encode("utf-8")
    ).digest()
    return OpaqueIdCodec(key)


def _build_official_option_sim_api() -> OfficialOptionSimulationApi | None:
    if not _enabled("OFFICIAL_OPTION_SIMULATION_ENABLED"):
        return None
    codec = _official_option_sim_codec()
    if codec is None:
        return None
    return OfficialOptionSimulationApi(
        OfficialOptionSimulationReadModel(legacy_database_path(), codec),
        authenticate=_identity,
        has_capability=lambda identity, capability: can(
            identity.effective_plan, capability
        ),
    )


def _build_official_option_sim_receiver() -> OfficialOptionSimulationReceiver | None:
    if not (
        _enabled("OFFICIAL_OPTION_SIMULATION_ENABLED")
        and _enabled("OFFICIAL_OPTION_SIMULATION_RECEIVER_ENABLED")
    ):
        return None
    secret = os.getenv("OFFICIAL_OPTION_SIMULATION_SHARED_SECRET", "").encode("utf-8")
    if len(secret) < 32:
        return None
    receiver = OfficialOptionSimulationReceiver(
        OfficialOptionSimulationJournal(DatabaseManager(str(legacy_database_path()))),
        shared_secret=secret,
    )
    receiver.enabled = True
    return receiver


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


def _chart_drawing_service(request: Request) -> ChartDrawingService:
    service = getattr(request.app.state, "chart_drawing_service", None)
    return service if service is not None else ChartDrawingService()


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
        "display_name": user.get("display_name") or "CicloTrade 用户",
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
            "app": "ciclotrade-rewrite-api",
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
                "quant_performance", "official_validation_portfolio", "telegram_status",
            ],
            "external_side_effects": {"payments": False, "telegram": False, "live_trading": False},
            "protected_writes": ["risk_settings", "telegram_preferences", "ui_locale", "watchlist", "price_alerts", "opening_pause_resume", "pending_membership_orders", "chart_drawings"],
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
    try:
        identity = _repository(request).authenticate(result.access_token)
    except (ReadModelAuthError, ReadModelError) as exc:
        raise ApiError("会员权限暂时无法核验，请稍后重试。", 503) from exc
    response = JSONResponse(
        {
            "access_token": result.access_token,
            "user": _session_user(
                {
                    "id": identity.id,
                    "display_name": identity.display_name,
                    "plan_type": identity.plan_type,
                    "subscription_expire": identity.subscription_expire,
                }
            ),
            "new_ip": result.new_ip,
        }
    )
    _set_refresh_cookie(response, result.refresh_token)
    return response


def _auth_email_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:4175/login").strip().rstrip("/")


def _deliver_auth_email(email: str, kind: str, token: str) -> None:
    try:
        send_email(email, *auth_email(kind, token, _auth_email_base_url()))
    except RuntimeError as exc:
        raise ApiError("认证邮件暂时无法发送，请稍后重试。", 503) from exc


def _discard_auth_token(auth: AuthService, kind: str, token: str) -> None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if kind == "verify":
        auth.db.execute(
            "DELETE FROM email_verifications WHERE token_hash=? AND used_at IS NULL",
            (digest,),
        )
    elif kind == "reset":
        auth.db.execute(
            "DELETE FROM password_resets WHERE token_hash=? AND used_at IS NULL",
            (digest,),
        )


async def session_register(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    required = {"email", "password", "display_name", "terms_accepted"}
    allowed = required | {"referral"}
    if set(payload) - allowed or not required.issubset(payload):
        raise ApiError("注册字段不完整或包含未知字段。")
    email = payload["email"]
    password = payload["password"]
    display_name = payload["display_name"]
    referral = payload.get("referral", "")
    if not all(isinstance(value, str) for value in (email, password, display_name, referral)):
        raise ApiError("注册资料格式无效。")
    verification_required = email_verification_required()
    if verification_required and not smtp_configured():
        raise ApiError("注册邮件服务尚未就绪，请稍后重试。", 503)
    client_ip = request.client.host if request.client else "unknown"
    auth = _auth_service(request)
    try:
        auth.register(
            email,
            password,
            display_name,
            payload["terms_accepted"] is True,
            referral,
            ip_address=client_ip,
        )
        token = (
            auth.request_email_verification(email, client_ip)
            if verification_required
            else None
        )
    except AuthError as exc:
        raise ApiError(str(exc), 429 if "频繁" in str(exc) or "稍后" in str(exc) else 400) from exc
    if token:
        try:
            _deliver_auth_email(email.strip().lower(), "verify", token)
        except ApiError:
            _discard_auth_token(auth, "verify", token)
            raise
    return JSONResponse(
        {
            "accepted": True,
            "verification_required": verification_required,
            "message": (
                "如果邮箱可用于注册，验证邮件已经发送。"
                if verification_required
                else "如果邮箱可用于注册，账户已经建立。"
            ),
        },
        status_code=202,
    )


async def session_verification_request(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    if set(payload) != {"email"} or not isinstance(payload.get("email"), str):
        raise ApiError("请输入注册邮箱。")
    if not smtp_configured():
        raise ApiError("注册邮件服务尚未就绪，请稍后重试。", 503)
    client_ip = request.client.host if request.client else "unknown"
    auth = _auth_service(request)
    try:
        token = auth.request_email_verification(
            payload["email"], client_ip
        )
    except AuthError as exc:
        raise ApiError(str(exc), 429) from exc
    if token:
        try:
            _deliver_auth_email(payload["email"].strip().lower(), "verify", token)
        except ApiError:
            _discard_auth_token(auth, "verify", token)
            raise
    return JSONResponse(
        {"accepted": True, "message": "如果账户需要验证，邮件已经发送。"},
        status_code=202,
    )


async def session_verify_email(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    if set(payload) != {"token"} or not isinstance(payload.get("token"), str):
        raise ApiError("邮箱验证码无效或已过期。")
    try:
        _auth_service(request).verify_email(payload["token"])
    except AuthError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"verified": True})


async def session_password_reset_request(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    if set(payload) != {"email"} or not isinstance(payload.get("email"), str):
        raise ApiError("请输入注册邮箱。")
    if not smtp_configured():
        raise ApiError("密码重设邮件服务尚未就绪，请稍后重试。", 503)
    client_ip = request.client.host if request.client else "unknown"
    auth = _auth_service(request)
    try:
        token = auth.request_password_reset(
            payload["email"], client_ip
        )
    except AuthError as exc:
        raise ApiError(str(exc), 429) from exc
    if token:
        try:
            _deliver_auth_email(payload["email"].strip().lower(), "reset", token)
        except ApiError:
            _discard_auth_token(auth, "reset", token)
            raise
    return JSONResponse(
        {"accepted": True, "message": "如果账户存在，密码重设邮件已经发送。"},
        status_code=202,
    )


async def session_password_reset_confirm(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    if set(payload) != {"token", "password"} or not all(
        isinstance(payload.get(key), str) for key in ("token", "password")
    ):
        raise ApiError("密码重设资料不完整。")
    client_ip = request.client.host if request.client else "unknown"
    try:
        _auth_service(request).reset_password(
            payload["token"], payload["password"], client_ip
        )
    except AuthError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"reset": True})


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
    "1分": ("1d", "1m", None),
    "2分": ("5d", "1m", "2min"),
    "3分": ("5d", "1m", "3min"),
    "4分": ("5d", "1m", "4min"),
    "5分": ("5d", "5m", None),
    "10分": ("1mo", "5m", "10min"),
    "15分": ("1mo", "15m", None),
    "20分": ("1mo", "5m", "20min"),
    "30分": ("1mo", "30m", None),
    "45分": ("1mo", "15m", "45min"),
    "1小时": ("3mo", "60m", None),
    "2小时": ("3mo", "60m", "2h"),
    "3小时": ("3mo", "60m", "3h"),
    "4小时": ("3mo", "60m", "4h"),
    "6小时": ("3mo", "60m", "6h"),
    "8小时": ("3mo", "60m", "8h"),
    "日线": ("6mo", "1d", None),
    "周线": ("2y", "1d", "W-FRI"),
    "月线": ("5y", "1d", "MS"),
}

# These are native OpenD intervals.  Option bars deliberately do not use the
# stock endpoint's synthetic resampling or its resilient-provider fallback.
OPTION_TIMEFRAMES = {
    "1分": ("1d", "1m"),
    "5分": ("5d", "5m"),
    "15分": ("1mo", "15m"),
    "30分": ("1mo", "30m"),
    "1小时": ("3mo", "60m"),
    "日线": ("6mo", "1d"),
    "周线": ("2y", "1wk"),
    "月线": ("5y", "1mo"),
}
_OPEND_OPTION_CONTRACT_PATTERN = re.compile(r"US\.[A-Z][A-Z0-9.-]{0,11}\d{6}[CP]\d{6}")
_YAHOO_OPTION_CONTRACT_PATTERN = re.compile(r"[A-Z][A-Z0-9.-]{0,11}\d{6}[CP]\d{8}")

_MARKET_SEARCH_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}
_MARKET_SEARCH_RATE: dict[int, list[float]] = {}
_MARKET_SEARCH_LOCK = threading.Lock()
_MARKET_SEARCH_TTL_SECONDS = 300
_MARKET_SEARCH_LIMIT_PER_MINUTE = 30
_MARKET_STATUS_TTL_SECONDS = 8.0
_MARKET_STATUS_LOCK = threading.Lock()
_MARKET_STATUS_CACHE: tuple[float, dict[str, object]] | None = None
_MARKET_TIMEZONES = {"美股": ZoneInfo("America/New_York"), "A股": ZoneInfo("Asia/Shanghai")}
_MARKET_BAR_DURATIONS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "60m": timedelta(minutes=60),
    "1h": timedelta(minutes=60),
    "1d": timedelta(days=1),
}


def _is_yahoo_source(source: Any) -> bool:
    """Recognize Yahoo adapters without depending on one display-name spelling."""
    return str(getattr(source, "name", "")).strip().casefold() in {"yahoo finance", "yfinance"}


def _market_timezone(market: str) -> ZoneInfo:
    return _MARKET_TIMEZONES.get(market, _MARKET_TIMEZONES["美股"])


def _market_datetime(value: Any, market: str) -> datetime | None:
    """Interpret offset-free timestamps in their exchange's timezone.

    Providers commonly return a naive ``DatetimeIndex`` or OpenD's
    ``YYYY-mm-dd HH:MM:SS`` timestamp.  Never let the API server timezone
    decide what market time that was.
    """
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_market_timezone(market))
    return value.astimezone(UTC)


def _iso_market_datetime(value: Any, market: str) -> str | None:
    timestamp = _market_datetime(value, market)
    return timestamp.isoformat() if timestamp is not None else None


def _visible_as_of(delay_minutes: int, now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC) - timedelta(minutes=max(0, int(delay_minutes)))


def _frame_observed_at(frame: Any, market: str) -> datetime | None:
    observed = [_market_datetime(index, market) for index in frame.index]
    return max((value for value in observed if value is not None), default=None)


def _market_bar_end(timestamp: datetime, market: str, interval: str) -> datetime:
    if interval == "1d":
        timezone = _market_timezone(market)
        local_day = timestamp.astimezone(timezone).date() + timedelta(days=1)
        return datetime(
            local_day.year, local_day.month, local_day.day, tzinfo=timezone
        ).astimezone(UTC)
    duration = _MARKET_BAR_DURATIONS.get(interval)
    if duration is None:
        raise ApiError("行情服务返回了不支持的原生 K 线周期。", 503)
    return timestamp + duration


def _clip_market_frame_for_delivery(
    frame: Any,
    *,
    market: str,
    interval: str,
    delivery_delay_minutes: int,
    now: datetime | None = None,
) -> tuple[Any, datetime | None, datetime]:
    """Clip provider-native bars by their end before any aggregation."""
    visible_as_of = _visible_as_of(delivery_delay_minutes, now)
    observed_at = _frame_observed_at(frame, market)
    if delivery_delay_minutes <= 0:
        return frame, observed_at, visible_as_of
    indexed = [_market_datetime(index, market) for index in frame.index]
    positions = [
        position for position, timestamp in enumerate(indexed)
        if timestamp is not None and _market_bar_end(timestamp, market, interval) <= visible_as_of
    ]
    return frame.iloc[positions], observed_at, visible_as_of


def _market_source_realtime(source: Any, market: str) -> bool:
    """Verify upstream real-time entitlement without treating it as user access."""
    if market != "美股" or not bool(getattr(source, "supports_realtime", False)):
        return False
    upstream = _upstream_market_status()
    return bool(
        upstream["connected"]
        and upstream["configuration_allows_realtime"]
        and upstream["equity_realtime_entitled"]
    )


def _merge_delayed_current_daily_bar(
    frame: Any,
    intraday: Any,
    *,
    market: str,
    visible_as_of: datetime,
) -> tuple[Any, datetime | None]:
    """Replace an in-progress daily bar with minute data capped at delivery time."""
    if intraday.empty:
        return frame, None
    timezone = _market_timezone(market)
    current_day = visible_as_of.astimezone(timezone).date()
    entries = [
        (position, _market_datetime(index, market))
        for position, index in enumerate(intraday.index)
    ]
    positions = [
        position for position, timestamp in entries
        if timestamp is not None and timestamp.astimezone(timezone).date() == current_day
    ]
    if not positions:
        return frame, None
    session = intraday.iloc[positions]
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(session.columns):
        return frame, None
    day_start = pd.Timestamp(datetime(
        current_day.year, current_day.month, current_day.day, tzinfo=timezone
    ))
    frame_timezone = getattr(frame.index, "tz", None)
    if frame_timezone is not None:
        day_start = day_start.tz_convert(frame_timezone)
    else:
        day_start = day_start.tz_localize(None)
    existing_positions = [
        position for position, index in enumerate(frame.index)
        if (timestamp := _market_datetime(index, market)) is not None
        and timestamp.astimezone(timezone).date() == current_day
    ]
    base = frame.drop(frame.index[existing_positions]) if existing_positions else frame
    aggregate = pd.DataFrame(
        [{
            "Open": float(session["Open"].iloc[0]),
            "High": float(session["High"].max()),
            "Low": float(session["Low"].min()),
            "Close": float(session["Close"].iloc[-1]),
            "Volume": float(session["Volume"].sum()),
        }],
        index=pd.DatetimeIndex([day_start]),
    )
    observed_at = max(
        (timestamp for _, timestamp in entries if timestamp is not None),
        default=None,
    )
    return pd.concat([base, aggregate]).sort_index(), observed_at


async def _delayed_daily_frame(
    frame: Any,
    *,
    source: Any,
    symbol: str,
    market: str,
    delivery_delay_minutes: int,
) -> tuple[Any, datetime | None, datetime, bool]:
    """Build a current daily candle from capped minute bars when available."""
    visible_as_of = _visible_as_of(delivery_delay_minutes)
    try:
        intraday = await run_in_threadpool(source.bars, symbol, "5d", "1m")
        intraday, intraday_observed, visible_as_of = await run_in_threadpool(
            _clip_market_frame_for_delivery,
            intraday,
            market=market,
            interval="1m",
            delivery_delay_minutes=delivery_delay_minutes,
        )
    except (AttributeError, DataSourceError, TypeError, ValueError):
        return frame, None, visible_as_of, False
    merged, merged_observed = await run_in_threadpool(
        _merge_delayed_current_daily_bar,
        frame,
        intraday,
        market=market,
        visible_as_of=visible_as_of,
    )
    return merged, merged_observed or intraday_observed, visible_as_of, merged_observed is not None


def _final_market_status(
    status: dict[str, object],
    *,
    delivery_delay_minutes: int,
    provider_realtime: bool,
    visible_as_of: datetime,
    observed_at: datetime | None,
) -> dict[str, object]:
    """Apply the membership delivery boundary to an upstream status object."""
    delay = max(0, int(delivery_delay_minutes))
    realtime = bool(provider_realtime and delay == 0)
    result = dict(status)
    result.update({
        "delivery_delay_minutes": delay,
        "visible_as_of": visible_as_of.isoformat(),
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "provider_realtime": bool(provider_realtime),
        "is_realtime": realtime,
    })
    if delay:
        result["freshness"] = f"延迟 {delay} 分钟"
        result["detail"] = "会员数据延迟已生效"
    return result


def _upstream_market_status() -> dict[str, object]:
    """Probe upstream quote health with one cold-cache caller at a time."""
    global _MARKET_STATUS_CACHE
    with _MARKET_STATUS_LOCK:
        now = time.monotonic()
        if _MARKET_STATUS_CACHE is not None and now - _MARKET_STATUS_CACHE[0] < _MARKET_STATUS_TTL_SECONDS:
            return dict(_MARKET_STATUS_CACHE[1])
        status: dict[str, object] = {
            "connected": False,
            "equity_realtime_entitled": False,
            "option_realtime_entitled": False,
            "configuration_allows_realtime": _enabled("MARKET_DATA_REALTIME"),
        }
        try:
            adapter = OpenDAdapter()
            status["connected"] = bool(adapter.available())
            if status["connected"]:
                rights = adapter.quote_rights()
                status["equity_realtime_entitled"] = bool(rights.get("us_realtime_entitlement"))
                status["option_realtime_entitled"] = bool(rights.get("us_option_realtime_entitlement"))
        except Exception:
            # This is a status probe only.  Any unexpected adapter failure must
            # degrade to unavailable rather than make an authenticated dashboard
            # request fail open or disclose upstream implementation details.
            pass
        _MARKET_STATUS_CACHE = (time.monotonic(), dict(status))
        return dict(status)


def _market_bars_with_fallback(
    symbol: str, market: str, period: str, interval: str
) -> tuple[Any, Any, str | None]:
    """Load stock bars and return the provider that actually answered.

    Option bars do not use this helper: their OpenD-only contract is handled by
    ``option_candles`` below.
    """
    source = get_resilient_data_source("akshare") if market == "A股" else get_resilient_data_source()
    try:
        return source.bars(symbol, period, interval), source, None
    except DataSourceError:
        if _is_yahoo_source(source):
            raise
        fallback = get_resilient_data_source("yfinance")
        return fallback.bars(symbol, period, interval), fallback, str(getattr(source, "name", "市场数据"))


async def _delayed_market_quote(
    symbol: str,
    *,
    market: str,
    delivery_delay_minutes: int,
) -> JSONResponse:
    """Construct a delayed quote from bars, never from a current snapshot."""
    try:
        frame, source, fallback_from = await run_in_threadpool(
            _market_bars_with_fallback, symbol, market, "5d", "1m"
        )
        visible, observed_at, visible_as_of = await run_in_threadpool(
            _clip_market_frame_for_delivery,
            frame,
            market=market,
            interval="1m",
            delivery_delay_minutes=delivery_delay_minutes,
        )
    except DataSourceError as exc:
        return _opend_quote_unavailable(
            symbol,
            str(exc),
            source="市场数据",
            delivery_delay_minutes=delivery_delay_minutes,
        )
    required = {"Open", "High", "Low", "Close", "Volume"}
    if visible.empty or not required.issubset(visible.columns):
        return _opend_quote_unavailable(
            symbol,
            "延迟窗口内没有可用研究报价。",
            source="市场数据",
            delivery_delay_minutes=delivery_delay_minutes,
            visible_as_of=visible_as_of,
            observed_at=observed_at,
        )
    row = visible.iloc[-1]
    quote_at = _iso_market_datetime(visible.index[-1], market)
    return JSONResponse({
        "symbol": symbol,
        "last": float(row["Close"]),
        "bid": None,
        "ask": None,
        "spread": None,
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "prev_close": None,
        "volume": float(row["Volume"]),
        "quote_at": quote_at,
        "source": str(getattr(source, "name", "市场数据")),
        "is_realtime": False,
        "provider_realtime": False,
        "actionable_quote": False,
        "freshness": f"延迟 {delivery_delay_minutes} 分钟的研究报价",
        "verification": "plan_delayed_market_data",
        "configuration_allows_realtime": _enabled("MARKET_DATA_REALTIME"),
        "delivery_delay_minutes": delivery_delay_minutes,
        "visible_as_of": visible_as_of.isoformat(),
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "request_succeeded": True,
        "fallback_from": fallback_from,
        "status": "available",
    })


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


def _professional_identity(request: Request) -> BrowserIdentity:
    """Authorize before constructing or calling the private OpenD adapter."""
    identity = _identity(request)
    if not can(identity.effective_plan, "option_chain"):
        raise ApiError("期权研究仅对专业会员开放。", 403)
    return identity


def _option_contract_code(request: Request) -> str:
    code = request.query_params.get("contract_code", "").strip().upper()
    if not (_OPEND_OPTION_CONTRACT_PATTERN.fullmatch(code) or _YAHOO_OPTION_CONTRACT_PATTERN.fullmatch(code)):
        raise ApiError("期权合约代码无效。")
    return code


def _option_chain_symbol(request: Request) -> str:
    symbol = _market_symbol(request)
    if symbol.isdigit():
        raise ApiError("OpenD 期权链仅支持美股标的。")
    return symbol


def _equity_quote_symbol(request: Request) -> str:
    return _market_symbol(request)


def _option_expiry(request: Request) -> str | None:
    expiry = request.query_params.get("expiry")
    if expiry is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry):
        raise ApiError("期权到期日无效。")
    try:
        parsed = date.fromisoformat(expiry)
    except ValueError as exc:
        raise ApiError("期权到期日无效。") from exc
    if parsed.isoformat() != expiry:
        raise ApiError("期权到期日无效。")
    return expiry


def _option_number(row: Any, column: str) -> float | None:
    value = row.get(column)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _option_text(row: Any, column: str) -> str | None:
    value = row.get(column)
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.casefold() not in {"nan", "nat", "<na>"} else None


def _opend_option_status(rights: dict[str, object], has_executable_quotes: bool) -> dict[str, object]:
    configured = bool(os.getenv("MARKET_DATA_REALTIME", "").strip().lower() in {"1", "true", "yes", "on"})
    qot_right = str(rights.get("us_option_qot_right") or "N/A").strip().upper()
    entitlement = bool(rights.get("us_option_realtime_entitlement"))
    realtime = configured and entitlement
    known = qot_right in {"BMP", "LV1", "LV2", "LV3", "SF", "NO"}
    if entitlement:
        freshness = f"OpenD · 美股期权 {qot_right} 实时权限已验证"
        if not configured:
            freshness += "；平台实时开关未启用"
    elif known:
        freshness = f"OpenD · 美股期权权限 {qot_right}，仅供研究"
    else:
        freshness = "OpenD · 美股期权权限未验证，仅供研究"
    return {
        "source": "OpenD",
        "is_realtime": realtime,
        "actionable_quote": realtime and has_executable_quotes,
        "freshness": freshness,
        "verification": f"opend_option_qot_right_{qot_right.lower()}" if known else "opend_option_right_unverified",
        "configuration_allows_realtime": configured,
        "qot_right": qot_right,
        "missing_fields": [],
    }


def _delayed_option_status(*, fallback_from: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "source": "Yahoo Finance",
        "is_realtime": False,
        "actionable_quote": False,
        "freshness": "免费延迟期权研究数据，不用于立即交易",
        "verification": "delayed_research_fallback",
        "configuration_allows_realtime": False,
        "qot_right": "N/A",
        "missing_fields": ["delta", "gamma", "theta", "vega", "rho"],
    }
    if fallback_from:
        result["fallback_from"] = fallback_from
    return result


def _unknown_opend_rights() -> dict[str, object]:
    """Return a fail-closed permission snapshot independent of adapter test doubles."""
    return {
        "us_qot_right": "N/A",
        "us_option_qot_right": "N/A",
        "us_realtime_entitlement": False,
        "us_option_realtime_entitlement": False,
    }


def _option_rows(frame: Any, option_type: str, expiry: str) -> list[dict[str, Any]]:
    items = []
    for _, row in frame.iterrows():
        bid, ask = _option_number(row, "bid"), _option_number(row, "ask")
        items.append({
            "expiry": expiry,
            "option_type": option_type,
            "contract_code": _option_text(row, "contractSymbol") or "",
            "strike": _option_number(row, "strike"),
            "last": _option_number(row, "lastPrice"),
            "bid": bid,
            "ask": ask,
            "spread": ask - bid if bid is not None and ask is not None else None,
            "volume": _option_number(row, "volume"),
            "open_interest": _option_number(row, "openInterest"),
            "implied_volatility": _option_number(row, "impliedVolatility"),
            "greeks": {
                "delta": _option_number(row, "delta"),
                "gamma": _option_number(row, "gamma"),
                "theta": _option_number(row, "theta"),
                "vega": _option_number(row, "vega"),
                "rho": _option_number(row, "rho"),
            },
            "quote_at": _option_text(row, "lastTradeDate"),
        })
    return items


def _opend_unavailable(message: str, **context: Any) -> JSONResponse:
    """Return a truthful, empty state; option data is never substituted."""
    return JSONResponse(
        {**context, "items": [], "status": "unavailable", "error": message},
        status_code=503,
    )


def _opend_quote_unavailable(
    symbol: str,
    message: str,
    source: str = "OpenD",
    *,
    delivery_delay_minutes: int = 0,
    visible_as_of: datetime | None = None,
    observed_at: datetime | None = None,
) -> JSONResponse:
    """Expose a complete, non-actionable shape when no research quote succeeds."""
    return JSONResponse({
        "symbol": symbol,
        "last": None,
        "bid": None,
        "ask": None,
        "spread": None,
        "open": None,
        "high": None,
        "low": None,
        "prev_close": None,
        "volume": None,
        "quote_at": None,
        "source": source,
        "is_realtime": False,
        "actionable_quote": False,
        "freshness": "无可用研究报价",
        "verification": "request_failed",
        "configuration_allows_realtime": bool(os.getenv("MARKET_DATA_REALTIME", "").strip().lower() in {"1", "true", "yes", "on"}),
        "delivery_delay_minutes": max(0, int(delivery_delay_minutes)),
        "visible_as_of": (visible_as_of or _visible_as_of(delivery_delay_minutes)).isoformat(),
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "provider_realtime": False,
        "request_succeeded": False,
        "status": "unavailable",
        "error": message,
    }, status_code=503)


def _resample_market_bars(frame: Any, rule: str | None) -> Any:
    if not rule:
        return frame
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        raise ApiError("行情服务返回的 K 线字段不完整。", 503)
    try:
        return frame.resample(rule).agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }).dropna(subset=["Open", "High", "Low", "Close"])
    except (TypeError, ValueError) as exc:
        raise ApiError("行情服务无法生成所选 K 线周期。", 503) from exc


async def market_candles(request: Request) -> JSONResponse:
    identity = _identity(request)
    symbol = _market_symbol(request)
    timeframe = request.query_params.get("timeframe", "日线")
    if timeframe not in MARKET_TIMEFRAMES:
        raise ApiError("K线周期无效。")
    period, interval, resample_rule = MARKET_TIMEFRAMES[timeframe]
    market_name = "A股" if symbol.isdigit() else "美股"
    delivery_delay_minutes = web_market_data_visibility(
        identity.effective_plan, "stock"
    )["delivery_delay_minutes"]
    try:
        frame, source, fallback_from = await run_in_threadpool(
            _market_bars_with_fallback, symbol, market_name, period, interval
        )
        frame, observed_at, visible_as_of = await run_in_threadpool(
            _clip_market_frame_for_delivery,
            frame,
            market=market_name,
            interval=interval,
            delivery_delay_minutes=delivery_delay_minutes,
        )
        assembled_daily = False
        delayed_daily_observed_at = None
        if (
            delivery_delay_minutes > 0
            and timeframe in {"日线", "周线", "月线"}
        ):
            frame, delayed_daily_observed_at, visible_as_of, assembled_daily = await _delayed_daily_frame(
                frame,
                source=source,
                symbol=symbol,
                market=market_name,
                delivery_delay_minutes=delivery_delay_minutes,
            )
        frame = await run_in_threadpool(_resample_market_bars, frame, resample_rule)
        if assembled_daily:
            observed_at = delayed_daily_observed_at
    except DataSourceError as exc:
        raise ApiError(str(exc), 503) from exc
    items = []
    for index, row in frame.tail(600).iterrows():
        timestamp = _market_datetime(index, market_name)
        if timestamp is None:
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
    provider_realtime = await run_in_threadpool(_market_source_realtime, source, market_name)
    status = public_market_status(
        market=market_name,
        source=source,
        request_succeeded=True,
        realtime_verified=provider_realtime,
        fallback_from=fallback_from,
    )
    return JSONResponse({
        "symbol": symbol,
        "timeframe": timeframe,
        "items": items,
        "status": _final_market_status(
            status,
            delivery_delay_minutes=delivery_delay_minutes,
            provider_realtime=provider_realtime,
            visible_as_of=visible_as_of,
            observed_at=observed_at,
        ),
    })


async def market_quote(request: Request) -> JSONResponse:
    """Return a US quote with a truthful delayed-research fallback."""
    identity = _identity(request)
    symbol = _equity_quote_symbol(request)
    market_name = "A股" if symbol.isdigit() else "美股"
    delivery_delay_minutes = web_market_data_visibility(
        identity.effective_plan, "stock"
    )["delivery_delay_minutes"]
    if delivery_delay_minutes:
        return await _delayed_market_quote(
            symbol,
            market=market_name,
            delivery_delay_minutes=delivery_delay_minutes,
        )
    if symbol.isdigit():
        try:
            primary = get_resilient_data_source("akshare")
            quote = await run_in_threadpool(primary.stock_quote, symbol)
        except DataSourceError as exc:
            try:
                fallback = get_resilient_data_source("yfinance")
                quote = await run_in_threadpool(fallback.stock_quote, symbol)
            except DataSourceError as fallback_exc:
                return _opend_quote_unavailable(
                    symbol,
                    f"AKShare 研究报价失败：{exc}；Yahoo Finance 回退失败：{fallback_exc}",
                    source="AKShare",
                    delivery_delay_minutes=delivery_delay_minutes,
                )
            quote_at = _iso_market_datetime(quote.get("quote_at"), market_name)
            return JSONResponse({
                "symbol": symbol,
                **quote,
                "quote_at": quote_at,
                "is_realtime": False,
                "provider_realtime": False,
                "actionable_quote": False,
                "freshness": str(quote.get("freshness") or "约 15 分钟延迟的研究报价"),
                "verification": str(quote.get("verification") or "delayed_research_quote"),
                "configuration_allows_realtime": False,
                "delivery_delay_minutes": delivery_delay_minutes,
                "visible_as_of": _visible_as_of(delivery_delay_minutes).isoformat(),
                "observed_at": quote_at,
                "request_succeeded": True,
                "fallback_from": "AKShare",
                "status": "available",
            })
        quote_at = _iso_market_datetime(quote.get("quote_at"), market_name)
        return JSONResponse({
            "symbol": symbol,
            **quote,
            "quote_at": quote_at,
            "is_realtime": False,
            "provider_realtime": False,
            "actionable_quote": False,
            "freshness": str(quote.get("freshness") or "A 股免费研究报价；实时等级未验证"),
            "verification": str(quote.get("verification") or "delayed_research_quote"),
            "configuration_allows_realtime": False,
            "delivery_delay_minutes": delivery_delay_minutes,
            "visible_as_of": _visible_as_of(delivery_delay_minutes).isoformat(),
            "observed_at": quote_at,
            "request_succeeded": True,
            "status": "available",
        })
    try:
        quote = await run_in_threadpool(OpenDAdapter().stock_quote, symbol)
    except DataSourceError as exc:
        opend_error = str(exc)
        try:
            fallback = get_resilient_data_source("yfinance")
            quote = await run_in_threadpool(fallback.stock_quote, symbol)
        except DataSourceError as fallback_exc:
            return _opend_quote_unavailable(
                symbol,
                f"{opend_error}；Yahoo Finance 回退失败：{fallback_exc}",
                delivery_delay_minutes=delivery_delay_minutes,
            )
        quote_at = _iso_market_datetime(quote.get("quote_at"), market_name)
        return JSONResponse({
            "symbol": symbol,
            **quote,
            "quote_at": quote_at,
            "is_realtime": False,
            "provider_realtime": False,
            "actionable_quote": False,
            "freshness": str(quote.get("freshness") or "约 15 分钟延迟的研究报价"),
            "verification": str(quote.get("verification") or "delayed_research_quote"),
            "configuration_allows_realtime": bool(os.getenv("MARKET_DATA_REALTIME", "").strip().lower() in {"1", "true", "yes", "on"}),
            "delivery_delay_minutes": delivery_delay_minutes,
            "visible_as_of": _visible_as_of(delivery_delay_minutes).isoformat(),
            "observed_at": quote_at,
            "request_succeeded": True,
            "fallback_from": "OpenD",
            "status": "available",
        })
    configured_realtime = bool(os.getenv("MARKET_DATA_REALTIME", "").strip().lower() in {"1", "true", "yes", "on"})
    qot_right = str(quote.get("us_qot_right") or "N/A").strip().upper()
    entitlement_realtime = bool(quote.get("us_realtime_entitlement"))
    realtime_verified = configured_realtime and entitlement_realtime
    actionable_quote = realtime_verified and bool(quote.get("actionable_snapshot"))
    right_verified = qot_right in {"BMP", "LV1", "LV2", "LV3", "SF", "NO"}
    if entitlement_realtime:
        freshness = f"OpenD 快照 · 美股 {qot_right} 实时权限已验证"
        if not configured_realtime:
            freshness += "；平台实时开关未启用"
    elif right_verified:
        freshness = f"OpenD 快照 · 美股权限 {qot_right}，仅供研究"
    else:
        freshness = "OpenD 快照已返回；实时等级未验证"
    quote_at = _iso_market_datetime(quote.get("quote_at"), market_name)
    return JSONResponse({
        "symbol": symbol,
        **quote,
        "quote_at": quote_at,
        "is_realtime": realtime_verified,
        "provider_realtime": realtime_verified,
        "actionable_quote": actionable_quote,
        "freshness": freshness,
        "verification": f"opend_qot_right_{qot_right.lower()}" if right_verified else "opend_snapshot_realtime_unverified",
        "configuration_allows_realtime": configured_realtime,
        "delivery_delay_minutes": delivery_delay_minutes,
        "visible_as_of": _visible_as_of(delivery_delay_minutes).isoformat(),
        "observed_at": quote_at,
        "request_succeeded": True,
        "status": "available",
    })


async def market_status(request: Request) -> JSONResponse:
    """Return the authenticated, vendor-neutral website data boundary."""
    identity = _identity(request)
    visibility = web_market_data_visibility(identity.effective_plan, "stock")
    upstream = await run_in_threadpool(_upstream_market_status)
    provider_realtime = bool(
        upstream["connected"]
        and upstream["configuration_allows_realtime"]
        and upstream["equity_realtime_entitled"]
    )
    delay = visibility["delivery_delay_minutes"]
    now = datetime.now(UTC)
    return JSONResponse({
        "status": "available" if upstream["connected"] else "unavailable",
        "upstream_connected": bool(upstream["connected"]),
        "provider_realtime": provider_realtime,
        "configuration_allows_realtime": bool(upstream["configuration_allows_realtime"]),
        "equity_realtime_entitled": bool(upstream["equity_realtime_entitled"]),
        "option_realtime_entitled": bool(upstream["option_realtime_entitled"]),
        "delivery_delay_minutes": delay,
        "is_realtime": provider_realtime and delay == 0,
        "visible_as_of": _visible_as_of(delay, now).isoformat(),
        "observed_at": now.isoformat(),
        "refresh_after_seconds": int(_MARKET_STATUS_TTL_SECONDS),
    })


async def options_chain(request: Request) -> JSONResponse:
    """Return an OpenD option chain or a clearly delayed professional fallback."""
    _professional_identity(request)
    symbol = _option_chain_symbol(request)
    expiry = _option_expiry(request)
    adapter = OpenDAdapter()
    source_status: dict[str, object]
    try:
        selected, expiries, calls, puts = await run_in_threadpool(
            adapter.option_chain_with_expiries, symbol, expiry
        )
    except OptionExpiryUnavailableError as exc:
        raise ApiError(str(exc), 404) from exc
    except DataSourceError as opend_exc:
        fallback = get_resilient_data_source("yfinance")
        try:
            selected, expiries, calls, puts = await run_in_threadpool(
                fallback.option_chain_with_expiries, symbol, expiry
            )
        except YahooOptionExpiryUnavailableError as exc:
            raise ApiError(str(exc), 404) from exc
        except DataSourceError as fallback_exc:
            return _opend_unavailable(
                f"OpenD 期权链不可用：{opend_exc}；Yahoo Finance 回退失败：{fallback_exc}",
                symbol=symbol, expiry=expiry, expiries=[], calls=[], puts=[],
                source="OpenD", fallback_from="OpenD",
            )
        source_status = _delayed_option_status(fallback_from="OpenD")
    else:
        try:
            rights = await run_in_threadpool(adapter.quote_rights)
        except (AttributeError, DataSourceError, TypeError, ValueError):
            rights = _unknown_opend_rights()
        all_rows = [*calls.to_dict("records"), *puts.to_dict("records")]
        has_quotes = any(
            _option_number(row, "bid") is not None
            and _option_number(row, "ask") is not None
            and _option_text(row, "lastTradeDate") is not None
            for row in all_rows
        )
        source_status = _opend_option_status(rights, has_quotes)
    call_items = _option_rows(calls, "CALL", selected)
    put_items = _option_rows(puts, "PUT", selected)
    return JSONResponse({
        "symbol": symbol,
        "expiry": selected,
        "expiries": expiries,
        "calls": call_items,
        "puts": put_items,
        "items": [*call_items, *put_items],
        **source_status,
        "status": "available",
    })


async def option_candles(request: Request) -> JSONResponse:
    """Return OpenD bars or delayed Yahoo bars for an exact Yahoo contract."""
    _professional_identity(request)
    contract_code = _option_contract_code(request)
    timeframe = request.query_params.get("timeframe", "日线")
    if timeframe not in OPTION_TIMEFRAMES:
        raise ApiError("期权 K 线周期无效。")
    period, interval = OPTION_TIMEFRAMES[timeframe]
    adapter = OpenDAdapter()
    try:
        frame = await run_in_threadpool(adapter.option_bars, contract_code, period, interval)
    except DataSourceError as opend_exc:
        if not _YAHOO_OPTION_CONTRACT_PATTERN.fullmatch(contract_code):
            return _opend_unavailable(
                f"OpenD 期权 K 线不可用：{opend_exc}；该 OpenD 合约无法安全映射到免费来源。",
                contract_code=contract_code, timeframe=timeframe, source="OpenD",
            )
        fallback = get_resilient_data_source("yfinance")
        try:
            frame = await run_in_threadpool(fallback.option_bars, contract_code, period, interval)
        except DataSourceError as fallback_exc:
            return _opend_unavailable(
                f"OpenD 期权 K 线不可用：{opend_exc}；Yahoo Finance 回退失败：{fallback_exc}",
                contract_code=contract_code, timeframe=timeframe, source="OpenD", fallback_from="OpenD",
            )
        source_status = _delayed_option_status(fallback_from="OpenD")
    else:
        try:
            rights = await run_in_threadpool(adapter.quote_rights)
        except (AttributeError, DataSourceError, TypeError, ValueError):
            rights = _unknown_opend_rights()
        source_status = _opend_option_status(rights, has_executable_quotes=False)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame.empty or not required.issubset(frame.columns):
        return _opend_unavailable(
            f"{source_status['source']} 没有返回可用的期权 K 线。", contract_code=contract_code, timeframe=timeframe
        )
    items = []
    for index, row in frame.tail(600).iterrows():
        timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
        if not hasattr(timestamp, "timestamp"):
            continue
        items.append({
            "time": int(timestamp.timestamp()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    if not items:
        return _opend_unavailable(
            f"{source_status['source']} 没有返回可用的期权 K 线。", contract_code=contract_code, timeframe=timeframe
        )
    return JSONResponse({
        "contract_code": contract_code,
        "timeframe": timeframe,
        "items": items,
        **source_status,
        "status": "available",
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
        source = get_resilient_data_source("akshare") if scope == "A股" else get_resilient_data_source()
        is_yahoo = _is_yahoo_source(source)
        try:
            results = source.search(query, scope, 8)
        except DataSourceError:
            if not is_yahoo:
                return get_resilient_data_source("yfinance").search(query, scope, 8)
            raise
        if not results and not is_yahoo:
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
        "execution_control": repository.execution_control(identity),
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
        values = service.settings(identity)
        return JSONResponse({"watchlists": values["watchlists"], "pins": values["watchlist_pins"]})
    try:
        if request.method == "PATCH":
            pins = service.update_watchlist_pin(identity, await _json_body(request))
            values = service.settings(identity)
            return JSONResponse({"watchlists": values["watchlists"], "pins": pins})
        value = service.update_watchlist(identity, await _json_body(request), remove=request.method == "DELETE")
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    values = service.settings(identity)
    return JSONResponse({"watchlists": value, "pins": values["watchlist_pins"]})


async def chart_drawings(request: Request) -> JSONResponse:
    """Read and mutate only the authenticated user's chart annotation scope."""
    identity = _identity(request)
    service = _chart_drawing_service(request)
    try:
        if request.method == "GET":
            unknown = set(request.query_params) - {"market", "symbol", "timeframe", "cross_timeframe"}
            if unknown:
                raise ChartDrawingError("画线查询包含未知字段。")
            raw_cross = request.query_params.get("cross_timeframe")
            if raw_cross not in {"true", "false"}:
                raise ChartDrawingError("跨周期标志必须是 true 或 false。")
            result = await run_in_threadpool(
                service.list,
                identity,
                market=request.query_params.get("market"),
                symbol=request.query_params.get("symbol"),
                timeframe=request.query_params.get("timeframe"),
                cross_timeframe=raw_cross == "true",
            )
        else:
            result = await run_in_threadpool(service.batch, identity, await _json_body(request, limit=256 * 1024))
    except ChartDrawingConflict as exc:
        raise ApiError(str(exc), 409) from exc
    except ChartDrawingError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse(result)


async def locale_preference(request: Request) -> JSONResponse:
    identity = _identity(request)
    try:
        value = _write_service(request).update_locale(identity, await _json_body(request))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"locale": value})


async def opening_pause(request: Request) -> JSONResponse:
    identity = _identity(request)
    payload = await _json_body(request)
    if set(payload) != {"paused", "confirmation", "password"}:
        raise ApiError("恢复新开仓请求字段不完整或包含未知字段。")
    if payload.get("paused") is not False:
        raise ApiError("账户页只允许恢复个人新开仓；暂停请使用安全入口。")
    if not isinstance(payload.get("confirmation"), str) or payload["confirmation"] != "恢复新开仓":
        raise ApiError("请输入“恢复新开仓”完成明确确认。")
    password = payload.get("password")
    if not isinstance(password, str):
        raise ApiError("请输入当前账户密码。")
    client_ip = request.client.host if request.client else "unknown"
    try:
        _auth_service(request).verify_password(identity.id, password, client_ip)
        resumed = _write_service(request).resume_opening(identity)
    except AuthError as exc:
        raise ApiError(str(exc), 403) from exc
    return JSONResponse({
        "execution_control": _repository(request).execution_control(identity),
        # Repeating a re-authenticated request is deliberately idempotent.
        "resumed": resumed,
    })


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


async def alert_item(request: Request) -> JSONResponse:
    identity = _identity(request)
    raw_alert_id = str(request.path_params.get("alert_id", ""))
    if not raw_alert_id.isdecimal():
        raise ApiError("预警编号无效。")
    try:
        items = _write_service(request).deactivate_alert(identity, int(raw_alert_id))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"items": items, "deactivated": True})


async def membership_orders(request: Request) -> JSONResponse:
    identity = _identity(request)
    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if not 8 <= len(idempotency_key) <= 128:
        raise ApiError("会员订单必须提供 8 至 128 字符的幂等键。")
    try:
        order = _write_service(request).create_membership_order(
            identity, await _json_body(request), idempotency_key
        )
    except MembershipPlanConflict as exc:
        raise ApiError(str(exc), 409) from exc
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
    except MembershipPlanConflict as exc:
        raise ApiError(str(exc), 409) from exc
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


async def backtest_queue_error_handler(_: Request, exc: BacktestQueueError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


async def earnings_forecast_unavailable_handler(
    _: Request, exc: EarningsForecastUnavailable
) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc)},
        status_code=503,
        headers={
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "Vary": "Cookie, Authorization",
        },
    )


routes = [
    Route("/api/rewrite/health", health, methods=["GET"]),
    Route("/api/rewrite/v1/capabilities", capabilities, methods=["GET"]),
    Route("/api/rewrite/v1/session", session_login, methods=["POST"]),
    Route("/api/rewrite/v1/session/register", session_register, methods=["POST"]),
    Route("/api/rewrite/v1/session/verification", session_verification_request, methods=["POST"]),
    Route("/api/rewrite/v1/session/verify-email", session_verify_email, methods=["POST"]),
    Route("/api/rewrite/v1/session/password-reset", session_password_reset_request, methods=["POST"]),
    Route("/api/rewrite/v1/session/password-reset/confirm", session_password_reset_confirm, methods=["POST"]),
    Route("/api/rewrite/v1/session/refresh", session_refresh, methods=["POST"]),
    Route("/api/rewrite/v1/session", session_logout, methods=["DELETE"]),
    Route("/api/rewrite/v1/me", me, methods=["GET"]),
    Route("/api/rewrite/v1/bootstrap", bootstrap, methods=["GET"]),
    Route("/api/rewrite/v1/recommendations", recommendations, methods=["GET"]),
    Route("/api/rewrite/v1/quant/timeline", quant_timeline, methods=["GET"]),
    Route("/api/rewrite/v1/quant/performance", quant_performance, methods=["GET"]),
    Route("/api/rewrite/v1/portfolio", portfolio, methods=["GET"]),
    Route("/api/rewrite/v1/market/candles", market_candles, methods=["GET"]),
    Route("/api/rewrite/v1/market/quote", market_quote, methods=["GET"]),
    Route("/api/rewrite/v1/market/status", market_status, methods=["GET"]),
    Route("/api/rewrite/v1/options/chain", options_chain, methods=["GET"]),
    Route("/api/rewrite/v1/options/candles", option_candles, methods=["GET"]),
    Route("/api/rewrite/v1/market/search", market_search, methods=["GET"]),
    Route("/api/rewrite/v1/earnings-forecasts", earnings_forecast_overview, methods=["GET"]),
    Route("/api/rewrite/v1/earnings-forecasts/history", earnings_forecast_history, methods=["GET"]),
    Route("/api/rewrite/v1/earnings-forecasts/statistics", earnings_forecast_statistics, methods=["GET"]),
    Route("/api/rewrite/v1/earnings-forecasts/{event_id:str}", earnings_forecast_detail, methods=["GET"]),
    Route("/api/rewrite/v1/earnings-forecasts/{event_id:str}/options/{option_id:str}", earnings_option_detail, methods=["GET"]),
    Route("/api/rewrite/v1/official-option-simulation", official_option_sim_overview, methods=["GET"]),
    Route("/api/rewrite/v1/official-option-simulation/{position_id:str}", official_option_sim_detail, methods=["GET"]),
    Route("/api/rewrite/internal/v1/official-option-simulation/receipts", official_option_sim_receipt, methods=["POST"]),
    Route("/api/rewrite/v1/membership", membership, methods=["GET"]),
    Route("/api/rewrite/v1/telegram/status", telegram_status, methods=["GET"]),
    Route("/api/rewrite/v1/settings", settings, methods=["GET"]),
    Route("/api/rewrite/v1/settings/risk", risk_settings, methods=["PUT"]),
    Route("/api/rewrite/v1/settings/telegram", telegram_preferences, methods=["PUT"]),
    Route("/api/rewrite/v1/watchlist", watchlist, methods=["GET", "POST", "PATCH", "DELETE"]),
    Route("/api/rewrite/v1/chart-drawings", chart_drawings, methods=["GET"]),
    Route("/api/rewrite/v1/chart-drawings/batch", chart_drawings, methods=["POST"]),
    Route("/api/rewrite/v1/settings/locale", locale_preference, methods=["PUT"]),
    Route("/api/rewrite/v1/settings/opening-pause", opening_pause, methods=["PUT"]),
    Route("/api/rewrite/v1/alerts", alerts, methods=["GET", "POST"]),
    Route("/api/rewrite/v1/alerts/{alert_id:str}", alert_item, methods=["DELETE"]),
    Route("/api/rewrite/v1/membership/orders", membership_orders, methods=["POST"]),
    Route("/api/rewrite/v1/membership/orders/{order_no:str}/payment-qr", membership_order_payment_qr, methods=["GET"]),
    Route("/api/rewrite/v1/membership/orders/{order_no:str}/proof", membership_order_proof, methods=["POST"]),
    Route("/api/rewrite/v1/backtests", backtests, methods=["GET", "POST"]),
    Route("/api/rewrite/v1/backtests/{job_id:str}", backtest_item, methods=["GET"]),
    Route("/api/rewrite/v1/backtests/{job_id:str}/cancel", backtest_cancel, methods=["POST"]),
    Route("/api/rewrite/v1/backtests/{job_id:str}/artifacts/{artifact_key:str}", backtest_artifact, methods=["GET"]),
    Route("/api/rewrite/internal/v1/backtest-worker/claims", worker_claim, methods=["POST"]),
    Route("/api/rewrite/internal/v1/backtest-worker/jobs/{job_id:str}/heartbeat", worker_heartbeat, methods=["POST"]),
    Route("/api/rewrite/internal/v1/backtest-worker/jobs/{job_id:str}/inputs/{artifact_key:str}", worker_input, methods=["GET"]),
    Route("/api/rewrite/internal/v1/backtest-worker/jobs/{job_id:str}/outputs/{artifact_key:str}", worker_output, methods=["PUT"]),
    Route("/api/rewrite/internal/v1/backtest-worker/jobs/{job_id:str}/complete", worker_complete, methods=["POST"]),
    Route("/api/rewrite/internal/v1/backtest-worker/jobs/{job_id:str}/fail", worker_fail, methods=["POST"]),
]

app = Starlette(
    debug=False,
    routes=routes,
    middleware=[Middleware(PaymentProofBodyLimit), Middleware(RewriteSecurityHeaders)],
    exception_handlers={
        ApiError: api_error_handler,
        ReadModelError: read_model_error_handler,
        BacktestQueueError: backtest_queue_error_handler,
        EarningsForecastUnavailable: earnings_forecast_unavailable_handler,
        OfficialOptionSimulationUnavailable: official_option_sim_unavailable_handler,
        OfficialOptionSimulationReceiverError: official_option_sim_receiver_error,
    },
)
app.state.repository = ReadOnlyLegacyRepository()
app.state.earnings_forecast_api = _build_earnings_forecast_api()
app.state.official_option_sim_api = _build_official_option_sim_api()
app.state.official_option_sim_receiver = _build_official_option_sim_receiver()
