# -*- coding: utf-8 -*-
"""Streamlit UI、专业版 API 与支付 Webhook 的 ASGI 入口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from core.compat import UTC
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qsl, urlencode, urlparse

from dotenv import load_dotenv
import streamlit as st
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from core.alerts import AlertService
from core.auth import AuthError, AuthService
from core.database import get_database
from core.membership import authoritative_membership_user
from core.plans import can, effective_plan, trading_limits
from core.quant_journal import QuantJournal
from core.signal_imports import SignalImportService
from core.user_settings import load_user_settings
from notification.telegram_bot import (
    answer_telegram_callback,
    configure_telegram_bot,
    copy_telegram_message,
    edit_telegram_message,
    entitled_user_target,
    send_telegram,
    send_telegram_photo,
    telegram_callback_allowed,
    telegram_configured,
    telegram_token,
)
from notification.telegram_desk import (
    TelegramDeskResponse,
    claim_telegram_callback,
    claim_telegram_update,
    consume_telegram_quota,
    telegram_desk_response,
)
from notification.telegram_outbox import dispatch_telegram_service_outbox
from notification.templates import telegram_incident, telegram_order_message
from payment.order_service import OrderService
from payment.paypal_client import PayPalClient
from scheduler.tasks import build_scheduler
from trading.order_manager import OrderManager


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
MAX_REQUEST_BODY_BYTES = 524_288
PAYPAL_WEBHOOK_RATE_LIMIT = 120
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
    "form-action 'self'; img-src 'self' data: blob: https:; font-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline' https:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: "
    "https://static.cloudflareinsights.com; "
    "connect-src 'self' ws: wss: https:; worker-src 'self' blob:; "
    "frame-src 'self' https://*.paypal.com https://*.paddle.com"
)
STREAMLIT_ROOT_RESOURCES = ("_stcore/", "static/", "media/", "component/", "app/static/")
STREAMLIT_PAGE_PREFIXES = {
    "terminal",
    "recommendations",
    "research",
    "dashboard",
    "markets",
    "strategies",
    "backtest",
    "trading",
    "monitor",
    "emergency",
    "subscription",
    "account",
    "rewards",
    "legal",
    "settings",
    "logs",
    "roadmap",
    "help",
    "admin",
    "templates",
}


def _legacy_flag_enabled(name: str) -> bool:
    """Require an explicit opt-in before exposing a legacy write/UI surface."""
    return os.getenv(name, "false").strip().lower() == "true"


def _trusted_hosts() -> list[str]:
    if os.getenv("APP_ENV", "development").strip().lower() != "production":
        return ["*"]
    configured = [
        value.strip()
        for value in os.getenv("APP_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    ]
    hosts = configured or [urlparse(os.getenv("APP_BASE_URL", "")).hostname or ""]
    if not hosts[0] or "*" in hosts:
        raise RuntimeError(
            "生产环境必须通过 APP_BASE_URL 或 APP_ALLOWED_HOSTS 配置受信任 Host。"
        )
    return hosts


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class StreamlitDeepLinkMiddleware:
    """Resolve Streamlit's relative runtime resources after a page refresh."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            first, separator, tail = scope.get("path", "").lstrip("/").partition("/")
            if first == "trading" and not _legacy_flag_enabled("TRADEAI_LEGACY_TRADING_UI_ENABLED"):
                if scope["type"] == "http":
                    await JSONResponse({"error": "Legacy trading UI is disabled."}, status_code=404)(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", ())
            }
            if (
                scope["type"] == "http"
                and scope.get("method") == "GET"
                and first in STREAMLIT_PAGE_PREFIXES
                and not separator
                and "text/html" in headers.get("accept", "")
            ):
                query = parse_qsl(
                    scope.get("query_string", b"").decode("latin-1"),
                    keep_blank_values=True,
                )
                target = "/?" + urlencode(
                    [("next", first), *((key, value) for key, value in query if key != "next")]
                )
                await RedirectResponse(target, status_code=307)(scope, receive, send)
                return
            if first in STREAMLIT_PAGE_PREFIXES and separator and (
                tail == "favicon.png" or tail.startswith(STREAMLIT_ROOT_RESOURCES)
            ):
                scope = dict(scope)
                scope["path"] = f"/{tail}"
                scope["raw_path"] = scope["path"].encode("utf-8")
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        length = request.headers.get("content-length")
        try:
            content_too_large = bool(length) and (
                int(length) < 0 or int(length) > MAX_REQUEST_BODY_BYTES
            )
        except ValueError:
            response = JSONResponse({"error": "Content-Length 无效。"}, status_code=400)
        else:
            response = (
                JSONResponse({"error": "请求内容过大。"}, status_code=413)
                if content_too_large
                else await call_next(request)
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        if os.getenv("APP_ENV", "development").strip().lower() == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        path = getattr(getattr(request, "url", None), "path", "")
        if path.startswith(("/api/", "/webhooks/", "/payments/")):
            response.headers["Cache-Control"] = "no-store"
        return response


def _api_user(request):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 4096:
        raise ApiError("缺少 Bearer Access Token。", 401)
    database = get_database()
    try:
        user = AuthService(database).verify(authorization.removeprefix("Bearer ").strip())
    except AuthError as exc:
        raise ApiError(str(exc), 401) from exc
    try:
        user = authoritative_membership_user(database, user)
    except Exception as exc:
        raise ApiError("会员权限暂时无法核验，请稍后重试。", 503) from exc
    if not can(effective_plan(user), "api"):
        raise ApiError("API 读写仅限专业版与定制版。", 403)
    return user


def _consume_api_quota(user: dict) -> None:
    """Atomically enforce the plan's advertised per-user API/minute quota."""
    # Test doubles and legacy callers may only provide an id; _api_user rejects
    # such records before this helper in the real request path.
    if not user.get("plan_type"):
        return
    limit = trading_limits(effective_plan(user)).get("api_per_minute")
    if limit is None:
        return
    if int(limit) <= 0:
        raise ApiError("当前订阅方案不具备 API 配额。", 403)
    now = datetime.now(UTC)
    key = AuthService._rate_key("api-user", str(user["id"]), "*")
    database = get_database()
    with database.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT attempts,window_started FROM auth_rate_limits WHERE rate_key=?", (key,)
        ).fetchone()
        attempts = 0
        started = now
        if row:
            try:
                existing_start = datetime.fromisoformat(row["window_started"])
            except (TypeError, ValueError):
                existing_start = now
            if existing_start.tzinfo is None:
                existing_start = existing_start.replace(tzinfo=UTC)
            if now - existing_start < timedelta(minutes=1):
                try:
                    attempts = max(0, int(row["attempts"]))
                except (TypeError, ValueError):
                    attempts = 0
                started = existing_start
        if attempts >= int(limit):
            raise ApiError("API 请求已达到当前方案的每分钟上限，请稍后再试。", 429)
        conn.execute(
            """INSERT INTO auth_rate_limits(rate_key,attempts,window_started,blocked_until)
               VALUES (?,?,?,NULL)
               ON CONFLICT(rate_key) DO UPDATE SET attempts=excluded.attempts,
               window_started=excluded.window_started,blocked_until=NULL""",
            (key, attempts + 1, started.isoformat()),
        )


def _strategy_ingest_authorized(request) -> None:
    expected = os.getenv("TRADEAI_STRATEGY_INGEST_TOKEN", "")
    if len(expected) < 32:
        raise ApiError("量化事件接收服务尚未配置。", 503)
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 4096:
        raise ApiError("缺少量化事件 Bearer Token。", 401)
    supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise ApiError("量化事件 Bearer Token 无效。", 401)


async def _limited_body(request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) < 0 or int(length) > limit:
                raise ApiError("请求内容过大。", 413)
        except ValueError as exc:
            raise ApiError("Content-Length 无效。") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise ApiError("请求内容过大。", 413)
    return bytes(body)


async def _json_object(request, limit: int = 16_384) -> dict:
    try:
        payload = json.loads((await _limited_body(request, limit)).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("请求内容必须是有效的 JSON 对象。") from exc
    if not isinstance(payload, dict):
        raise ApiError("请求内容必须是 JSON 对象。")
    return payload


async def health(request):
    del request
    return JSONResponse({"status": "ok", "app": "CicloTrade"})


async def api_me(request):
    user = _api_user(request)
    _consume_api_quota(user)
    return JSONResponse({key: user.get(key) for key in ("id", "email", "display_name", "plan_type", "subscription_expire")})


async def api_alerts(request):
    user = _api_user(request)
    _consume_api_quota(user)
    service = AlertService()
    if request.method == "GET":
        return JSONResponse({"items": service.list(user["id"])})
    payload = await _json_object(request)
    target = payload.get("target_price")
    conditions = payload.get("conditions")
    if conditions is None and (isinstance(target, bool) or not isinstance(target, (int, float))):
        raise ApiError("target_price 必须是有限正数。")
    if conditions is not None and not isinstance(conditions, list):
        raise ApiError("conditions 必须是条件对象数组。")
    try:
        service.create(
            user["id"],
            effective_plan(user),
            str(payload.get("symbol", "")),
            str(payload.get("operator", "")) if conditions is None else None,
            float(target) if conditions is None else None,
            conditions=conditions,
            logic=str(payload.get("logic", "AND")),
        )
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse({"status": "created"}, status_code=201)


async def api_orders(request):
    if request.method == "POST" and not _legacy_flag_enabled("TRADEAI_LEGACY_ORDER_WRITE_ENABLED"):
        raise ApiError("Legacy order writes are disabled.", 503)
    user = _api_user(request)
    _consume_api_quota(user)
    if request.method == "GET":
        rows = get_database().fetch_all(
            "SELECT order_id,symbol,side,quantity,price,status,account_mode,created_at FROM orders WHERE reason=? ORDER BY created_at DESC LIMIT 200",
            (f"user={user['id']}",),
        )
        return JSONResponse({"items": rows})
    payload = await _json_object(request)

    symbol, side, mode = payload.get("symbol"), payload.get("side"), payload.get("mode", "paper")
    instrument_type = payload.get("instrument_type", "stock")
    if not isinstance(instrument_type, str) or instrument_type.strip().lower() != "stock":
        raise ApiError("当前 API 仅支持正股订单；期权自动交易尚未接入券商通道。", 501)
    if not isinstance(symbol, str) or not symbol.strip():
        raise ApiError("symbol 不能为空。")
    if not isinstance(side, str) or side.upper() not in {"BUY", "SELL"}:
        raise ApiError("side 必须是 BUY 或 SELL。")
    if not isinstance(mode, str) or mode.lower() not in {"paper", "live"}:
        raise ApiError("mode 必须是 paper 或 live。")
    side, mode = side.upper(), mode.lower()
    strategy = payload.get("strategy", "API")
    if not isinstance(strategy, str) or not strategy.strip():
        raise ApiError("strategy 必须是非空字符串。")

    quantity_value, price_value = payload.get("quantity"), payload.get("price")
    if isinstance(quantity_value, bool) or not isinstance(quantity_value, (int, float)):
        raise ApiError("quantity 必须是正整数。")
    if isinstance(price_value, bool) or not isinstance(price_value, (int, float)):
        raise ApiError("price 必须是正数。")
    try:
        quantity_number, price = float(quantity_value), float(price_value)
    except (OverflowError, ValueError) as exc:
        raise ApiError("quantity 与 price 必须是有限数值。") from exc
    if not math.isfinite(quantity_number) or not quantity_number.is_integer() or quantity_number <= 0:
        raise ApiError("quantity 必须是正整数。")
    if not math.isfinite(price) or price <= 0:
        raise ApiError("price 必须是有限正数。")
    quantity = int(quantity_value)
    live_confirmed = payload.get("confirm_live") is True
    if mode == "live" and not live_confirmed:
        raise ApiError("实盘订单必须明确设置 confirm_live=true。")

    database = get_database()
    settings = database.fetch_one("SELECT settings_json FROM user_settings WHERE user_id=?", (user["id"],))
    risk = json.loads(settings["settings_json"]).get("risk", {}) if settings else {}
    user_control = database.fetch_one("SELECT opening_paused FROM user_controls WHERE user_id=?", (user["id"],)) or {}
    platform_control = database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    ) or {}
    paused = bool(user_control.get("opening_paused")) or str(platform_control.get("control_value", "0")).lower() in {
        "1", "true", "yes", "on"
    }
    try:
        order = OrderManager().submit(
            user_id=user["id"], symbol=symbol.strip().upper(), side=side,
            quantity=quantity, price=price, strategy=strategy.strip()[:80], mode=mode,
            risk_config=risk, paused=paused, live_confirmed=live_confirmed,
            instrument_type=instrument_type,
        )
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    event = "order_filled" if order.get("status") == "FILLED" else "order_submitted"
    target_chat = entitled_user_target(database, user, load_user_settings(user["id"], database), event)
    if target_chat and telegram_configured(target_chat):
        try:
            send_telegram(
                telegram_order_message(
                    mode, side, quantity, symbol.strip().upper(), price, order.get("status")
                ),
                chat_id=target_chat,
                protect_content=True,
            )
        except RuntimeError:
            database.log_system_event("WARN", "NOTIFICATION", "API 订单 Telegram 通知失败", f"user={user['id']}")
    return JSONResponse(order, status_code=201)


async def api_import_signals(request):
    user = _api_user(request)
    _consume_api_quota(user)
    payload = await _json_object(request, 262_144)
    unknown = set(payload) - {"signals"}
    if unknown:
        raise ApiError(f"請求包含未知欄位：{', '.join(sorted(unknown))}。")
    if "signals" not in payload:
        raise ApiError("請求缺少 signals 陣列。")
    try:
        result = SignalImportService().import_signals(
            int(user["id"]), effective_plan(user), payload["signals"], import_type="api"
        )
    except PermissionError as exc:
        raise ApiError(str(exc), 403) from exc
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse(result, status_code=201 if result["created"] else 200)


async def api_export_signals(request):
    user = _api_user(request)
    _consume_api_quota(user)
    raw_limit = request.query_params.get("limit", "500")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ApiError("limit 必須是整數。") from exc
    if not 1 <= limit <= 500:
        raise ApiError("limit 必須介於 1 與 500。")
    return JSONResponse(
        {
            "schema": "ciclotrade.signal.v1",
            "items": SignalImportService().export(int(user["id"]), limit),
            "disclaimer": "僅供參考，不構成投資建議",
        }
    )


async def api_quant_events(request):
    _strategy_ingest_authorized(request)
    payload = await _json_object(request, 131_072)
    allowed = {
        "source",
        "external_event_id",
        "strategy_name",
        "strategy_version",
        "legs",
        "event_type",
        "corrects_event_id",
        "occurred_at",
        "metadata",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ApiError(f"量化事件包含未知字段：{', '.join(sorted(unknown))}。")
    required = {"source", "external_event_id", "strategy_name", "strategy_version"}
    missing = sorted(required - set(payload))
    if missing:
        raise ApiError(f"量化事件缺少字段：{', '.join(missing)}。")
    try:
        event = QuantJournal().append_event(
            ledger_key=os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system"),
            **payload,
        )
    except (TypeError, ValueError) as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse(
        {
            "id": event["id"],
            "created": event["created"],
            "source": event["source"],
            "external_event_id": event["external_event_id"],
            "occurred_at": event["occurred_at"],
            "recorded_at": event["recorded_at"],
            "payload_hash": event["payload_hash"],
        },
        status_code=201 if event["created"] else 200,
    )


async def api_quant_snapshots(request):
    _strategy_ingest_authorized(request)
    payload = await _json_object(request, 32_768)
    allowed = {
        "source",
        "external_snapshot_id",
        "currency",
        "initial_cash",
        "cash",
        "market_value",
        "realized_pnl",
        "unrealized_pnl",
        "captured_at",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ApiError(f"量化净值快照包含未知字段：{', '.join(sorted(unknown))}。")
    required = allowed - {"initial_cash"}
    missing = sorted(required - set(payload))
    if missing:
        raise ApiError(f"量化净值快照缺少字段：{', '.join(missing)}。")
    try:
        snapshot = QuantJournal().append_equity_snapshot(
            ledger_key=os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system"),
            **payload,
        )
    except (TypeError, ValueError) as exc:
        raise ApiError(str(exc)) from exc
    return JSONResponse(
        {
            "id": snapshot["id"],
            "created": snapshot["created"],
            "currency": snapshot["currency"],
            "captured_at": snapshot["captured_at"],
            "recorded_at": snapshot["recorded_at"],
            "total_equity": snapshot["total_equity"],
            "total_pnl": snapshot["total_pnl"],
            "payload_hash": snapshot["payload_hash"],
        },
        status_code=201 if snapshot["created"] else 200,
    )


def _verify_paddle(body: bytes, signature: str) -> bool:
    secret = os.getenv("PADDLE_WEBHOOK_SECRET", "")
    parts = dict(item.split("=", 1) for item in signature.split(";") if "=" in item)
    timestamp, received = parts.get("ts", ""), parts.get("h1", "")
    if not secret or not timestamp or not received:
        return False
    try:
        if abs(datetime.now(UTC).timestamp() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def _paddle_transaction_matches(data: object, order: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if order.get("pay_method") != "paddle" or data.get("id") != order.get("external_id"):
        return False
    if data.get("currency_code") != order.get("currency"):
        return False
    items = data.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        return False
    price = items[0].get("price")
    if (
        not isinstance(price, dict)
        or price.get("id") != order.get("external_price_id")
        or items[0].get("quantity") != 1
    ):
        return False
    totals = (data.get("details") or {}).get("totals") if isinstance(data.get("details"), dict) else None
    if not isinstance(totals, dict) or totals.get("currency_code") != order.get("currency"):
        return False
    try:
        received = (Decimal(str(totals.get("grand_total"))) / 100).quantize(Decimal("0.01"))
        balance = (Decimal(str(totals.get("balance"))) / 100).quantize(Decimal("0.01"))
        expected = Decimal(str(order.get("amount"))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return False
    # Full entitlement requires the positive post-credit amount to be fully settled.
    return expected > 0 and received == expected and balance == 0


def _paddle_full_reversal_matches(data: object, order: dict) -> bool:
    if not isinstance(data, dict) or data.get("currency_code") != order.get("currency"):
        return False
    totals = data.get("totals")
    if not isinstance(totals, dict):
        return False
    try:
        reversed_minor = int(totals.get("total"))
        expected_minor = int(order.get("amount_minor") or round(float(order["amount"]) * 100))
    except (TypeError, ValueError):
        return False
    return reversed_minor == expected_minor


def _paypal_full_reversal_matches(resource: dict, order: dict) -> bool:
    amount = resource.get("amount")
    if _paypal_amount_matches(amount, order):
        return True
    disputed = resource.get("disputed_transactions")
    if not isinstance(disputed, list) or len(disputed) != 1 or not isinstance(disputed[0], dict):
        return False
    return _paypal_amount_matches(disputed[0].get("seller_transaction_amount"), order)


def _paypal_webhook_rate_guard() -> None:
    # ponytail: one merchant-wide bucket; split only if legitimate webhook volume reaches it.
    limiter = AuthService(get_database())
    key = limiter._rate_key("paypal-webhook", "*", "*")
    now = datetime.now(UTC)
    try:
        limiter._check_rate_limit(key, now)
        limiter._record_attempt(
            key,
            now,
            limit=PAYPAL_WEBHOOK_RATE_LIMIT,
            window=timedelta(minutes=1),
            block=timedelta(minutes=1),
        )
    except AuthError as exc:
        raise ApiError("PayPal Webhook 请求过于频繁。", 429) from exc


async def paddle_webhook(request):
    body = await _limited_body(request, 524_288)
    if not _verify_paddle(body, request.headers.get("paddle-signature", "")):
        raise ApiError("Paddle Webhook 签名无效。", 401)
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("Paddle Webhook 内容无效。") from exc
    if not isinstance(event, dict):
        raise ApiError("Paddle Webhook 内容无效。")
    data = event.get("data")
    if not isinstance(data, dict):
        raise ApiError("Paddle Webhook data 无效。")
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
        raise ApiError("Paddle event_id 无效。")
    event_type = event.get("event_type")
    if event_type in {"adjustment.created", "adjustment.updated"}:
        action = str(data.get("action", "")).lower()
        adjustment_status = str(data.get("status", "")).lower()
        transaction_id = data.get("transaction_id")
        if action not in {"refund", "chargeback"} or adjustment_status != "approved":
            return JSONResponse({"status": "ignored"})
        order = get_database().fetch_one(
            "SELECT * FROM subscription_orders WHERE external_id=? AND pay_method='paddle'",
            (transaction_id,),
        ) if isinstance(transaction_id, str) and transaction_id else None
        if not order:
            raise ApiError("Paddle 逆转无法绑定对应订单。")
        if not _paddle_full_reversal_matches(data, order):
            raise ApiError("Paddle 仅支持币种和金额匹配的全额逆转。", 409)
        reversal = {
            "verified_refund_amount_minor": int(
                order.get("final_amount_minor")
                or order.get("amount_minor")
                or round(float(order["amount"]) * 100)
            )
        }
        processed = OrderService().process_reversal(
            event_id,
            order["order_no"],
            reversal,
            f"paddle:{action}",
        )
        return JSONResponse({"status": "processed" if processed else "duplicate"})

    custom_data = data.get("custom_data")
    order_no = custom_data.get("order_no") if isinstance(custom_data, dict) else None
    mapping = {"transaction.completed": "paid", "transaction.payment_failed": "failed", "transaction.canceled": "cancelled"}
    status = mapping.get(event_type)
    if not order_no or not status:
        return JSONResponse({"status": "ignored"})
    order = get_database().fetch_one("SELECT * FROM subscription_orders WHERE order_no=?", (str(order_no),))
    if not order or order.get("pay_method") != "paddle" or data.get("id") != order.get("external_id"):
        raise ApiError("Paddle 交易无法绑定对应订单。")
    if status == "paid" and not _paddle_transaction_matches(data, order):
        raise ApiError("Paddle 交易的 Price ID、币种或金额与订单不符。")
    audit = {
        "provider": "paddle",
        "event_id": event_id,
        "event_type": event_type,
        "transaction_id": data.get("id"),
        "order_no": order_no,
    }
    processed = OrderService().process_callback(event_id, str(order_no), status, audit)
    return JSONResponse({"status": "processed" if processed else "duplicate"})


async def paypal_webhook(request):
    headers = {key.lower(): value for key, value in request.headers.items()}
    if not PayPalClient.webhook_headers_valid(headers):
        raise ApiError("PayPal Webhook 传输头无效。", 401)
    event = await _json_object(request, 524_288)
    _paypal_webhook_rate_guard()
    client = PayPalClient()
    if not await asyncio.to_thread(client.verify_webhook, headers, event):
        raise ApiError("PayPal Webhook 签名无效。", 401)
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
        raise ApiError("PayPal event id 无效。")
    resource = event.get("resource")
    if not isinstance(resource, dict):
        resource = {}
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    if not isinstance(related, dict):
        related = {}
    reversal_events = {
        "PAYMENT.CAPTURE.REFUNDED",
        "PAYMENT.CAPTURE.REVERSED",
        "CUSTOMER.DISPUTE.RESOLVED",
    }
    event_type = event.get("event_type")
    if event_type == "CUSTOMER.DISPUTE.RESOLVED":
        outcome = resource.get("dispute_outcome")
        outcome_code = outcome.get("outcome_code") if isinstance(outcome, dict) else None
        if outcome_code != "RESOLVED_BUYER_FAVOUR":
            return JSONResponse({"status": "ignored"})
    if event_type in reversal_events:
        candidates = {
            value
            for value in (related.get("order_id"), related.get("capture_id"), resource.get("id"))
            if isinstance(value, str) and value
        }
        disputed = resource.get("disputed_transactions")
        if isinstance(disputed, list):
            candidates.update(
                item.get("seller_transaction_id")
                for item in disputed
                if isinstance(item, dict) and isinstance(item.get("seller_transaction_id"), str)
            )
        placeholders = ",".join("?" for _ in candidates)
        order = get_database().fetch_one(
            f"""SELECT * FROM subscription_orders
                WHERE pay_method='paypal' AND
                (external_id IN ({placeholders}) OR external_capture_id IN ({placeholders}))
                ORDER BY id DESC LIMIT 1""",
            (*candidates, *candidates),
        ) if candidates else None
        if not order:
            raise ApiError("PayPal 逆转无法绑定对应订单。")
        if not _paypal_full_reversal_matches(resource, order):
            raise ApiError("PayPal 仅支持币种和金额匹配的全额逆转。", 409)
        reversal = {
            "verified_refund_amount_minor": int(
                order.get("final_amount_minor")
                or order.get("amount_minor")
                or round(float(order["amount"]) * 100)
            )
        }
        processed = OrderService().process_reversal(
            event_id,
            order["order_no"],
            reversal,
            f"paypal:{event_type.lower()}",
        )
        return JSONResponse({"status": "processed" if processed else "duplicate"})

    mapping = {"PAYMENT.CAPTURE.COMPLETED": "paid", "PAYMENT.CAPTURE.DENIED": "failed", "CHECKOUT.ORDER.CANCELLED": "cancelled"}
    status = mapping.get(event_type)
    external_id = related.get("order_id") or resource.get("id")
    order = get_database().fetch_one(
        "SELECT * FROM subscription_orders WHERE external_id=? AND pay_method='paypal'", (external_id,)
    ) if external_id else None
    if not order or not status:
        return JSONResponse({"status": "ignored"})
    if status == "paid" and not _paypal_amount_matches(resource.get("amount"), order):
        raise ApiError("PayPal Webhook 金额或币种与订单不符。", 400)
    audit = {
        "provider": "paypal",
        "event_id": event_id,
        "event_type": event_type,
        "external_id": external_id,
        "capture_id": resource.get("id") if status == "paid" else None,
        "order_no": order["order_no"],
    }
    processed = OrderService().process_callback(event_id, order["order_no"], status, audit)
    return JSONResponse({"status": "processed" if processed else "duplicate"})


def _paypal_amount_matches(amount: object, order: dict) -> bool:
    if not isinstance(amount, dict) or amount.get("currency_code") != order["currency"]:
        return False
    try:
        return Decimal(str(amount.get("value"))).quantize(Decimal("0.01")) == Decimal(
            str(order["amount"])
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _verified_paypal_capture_id(capture: dict, order: dict) -> str | None:
    if capture.get("id") != order["external_id"] or capture.get("status") != "COMPLETED":
        return None
    units = capture.get("purchase_units")
    if not isinstance(units, list):
        return None
    unit = next(
        (item for item in units if isinstance(item, dict) and item.get("reference_id") == order["order_no"]),
        None,
    )
    if not unit or not _paypal_amount_matches(unit.get("amount"), order):
        return None
    payments = unit.get("payments") or {}
    captures = payments.get("captures") or [] if isinstance(payments, dict) else []
    if not isinstance(captures, list):
        return None
    completed = [item for item in captures if isinstance(item, dict) and item.get("status") == "COMPLETED"]
    if len(completed) != 1 or not completed[0].get("id") or not _paypal_amount_matches(completed[0].get("amount"), order):
        return None
    return str(completed[0]["id"])


def _subscription_redirect(status: str) -> RedirectResponse:
    return RedirectResponse(f"/subscription?payment={status}", status_code=303)


async def paypal_return(request):
    token = str(request.query_params.get("token", "")).strip()
    if not token or len(token) > 128:
        return _subscription_redirect("order_not_found")
    database = get_database()
    order = database.fetch_one(
        "SELECT * FROM subscription_orders WHERE external_id=? AND pay_method='paypal' AND status='pending'",
        (token,),
    )
    if not order:
        existing = database.fetch_one(
            "SELECT status FROM subscription_orders WHERE external_id=? AND pay_method='paypal'", (token,)
        )
        return _subscription_redirect("success" if existing and existing["status"] == "paid" else "order_not_found")
    try:
        capture = PayPalClient().capture_order(order["external_id"])
        capture_id = _verified_paypal_capture_id(capture, order)
        if not capture_id:
            database.log_system_event("ERROR", "PAYMENT", "PayPal capture verification failed", order["order_no"])
            return _subscription_redirect("verification_failed")
        processed = OrderService(database).process_callback(
            f"paypal-capture-{capture_id}",
            order["order_no"],
            "paid",
            {
                "provider": "paypal",
                "capture_id": capture_id,
                "external_id": order["external_id"],
                "order_no": order["order_no"],
            },
        )
        if not processed and OrderService(database).get_order(order["order_no"])["status"] != "paid":
            return _subscription_redirect("capture_failed")
    except Exception as exc:
        database.log_system_event("ERROR", "PAYMENT", "PayPal capture failed", type(exc).__name__)
        return _subscription_redirect("capture_failed")
    return _subscription_redirect("success")


async def paypal_cancel(request):
    del request
    return _subscription_redirect("cancelled")


async def telegram_webhook(request):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    provided = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not secret or len(provided) > 256 or not hmac.compare_digest(provided, secret):
        raise ApiError("Telegram webhook 未授权。", 401)
    payload = await _json_object(request, 65_536)
    database = get_database()

    def private_message(source: object) -> tuple[str, int] | None:
        if not isinstance(source, dict):
            return None
        chat = source.get("chat")
        message_id = source.get("message_id")
        if (
            not isinstance(chat, dict)
            or chat.get("type") != "private"
            or isinstance(chat.get("id"), bool)
            or not isinstance(chat.get("id"), int)
            or chat["id"] <= 0
            or isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or not 1 <= message_id <= 2_147_483_647
        ):
            return None
        return str(chat["id"]), message_id

    async def deliver_followups(result: TelegramDeskResponse) -> None:
        for item in result.followups:
            if item.copy_from_chat_id and item.copy_message_id:
                await asyncio.to_thread(
                    copy_telegram_message,
                    item.chat_id,
                    item.copy_from_chat_id,
                    item.copy_message_id,
                )
            if item.photo_file_id:
                await asyncio.to_thread(
                    send_telegram_photo,
                    item.message,
                    item.photo_file_id,
                    item.chat_id,
                    buttons=item.buttons,
                    protect_content=True,
                )
            else:
                await asyncio.to_thread(
                    send_telegram,
                    item.message,
                    item.chat_id,
                    parse_mode="HTML",
                    buttons=item.buttons,
                    protect_content=True,
                )

    async def deliver_service_outbox() -> None:
        try:
            await asyncio.to_thread(dispatch_telegram_service_outbox, database, 20)
        except Exception as exc:
            database.log_system_event(
                "WARN", "TELEGRAM", "Telegram 服务通知队列暂时失败", type(exc).__name__
            )

    update_id = payload.get("update_id")
    if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
        update_id = None
    message = payload.get("message")
    if isinstance(message, dict):
        destination = private_message(message)
        text = message.get("text") if isinstance(message.get("text"), str) else ""
        caption = message.get("caption") if isinstance(message.get("caption"), str) else ""
        photos = message.get("photo")
        photo = None
        if isinstance(photos, list) and photos:
            candidate = photos[-1]
            if (
                isinstance(candidate, dict)
                and isinstance(candidate.get("file_id"), str)
                and isinstance(candidate.get("file_unique_id"), str)
                and 1 <= len(candidate["file_id"]) <= 256
                and 1 <= len(candidate["file_unique_id"]) <= 256
            ):
                photo = {
                    "file_id": candidate["file_id"],
                    "file_unique_id": candidate["file_unique_id"],
                }
        value = text.strip() or caption.strip() or ("photo" if photo else "")
        if destination is None or not value or len(value) > 512:
            return JSONResponse({"ok": True})
        chat_id, message_id = destination
        update_fingerprint = f"{value}:{photo.get('file_unique_id', '') if photo else ''}"
        if update_id is not None and not claim_telegram_update(database, update_id, chat_id, update_fingerprint):
            return JSONResponse({"ok": True})
        if not consume_telegram_quota(database, chat_id, "photo" if photo else value):
            return JSONResponse({"ok": True})
        result = await asyncio.to_thread(
            telegram_desk_response,
            database,
            chat_id,
            value,
            message_id=message_id,
            update_id=update_id,
            photo=photo,
        )
        try:
            await asyncio.to_thread(
                send_telegram,
                result.message,
                chat_id,
                parse_mode="HTML",
                buttons=result.keyboard,
                protect_content=True,
            )
            await deliver_followups(result)
            await deliver_service_outbox()
        except RuntimeError as exc:
            database.log_system_event("WARN", "TELEGRAM", "Telegram 设置回覆失败", str(exc)[:500])
        return JSONResponse({"ok": True})

    callback = payload.get("callback_query")
    if not isinstance(callback, dict):
        return JSONResponse({"ok": True})
    callback_id = callback.get("id")
    destination = private_message(callback.get("message"))
    data = callback.get("data")
    actor = callback.get("from")
    if (
        not isinstance(callback_id, str)
        or not 1 <= len(callback_id) <= 128
        or destination is None
        or not isinstance(actor, dict)
        or isinstance(actor.get("id"), bool)
        or not isinstance(actor.get("id"), int)
        or actor["id"] <= 0
        or str(actor["id"]) != destination[0]
        or not telegram_callback_allowed(data)
    ):
        return JSONResponse({"ok": True})
    chat_id, message_id = destination
    try:
        if not consume_telegram_quota(database, chat_id, data):
            await asyncio.to_thread(answer_telegram_callback, callback_id, "操作太频繁，请稍后再试。")
            return JSONResponse({"ok": True})
        if update_id is not None and not claim_telegram_update(database, update_id, chat_id, data):
            await asyncio.to_thread(answer_telegram_callback, callback_id, "此操作已经处理。")
            return JSONResponse({"ok": True})
        if not claim_telegram_callback(database, callback_id, chat_id):
            await asyncio.to_thread(answer_telegram_callback, callback_id, "此操作已经处理。")
            return JSONResponse({"ok": True})
        await asyncio.to_thread(answer_telegram_callback, callback_id)
        result = await asyncio.to_thread(
            telegram_desk_response,
            database,
            chat_id,
            data,
            callback=True,
            message_id=message_id,
            update_id=update_id,
        )
        await asyncio.to_thread(
            edit_telegram_message,
            chat_id,
            message_id,
            result.message,
            buttons=result.keyboard,
            parse_mode="HTML",
        )
        await deliver_followups(result)
        await deliver_service_outbox()
    except RuntimeError as exc:
        database.log_system_event("WARN", "TELEGRAM", "Telegram callback 回覆失败", str(exc)[:500])
    return JSONResponse({"ok": True})


async def api_error_handler(request, exc: ApiError):
    del request
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def on_script_error(exc: Exception):
    incident_id = secrets.token_urlsafe(9)
    get_database().log_system_event(
        "ERROR",
        "STREAMLIT",
        "页面运行异常",
        f"incident={incident_id}; type={type(exc).__name__}; detail={str(exc)[:1000]}",
    )
    if os.getenv("EXTERNAL_ALERTS_ENABLED", "false").lower() == "true" and telegram_configured():
        try:
            send_telegram(telegram_incident(incident_id, type(exc).__name__))
        except RuntimeError:
            pass
    return False


@asynccontextmanager
async def lifespan(app):
    del app
    scheduler = None
    if telegram_token():
        try:
            await asyncio.to_thread(configure_telegram_bot)
        except RuntimeError as exc:
            get_database().log_system_event(
                "WARN", "TELEGRAM", "Telegram Bot 启动配置失败", str(exc)[:500]
            )
    if os.getenv("SCHEDULER_ENABLED", "false").lower() == "true":
        scheduler = build_scheduler()
        scheduler.start()
    yield {"ready": True}
    if scheduler:
        scheduler.shutdown(wait=False)


app = st.App(
    str(ROOT / "app.py"),
    routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/v1/me", api_me, methods=["GET"]),
        Route("/api/v1/alerts", api_alerts, methods=["GET", "POST"]),
        Route("/api/v1/orders", api_orders, methods=["GET", "POST"]),
        Route("/api/v1/import/signals", api_import_signals, methods=["POST"]),
        Route("/api/v1/export/signals", api_export_signals, methods=["GET"]),
        Route("/api/v1/quant/events", api_quant_events, methods=["POST"]),
        Route("/api/v1/quant/snapshots", api_quant_snapshots, methods=["POST"]),
        Route("/webhooks/paddle", paddle_webhook, methods=["POST"]),
        Route("/webhooks/paypal", paypal_webhook, methods=["POST"]),
        Route("/webhooks/telegram", telegram_webhook, methods=["POST"]),
        Route("/payments/paypal/return", paypal_return, methods=["GET"]),
        Route("/payments/paypal/cancel", paypal_cancel, methods=["GET"]),
    ],
    middleware=[
        Middleware(StreamlitDeepLinkMiddleware),
        Middleware(SecurityHeadersMiddleware),
        Middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts()),
    ],
    lifespan=lifespan,
    exception_handlers={ApiError: api_error_handler},
    on_script_error=on_script_error,
)
