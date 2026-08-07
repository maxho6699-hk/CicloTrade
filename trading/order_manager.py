# -*- coding: utf-8 -*-
"""防重复、风控先行的模拟盘/实盘订单入口。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import math
import os
import re
import secrets
from typing import Any

from core.database import DatabaseManager, get_database
from core.plans import can, effective_plan, trading_limits
from core.strategy_registry import StrategyRegistry
from core.user_settings import load_user_settings
from trading.risk_filter import validate_order
from trading.tiger_api import TigerAPI


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


class OrderManager:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

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
        valid_symbol = bool(re.fullmatch(r"(?:[A-Z][A-Z0-9.-]{0,11}|\d{6})", symbol))
        if not valid_symbol or quantity <= 0 or price <= 0 or not math.isfinite(price):
            raise ValueError("标的、数量和价格必须有效。")
        user = self.db.fetch_one(
            "SELECT plan_type,subscription_expire,is_admin FROM users WHERE id=? AND is_active=1", (user_id,)
        )
        plan = effective_plan(user or {})
        strategy_access = StrategyRegistry(self.db).check_plan_access(plan, strategy)
        if strategy_access is False:
            raise ValueError("当前订阅方案未开放该策略。")
        tiger = None
        limits: dict[str, Any] | None = None
        notional = float(quantity) * float(price)
        if mode == "live":
            if not user_auto_trading_open(self.db):
                raise ValueError("用户自动交易总开关当前关闭，请联系管理员申请开通。")
            allowed_users = {
                value.strip()
                for value in os.getenv("TRADEAI_STOCK_AUTO_CONTRACT_USER_IDS", "").split(",")
                if value.strip()
            }
            operator_id = os.getenv("TRADEAI_LIVE_OPERATOR_USER_ID", "").strip()
            if not user or not can(plan, "real_trade"):
                raise ValueError("当前订阅方案不具备正股实盘权限。")
            if not user["is_admin"]:
                raise ValueError("Tiger 实盘现阶段仅允许平台管理员联调；高阶会员请联系客服。")
            limits = trading_limits(plan)
            if plan == "高级版" and str(user_id) not in allowed_users:
                raise ValueError("此账户尚未完成实盘额外签约或未进入白名单。")
            if str(user_id) != operator_id:
                raise ValueError("当前共享券商账户只允许已配置的实盘操作员；多用户实盘尚未绑定独立券商账户。")
            if load_user_settings(user_id, self.db).get("live_auto_enabled") is not True:
                raise ValueError("用户实盘自动交易开关未开启。")
            if not live_confirmed:
                raise ValueError("实盘订单必须明确确认。")
            if symbol.isdigit() and len(symbol) == 6:
                raise ValueError("A 股实盘通道尚未接入。")
            tiger = TigerAPI()
            if tiger.environment != "live":
                raise ValueError("Tiger 当前不是 live 环境，订单未发送。")
        reason = f"user={user_id}"
        cutoff = (datetime.now(UTC) - timedelta(seconds=60)).isoformat(timespec="seconds")
        order_id = f"{mode.upper()}-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
        created = datetime.now(UTC).isoformat(timespec="seconds")
        blocked_message: str | None = None
        with self.db.transaction() as conn:
            if mode == "live":
                conn.execute("BEGIN IMMEDIATE")
                assert limits is not None
                if limits["daily_orders"] is not None:
                    today = datetime.now(UTC).date().isoformat()
                    order_count = conn.execute(
                        """SELECT COUNT(*) FROM orders WHERE reason=? AND account_mode='live'
                           AND created_at>=? AND status IN ('PENDING','FILLED')""",
                        (reason, today),
                    ).fetchone()[0]
                    if int(order_count) >= int(limits["daily_orders"]):
                        raise ValueError("已达到当前方案的每日订单上限。")
                    if notional > float(limits["single_notional"]):
                        raise ValueError("订单金额超过当前方案的单笔上限。")
                    daily_notional = conn.execute(
                        """SELECT COALESCE(SUM(quantity*COALESCE(price,0)),0) FROM orders
                           WHERE reason=? AND account_mode='live' AND created_at>=?
                           AND status IN ('PENDING','FILLED')""",
                        (reason, today),
                    ).fetchone()[0]
                    if float(daily_notional) + notional > float(limits["daily_notional"]):
                        raise ValueError("订单金额超过当前方案的单日总额上限。")
            duplicate = conn.execute(
                """SELECT 1 FROM orders WHERE symbol=? AND side=? AND quantity=? AND price=?
                   AND reason=? AND created_at>=? AND status IN ('PENDING','FILLED') LIMIT 1""",
                (symbol, side, quantity, price, reason, cutoff),
            ).fetchone()
            if duplicate:
                raise ValueError("60 秒内存在相同订单，已阻止重复提交。")
            trades = [
                dict(row)
                for row in conn.execute(
                    """SELECT t.symbol,t.side,t.quantity,t.price,t.commission,t.trade_time
                       FROM trades t JOIN orders o ON o.order_id=t.order_id
                       WHERE o.reason=? ORDER BY t.trade_time,t.id""",
                    (reason,),
                )
            ]
            is_a_share = symbol.isdigit() and len(symbol) == 6
            market_trades = [
                trade
                for trade in trades
                if (str(trade["symbol"]).isdigit() and len(str(trade["symbol"])) == 6)
                == is_a_share
            ]
            state = trade_ledger_state(market_trades)
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
            current_quantity = float(state["positions"].get(symbol, 0))
            projected_quantity = current_quantity + signed_quantity
            reduces_only = (
                current_quantity != 0
                and current_quantity * signed_quantity < 0
                and abs(signed_quantity) <= abs(current_quantity)
            )
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
            decision = validate_order(
                symbol=symbol,
                quantity=quantity,
                price=price,
                symbol_exposure=float(state["exposures"].get(symbol, 0)),
                total_exposure=float(state["total_exposure"]),
                daily_pnl=float(state["daily_pnl"]),
                config=market_risk,
                paused=paused and not reduces_only,
                require_market_hours=mode == "live",
                projected_symbol_exposure=projected_symbol,
                projected_total_exposure=projected_total,
                cooldown_active=cooldown_active,
            )
            if not decision.allowed:
                conn.execute(
                    """INSERT INTO risk_log (user_id,event_type,symbol,details,severity,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (user_id, decision.code, symbol, decision.message, "WARN", created),
                )
                blocked_message = decision.message
            else:
                status = "PENDING" if mode == "live" else "FILLED"
                conn.execute(
                    """INSERT INTO orders
                       (order_id,symbol,side,order_type,quantity,price,status,strategy_name,reason,created_at,account_mode)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (order_id, symbol, side, "LMT", quantity, price, status, strategy, reason, created, mode),
                )
                if mode == "paper":
                    conn.execute(
                        """INSERT INTO trades
                           (trade_id,order_id,symbol,side,quantity,price,commission,trade_time)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (f"T-{order_id}", order_id, symbol, side, quantity, price, 0, created),
                    )
        if blocked_message:
            raise ValueError(blocked_message)
        if mode == "live":
            try:
                result = tiger.place_stock_limit(symbol, side, quantity, price, user_id=user_id)
                self.db.update_order_status(order_id, str(getattr(result, "status", "PENDING")).upper())
            except Exception:
                self.db.update_order_status(order_id, "REJECTED")
                raise
        return self.db.fetch_one("SELECT * FROM orders WHERE order_id=?", (order_id,)) or {}

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
                "SELECT plan_type,subscription_expire FROM users WHERE id=? AND is_active=1",
                (user_id,),
            ).fetchone()
            if not user:
                raise ValueError("用户不存在或已停用。")
            control = conn.execute(
                "SELECT control_value FROM platform_controls WHERE control_key='user_auto_trading_enabled'"
            ).fetchone()
            if not control or str(control[0]).lower() not in {"1", "true", "yes", "on"}:
                raise ValueError("券商账户自助连接当前关闭，请联系管理员申请开通。")
            plan = effective_plan(dict(user))
            limits = trading_limits(plan)
            if not limits["brokers"]:
                raise ValueError("当前方案暂不支持连接券商。")
            allowed_providers = {
                "标准版": {"Tiger"},
                "高级版": {"Tiger", "Alpaca"},
                "专业版": {"Tiger", "Alpaca", "IBKR"},
                "定制版": providers,
            }[plan]
            if provider not in allowed_providers:
                raise ValueError(f"{plan}暂不支持连接 {provider}，请查看券商接入指南。")
            if mode == "live" and not can(plan, "real_trade"):
                raise ValueError("当前方案仅可登记模拟账户，实盘需升级并完成签约。")
            if mode == "live" and plan == "高级版":
                contracted = {
                    value.strip()
                    for value in os.getenv("TRADEAI_STOCK_AUTO_CONTRACT_USER_IDS", "").split(",")
                    if value.strip()
                }
                if str(user_id) not in contracted:
                    raise ValueError("此账户尚未完成实盘额外签约或未进入白名单。")
            duplicate = conn.execute(
                """SELECT 1 FROM broker_accounts
                   WHERE user_id=? AND provider=? AND external_account_id=? AND is_active=1""",
                (user_id, provider, external_id),
            ).fetchone()
            if duplicate:
                raise ValueError("该券商账户已经登记。")
            account_limit = limits["broker_accounts"]
            account_count = conn.execute(
                "SELECT COUNT(*) FROM broker_accounts WHERE user_id=? AND is_active=1", (user_id,)
            ).fetchone()[0]
            if account_limit is not None and int(account_count) >= int(account_limit):
                raise ValueError(f"{plan}最多登记 {account_limit} 个券商账户。")
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
                raise ValueError(f"{plan}最多连接 {broker_limit} 家券商。")
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
            "SELECT plan_type,subscription_expire FROM users WHERE id=? AND is_active=1", (user_id,)
        )
        if not user or not can(effective_plan(user), "liquidate_all"):
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
