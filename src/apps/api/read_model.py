"""Side-effect-free compatibility reads over the canonical legacy SQLite database."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from core.auth import AuthError, _decode_token
from core.compat import UTC
from core.broker_authorization import broker_execution_authorized
from core.plans import CAPABILITIES, PLAN_ORDER, PLANS, can, effective_plan, plan_display_name, trading_limits
from core.membership import membership_purchase_state, resolve_membership_snapshot
from core.official_paper_consumers import (
    OFFICIAL_PAPER_V2,
    active_events as official_consumer_events,
)
from core.quant_journal import OFFICIAL_PAPER_V2_INITIAL_CASH, OfficialPaperJournalV2
from core.trade_timeline import project_trade_cycles
from payment.receiving_profile import ReceivingProfileService, payment_profile_public
from src.apps.api.watchlists import normalize_watchlist_pins, normalize_watchlists


_PAPER_INTERVAL_LIMIT = 200
_PAPER_EXECUTION_LIMIT = 500
_OFFICIAL_PAPER_V2_EVENT_ID_OFFSET = 1_000_000_000
_BROKER_CAPABILITY_CATALOG = (
    {
        "key": "tiger", "display_name": "Tiger Brokers",
        "status": "limited_manual_onboarding",
        "capabilities": ("market_data", "us_stock_limit_orders"),
        # The browser has no self-service connection flow: registration is a
        # reviewed back-office process and must not be presented as connectable.
        "connection_available": False,
    },
    {
        "key": "futu", "display_name": "Futu OpenD",
        "status": "market_data_only", "capabilities": ("market_data",),
        "connection_available": False,
    },
    {
        "key": "alpaca", "display_name": "Alpaca",
        "status": "planned", "capabilities": (), "connection_available": False,
    },
    {
        "key": "ibkr", "display_name": "Interactive Brokers",
        "status": "planned", "capabilities": (), "connection_available": False,
    },
    {
        "key": "qmt", "display_name": "QMT",
        "status": "evaluating", "capabilities": (), "connection_available": False,
    },
    {
        "key": "ptrade", "display_name": "PTrade",
        "status": "evaluating", "capabilities": (), "connection_available": False,
    },
)


class ReadModelError(RuntimeError):
    pass


class ReadModelAuthError(ReadModelError):
    pass


def legacy_database_path() -> Path:
    value = os.getenv("DATABASE_URL", "sqlite:///data/tradeai.db")
    if not value.startswith("sqlite:///"):
        raise ReadModelError("rewrite compatibility API only supports SQLite")
    raw_path = value.removeprefix("sqlite:///")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    return path.resolve()


def _json_object(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_number(*values: Any) -> float | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _recommendation_contract(metadata: dict[str, Any], leg: dict[str, Any]) -> dict[str, Any]:
    contracts = metadata.get("contracts") if isinstance(metadata.get("contracts"), dict) else {}
    specific = contracts.get(str(leg.get("instrument_key"))) or contracts.get(str(leg.get("symbol")))
    action_contract = metadata.get("action_contract")
    candidates = [
        value for value in (specific, action_contract, metadata)
        if isinstance(value, dict)
    ]

    def first(key: str) -> Any:
        return next((candidate[key] for candidate in candidates if key in candidate), None)

    rationale = first("rationale") or first("reason")
    if not isinstance(rationale, str) or not rationale.strip():
        rationale = None
    else:
        rationale = rationale.strip()[:500]
    return {
        "stop_price": _optional_number(first("stop_price"), first("stop")),
        "target_price": _optional_number(first("target_price"), first("target"), first("take_profit")),
        "max_loss": _optional_number(first("max_loss"), first("risk_amount")),
        "rationale": rationale,
        "bid": _optional_number(first("bid")),
        "ask": _optional_number(first("ask")),
        "implied_volatility": _optional_number(first("implied_volatility"), first("iv")),
        "volume": _optional_number(first("volume")),
        "open_interest": _optional_number(first("open_interest"), first("oi")),
        "current_price": _optional_number(first("current_price"), first("last_price")),
        "quote_at": str(first("quote_at") or first("data_time") or "").strip() or None,
    }


def _quote_is_fresh(value: Any, *, now: datetime | None = None) -> bool:
    if not value:
        return False
    try:
        quoted_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if quoted_at.tzinfo is None:
        quoted_at = quoted_at.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - quoted_at.astimezone(UTC)
    return -timedelta(minutes=1) <= age <= timedelta(minutes=15)


def _mask_identifier(value: str) -> str:
    cleaned = str(value or "")
    return f"···· {cleaned[-4:]}" if cleaned else ""


def _project_activity(
    events: list[dict[str, Any]],
    sources: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    marks = {
        str(leg["instrument_key"]): float(leg["price"])
        for event in events
        if event.get("active") is True
        for leg in event.get("legs") or ()
    }
    projected = [
        cycle
        for instrument_type in ("stock", "option")
        for cycle in project_trade_cycles(events, instrument_type, marks=marks)
    ]
    intervals: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    for cycle in projected:
        interval_id = f"{cycle['instrument_key']}-{str(cycle['direction']).upper()}-{cycle['sequence']}"
        closed = bool(cycle.get("closed_at"))
        if closed:
            pnl = float(cycle.get("realized_pnl") or 0)
            return_value = cycle.get("return")
            result = "profit" if pnl > 1e-12 else "loss" if pnl < -1e-12 else "breakeven"
        else:
            mark = cycle.get("mark_price")
            pnl = (
                float(cycle["net_cash"])
                + float(cycle["current_quantity"]) * float(mark) * float(cycle["multiplier"])
                if mark is not None else None
            )
            return_value = (
                pnl / float(cycle["entry_notional"])
                if pnl is not None and float(cycle["entry_notional"]) > 0 else None
            )
            result = "open"
        interval_execution_ids: list[str] = []
        for index, execution in enumerate(cycle["executions"]):
            source_id = execution.get("event_id")
            source = sources.get((source_id, str(cycle["instrument_key"]))) if isinstance(source_id, int) else None
            if source is None:
                continue
            execution_id = f"{source['trade_id']}:{cycle['sequence']}:{index}"
            interval_execution_ids.append(execution_id)
            executions.append({
                "execution_id": execution_id,
                "trade_id": str(source["trade_id"]),
                "order_id": str(source["order_id"]),
                "interval_id": interval_id,
                "symbol": str(cycle["symbol"]),
                "market": str(cycle["market"]),
                "currency": str(cycle["currency"]),
                "instrument_type": str(cycle["instrument_type"]),
                "side": str(source["side"]).upper(),
                "effect": str(execution["role"]).upper(),
                "quantity": float(execution["quantity"]),
                "price": float(execution["price"]),
                "commission": float(execution["commission"]),
                "executed_at": str(execution["occurred_at"]),
                "position_after": float(execution["position_after"]),
            })
        opened_quantity = float(cycle["opened_quantity"])
        closed_quantity = float(cycle["closed_quantity"])
        multiplier = float(cycle["multiplier"])
        intervals.append({
            "interval_id": interval_id,
            "instrument_key": str(cycle["instrument_key"]),
            "instrument_type": str(cycle["instrument_type"]),
            "symbol": str(cycle["symbol"]),
            "market": str(cycle["market"]),
            "currency": str(cycle["currency"]),
            "option_expiry": cycle.get("option_expiry"),
            "option_right": cycle.get("option_right"),
            "option_strike": cycle.get("option_strike"),
            "multiplier": multiplier,
            "direction": str(cycle["direction"]).upper(),
            "opened_at": str(cycle["opened_at"]),
            "closed_at": str(cycle["closed_at"]) if cycle.get("closed_at") else None,
            "average_entry_price": (
                float(cycle["entry_notional"]) / (opened_quantity * multiplier)
                if opened_quantity and multiplier else 0
            ),
            "average_exit_price": (
                float(cycle["exit_notional"]) / (closed_quantity * multiplier)
                if closed_quantity and multiplier else None
            ),
            "average_cost": float(cycle["average_cost"]),
            "opened_quantity": opened_quantity,
            "closed_quantity": closed_quantity,
            "current_quantity": float(cycle["current_quantity"]),
            "entry_notional": float(cycle["entry_notional"]),
            "net_cash": float(cycle["net_cash"]),
            "commission": float(cycle["commission"]),
            "mark_price": float(cycle["mark_price"]) if cycle.get("mark_price") is not None else None,
            "realized_pnl": pnl if closed else None,
            "realized_return_pct": float(return_value) * 100 if closed and return_value is not None else None,
            "estimated_pnl": pnl if not closed else None,
            "estimated_return_pct": float(return_value) * 100 if not closed and return_value is not None else None,
            "status": "CLOSED" if closed else "OPEN",
            "result": result,
            "execution_ids": interval_execution_ids,
        })

    executions.sort(key=lambda item: (str(item["executed_at"]), str(item["execution_id"])), reverse=True)
    intervals.sort(key=lambda item: (str(item["opened_at"]), str(item["interval_id"])), reverse=True)
    execution_counts_by_market = {
        market: sum(1 for execution in executions if execution["market"] == market)
        for market in ("US", "CN", "HK")
    }
    execution_previews_by_market = {
        market: [
            execution for execution in executions if execution["market"] == market
        ][:_PAPER_EXECUTION_LIMIT]
        for market in ("US", "CN", "HK")
    }
    truncated = len(intervals) > _PAPER_INTERVAL_LIMIT or len(executions) > _PAPER_EXECUTION_LIMIT
    return {
        "pnl_method": "weighted_average",
        "pnl_net_of_commission": True,
        "executions": executions[:_PAPER_EXECUTION_LIMIT],
        "intervals": intervals[:_PAPER_INTERVAL_LIMIT],
        # This is deliberately computed before the public execution preview is
        # capped, so a UI never mistakes its preview length for the total.
        "execution_counts_by_market": execution_counts_by_market,
        "execution_previews_by_market": execution_previews_by_market,
        "returned_execution_limit": _PAPER_EXECUTION_LIMIT,
        "truncated": truncated,
    }


def _official_activity(events: list[dict[str, Any]]) -> dict[str, Any]:
    sources: dict[tuple[int, str], dict[str, Any]] = {}
    for event in events:
        if event.get("active") is not True:
            continue
        for leg_index, leg in enumerate(event.get("legs") or ()):
            event_id = int(event["id"])
            instrument_key = str(leg["instrument_key"])
            record_id = f"QE-{event_id}-{leg_index}"
            sources[(event_id, instrument_key)] = {
                "trade_id": record_id,
                "order_id": record_id,
                "side": "BUY" if float(leg["quantity_delta"]) > 0 else "SELL",
            }
    return _project_activity(events, sources)


class _ReadOnlyJournalAdapter:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(query, params).fetchall()]


@dataclass(frozen=True)
class BrowserIdentity:
    id: int
    display_name: str
    plan_type: str
    subscription_expire: str | None
    admin_role: str | None = None

    @property
    def effective_plan(self) -> str:
        return effective_plan(
            {"plan_type": self.plan_type, "subscription_expire": self.subscription_expire}
        )


class ReadOnlyLegacyRepository:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path).resolve() if db_path is not None else legacy_database_path()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.is_file():
            raise ReadModelError("canonical legacy database is unavailable")
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        allowed = {
            "users", "user_sessions", "quant_events", "quant_event_legs",
            "quant_equity_snapshots", "subscription_orders", "telegram_accounts",
            "official_paper_events_v2", "official_paper_event_legs_v2",
            "official_paper_equity_snapshots_v2",
            "admin_roles",
            "user_settings", "orders", "trades", "risk_log", "broker_accounts", "price_alerts",
            "price_alert_metadata", "user_controls", "platform_controls",
            "membership_entitlements",
        }
        if table not in allowed:
            raise ReadModelError("table is not part of the compatibility allowlist")
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    def _execution_snapshot(
        self, connection: sqlite3.Connection, identity: BrowserIdentity
    ) -> dict[str, Any]:
        limits = trading_limits(identity.effective_plan)
        account_limit = int(limits["auto_control_accounts"])
        global_opening_paused = True
        auto_trading_service_enabled = False
        if self._table_exists(connection, "platform_controls"):
            controls = {
                str(row["control_key"]): str(row["control_value"]).strip().lower()
                for row in connection.execute(
                    "SELECT control_key,control_value FROM platform_controls "
                    "WHERE control_key IN ('opening_paused','user_auto_trading_enabled')"
                ).fetchall()
            }
            global_opening_paused = controls.get("opening_paused", "1") in {"1", "true", "yes", "on"}
            auto_trading_service_enabled = controls.get("user_auto_trading_enabled", "0") in {
                "1", "true", "yes", "on",
            }

        user_opening_paused = False
        if self._table_exists(connection, "user_controls"):
            row = connection.execute(
                "SELECT opening_paused FROM user_controls WHERE user_id=?", (identity.id,)
            ).fetchone()
            user_opening_paused = bool(row and int(row["opening_paused"]))

        accounts: list[dict[str, Any]] = []
        if self._table_exists(connection, "broker_accounts"):
            rows = connection.execute(
                """SELECT id,provider,account_alias,external_account_id,mode,is_active,status,
                          last_checked,metadata_json
                   FROM broker_accounts WHERE user_id=? ORDER BY created_at,id""",
                (identity.id,),
            ).fetchall()
            for row in rows:
                status = str(row["status"] or "not_configured")
                active = bool(row["is_active"])
                accounts.append({
                    "id": int(row["id"]),
                    "provider": str(row["provider"]),
                    "alias": str(row["account_alias"]),
                    "mode": str(row["mode"]),
                    "status": status,
                    "authorized": broker_execution_authorized(row),
                    "active": active,
                    # Display-only diagnostic; authorization freshness comes from
                    # the shared helper's private metadata proof and is not exposed.
                    "last_checked": row["last_checked"],
                })

        accounts_used = sum(1 for account in accounts if account["active"])
        has_authorized_broker_account = any(account["authorized"] for account in accounts)
        effective_opening_paused = global_opening_paused or user_opening_paused
        can_register_broker_account = (
            auto_trading_service_enabled and account_limit > 0 and accounts_used < account_limit
        )
        can_increase_exposure = (
            account_limit > 0
            and auto_trading_service_enabled
            and has_authorized_broker_account
            and not effective_opening_paused
        )
        can_reduce_exposure = has_authorized_broker_account
        block_reasons: list[str] = []
        if account_limit <= 0:
            block_reasons.append("当前会员没有自动交易控制账号名额")
        if not auto_trading_service_enabled:
            block_reasons.append("平台自动交易服务当前暂停")
        if not has_authorized_broker_account:
            block_reasons.append("尚未授权可用的个人券商账户")
        if global_opening_paused:
            block_reasons.append("平台已暂停全部新开仓")
        if user_opening_paused:
            block_reasons.append("当前账户已暂停新开仓")
        return {
            "global_opening_paused": global_opening_paused,
            "user_opening_paused": user_opening_paused,
            "effective_opening_paused": effective_opening_paused,
            "auto_trading_service_enabled": auto_trading_service_enabled,
            "has_authorized_broker_account": has_authorized_broker_account,
            "can_register_broker_account": can_register_broker_account,
            "can_increase_exposure": can_increase_exposure,
            "can_reduce_exposure": can_reduce_exposure,
            "account_limit": account_limit,
            "accounts_used": accounts_used,
            "accounts": accounts,
            "block_reasons": block_reasons,
        }

    def execution_control(self, identity: BrowserIdentity) -> dict[str, Any]:
        with self.connection() as connection:
            return self._execution_snapshot(connection, identity)

    def authenticate(self, bearer_token: str) -> BrowserIdentity:
        try:
            payload = _decode_token(bearer_token)
        except AuthError as exc:
            raise ReadModelAuthError(str(exc)) from exc
        if payload.get("type") != "access":
            raise ReadModelAuthError("登录凭证类型无效。")
        with self.connection() as connection:
            row = connection.execute(
                """SELECT u.id,u.display_name,u.plan_type,u.subscription_expire,u.is_admin
                   FROM users u JOIN user_sessions s ON s.user_id=u.id
                   WHERE u.id=? AND u.is_active=1 AND s.session_token=? AND s.is_active=1""",
                (int(payload["sub"]), payload["sid"]),
            ).fetchone()
            resolved = None
            admin_role = None
            if row is not None:
                if bool(row["is_admin"]) and self._table_exists(connection, "admin_roles"):
                    role_row = connection.execute(
                        "SELECT role FROM admin_roles WHERE user_id=?", (int(row["id"]),)
                    ).fetchone()
                    if role_row is not None and role_row["role"] in {
                        "super_admin", "support", "finance", "research", "risk_audit"
                    }:
                        admin_role = str(role_row["role"])
                resolved = (
                    resolve_membership_snapshot(
                        connection,
                        int(row["id"]),
                        cached_plan=str(row["plan_type"] or "免费版"),
                        cached_expiry=row["subscription_expire"],
                    )
                    if self._table_exists(connection, "membership_entitlements")
                    else {"plan_type": "免费版", "subscription_expire": None}
                )
        if row is None:
            raise ReadModelAuthError("账户会话已失效，请重新登录。")
        return BrowserIdentity(
            id=int(row["id"]),
            display_name=str(row["display_name"] or "CicloTrade 用户"),
            plan_type=str((resolved or {}).get("plan_type") or row["plan_type"] or "免费版"),
            subscription_expire=(resolved or {}).get("subscription_expire")
            if resolved is not None
            else row["subscription_expire"],
            admin_role=admin_role,
        )

    def me(self, identity: BrowserIdentity) -> dict[str, Any]:
        return {
            "id": identity.id,
            "display_name": identity.display_name,
            "plan": identity.effective_plan,
            "plan_display_name": plan_display_name(identity.effective_plan),
            "subscription_expire": identity.subscription_expire,
            "admin_role": identity.admin_role,
        }

    def settings(self, identity: BrowserIdentity) -> dict[str, Any]:
        with self.connection() as connection:
            if not self._table_exists(connection, "user_settings"):
                return {
                    "risk": {},
                    "telegram_events": {},
                    "watchlists": {"us": [], "a_share": []},
                    "watchlist_pins": {"us": [], "a_share": []},
                    "ui_locale": None,
                }
            row = connection.execute(
                "SELECT settings_json FROM user_settings WHERE user_id=?", (identity.id,)
            ).fetchone()
        settings = _json_object(row["settings_json"]) if row else {}
        risk = settings.get("risk") if isinstance(settings.get("risk"), dict) else {}
        events = settings.get("tg_events") if isinstance(settings.get("tg_events"), dict) else {}
        watchlists = normalize_watchlists(settings)
        return {
            "risk": {str(key): value for key, value in risk.items() if isinstance(value, (int, float)) and not isinstance(value, bool)},
            "telegram_events": {str(key): value is True for key, value in events.items()},
            "watchlists": watchlists,
            "watchlist_pins": normalize_watchlist_pins(settings, watchlists),
            "ui_locale": settings.get("ui_locale") if settings.get("ui_locale") in {"zh-Hant", "zh-Hans"} else None,
        }

    def alerts(self, identity: BrowserIdentity) -> list[dict[str, Any]]:
        with self.connection() as connection:
            if not self._table_exists(connection, "price_alerts"):
                return []
            has_metadata = self._table_exists(connection, "price_alert_metadata")
            query = ("SELECT a.id,a.symbol,a.operator,a.target_price,a.conditions,a.logic,a.is_active,a.created_at,a.last_triggered, "
                     "m.trigger_mode,m.repeat_mode,m.expires_at,m.channels,m.notify_only "
                     "FROM price_alerts a LEFT JOIN price_alert_metadata m ON m.alert_id=a.id " if has_metadata else
                     "SELECT id,symbol,operator,target_price,conditions,logic,is_active,created_at,last_triggered, "
                     "NULL trigger_mode,NULL repeat_mode,NULL expires_at,NULL channels,NULL notify_only FROM price_alerts ")
            rows = connection.execute(query + "WHERE a.user_id=? ORDER BY a.created_at DESC LIMIT 100" if has_metadata else query + "WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (identity.id,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            # Keep persisted condition metadata bounded and do not expose delivery payloads.
            try:
                item["conditions"] = json.loads(item["conditions"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["conditions"] = []
            if item.get("channels"):
                try:
                    item["channels"] = json.loads(item["channels"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    item["channels"] = ["website"]
            else:
                item["channels"] = ["website"]
            item["notify_only"] = True
            items.append(item)
        return items

    def membership(self, identity: BrowserIdentity) -> dict[str, Any]:
        plan = identity.effective_plan
        capabilities = sorted({
            capability
            for level in PLAN_ORDER[: PLAN_ORDER.index(plan) + 1]
            for capability in CAPABILITIES[level]
        })
        limits = trading_limits(plan)
        with self.connection() as connection:
            annual_bonus_enabled = True
            if self._table_exists(connection, "platform_controls"):
                annual_bonus = connection.execute(
                    "SELECT control_value FROM platform_controls WHERE control_key='annual_bonus_enabled'"
                ).fetchone()
                if annual_bonus:
                    annual_bonus_enabled = str(annual_bonus["control_value"]).strip().lower() in {
                        "1", "true", "yes", "on"
                    }
            payment_methods = {}
            for method in ("fps", "alipay", "wechat"):
                profile = ReceivingProfileService.current_from_connection(connection, method)
                payment_methods[method] = {
                    "available": bool(profile["available"]),
                    "has_text": bool(profile.get("receiver_text")),
                    "has_qr": bool(profile.get("qr_storage_key")),
                }
            rows = connection.execute(
                """SELECT order_no,plan_type,billing_cycle,amount,currency,status,created_at,
                          paid_at,refunded_at,expires_at,pay_method,
                          (SELECT status FROM manual_payment_claims c
                           WHERE c.order_no=subscription_orders.order_no
                           ORDER BY c.id DESC LIMIT 1) AS proof_status
                   FROM subscription_orders WHERE user_id=?
                   ORDER BY created_at DESC LIMIT 50""",
                (identity.id,),
            ).fetchall()
            orders = []
            for row in rows:
                item = dict(row)
                purchase_state = membership_purchase_state(plan, str(item["plan_type"]))
                item.update(
                    {
                        "can_purchase": purchase_state["can_purchase"],
                        "purchase_action": purchase_state["purchase_action"],
                        "can_submit_proof": bool(
                            item["status"] == "pending"
                            and item.get("proof_status") != "submitted"
                            and purchase_state["can_purchase"]
                        ),
                        "blocked_reason": purchase_state["blocked_reason"],
                    }
                )
                if (
                    item["status"] == "pending"
                    and item["can_submit_proof"]
                    and str(item.get("pay_method")) in {"fps", "alipay", "wechat"}
                ):
                    snapshot = connection.execute(
                        "SELECT * FROM subscription_order_payment_receivers WHERE order_no=?",
                        (item["order_no"],),
                    ).fetchone()
                    profile = dict(snapshot) if snapshot else ReceivingProfileService.current_from_connection(
                        connection, str(item.get("pay_method"))
                    )
                    item.update(payment_profile_public(profile))
                else:
                    item["payment_instructions"] = ""
                    item["payment_qr_available"] = False
                orders.append(item)
            execution = self._execution_snapshot(connection, identity)
        return {
            "current": self.me(identity),
            "capabilities": capabilities,
            "plans": [
                {
                    "key": key,
                    "display_name": plan_display_name(key),
                    "prices": dict(PLANS[key]["prices"]),
                    "summary": PLANS[key]["summary"],
                    "features": list(PLANS[key]["features"]),
                    **membership_purchase_state(plan, key),
                }
                for key in PLAN_ORDER
            ],
            "orders": orders,
            "payment_methods": payment_methods,
            "brokerage": {
                "auto_control_account_limit": int(limits["auto_control_accounts"]),
                "accounts_used": execution["accounts_used"],
                "accounts": execution["accounts"],
                "requires_user_authorization": True,
                "short_eligibility_source": "broker",
                "subscription_auto_connects_broker": False,
                "capability_catalog": [
                    {**provider, "capabilities": list(provider["capabilities"])}
                    for provider in _BROKER_CAPABILITY_CATALOG
                ],
                "us_short": {
                    "requires_ciclotrade_manual_approval": False,
                    "requires_broker_authorization": True,
                    "requires_margin": True,
                    "requires_borrowability": True,
                },
            },
            "auto_renewal": False,
            "annual_bonus_enabled": annual_bonus_enabled,
        }

    def telegram_status(self, identity: BrowserIdentity) -> dict[str, Any]:
        with self.connection() as connection:
            if not self._table_exists(connection, "telegram_accounts"):
                return {"bound": False, "verified": False, "consented": False, "events": {}}
            account = connection.execute(
                "SELECT chat_id,is_active,revoked_at,updated_at FROM telegram_accounts WHERE user_id=?",
                (identity.id,),
            ).fetchone()
            settings_row = connection.execute(
                "SELECT settings_json,updated_at FROM user_settings WHERE user_id=?", (identity.id,)
            ).fetchone()
        settings = _json_object(settings_row["settings_json"]) if settings_row else {}
        channel = settings.get("telegram") if isinstance(settings.get("telegram"), dict) else {}
        events = settings.get("tg_events") if isinstance(settings.get("tg_events"), dict) else {}
        active = bool(account and account["is_active"] and account["revoked_at"] is None)
        verified = active and channel.get("verified") is True
        consented = verified and channel.get("consent") is True
        return {
            "bound": active,
            "verified": verified,
            "consented": consented,
            "chat_id_masked": _mask_identifier(str(account["chat_id"])) if active else "",
            "events": {str(key): value is True for key, value in events.items()},
            "updated_at": account["updated_at"] if account else None,
        }

    @staticmethod
    def _consumer_event_id(event: dict[str, Any]) -> int:
        raw_id = int(event["id"])
        return raw_id + _OFFICIAL_PAPER_V2_EVENT_ID_OFFSET if event.get("_consumer_store") == OFFICIAL_PAPER_V2 else raw_id

    def _event_rows(self, *, limit: int, cursor: int | None = None) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 100))
        with self.connection() as connection:
            adapter = _ReadOnlyJournalAdapter(connection)
            events = official_consumer_events(
                adapter,
                include_legacy=self._table_exists(connection, "quant_events"),
                include_v2=self._table_exists(connection, "official_paper_events_v2"),
            )
        rows: list[dict[str, Any]] = []
        for event in events:
            if not event.get("active") and event.get("event_type") != "reversal":
                continue
            public_id = self._consumer_event_id(event)
            if cursor is not None and public_id >= cursor:
                continue
            rows.append({
                "id": public_id,
                "_raw_event_id": int(event["id"]),
                "_consumer_store": event["_consumer_store"],
                "_metadata": event.get("metadata") or {},
                "event_type": event["event_type"],
                "strategy_name": event["strategy_name"],
                "strategy_version": event["strategy_version"],
                "corrects_event_id": event.get("corrects_event_id"),
                "occurred_at": event["occurred_at"],
                "recorded_at": event["recorded_at"],
                "active": bool(event.get("active")),
                "_legs": event.get("legs") or [],
            })
        return sorted(rows, key=lambda row: (str(row["recorded_at"]), int(row["id"])), reverse=True)[:bounded]

    def _legs_for_events(
        self, event_rows: list[dict[str, Any]], *, include_stocks: bool, include_options: bool
    ) -> dict[int, list[dict[str, Any]]]:
        output = {int(event["id"]): [] for event in event_rows}
        for event in event_rows:
            for leg in event["_legs"]:
                item = dict(leg)
                item.pop("id", None)
                item.pop("leg_no", None)
                event_id = int(event["id"])
                instrument_type = str(item["instrument_type"])
                if (instrument_type == "stock" and not include_stocks) or (
                    instrument_type == "option" and not include_options
                ):
                    item = {"instrument_type": instrument_type, "locked": True}
                output[event_id].append(item)
        return output

    def timeline(
        self, identity: BrowserIdentity, *, limit: int = 30, cursor: int | None = None
    ) -> dict[str, Any]:
        rows = self._event_rows(limit=limit, cursor=cursor)
        include_stocks = can(identity.effective_plan, "signal_web")
        include_options = can(identity.effective_plan, "tg_option_signal")
        legs = self._legs_for_events(
            rows,
            include_stocks=include_stocks,
            include_options=include_options,
        )
        items = [{
            key: value for key, value in row.items() if not key.startswith("_")
        } | {"active": bool(row["active"]), "legs": legs[int(row["id"])]} for row in rows]
        return {"items": items, "next_cursor": items[-1]["id"] if len(items) == limit else None}

    def recommendations(self, identity: BrowserIdentity, *, limit: int = 20) -> dict[str, Any]:
        rows = self._event_rows(limit=min(max(limit * 3, limit), 100))
        include_stocks = can(identity.effective_plan, "signal_web")
        include_options = can(identity.effective_plan, "tg_option_signal")
        legs_by_event = self._legs_for_events(rows, include_stocks=include_stocks, include_options=include_options)
        items = []
        for row in rows:
            event = {key: value for key, value in row.items() if not key.startswith("_")}
            event["legs"] = legs_by_event[int(event["id"])]
            if not event["active"]:
                continue
            locked_types = sorted({
                str(leg.get("instrument_type"))
                for leg in event["legs"] if leg.get("locked")
            })
            for instrument_type in locked_types:
                items.append({
                    "event_id": event["id"], "state": "locked",
                    "instrument_type": instrument_type,
                    "strategy_name": event["strategy_name"], "strategy_version": event["strategy_version"],
                    "occurred_at": event["occurred_at"],
                })
            visible_legs = [leg for leg in event["legs"] if not leg.get("locked")]
            if not visible_legs:
                continue
            for leg in visible_legs:
                delta = float(leg["quantity_delta"])
                target = float(leg["target_quantity"])
                previous = target - delta
                if previous < 0 < target:
                    position_action = "reverse_to_long"
                elif previous > 0 > target:
                    position_action = "reverse_to_short"
                elif target > 0:
                    position_action = "add_long" if delta > 0 and previous > 0 else "open_long" if delta > 0 else "reduce_long"
                elif target < 0:
                    position_action = "add_short" if delta < 0 and previous < 0 else "open_short" if delta < 0 else "reduce_short"
                elif previous < 0:
                    position_action = "close_short"
                else:
                    position_action = "close_long"
                action = (
                    "SHORT" if position_action in {"open_short", "add_short", "reverse_to_short"}
                    else "COVER" if position_action in {"reduce_short", "close_short"}
                    else "BUY" if position_action in {"open_long", "add_long", "reverse_to_long"}
                    else "EXIT" if position_action == "close_long"
                    else "REDUCE"
                )
                contract = _recommendation_contract(
                    row["_metadata"], leg
                )
                required_fields = {
                    "stop_price": contract["stop_price"],
                    "target_price": contract["target_price"],
                    "max_loss": contract["max_loss"],
                    "rationale": contract["rationale"],
                    "current_price": contract["current_price"] if (contract["current_price"] or 0) > 0 else None,
                    "quote_at": contract["quote_at"] if _quote_is_fresh(contract["quote_at"]) else None,
                }
                if leg["instrument_type"] == "option":
                    required_fields.update({
                        "option_expiry": leg.get("option_expiry"),
                        "option_right": leg.get("option_right"),
                        "option_strike": leg.get("option_strike"),
                        "bid": contract["bid"] if (contract["bid"] or 0) > 0 else None,
                        "ask": contract["ask"] if (contract["ask"] or 0) >= (contract["bid"] or float("inf")) else None,
                        "implied_volatility": contract["implied_volatility"] if (contract["implied_volatility"] or 0) > 0 else None,
                        "volume": contract["volume"] if (contract["volume"] or 0) > 0 else None,
                        "open_interest": contract["open_interest"] if (contract["open_interest"] or 0) > 0 else None,
                    })
                missing_fields = [name for name, value in required_fields.items() if value is None]
                items.append({
                    "event_id": event["id"], "state": "official", "action": action,
                    "market": leg["market"], "instrument_type": leg["instrument_type"],
                    "symbol": leg["symbol"], "currency": leg["currency"], "reference_price": leg["price"],
                    "quantity_hint": abs(delta), "quantity_delta": delta, "target_quantity": target,
                    "position_action": position_action,
                    "option_expiry": leg.get("option_expiry"), "option_right": leg.get("option_right"),
                    "option_strike": leg.get("option_strike"), "multiplier": leg.get("multiplier"),
                    "spread": (
                        contract["ask"] - contract["bid"]
                        if contract["ask"] is not None and contract["bid"] is not None else None
                    ),
                    "actionable": not missing_fields,
                    "contract_status": "complete" if not missing_fields else "incomplete",
                    "missing_fields": missing_fields,
                    **contract,
                    "strategy_name": event["strategy_name"], "strategy_version": event["strategy_version"],
                    "occurred_at": event["occurred_at"], "recorded_at": event["recorded_at"],
                })
            if len(items) >= limit:
                break
        return {"items": items[:limit], "source": "immutable_quant_journal", "fresh_marks": False}

    def performance(self, identity: BrowserIdentity, *, limit: int = 200) -> dict[str, Any]:
        bounded = max(1, min(int(limit), 500))
        ledger_key = os.getenv("TRADEAI_OFFICIAL_PAPER_V2_LEDGER_KEY", "tradeai-official-paper-v2")
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT id,captured_at,market,currency,initial_cash,cash,market_value,realized_pnl,
                           unrealized_pnl,total_equity,total_pnl,recorded_at
                   FROM official_paper_equity_snapshots_v2 WHERE ledger_key=?
                   ORDER BY captured_at DESC,id DESC LIMIT ?""",
                (ledger_key, bounded),
            ).fetchall()
        items = [dict(row) for row in reversed(rows)]
        # System snapshots are model-validation evidence, never a user's personal return.
        return {"items": items, "fresh_marks": False, "mark_source": "recorded_system_snapshot", "scope": "system_model_validation", "user_id": identity.id}

    def portfolio(self, identity: BrowserIdentity) -> dict[str, Any]:
        del identity
        ledger_key = os.getenv("TRADEAI_OFFICIAL_PAPER_V2_LEDGER_KEY", "tradeai-official-paper-v2")
        with self.connection() as connection:
            journal = OfficialPaperJournalV2(_ReadOnlyJournalAdapter(connection))
            events = journal.list_events(ledger_key)
            replay = journal.replay(ledger_key, initial_cash=OFFICIAL_PAPER_V2_INITIAL_CASH)
            snapshots = connection.execute(
                """SELECT s.* FROM official_paper_equity_snapshots_v2 s
                   JOIN (SELECT market,MAX(id) id FROM official_paper_equity_snapshots_v2
                         WHERE ledger_key=? GROUP BY market) latest ON latest.id=s.id
                   ORDER BY CASE s.market WHEN 'US' THEN 1 WHEN 'HK' THEN 2 ELSE 3 END""",
                (ledger_key,),
            ).fetchall()

        positions = [{
            "symbol": position["symbol"],
            "market": position["market"],
            "currency": position["currency"],
            "instrument_type": position["instrument_type"],
            "instrument_key": instrument_key,
            "option_expiry": position.get("option_expiry"),
            "option_right": position.get("option_right"),
            "option_strike": position.get("option_strike"),
            "multiplier": position["multiplier"],
            "quantity": position["quantity"],
            "average_price": position["average_cost"],
            "last_trade_price": position["last_price"],
            "market_value": position["market_value"],
            "unrealized_pnl": position["unrealized_pnl"],
        } for instrument_key, position in replay["positions"].items()]

        activity = _official_activity(events)
        orders = [{
            "order_id": execution["order_id"],
            "symbol": execution["symbol"],
            "market": execution["market"],
            "currency": execution["currency"],
            "instrument_type": execution["instrument_type"],
            "side": execution["side"],
            "quantity": execution["quantity"],
            "price": execution["price"],
            "status": "VERIFIED",
            "account_mode": "official",
            "created_at": execution["executed_at"],
        } for execution in activity["executions"][:100]]

        latest = {str(row["market"]): dict(row) for row in snapshots}
        currency_totals = replay.get("currencies", {})

        def account(market: str, currency: str) -> dict[str, Any]:
            snapshot = latest.get(market)
            replay_total = currency_totals.get(currency, {})
            return {
                "market": market,
                "currency": currency,
                "status": "recorded" if snapshot else "not_recorded",
                "captured_at": snapshot.get("captured_at") if snapshot else None,
                "initial_cash": snapshot.get("initial_cash") if snapshot else None,
                "cash": snapshot.get("cash") if snapshot else None,
                "market_value": snapshot.get("market_value") if snapshot else replay_total.get("market_value"),
                "realized_pnl": snapshot.get("realized_pnl") if snapshot else replay_total.get("realized_pnl"),
                "unrealized_pnl": snapshot.get("unrealized_pnl") if snapshot else replay_total.get("unrealized_pnl"),
                "total_equity": snapshot.get("total_equity") if snapshot else None,
                "total_pnl": snapshot.get("total_pnl") if snapshot else replay_total.get("total_pnl"),
            }

        return {
            "account_mode": "official",
            "scope": "ciclotrade_system_validation",
            "positions": positions,
            "orders": orders,
            "accounts": {
                "US": account("US", "USD"),
                "HK": account("HK", "HKD"),
                "CN": account("CN", "CNY"),
            },
            "fresh_marks": False,
            "mark_source": "official_paper_v2_last_recorded_price",
            "activity": activity,
        }
