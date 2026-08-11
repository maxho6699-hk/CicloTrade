# -*- coding: utf-8 -*-
"""防重复、风控先行的模拟盘/实盘订单入口。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import json
import math
import os
import re
import secrets
from typing import Any

from core.broker_authorization import broker_execution_authorized
from core.database import DatabaseManager, get_database
from core.membership import authoritative_membership_row, authoritative_membership_user
from core.plans import can, effective_plan, trading_limits
from core.strategy_registry import StrategyRegistry, _FREE_STRATEGY_KEYS
from trading.risk_filter import validate_order
from trading.tiger_api import (
    TigerAPI,
    TigerAPIRejected,
    TigerSendClaim,
    TigerSubmissionUnknown,
    _new_tiger_send_claim,
)


def user_auto_trading_open(database: DatabaseManager) -> bool:
    row = database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='user_auto_trading_enabled'"
    )
    return bool(row and str(row["control_value"]).lower() in {"1", "true", "yes", "on"})


def trade_ledger_state(trades: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Calculate user-scoped open exposure, realized P/L, and loss streak."""
    current_time = now or datetime.now(UTC)
    positions: dict[str, dict[str, float]] = {}
    realized_today = 0.0
    consecutive_losses = 0
    last_loss_at: datetime | None = None
    cash_change = 0.0

    for trade in trades:
        symbol = str(trade["symbol"]).upper()
        price = float(trade["price"])
        signed = float(trade["quantity"]) * (1 if str(trade["side"]).upper() == "BUY" else -1)
        commission = float(trade.get("commission") or 0)
        cash_change -= signed * price + commission
        position = positions.setdefault(symbol, {"quantity": 0.0, "average": 0.0, "last_price": price})
        quantity, average = position["quantity"], position["average"]
        timestamp = datetime.fromisoformat(str(trade["trade_time"]))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        closing_pnl: float | None = None
        if quantity == 0 or quantity * signed > 0:
            new_quantity = quantity + signed
            position["average"] = (
                (abs(quantity) * average + abs(signed) * price) / abs(new_quantity)
                if new_quantity
                else 0.0
            )
        else:
            closed = min(abs(quantity), abs(signed))
            closing_pnl = closed * (price - average) * (1 if quantity > 0 else -1)
            new_quantity = quantity + signed
            if not new_quantity:
                position["average"] = 0.0
            elif quantity * new_quantity < 0:
                position["average"] = price
            if closing_pnl < 0:
                consecutive_losses += 1
                last_loss_at = timestamp
            else:
                consecutive_losses = 0
                last_loss_at = None

        position["quantity"] = new_quantity
        position["last_price"] = price
        if timestamp.astimezone(UTC).date() == current_time.astimezone(UTC).date():
            realized_today += (closing_pnl or 0.0) - commission

    exposures = {
        symbol: abs(values["quantity"]) * values["last_price"]
        for symbol, values in positions.items()
    }
    return {
        "positions": {symbol: values["quantity"] for symbol, values in positions.items()},
        "average_costs": {symbol: values["average"] for symbol, values in positions.items()},
        "exposures": exposures,
        "total_exposure": sum(exposures.values()),
        "cash_change": cash_change,
        "daily_pnl": realized_today,
        "consecutive_losses": consecutive_losses,
        "last_loss_at": last_loss_at,
    }


def derive_execution_slices(current_quantity: float, side: str, quantity: int) -> list[dict[str, Any]]:
    """Translate a simple BUY/SELL instruction into auditable position slices."""
    normalized_side = str(side).strip().upper()
    if normalized_side not in {"BUY", "SELL"} or int(quantity) <= 0:
        raise ValueError("订单方向和数量必须有效。")
    current = float(current_quantity)
    remaining = int(quantity)
    slices: list[dict[str, Any]] = []

    if normalized_side == "SELL" and current > 0:
        closing = min(int(current), remaining)
        if closing:
            slices.append({"action": "close_long", "side": "SELL", "quantity": closing})
            remaining -= closing
        if remaining:
            slices.append({"action": "open_short", "side": "SELL", "quantity": remaining})
    elif normalized_side == "BUY" and current < 0:
        closing = min(int(abs(current)), remaining)
        if closing:
            slices.append({"action": "close_short", "side": "BUY", "quantity": closing})
            remaining -= closing
        if remaining:
            slices.append({"action": "open_long", "side": "BUY", "quantity": remaining})
    else:
        slices.append({
            "action": "open_long" if normalized_side == "BUY" else "open_short",
            "side": normalized_side,
            "quantity": remaining,
        })
    return slices


class OrderManager:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def _revalidate_live_intent(
        self,
        *,
        order_id: str,
        user_id: int,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        strategy: str,
        expected_account_id: str | None = None,
        claim: bool = False,
    ) -> str | None:
        """Re-read mutable execution authority immediately before an external side effect."""
        rejection: str | None = None
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            intent = conn.execute(
                """SELECT reason,account_mode,status,symbol,side,quantity,price,strategy_name
                   FROM orders WHERE order_id=?""",
                (order_id,),
            ).fetchone()
            intent_status = str(intent["status"] or "").strip().upper() if intent else ""
            if not intent or (
                str(intent["reason"] or "") != f"user={user_id}"
                or str(intent["account_mode"] or "").strip().lower() != "live"
                or str(intent["status"] or "").strip().upper() != "PENDING"
                or str(intent["symbol"] or "").strip().upper() != symbol
                or str(intent["side"] or "").strip().upper() != side
                or int(intent["quantity"] or 0) != int(quantity)
                or float(intent["price"] or 0) != float(price)
                or str(intent["strategy_name"] or "") != strategy
            ):
                rejection = "实盘订单意图已失效，订单未发送。"
            else:
                trades = [
                    dict(row)
                    for row in conn.execute(
                        """SELECT t.symbol,t.side,t.quantity,t.price,t.commission,t.trade_time
                           FROM trades t JOIN orders o ON o.order_id=t.order_id
                           WHERE o.reason=? AND o.account_mode='live' ORDER BY t.trade_time,t.id""",
                        (f"user={user_id}",),
                    )
                ]
                current_state = trade_ledger_state(trades)
                current_quantity = float(current_state["positions"].get(symbol, 0))
                current_slices = derive_execution_slices(current_quantity, side, quantity)
                has_opening_now = any(
                    item["action"] in {"open_long", "open_short"} for item in current_slices
                )
                current_user = conn.execute(
                    """SELECT id,plan_type,subscription_expire,is_admin
                       FROM users WHERE id=? AND is_active=1""",
                    (user_id,),
                ).fetchone()
                operator_id = os.getenv("TRADEAI_LIVE_OPERATOR_USER_ID", "").strip()
                if not current_user or not bool(current_user["is_admin"]) or str(user_id) != operator_id:
                    rejection = "实盘操作员授权已失效，订单未发送。"
                broker_rows = conn.execute(
                    """SELECT provider,is_active,mode,status,external_account_id,metadata_json
                       FROM broker_accounts WHERE user_id=?""",
                    (user_id,),
                ).fetchall() if rejection is None else []
                if rejection is None and not any(
                    broker_execution_authorized(row, expected_account_id) for row in broker_rows
                ):
                    rejection = "Tiger 实盘执行授权已失效，订单未发送。"
                elif rejection is None and os.getenv("TIGER_ENV", "paper").strip().lower() != "live":
                    rejection = "Tiger 实盘环境已失效，订单未发送。"
                elif rejection is None and has_opening_now:
                    platform_pause = conn.execute(
                        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
                    ).fetchone()
                    user_pause = conn.execute(
                        "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
                    ).fetchone()
                    opening_paused = (
                        platform_pause is None
                        or str(platform_pause[0]).lower() in {"1", "true", "yes", "on"}
                        or bool(user_pause and int(user_pause[0] or 0))
                    )
                    if opening_paused:
                        rejection = "新开仓在发送前已暂停，订单未发送。"
                    service_control = conn.execute(
                        "SELECT control_value FROM platform_controls WHERE control_key='user_auto_trading_enabled'"
                    ).fetchone()
                    if rejection is None and (
                        not service_control
                        or str(service_control[0]).lower() not in {"1", "true", "yes", "on"}
                    ):
                        rejection = "平台自动交易开仓服务已关闭，订单未发送。"
                    settings_row = conn.execute(
                        "SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)
                    ).fetchone()
                    try:
                        settings = json.loads(settings_row[0]) if settings_row else {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        settings = {}
                    if rejection is None and (
                        not isinstance(settings, dict) or settings.get("live_auto_enabled") is not True
                    ):
                        rejection = "用户实盘自动交易开关已关闭，订单未发送。"
                    assert current_user is not None
                    current_plan = effective_plan(authoritative_membership_row(conn, current_user))
                    if rejection is None and int(trading_limits(current_plan)["auto_control_accounts"]) <= 0:
                        rejection = "当前会员实盘自动交易控制账号名额已失效，订单未发送。"
                    if rejection is None:
                        strategy_row = conn.execute(
                            """SELECT strategy_key,family,is_active,rules_json
                               FROM strategy_definitions WHERE name=? ORDER BY id LIMIT 1""",
                            (strategy.strip(),),
                        ).fetchone()
                        if strategy_row:
                            strategy_access = bool(strategy_row["is_active"])
                            family = str(strategy_row["family"] or "")
                            if strategy_access and family == "option":
                                try:
                                    rules = json.loads(strategy_row["rules_json"] or "{}")
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    rules = {}
                                leg_count = int(rules.get("leg_count", 1) or 1)
                                strategy_access = can(current_plan, "option_strategy") and (
                                    leg_count <= 1 or can(current_plan, "option_strategy_multi_leg")
                                )
                            elif strategy_access:
                                strategy_access = (
                                    can(current_plan, "strategy_all")
                                    or str(strategy_row["strategy_key"]) in _FREE_STRATEGY_KEYS
                                )
                            if not strategy_access:
                                rejection = "当前策略执行权限已失效，订单未发送。"
            if rejection is None and claim:
                claimed = conn.execute(
                    "UPDATE orders SET status='SENDING' WHERE order_id=? AND status='PENDING'",
                    (order_id,),
                ).rowcount
                if claimed != 1:
                    rejection = "实盘订单发送权已被其他执行者占用，订单未发送。"
            if rejection:
                if intent_status not in {"SENDING", "SUBMISSION_UNKNOWN", "FILLED"}:
                    conn.execute("UPDATE orders SET status='REJECTED' WHERE order_id=?", (order_id,))
                conn.execute(
                    """INSERT INTO risk_log (user_id,event_type,symbol,details,severity,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        user_id,
                        "LIVE_SEND_REVALIDATION_REJECTED",
                        symbol,
                        rejection,
                        "WARN",
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ),
                )
        return rejection

    def current_position(self, user_id: int, symbol: str, mode: str = "paper") -> float:
        """Return the user's net position without exposing ledger rows to callers."""
        rows = self.db.fetch_all(
            """SELECT t.symbol,t.side,t.quantity,t.price,t.commission,t.trade_time
               FROM trades t JOIN orders o ON o.order_id=t.order_id
               WHERE o.reason=? AND o.account_mode=? AND t.symbol=?
               ORDER BY t.trade_time,t.id""",
            (f"user={user_id}", str(mode).strip().lower(), str(symbol).strip().upper()),
        )
        return float(trade_ledger_state([dict(row) for row in rows])["positions"].get(str(symbol).strip().upper(), 0))

    def submit(
        self,
        *,
        user_id: int,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        strategy: str,
        mode: str,
        risk_config: dict,
        paused: bool,
        live_confirmed: bool = False,
        instrument_type: str = "stock",
        position_action: str | None = None,
    ) -> dict[str, Any]:
        symbol = symbol.strip().upper()
        side = side.strip().upper()
        mode = mode.strip().lower()
        if side not in {"BUY", "SELL"}:
            raise ValueError("订单方向必须是 BUY 或 SELL。")
        if mode not in {"paper", "live"}:
            raise ValueError("账户模式必须是 paper 或 live。")
        if str(instrument_type).strip().lower() != "stock":
            raise ValueError("期权自动交易尚未接入券商通道；当前仅支持正股订单。")
        if position_action and str(position_action).strip().lower() not in {
            "open_long", "close_long", "open_short", "close_short"
        }:
            raise ValueError("仓位动作无效。")
        is_a_share = symbol.isdigit() and len(symbol) == 6
        valid_symbol = bool(re.fullmatch(r"(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})", symbol))
        if not valid_symbol or quantity <= 0 or price <= 0 or not math.isfinite(price):
            raise ValueError("标的、数量和价格必须有效。")
        tiger = None
        notional = float(quantity) * float(price)
        reason = f"user={user_id}"
        cutoff = (datetime.now(UTC) - timedelta(seconds=60)).isoformat(timespec="seconds")
        order_id = f"{mode.upper()}-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
        created = datetime.now(UTC).isoformat(timespec="seconds")
        blocked_message: str | None = None
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute(
                "SELECT id,plan_type,subscription_expire,is_admin FROM users WHERE id=? AND is_active=1",
                (user_id,),
            ).fetchone()
            if not user:
                raise ValueError("用户不存在或已停用。")

            # Derive the current instruction before evaluating any entitlement or
            # service switch.  A close-only instruction is always eligible for
            # the narrow reduce-only escape hatch below.
            trades = [
                dict(row)
                for row in conn.execute(
                    """SELECT t.symbol,t.side,t.quantity,t.price,t.commission,t.trade_time
                       FROM trades t JOIN orders o ON o.order_id=t.order_id
                       WHERE o.reason=? AND o.account_mode=? ORDER BY t.trade_time,t.id""",
                    (reason, mode),
                )
            ]
            market_trades = [
                trade
                for trade in trades
                if (str(trade["symbol"]).isdigit() and len(str(trade["symbol"])) == 6)
                == is_a_share
            ]
            state = trade_ledger_state(market_trades)
            current_quantity = float(state["positions"].get(symbol, 0))
            execution_slices = derive_execution_slices(current_quantity, side, quantity)
            has_opening_slice = any(
                item["action"] in {"open_long", "open_short"} for item in execution_slices
            )
            reduces_only = bool(execution_slices) and not has_opening_slice

            platform_pause = conn.execute(
                "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
            ).fetchone()
            user_pause = conn.execute(
                "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
            ).fetchone()
            final_opening_paused = (
                bool(paused)
                or platform_pause is None
                or str(platform_pause[0]).lower() in {"1", "true", "yes", "on"}
                or bool(user_pause and int(user_pause[0] or 0))
            )

            plan = effective_plan(authoritative_membership_row(conn, user))
            if has_opening_slice and StrategyRegistry(self.db).check_plan_access(plan, strategy) is False:
                raise ValueError("当前订阅方案未开放该策略。")

            limits: dict[str, Any] | None = None
            if mode == "live":
                operator_id = os.getenv("TRADEAI_LIVE_OPERATOR_USER_ID", "").strip()
                if not user["is_admin"]:
                    raise ValueError("Tiger 实盘现阶段仅允许平台管理员联调；高阶会员请联系客服。")
                if str(user_id) != operator_id:
                    raise ValueError("当前共享券商账户只允许已配置的实盘操作员；多用户实盘尚未绑定独立券商账户。")
                if not live_confirmed:
                    raise ValueError("实盘订单必须明确确认。")
                if is_a_share:
                    raise ValueError("A 股实盘通道尚未接入。")
                broker_rows = conn.execute(
                    """SELECT provider,is_active,mode,status,external_account_id,metadata_json
                       FROM broker_accounts WHERE user_id=?""",
                    (user_id,),
                ).fetchall()
                if not any(broker_execution_authorized(row) for row in broker_rows):
                    raise ValueError("当前用户缺少有效的 Tiger 实盘执行授权证明。")
                if has_opening_slice:
                    service_control = conn.execute(
                        "SELECT control_value FROM platform_controls WHERE control_key='user_auto_trading_enabled'"
                    ).fetchone()
                    if not service_control or str(service_control[0]).lower() not in {"1", "true", "yes", "on"}:
                        raise ValueError("用户自动交易总开关当前关闭，请联系管理员申请开通。")
                    settings_row = conn.execute(
                        "SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)
                    ).fetchone()
                    try:
                        settings = json.loads(settings_row[0]) if settings_row else {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        settings = {}
                    if not isinstance(settings, dict) or settings.get("live_auto_enabled") is not True:
                        raise ValueError("用户实盘自动交易开关未开启。")
                    limits = trading_limits(plan)
                    if int(limits["auto_control_accounts"]) <= 0:
                        raise ValueError("当前会员没有实盘自动交易控制账号名额。")
                if os.getenv("TIGER_ENV", "paper").strip().lower() != "live":
                    raise ValueError("Tiger 当前不是 live 环境，订单未发送。")

            duplicate = conn.execute(
                """SELECT 1 FROM orders WHERE symbol=? AND side=? AND quantity=? AND price=?
                   AND reason=? AND account_mode=?
                   AND (status IN ('PENDING','SENDING','SUBMITTED','SUBMISSION_UNKNOWN')
                        OR (status='FILLED' AND created_at>=?)) LIMIT 1""",
                (symbol, side, quantity, price, reason, mode, cutoff),
            ).fetchone()
            if duplicate:
                raise ValueError(
                    "存在相同未完成订单，或 60 秒内存在相同成交订单，已阻止重复提交。"
                )
            market_risk = dict(risk_config)
            if is_a_share:
                for key, fallback in (
                    ("max_position_per_symbol", 5_000),
                    ("max_total_position", 50_000),
                    ("max_daily_loss", 2_000),
                ):
                    market_risk[key] = risk_config.get(
                        f"{key}_cny", float(risk_config.get(key, fallback)) * 7
                    )
            signed_quantity = quantity if side == "BUY" else -quantity
            projected_quantity = current_quantity + signed_quantity
            if is_a_share and projected_quantity < 0:
                message = "A 股暂不支持建立空头仓位；卖出数量不能超过当前持仓。"
                conn.execute(
                    "INSERT INTO risk_log (user_id,event_type,symbol,details,severity,created_at) VALUES (?,?,?,?,?,?)",
                    (user_id, "CN_SHORT_NOT_SUPPORTED", symbol, message, "WARN", created),
                )
                raise ValueError(message)
            projected_symbol = abs(projected_quantity) * price
            projected_total = (
                float(state["total_exposure"])
                - float(state["exposures"].get(symbol, 0))
                + projected_symbol
            )
            last_loss_at = state["last_loss_at"]
            cooldown_active = bool(
                last_loss_at
                and int(state["consecutive_losses"]) >= int(risk_config.get("consecutive_loss_limit", 3))
                and datetime.now(UTC) - last_loss_at
                < timedelta(minutes=int(risk_config.get("cooldown_minutes", 30)))
            )
            if reduces_only:
                # Exit orders retain only the live-channel availability check;
                # exposure, loss, and cooldown limits must not trap a position.
                market_risk.update({
                    "max_position_per_symbol": float("inf"),
                    "max_total_position": float("inf"),
                    "max_daily_loss": float("inf"),
                })
            decision = validate_order(
                symbol=symbol,
                quantity=quantity,
                price=price,
                symbol_exposure=float(state["exposures"].get(symbol, 0)),
                total_exposure=float(state["total_exposure"]),
                daily_pnl=float(state["daily_pnl"]),
                config=market_risk,
                paused=final_opening_paused and has_opening_slice,
                require_market_hours=mode == "live",
                projected_symbol_exposure=projected_symbol,
                projected_total_exposure=projected_total,
                cooldown_active=cooldown_active and has_opening_slice,
            )
            if not decision.allowed:
                conn.execute(
                    """INSERT INTO risk_log (user_id,event_type,symbol,details,severity,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (user_id, decision.code, symbol, decision.message, "WARN", created),
                )
                blocked_message = decision.message
            else:
                if mode == "live" and has_opening_slice:
                    assert limits is not None
                    if limits["daily_orders"] is not None:
                        today = datetime.now(UTC).date().isoformat()
                        order_count = conn.execute(
                            """SELECT COUNT(*) FROM orders WHERE reason=? AND account_mode='live'
                               AND created_at>=?
                               AND status IN ('PENDING','SENDING','SUBMITTED','SUBMISSION_UNKNOWN','FILLED')""",
                            (reason, today),
                        ).fetchone()[0]
                        if int(order_count) >= int(limits["daily_orders"]):
                            raise ValueError("已达到实盘服务的每日订单安全上限。")
                        if notional > float(limits["single_notional"]):
                            raise ValueError("订单金额超过实盘服务的单笔安全上限。")
                        daily_notional = conn.execute(
                            """SELECT COALESCE(SUM(quantity*COALESCE(price,0)),0) FROM orders
                                WHERE reason=? AND account_mode='live' AND created_at>=?
                                AND status IN ('PENDING','SENDING','SUBMITTED','SUBMISSION_UNKNOWN','FILLED')""",
                            (reason, today),
                        ).fetchone()[0]
                        if float(daily_notional) + notional > float(limits["daily_notional"]):
                            raise ValueError("订单金额超过实盘服务的单日总额安全上限。")
                status = "PENDING" if mode == "live" else "FILLED"
                conn.execute(
                    """INSERT INTO orders
                       (order_id,symbol,side,order_type,quantity,price,status,strategy_name,reason,created_at,account_mode)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (order_id, symbol, side, "LMT", quantity, price, status, strategy, reason, created, mode),
                )
                if mode == "paper":
                    for index, execution in enumerate(execution_slices):
                        trade_id = f"T-{order_id}" if index == 0 else f"T-{order_id}-{index + 1}"
                        conn.execute(
                            """INSERT INTO trades
                               (trade_id,order_id,symbol,side,quantity,price,commission,trade_time)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (trade_id, order_id, symbol, side, execution["quantity"], price, 0, created),
                        )
        if blocked_message:
            raise ValueError(blocked_message)
        if mode == "live":
            rejection = self._revalidate_live_intent(
                order_id=order_id,
                user_id=user_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                strategy=strategy,
            )
            if rejection:
                raise ValueError(rejection)
            try:
                tiger = TigerAPI()
                if tiger.environment != "live":
                    raise ValueError("Tiger 当前不是 live 环境，订单未发送。")
                rejection = self._revalidate_live_intent(
                    order_id=order_id,
                    user_id=user_id,
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    strategy=strategy,
                )
                if rejection:
                    raise ValueError(rejection)

                def pre_send_check(resolved_account_id: str) -> TigerSendClaim:
                    send_rejection = self._revalidate_live_intent(
                        order_id=order_id,
                        user_id=user_id,
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        price=price,
                        strategy=strategy,
                        expected_account_id=resolved_account_id,
                        claim=True,
                    )
                    if send_rejection:
                        raise ValueError(send_rejection)
                    return _new_tiger_send_claim(
                        resolved_account_id,
                        order_id,
                        user_id=user_id,
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        price=price,
                    )

                result = tiger.place_stock_limit(
                    symbol,
                    side,
                    quantity,
                    price,
                    user_id=user_id,
                    intent_id=order_id,
                    pre_send_check=pre_send_check,
                )
            except TigerSubmissionUnknown:
                try:
                    self.db.execute(
                        "UPDATE orders SET status='SUBMISSION_UNKNOWN' WHERE order_id=? AND status='SENDING'",
                        (order_id,),
                    )
                    self.db.log_risk_event(
                        "LIVE_SUBMISSION_UNKNOWN",
                        symbol,
                        "Tiger 提交结果未知；禁止重试，必须等待券商订单对账。",
                        "ERROR",
                        user_id,
                    )
                except Exception:
                    pass
                raise
            except TigerAPIRejected:
                self.db.execute(
                    "UPDATE orders SET status='REJECTED' WHERE order_id=? AND status IN ('PENDING','SENDING')",
                    (order_id,),
                )
                raise
            except Exception:
                self.db.execute(
                    "UPDATE orders SET status='REJECTED' WHERE order_id=? AND status='PENDING'",
                    (order_id,),
                )
                raise
            try:
                broker_status = getattr(result, "status", None)
                broker_status = getattr(broker_status, "value", broker_status)
                normalized_status = str(broker_status or "").strip().upper()
                final_status = normalized_status if normalized_status in {
                    "FILLED", "CANCELLED", "CANCELED", "REJECTED"
                } else "SUBMITTED"
                self.db.update_order_status(order_id, final_status)
            except Exception as exc:
                try:
                    self.db.execute(
                        "UPDATE orders SET status='SUBMISSION_UNKNOWN' WHERE order_id=? AND status='SENDING'",
                        (order_id,),
                    )
                except Exception:
                    pass
                raise TigerSubmissionUnknown(
                    "Tiger 已返回提交结果但本地状态未能确认；禁止重试并等待订单对账。"
                ) from exc
        result = self.db.fetch_one("SELECT * FROM orders WHERE order_id=?", (order_id,)) or {}
        result["execution_slices"] = execution_slices
        result["position_action"] = execution_slices[0]["action"] if len(execution_slices) == 1 else "reverse_position"
        return result

    def add_broker_account(
        self,
        user_id: int,
        provider: str,
        alias: str,
        external_id: str,
        mode: str,
    ) -> None:
        provider, alias = provider.strip(), alias.strip()
        external_id, mode = external_id.strip(), mode.strip().lower()
        providers = {"Tiger", "Alpaca", "IBKR", "Futu", "QMT", "PTrade"}
        if provider not in providers or mode not in {"paper", "live"}:
            raise ValueError("券商或账户环境无效。")
        if not alias or len(alias) > 50 or not external_id or len(external_id) > 80:
            raise ValueError("账户别名和券商账户 ID 必须有效。")
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute(
                "SELECT id,plan_type,subscription_expire FROM users WHERE id=? AND is_active=1",
                (user_id,),
            ).fetchone()
            if not user:
                raise ValueError("用户不存在或已停用。")
            control = conn.execute(
                "SELECT control_value FROM platform_controls WHERE control_key='user_auto_trading_enabled'"
            ).fetchone()
            if not control or str(control[0]).lower() not in {"1", "true", "yes", "on"}:
                raise ValueError("券商账户自助连接当前关闭，请联系管理员申请开通。")
            plan = effective_plan(authoritative_membership_row(conn, user))
            limits = trading_limits(plan)
            account_limit = int(limits["auto_control_accounts"])
            if account_limit <= 0:
                raise ValueError("当前会员没有自动交易控制账号名额；高级会员可登记 1 个，专业会员最多 5 个。")
            duplicate = conn.execute(
                """SELECT 1 FROM broker_accounts
                   WHERE user_id=? AND provider=? AND external_account_id=? AND is_active=1""",
                (user_id, provider, external_id),
            ).fetchone()
            if duplicate:
                raise ValueError("该券商账户已经登记。")
            account_count = conn.execute(
                "SELECT COUNT(*) FROM broker_accounts WHERE user_id=? AND is_active=1", (user_id,)
            ).fetchone()[0]
            if int(account_count) >= account_limit:
                raise ValueError(f"{plan}最多登记 {account_limit} 个自动交易控制账号。")
            broker_limit = limits["brokers"]
            provider_exists = conn.execute(
                "SELECT 1 FROM broker_accounts WHERE user_id=? AND provider=? AND is_active=1",
                (user_id, provider),
            ).fetchone()
            broker_count = conn.execute(
                "SELECT COUNT(DISTINCT provider) FROM broker_accounts WHERE user_id=? AND is_active=1",
                (user_id,),
            ).fetchone()[0]
            if not provider_exists and broker_limit is not None and int(broker_count) >= int(broker_limit):
                raise ValueError(f"实盘接入服务最多连接 {broker_limit} 家券商。")
            conn.execute(
                """INSERT INTO broker_accounts
                   (user_id,provider,account_alias,external_account_id,mode,status,metadata_json,created_at)
                   VALUES (?,?,?,?,?,'not_configured',?,?)""",
                (user_id, provider, alias, external_id, mode, '{"credentials":"not_configured"}', now),
            )
            conn.execute(
                """INSERT INTO strategy_action_logs
                   (user_id,strategy_name,action,params,result,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    user_id,
                    "券商账户",
                    "BROKER_CONNECT",
                    f'{{"provider":"{provider}","mode":"{mode}"}}',
                    "success",
                    now,
                ),
            )

    def liquidate_paper(self, user_id: int) -> list[dict[str, Any]]:
        """按最新真实价格反向成交当前用户的全部模拟净持仓。"""
        user = self.db.fetch_one(
            "SELECT id,plan_type,subscription_expire FROM users WHERE id=? AND is_active=1", (user_id,)
        )
        if not user or not can(
            effective_plan(authoritative_membership_user(self.db, user)), "liquidate_all"
        ):
            raise ValueError("一键全平仅对定制版开放。")
        positions = self.db.fetch_all(
            """SELECT t.symbol,SUM(CASE WHEN t.side='BUY' THEN t.quantity ELSE -t.quantity END) quantity
               FROM trades t JOIN orders o ON o.order_id=t.order_id
               WHERE o.reason=? AND o.account_mode='paper' GROUP BY t.symbol""",
            (f"user={user_id}",),
        )
        positions = [row for row in positions if abs(float(row["quantity"] or 0)) > 0]
        if not positions:
            return []
        from data.yfinance_adapter import YFinanceAdapter

        symbols = tuple(row["symbol"] for row in positions)
        closes, _ = YFinanceAdapter().history(symbols, period="5d")
        created_orders = []
        for position in positions:
            quantity = abs(int(position["quantity"]))
            side = "SELL" if position["quantity"] > 0 else "BUY"
            price = float(closes[position["symbol"]].dropna().iloc[-1])
            order_id = f"FLAT-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
            created = datetime.now(UTC).isoformat(timespec="seconds")
            self.db.insert_order(
                {"order_id": order_id, "symbol": position["symbol"], "side": side, "order_type": "MKT", "quantity": quantity,
                 "price": price, "status": "FILLED", "strategy_name": "一键全平", "reason": f"user={user_id}", "created_at": created,
                 "account_mode": "paper"}
            )
            self.db.insert_trade(
                {"trade_id": f"T-{order_id}", "order_id": order_id, "symbol": position["symbol"], "side": side,
                 "quantity": quantity, "price": price, "commission": 0, "trade_time": created}
            )
            created_orders.append({"order_id": order_id, "symbol": position["symbol"], "side": side, "quantity": quantity, "price": price})
        return created_orders
