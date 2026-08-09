"""Side-effect-free compatibility reads over the canonical legacy SQLite database."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from core.auth import AuthError, _decode_token
from core.plans import CAPABILITIES, PLAN_ORDER, PLANS, can, effective_plan, plan_display_name
from core.trade_timeline import project_trade_cycles
from payment.receiving_profile import ReceivingProfileService, payment_profile_public
from src.apps.api.watchlists import normalize_watchlists


_PAPER_INTERVAL_LIMIT = 200
_PAPER_EXECUTION_LIMIT = 500


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


def _mask_identifier(value: str) -> str:
    cleaned = str(value or "")
    return f"···· {cleaned[-4:]}" if cleaned else ""


def _paper_market(symbol: str) -> str:
    return "CN" if symbol.isdigit() and len(symbol) == 6 else "US"


def _paper_activity(rows: list[sqlite3.Row]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    sources: dict[int, dict[str, Any]] = {}
    marks: dict[str, float] = {}
    for raw in rows:
        row = dict(raw)
        trade_id = int(row["id"])
        symbol = str(row["symbol"]).upper()
        market = _paper_market(symbol)
        currency = "CNY" if market == "CN" else "USD"
        side = str(row["side"]).upper()
        quantity = float(row["quantity"])
        price = float(row["price"])
        instrument_key = f"{market}:stock:{symbol}"
        sources[trade_id] = row
        marks[instrument_key] = price
        events.append({
            "id": trade_id,
            "active": True,
            "occurred_at": str(row["trade_time"]),
            "recorded_at": str(row["trade_time"]),
            "strategy_name": str(row.get("strategy_name") or "WEB-PAPER"),
            "legs": [{
                "instrument_key": instrument_key,
                "market": market,
                "instrument_type": "stock",
                "symbol": symbol,
                "currency": currency,
                "quantity_delta": quantity if side == "BUY" else -quantity,
                "price": price,
                "multiplier": 1,
                "commission": float(row["commission"] or 0),
            }],
        })

    projected = project_trade_cycles(events, "stock", marks=marks)
    intervals: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    for cycle in projected:
        interval_id = f"{cycle['symbol']}-{str(cycle['direction']).upper()}-{cycle['sequence']}"
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
            source = sources.get(source_id) if isinstance(source_id, int) else None
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
        intervals.append({
            "interval_id": interval_id,
            "symbol": str(cycle["symbol"]),
            "market": str(cycle["market"]),
            "currency": str(cycle["currency"]),
            "direction": str(cycle["direction"]).upper(),
            "opened_at": str(cycle["opened_at"]),
            "closed_at": str(cycle["closed_at"]) if cycle.get("closed_at") else None,
            "average_entry_price": (
                float(cycle["entry_notional"]) / opened_quantity if opened_quantity else 0
            ),
            "average_exit_price": (
                float(cycle["exit_notional"]) / closed_quantity if closed_quantity else None
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
    truncated = len(intervals) > _PAPER_INTERVAL_LIMIT or len(executions) > _PAPER_EXECUTION_LIMIT
    return {
        "pnl_method": "weighted_average",
        "pnl_net_of_commission": True,
        "executions": executions[:_PAPER_EXECUTION_LIMIT],
        "intervals": intervals[:_PAPER_INTERVAL_LIMIT],
        "returned_execution_limit": _PAPER_EXECUTION_LIMIT,
        "truncated": truncated,
    }


@dataclass(frozen=True)
class BrowserIdentity:
    id: int
    display_name: str
    plan_type: str
    subscription_expire: str | None

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
            "user_settings", "orders", "trades", "risk_log", "broker_accounts", "price_alerts",
        }
        if table not in allowed:
            raise ReadModelError("table is not part of the compatibility allowlist")
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    def authenticate(self, bearer_token: str) -> BrowserIdentity:
        try:
            payload = _decode_token(bearer_token)
        except AuthError as exc:
            raise ReadModelAuthError(str(exc)) from exc
        if payload.get("type") != "access":
            raise ReadModelAuthError("登录凭证类型无效。")
        with self.connection() as connection:
            row = connection.execute(
                """SELECT u.id,u.display_name,u.plan_type,u.subscription_expire
                   FROM users u JOIN user_sessions s ON s.user_id=u.id
                   WHERE u.id=? AND u.is_active=1 AND s.session_token=? AND s.is_active=1""",
                (int(payload["sub"]), payload["sid"]),
            ).fetchone()
        if row is None:
            raise ReadModelAuthError("账户会话已失效，请重新登录。")
        return BrowserIdentity(
            id=int(row["id"]),
            display_name=str(row["display_name"] or "TradeAI 用户"),
            plan_type=str(row["plan_type"] or "免费版"),
            subscription_expire=row["subscription_expire"],
        )

    def me(self, identity: BrowserIdentity) -> dict[str, Any]:
        return {
            "id": identity.id,
            "display_name": identity.display_name,
            "plan": identity.effective_plan,
            "plan_display_name": plan_display_name(identity.effective_plan),
            "subscription_expire": identity.subscription_expire,
        }

    def settings(self, identity: BrowserIdentity) -> dict[str, Any]:
        with self.connection() as connection:
            if not self._table_exists(connection, "user_settings"):
                return {
                    "risk": {},
                    "telegram_events": {},
                    "watchlists": {"us": [], "a_share": []},
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
            "ui_locale": settings.get("ui_locale") if settings.get("ui_locale") in {"zh-Hant", "zh-Hans"} else None,
        }

    def alerts(self, identity: BrowserIdentity) -> list[dict[str, Any]]:
        with self.connection() as connection:
            if not self._table_exists(connection, "price_alerts"):
                return []
            rows = connection.execute(
                "SELECT id,symbol,operator,target_price,conditions,logic,is_active,created_at,last_triggered "
                "FROM price_alerts WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (identity.id,)
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            # Keep persisted condition metadata bounded and do not expose delivery payloads.
            try:
                item["conditions"] = json.loads(item["conditions"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                item["conditions"] = []
            items.append(item)
        return items

    def membership(self, identity: BrowserIdentity) -> dict[str, Any]:
        plan = identity.effective_plan
        capabilities = sorted(
            capability
            for level in PLAN_ORDER[: PLAN_ORDER.index(plan) + 1]
            for capability in CAPABILITIES[level]
        )
        with self.connection() as connection:
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
                if item["status"] == "pending" and str(item.get("pay_method")) in {"fps", "alipay", "wechat"}:
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
        return {
            "current": self.me(identity),
            "capabilities": capabilities,
            "plans": [
                {
                    "key": key,
                    "display_name": plan_display_name(key),
                    "prices": value["prices"],
                    "summary": value["summary"],
                    "features": list(value["features"]),
                }
                for key, value in PLANS.items()
            ],
            "orders": orders,
            "payment_methods": payment_methods,
            "auto_renewal": False,
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

    def _event_rows(self, *, limit: int, cursor: int | None = None) -> list[sqlite3.Row]:
        bounded = max(1, min(int(limit), 100))
        cursor_clause = " AND e.id<?" if cursor is not None else ""
        params: tuple[Any, ...] = (
            os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system"),
            *((cursor,) if cursor is not None else ()),
            bounded,
        )
        with self.connection() as connection:
            return connection.execute(
                f"""SELECT e.id,e.source,e.event_type,e.strategy_name,e.strategy_version,
                           e.corrects_event_id,e.occurred_at,e.recorded_at,
                           CASE WHEN e.event_type='reversal' OR EXISTS(
                             SELECT 1 FROM quant_events later WHERE later.corrects_event_id=e.id
                           ) THEN 0 ELSE 1 END active
                    FROM quant_events e WHERE e.ledger_key=?{cursor_clause}
                    ORDER BY e.id DESC LIMIT ?""",
                params,
            ).fetchall()

    def _legs_for_events(
        self, event_ids: list[int], *, include_stocks: bool, include_options: bool
    ) -> dict[int, list[dict[str, Any]]]:
        output = {event_id: [] for event_id in event_ids}
        if not event_ids:
            return output
        placeholders = ",".join("?" for _ in event_ids)
        with self.connection() as connection:
            rows = connection.execute(
                f"""SELECT event_id,market,instrument_type,symbol,currency,option_expiry,
                            option_right,option_strike,target_quantity,quantity_delta,price,multiplier
                     FROM quant_event_legs WHERE event_id IN ({placeholders})
                     ORDER BY event_id,leg_no""",
                tuple(event_ids),
            ).fetchall()
        for row in rows:
            item = dict(row)
            instrument_type = str(item["instrument_type"])
            if (instrument_type == "stock" and not include_stocks) or (
                instrument_type == "option" and not include_options
            ):
                item = {"instrument_type": instrument_type, "locked": True}
            output[int(row["event_id"])].append(item)
        return output

    def timeline(
        self, identity: BrowserIdentity, *, limit: int = 30, cursor: int | None = None
    ) -> dict[str, Any]:
        rows = self._event_rows(limit=limit, cursor=cursor)
        include_stocks = can(identity.effective_plan, "signal_web")
        include_options = can(identity.effective_plan, "tg_option_signal")
        legs = self._legs_for_events(
            [int(row["id"]) for row in rows],
            include_stocks=include_stocks,
            include_options=include_options,
        )
        items = [{**dict(row), "active": bool(row["active"]), "legs": legs[int(row["id"])]} for row in rows]
        return {"items": items, "next_cursor": items[-1]["id"] if len(items) == limit else None}

    def recommendations(self, identity: BrowserIdentity, *, limit: int = 20) -> dict[str, Any]:
        timeline = self.timeline(identity, limit=min(max(limit * 3, limit), 100))
        items = []
        for event in timeline["items"]:
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
                action = "BUY" if delta > 0 else "EXIT" if target == 0 else "REDUCE"
                items.append({
                    "event_id": event["id"], "state": "official", "action": action,
                    "market": leg["market"], "instrument_type": leg["instrument_type"],
                    "symbol": leg["symbol"], "currency": leg["currency"], "reference_price": leg["price"],
                    "strategy_name": event["strategy_name"], "strategy_version": event["strategy_version"],
                    "occurred_at": event["occurred_at"], "recorded_at": event["recorded_at"],
                })
            if len(items) >= limit:
                break
        return {"items": items[:limit], "source": "immutable_quant_journal", "fresh_marks": False}

    def performance(self, identity: BrowserIdentity, *, limit: int = 200) -> dict[str, Any]:
        del identity
        bounded = max(1, min(int(limit), 500))
        ledger_key = os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT id,captured_at,currency,initial_cash,cash,market_value,realized_pnl,
                          unrealized_pnl,total_equity,total_pnl,recorded_at
                   FROM quant_equity_snapshots WHERE ledger_key=?
                   ORDER BY captured_at DESC,id DESC LIMIT ?""",
                (ledger_key, bounded),
            ).fetchall()
        items = [dict(row) for row in reversed(rows)]
        return {"items": items, "fresh_marks": False, "mark_source": "recorded_snapshot"}

    def portfolio(self, identity: BrowserIdentity) -> dict[str, Any]:
        reason = f"user={identity.id}"
        with self.connection() as connection:
            trades = connection.execute(
                """SELECT t.id,t.trade_id,t.order_id,t.symbol,t.side,t.quantity,t.price,
                          t.commission,t.trade_time,o.strategy_name
                   FROM trades t JOIN orders o ON o.order_id=t.order_id
                   WHERE o.reason=? AND o.account_mode='paper' ORDER BY t.trade_time,t.id""",
                (reason,),
            ).fetchall()
            orders = connection.execute(
                """SELECT order_id,symbol,side,quantity,price,status,account_mode,created_at
                   FROM orders WHERE reason=? AND account_mode='paper'
                   ORDER BY created_at DESC LIMIT 100""",
                (reason,),
            ).fetchall()
        positions: dict[str, dict[str, float | str]] = {}
        realized = 0.0
        for row in trades:
            symbol, side = str(row["symbol"]), str(row["side"]).upper()
            signed = float(row["quantity"]) * (1 if side == "BUY" else -1)
            price = float(row["price"])
            position = positions.setdefault(symbol, {"symbol": symbol, "quantity": 0.0, "average_price": 0.0, "last_trade_price": price})
            quantity = float(position["quantity"])
            average = float(position["average_price"])
            if quantity == 0 or quantity * signed > 0:
                next_quantity = quantity + signed
                position["average_price"] = (abs(quantity) * average + abs(signed) * price) / abs(next_quantity)
            else:
                closed = min(abs(quantity), abs(signed))
                realized += closed * (price - average) * (1 if quantity > 0 else -1)
                next_quantity = quantity + signed
                if not next_quantity:
                    position["average_price"] = 0.0
                elif quantity * next_quantity < 0:
                    position["average_price"] = price
            position["quantity"] = next_quantity
            position["last_trade_price"] = price
        active = []
        for position in positions.values():
            quantity = float(position["quantity"])
            if abs(quantity) < 1e-12:
                continue
            price = float(position["last_trade_price"])
            average = float(position["average_price"])
            active.append({**position, "market_value": quantity * price, "unrealized_pnl": quantity * (price - average)})
        return {
            "account_mode": "paper", "positions": active, "orders": [dict(row) for row in orders],
            "realized_pnl": realized, "fresh_marks": False, "mark_source": "last_recorded_trade",
            "activity": _paper_activity(trades),
        }
