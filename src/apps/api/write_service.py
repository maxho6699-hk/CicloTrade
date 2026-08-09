"""Narrow browser writes that reuse legacy authorization and risk controls."""

from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Any

from core.alerts import AlertService
from core.compat import UTC
from core.database import DatabaseManager, get_database
from core.plans import can
from core.user_settings import load_user_settings, merge_user_settings
from payment.order_service import (
    MANUAL_PAYMENT_METHODS,
    OrderService,
)
from payment.proof_storage import store_payment_proof, delete_payment_proof, MAX_PAYMENT_PROOF_BYTES
from payment.receiving_profile import ReceivingProfileService, payment_profile_public
from src.apps.api.read_model import BrowserIdentity
from src.apps.api.watchlists import (
    WATCHLIST_LIMIT,
    WATCHLIST_MARKETS,
    normalize_watchlist_symbol,
    normalize_watchlists,
)


RISK_LIMITS = {
    "max_position_per_symbol": (1_000.0, 1_000_000.0),
    "max_total_position": (5_000.0, 5_000_000.0),
    "max_daily_loss": (500.0, 500_000.0),
    "max_position_per_symbol_cny": (5_000.0, 7_000_000.0),
    "max_total_position_cny": (10_000.0, 35_000_000.0),
    "max_daily_loss_cny": (1_000.0, 3_500_000.0),
    "cooldown_minutes": (5, 240),
    "consecutive_loss_limit": (2, 10),
}


def _payment_claim_response(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": claim.get("id"),
        "order_no": claim.get("order_no"),
        "status": claim.get("status"),
        "attempt": claim.get("attempt"),
        "created_at": claim.get("created_at"),
    }

TELEGRAM_EVENTS = {
    "price_alert", "order_submitted", "order_filled", "risk_rejected",
    "force_liquidation", "system_exception", "stock_signal", "option_signal",
    "membership_update",
}

class BrowserWriteService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def settings(self, identity: BrowserIdentity) -> dict[str, Any]:
        stored = load_user_settings(identity.id, self.db)
        return {
            "risk": stored.get("risk") if isinstance(stored.get("risk"), dict) else {},
            "telegram_events": stored.get("tg_events") if isinstance(stored.get("tg_events"), dict) else {},
            "watchlists": normalize_watchlists(stored),
            "ui_locale": stored.get("ui_locale") if stored.get("ui_locale") in {"zh-Hant", "zh-Hans"} else None,
        }

    def update_locale(self, identity: BrowserIdentity, payload: dict[str, Any]) -> str:
        if set(payload) != {"locale"} or payload.get("locale") not in {"zh-Hant", "zh-Hans"}:
            raise ValueError("界面语言必须是 zh-Hant 或 zh-Hans。")
        locale = str(payload["locale"])
        merge_user_settings(identity.id, {"ui_locale": locale}, self.db)
        return locale

    def update_watchlist(
        self, identity: BrowserIdentity, payload: dict[str, Any], *, remove: bool = False
    ) -> dict[str, list[str]]:
        if set(payload) != {"market", "symbol"}:
            raise ValueError("自选请求字段不完整或包含未知字段。")
        market = str(payload.get("market", "")).upper()
        if market not in WATCHLIST_MARKETS:
            raise ValueError("自选市场必须是 US 或 CN。")
        symbol = normalize_watchlist_symbol(payload.get("symbol"), market)
        with self.db.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT settings_json FROM user_settings WHERE user_id=?", (identity.id,)
            ).fetchone()
            try:
                stored = json.loads(row["settings_json"]) if row else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                stored = {}
            stored = stored if isinstance(stored, dict) else {}
            watchlists = normalize_watchlists(stored)
            key = WATCHLIST_MARKETS[market]
            values = list(watchlists[key])
            if remove:
                values = [item for item in values if item != symbol]
            elif symbol not in values:
                if len(values) >= WATCHLIST_LIMIT:
                    raise ValueError(f"每个市场最多保存 {WATCHLIST_LIMIT} 个自选标的。")
                values.append(symbol)
            watchlists[key] = values
            stored["watchlists"] = watchlists
            connection.execute(
                """INSERT INTO user_settings (user_id,settings_json,updated_at) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
                (
                    identity.id,
                    json.dumps(stored, ensure_ascii=False),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
        return watchlists

    def update_risk(self, identity: BrowserIdentity, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != set(RISK_LIMITS):
            raise ValueError("风控设置字段不完整或包含未知字段。")
        normalized: dict[str, float | int] = {}
        for key, (minimum, maximum) in RISK_LIMITS.items():
            value = payload[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} 必须是数字。")
            numeric = float(value)
            if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
                raise ValueError(f"{key} 超出允许范围。")
            normalized[key] = int(numeric) if key in {"cooldown_minutes", "consecutive_loss_limit"} else numeric
        if normalized["max_position_per_symbol"] > normalized["max_total_position"]:
            raise ValueError("美股单标的上限不能超过账户总仓位上限。")
        if normalized["max_position_per_symbol_cny"] > normalized["max_total_position_cny"]:
            raise ValueError("A股单标的上限不能超过账户总仓位上限。")
        merge_user_settings(identity.id, {"risk": normalized}, self.db)
        return normalized

    def update_telegram_events(
        self, identity: BrowserIdentity, payload: dict[str, Any]
    ) -> dict[str, bool]:
        if not payload or set(payload) - TELEGRAM_EVENTS:
            raise ValueError("通知事件为空或包含未知字段。")
        if any(not isinstance(value, bool) for value in payload.values()):
            raise ValueError("通知事件开关必须是布尔值。")
        plan = identity.effective_plan
        if payload.get("stock_signal") and not can(plan, "tg_stock_signal"):
            raise PermissionError("当前会员等级不能开启正股即时建议。")
        if payload.get("option_signal") and not can(plan, "tg_option_signal"):
            raise PermissionError("当前会员等级不能开启期权即时建议。")
        existing = self.settings(identity)["telegram_events"]
        merged = {**existing, **payload}
        merge_user_settings(identity.id, {"tg_events": merged}, self.db)
        return {str(key): value is True for key, value in merged.items()}

    def list_alerts(self, identity: BrowserIdentity) -> list[dict[str, Any]]:
        return AlertService(self.db).list(identity.id)

    def create_alert(self, identity: BrowserIdentity, payload: dict[str, Any]) -> list[dict[str, Any]]:
        allowed = {"symbol", "conditions", "logic"}
        if set(payload) - allowed or not isinstance(payload.get("symbol"), str):
            raise ValueError("预警请求字段无效。")
        conditions = payload.get("conditions")
        if not isinstance(conditions, list):
            raise ValueError("conditions 必须是条件数组。")
        AlertService(self.db).create(
            identity.id,
            identity.effective_plan,
            payload["symbol"],
            conditions=conditions,
            logic=str(payload.get("logic", "AND")),
        )
        return self.list_alerts(identity)

    def create_paper_order(self, identity: BrowserIdentity, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {"symbol", "side", "quantity", "price", "instrument_type"}
        if set(payload) - allowed:
            raise ValueError("模拟订单包含未知字段。")
        quantity, price = payload.get("quantity"), payload.get("price")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValueError("quantity 必须是正整数。")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise ValueError("price 必须是正数。")
        settings = self.settings(identity)
        risk = {
            "max_position_per_symbol": 5_000,
            "max_total_position": 50_000,
            "max_daily_loss": 2_000,
            "max_position_per_symbol_cny": 35_000,
            "max_total_position_cny": 350_000,
            "max_daily_loss_cny": 14_000,
            "cooldown_minutes": 30,
            "consecutive_loss_limit": 3,
            **settings["risk"],
        }
        user_control = self.db.fetch_one(
            "SELECT opening_paused FROM user_controls WHERE user_id=?", (identity.id,)
        ) or {}
        platform_control = self.db.fetch_one(
            "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
        ) or {}
        paused = bool(user_control.get("opening_paused")) or str(
            platform_control.get("control_value", "0")
        ).lower() in {"1", "true", "yes", "on"}
        from trading.order_manager import OrderManager

        order = OrderManager(self.db).submit(
            user_id=identity.id,
            symbol=str(payload.get("symbol", "")),
            side=str(payload.get("side", "")),
            quantity=quantity,
            price=float(price),
            strategy="WEB-PAPER",
            mode="paper",
            risk_config=risk,
            paused=paused,
            live_confirmed=False,
            instrument_type=str(payload.get("instrument_type", "stock")),
        )
        return {
            key: order.get(key)
            for key in ("order_id", "symbol", "side", "quantity", "price", "status", "account_mode", "created_at")
        }

    def create_membership_order(
        self, identity: BrowserIdentity, payload: dict[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        if set(payload) != {"plan", "cycle", "method", "terms_accepted"}:
            raise ValueError("会员订单字段不完整或包含未知字段。")
        method = str(payload["method"]).strip().lower()
        if method not in MANUAL_PAYMENT_METHODS:
            raise ValueError("新订单仅支持 FPS、支付宝或微信支付人工付款。")
        order = OrderService(self.db).create_order(
            identity.id,
            str(payload["plan"]),
            str(payload["cycle"]),
            method,
            terms_accepted=payload["terms_accepted"] is True,
            idempotency_key=idempotency_key,
            source="web",
        )
        response = {
            key: order.get(key)
            for key in ("order_no", "plan_type", "billing_cycle", "amount", "currency", "pay_method", "status", "created_at", "expires_at")
        }
        response.update(
            payment_profile_public(
                ReceivingProfileService(self.db).for_order(str(order["order_no"]), identity.id)
            )
        )
        return response

    def membership_payment_qr(self, identity: BrowserIdentity, order_no: str) -> bytes:
        return ReceivingProfileService(self.db).qr_for_order(
            str(order_no).strip(), identity.id, pending_only=True
        )

    def submit_membership_proof(
        self, identity: BrowserIdentity, order_no: str, content: bytes, content_type: str
    ) -> dict[str, Any]:
        order = OrderService(self.db).get_order_for_user(identity.id, str(order_no).strip())
        if order["status"] != "pending":
            raise ValueError("只有待付款订单可以提交付款凭证。")
        if str(order["pay_method"]) not in MANUAL_PAYMENT_METHODS:
            raise ValueError("此订单不是人工付款订单。")
        existing = self.db.fetch_one(
            "SELECT * FROM manual_payment_claims WHERE order_no=? AND user_id=? AND status='submitted'",
            (str(order_no).strip(), identity.id),
        )
        if existing:
            return _payment_claim_response(existing)
        OrderService(self.db).require_payment_claim_capacity(identity.id)
        if len(content) > MAX_PAYMENT_PROOF_BYTES:
            raise ValueError("付款凭证图片必须小于 4 MB。")
        stored = store_payment_proof(content, content_type)
        try:
            claim = OrderService(self.db).submit_manual_payment_claim(
                identity.id,
                str(order_no).strip(),
                evidence_file_id=f"web:{stored.storage_key}",
                evidence_file_unique_id=stored.sha256,
                evidence_source="web",
                evidence_storage_key=stored.storage_key,
                evidence_sha256=stored.sha256,
            )
        except Exception:
            delete_payment_proof(stored.storage_key)
            raise
        if claim.get("evidence_storage_key") != stored.storage_key:
            delete_payment_proof(stored.storage_key)
        return _payment_claim_response(claim)
