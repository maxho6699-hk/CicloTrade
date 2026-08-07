# -*- coding: utf-8 -*-
"""订阅订单、支付回调幂等和退款资格。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import json
import secrets
from typing import Any

from core.database import DatabaseManager, get_database
from core.plans import PLANS


CORE_ACTIONS = {"BACKTEST", "ALERT_CREATE", "STRATEGY_DETAIL", "SIGNAL_COPY", "BROKER_CONNECT"}
CYCLE_DAYS = {"monthly": 30, "quarterly": 90, "yearly": 365}
YEARLY_PROMO_DAYS = 90
TERMINAL_STATUSES = {"paid", "failed", "cancelled", "refunded"}
REFERRAL_REWARD_PERCENT = 30


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="seconds")


def grant_subscription_days(
    conn: Any,
    user_id: int,
    days: int,
    fallback_plan: str,
    now: datetime | None = None,
) -> str:
    """Extend a user's current plan, or activate the supplied paid plan."""
    days = int(days)
    if days < 1 or fallback_plan not in PLANS or fallback_plan == "免费版":
        raise ValueError("奖励订阅权益无效。")
    user = conn.execute(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not user:
        raise ValueError("奖励关联用户不存在。")
    base = now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    plan = str(user["plan_type"] or "")
    active_expiry: datetime | None = None
    if user["subscription_expire"]:
        try:
            expiry = datetime.fromisoformat(user["subscription_expire"])
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry > base:
                active_expiry = expiry
            base = max(base, expiry)
        except (TypeError, ValueError):
            pass
    if plan not in PLANS or plan == "免费版" or active_expiry is None:
        plan = fallback_plan
    expiry = _iso(base + timedelta(days=days))
    conn.execute(
        "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?",
        (plan, expiry, user_id),
    )
    return expiry


class OrderService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def annual_bonus_enabled(self) -> bool:
        row = self.db.fetch_one(
            "SELECT control_value FROM platform_controls WHERE control_key='annual_bonus_enabled'"
        )
        return not row or str(row["control_value"]).lower() in {"1", "true", "yes", "on"}

    def create_order(self, user_id: int, plan: str, cycle: str, method: str) -> dict[str, Any]:
        if plan not in PLANS or plan == "免费版":
            raise ValueError("请选择可购买的订阅方案。")
        prices = PLANS[plan]["prices"]
        if plan == "定制版":
            cycle = "project"
        if cycle not in prices:
            raise ValueError("该方案不支持所选付款周期。")
        if method not in {"paddle", "paypal", "fps"}:
            raise ValueError("不支持的支付方式。")
        entitlement_days = CYCLE_DAYS.get(cycle, 3650)
        if cycle == "yearly" and self.annual_bonus_enabled():
            entitlement_days += YEARLY_PROMO_DAYS
        order_no = f"TA{datetime.now(UTC):%Y%m%d%H%M%S}{secrets.token_hex(3).upper()}"
        self.db.execute(
            """INSERT INTO subscription_orders
               (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,entitlement_days,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                order_no,
                user_id,
                plan,
                cycle,
                float(prices[cycle]),
                "HKD",
                method,
                "pending",
                entitlement_days,
                _iso(),
            ),
        )
        self.log_action(user_id, "ORDER_CREATE", {"order_no": order_no, "plan": plan, "method": method})
        return self.get_order(order_no)

    def get_order(self, order_no: str) -> dict[str, Any]:
        order = self.db.fetch_one("SELECT * FROM subscription_orders WHERE order_no=?", (order_no,))
        if not order:
            raise ValueError("订单不存在。")
        return order

    def list_orders(self, user_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM subscription_orders WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        )

    def attach_external_id(self, order_no: str, external_id: str, price_id: str | None = None) -> None:
        if not external_id or len(external_id) > 128 or (price_id is not None and len(price_id) > 128):
            raise ValueError("外部支付交易资料无效。")
        self.db.execute(
            """UPDATE subscription_orders SET external_id=?,external_price_id=?
               WHERE order_no=? AND status='pending' AND external_id IS NULL""",
            (external_id, price_id, order_no),
        )

    def process_callback(
        self,
        event_id: str,
        order_no: str,
        status: str,
        raw_data: dict[str, Any],
        *,
        audit_user_id: int | None = None,
        audit_action: str | None = None,
        audit_details: dict[str, Any] | None = None,
    ) -> bool:
        """首次回调返回 True；重复事件不再次变更订阅。"""
        if status not in TERMINAL_STATUSES - {"refunded"}:
            raise ValueError("未知支付状态。")
        if (audit_user_id is None) != (audit_action is None):
            raise ValueError("审计用户与动作必须同时提供。")
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO payment_callbacks (event_id,order_no,raw_data,processed,created_at) VALUES (?,?,?,?,?)",
                (event_id, order_no, json.dumps(raw_data, ensure_ascii=False), 0, _iso(now)),
            )
            if not inserted.rowcount:
                return False
            order_row = conn.execute(
                "SELECT * FROM subscription_orders WHERE order_no=?", (order_no,)
            ).fetchone()
            if not order_row:
                raise ValueError("支付回调无法匹配订单。")
            order = dict(order_row)
            if order["status"] != "pending":
                conn.execute("UPDATE payment_callbacks SET processed=1 WHERE event_id=?", (event_id,))
                return False
            if status == "paid":
                capture_id = raw_data.get("capture_id")
                if capture_id is not None and (
                    not isinstance(capture_id, str) or not capture_id or len(capture_id) > 128
                ):
                    raise ValueError("外部支付捕获编号无效。")
                current = conn.execute(
                    "SELECT plan_type,subscription_expire FROM users WHERE id=?", (order["user_id"],)
                ).fetchone()
                if not current:
                    raise ValueError("支付订单关联用户不存在。")
                conn.execute(
                    """UPDATE subscription_orders
                       SET status='paid',paid_at=?,previous_plan_type=?,previous_subscription_expire=?,
                           external_capture_id=COALESCE(external_capture_id,?)
                       WHERE order_no=? AND status='pending'""",
                    (
                        _iso(now),
                        current["plan_type"],
                        current["subscription_expire"],
                        capture_id,
                        order_no,
                    ),
                )
                base = now
                if current["subscription_expire"]:
                    try:
                        expiry = datetime.fromisoformat(current["subscription_expire"])
                        if expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=UTC)
                        base = max(now, expiry)
                    except ValueError:
                        base = now
                days = int(order.get("entitlement_days") or CYCLE_DAYS.get(order["billing_cycle"], 3650))
                conn.execute(
                    "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?",
                    (order["plan_type"], _iso(base + timedelta(days=days)), order["user_id"]),
                )
                referral = conn.execute(
                    "SELECT * FROM referrals WHERE referee_id=? AND status='registered'", (order["user_id"],)
                ).fetchone()
                if referral:
                    qualified = conn.execute(
                        "UPDATE referrals SET status='qualified' WHERE id=? AND status='registered'",
                        (referral["id"],),
                    )
                    if qualified.rowcount:
                        reward_days = max(1, days * REFERRAL_REWARD_PERCENT // 100)
                        reference = f"referral:{referral['id']}"
                        inserted_reward = conn.execute(
                            """INSERT OR IGNORE INTO rewards
                               (user_id,reward_type,days,reference,source_order_no,created_at)
                               VALUES (?,?,?,?,?,?)""",
                            (
                                referral["referrer_id"],
                                "REFERRAL_30",
                                reward_days,
                                reference,
                                order_no,
                                _iso(now),
                            ),
                        )
                        if inserted_reward.rowcount:
                            reward_expiry = grant_subscription_days(
                                conn,
                                referral["referrer_id"],
                                reward_days,
                                order["plan_type"],
                                now,
                            )
                            conn.execute(
                                """INSERT INTO user_action_logs
                                   (user_id,action_type,details,created_at) VALUES (?,?,?,?)""",
                                (
                                    referral["referrer_id"],
                                    "REFERRAL_REWARD_GRANTED",
                                    json.dumps(
                                        {
                                            "order_no": order_no,
                                            "referee_id": order["user_id"],
                                            "days": reward_days,
                                            "expiry": reward_expiry,
                                        },
                                        ensure_ascii=False,
                                    ),
                                    _iso(now),
                                ),
                            )
            else:
                conn.execute(
                    "UPDATE subscription_orders SET status=?,paid_at=NULL WHERE order_no=? AND status='pending'",
                    (status, order_no),
                )
            if audit_user_id is not None and audit_action is not None:
                details = audit_details if audit_details is not None else {"order_no": order_no}
                conn.execute(
                    "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                    (audit_user_id, audit_action, json.dumps(details, ensure_ascii=False), _iso(now)),
                )
            conn.execute("UPDATE payment_callbacks SET processed=1 WHERE event_id=?", (event_id,))
        return True

    @staticmethod
    def _reverse_entitlements(conn: Any, order: dict[str, Any], now: datetime) -> None:
        later_paid = conn.execute(
            """SELECT id,paid_at,plan_type,billing_cycle,entitlement_days FROM subscription_orders
               WHERE user_id=? AND status='paid' AND
               (paid_at>? OR (paid_at=? AND id>?)) ORDER BY paid_at,id""",
            (order["user_id"], order["paid_at"], order["paid_at"], order["id"]),
        ).fetchall()
        current = conn.execute(
            "SELECT plan_type,subscription_expire FROM users WHERE id=?", (order["user_id"],)
        ).fetchone()
        if not current:
            raise ValueError("支付订单关联用户不存在。")
        if not current["subscription_expire"]:
            conn.execute(
                "UPDATE users SET plan_type='免费版',subscription_expire=NULL WHERE id=?",
                (order["user_id"],),
            )
        else:
            try:
                def parse_time(value: str) -> datetime:
                    parsed = datetime.fromisoformat(value)
                    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

                current_expiry = parse_time(current["subscription_expire"])
                baseline_expiry = (
                    parse_time(order["previous_subscription_expire"])
                    if order.get("previous_subscription_expire")
                    else None
                )
                order_days = int(
                    order.get("entitlement_days")
                    or CYCLE_DAYS.get(order["billing_cycle"], 3650)
                )
                events = [
                    (
                        parse_time(order["paid_at"]),
                        0,
                        int(order["id"]),
                        order_days,
                        True,
                        order["plan_type"],
                    )
                ]
                events.extend(
                    (
                        parse_time(row["paid_at"]),
                        0,
                        int(row["id"]),
                        int(row["entitlement_days"] or CYCLE_DAYS.get(row["billing_cycle"], 3650)),
                        False,
                        row["plan_type"],
                    )
                    for row in later_paid
                )
                rewards = conn.execute(
                    """SELECT r.id,r.days,r.created_at,o.plan_type fallback_plan
                       FROM rewards r LEFT JOIN subscription_orders o ON o.order_no=r.source_order_no
                       WHERE r.user_id=? AND r.created_at>=? ORDER BY r.created_at,r.id""",
                    (order["user_id"], order["paid_at"]),
                ).fetchall()
                events.extend(
                    (
                        parse_time(row["created_at"]),
                        1,
                        int(row["id"]),
                        int(row["days"]),
                        False,
                        row["fallback_plan"] or order["plan_type"],
                    )
                    for row in rewards
                )

                def replay(include_reversed: bool) -> tuple[datetime | None, str]:
                    expiry = baseline_expiry
                    plan = str(order.get("previous_plan_type") or "免费版")
                    for granted_at, kind, _event_id, days, is_reversed, grant_plan in sorted(events):
                        if is_reversed and not include_reversed:
                            continue
                        active = bool(expiry and expiry > granted_at)
                        if kind == 0 or not active:
                            plan = str(grant_plan)
                        expiry = max(granted_at, expiry) if expiry else granted_at
                        expiry += timedelta(days=days)
                    return expiry, plan

                with_reversed, with_plan = replay(True)
                without_reversed, without_plan = replay(False)
                if with_reversed is None:
                    raise ValueError
                entitlement_impact = timedelta(days=order_days)
                if without_reversed is not None:
                    entitlement_impact = min(
                        entitlement_impact,
                        max(timedelta(0), with_reversed - without_reversed),
                    )
                reduced_expiry = current_expiry - entitlement_impact
            except (TypeError, ValueError) as exc:
                raise ValueError("订阅权益无效，需人工核对支付逆转。") from exc
            if reduced_expiry > now:
                manual_residual = reduced_expiry > max(without_reversed or now, now) + timedelta(seconds=2)
                restored_plan = (
                    current["plan_type"]
                    if current["plan_type"] != with_plan or manual_residual
                    else without_plan
                )
                conn.execute(
                    "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?",
                    (restored_plan, _iso(reduced_expiry), order["user_id"]),
                )
            else:
                conn.execute(
                    "UPDATE users SET plan_type='免费版',subscription_expire=NULL WHERE id=?",
                    (order["user_id"],),
                )
        referral = conn.execute(
            "SELECT * FROM referrals WHERE referee_id=? AND status='qualified'",
            (order["user_id"],),
        ).fetchone()
        reward = conn.execute(
            """SELECT * FROM rewards
               WHERE source_order_no=? AND reward_type='REFERRAL_30'""",
            (order["order_no"],),
        ).fetchone()
        if not referral or not reward:
            return
        rewarded_user = conn.execute(
            "SELECT plan_type,subscription_expire FROM users WHERE id=?",
            (reward["user_id"],),
        ).fetchone()
        if rewarded_user and rewarded_user["subscription_expire"]:
            try:
                reward_expiry = datetime.fromisoformat(rewarded_user["subscription_expire"])
                if reward_expiry.tzinfo is None:
                    reward_expiry = reward_expiry.replace(tzinfo=UTC)
                reduced_expiry = reward_expiry - timedelta(days=int(reward["days"]))
                if reduced_expiry > now:
                    conn.execute(
                        "UPDATE users SET subscription_expire=? WHERE id=?",
                        (_iso(reduced_expiry), reward["user_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE users SET plan_type='免费版',subscription_expire=NULL WHERE id=?",
                        (reward["user_id"],),
                    )
            except (TypeError, ValueError) as exc:
                raise ValueError("推荐奖励权益无效，需人工核对支付逆转。") from exc
        conn.execute("DELETE FROM rewards WHERE id=?", (reward["id"],))
        conn.execute(
            "UPDATE referrals SET status='registered' WHERE id=? AND status='qualified'",
            (referral["id"],),
        )
        conn.execute(
            "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
            (
                reward["user_id"],
                "REFERRAL_REWARD_REVOKED",
                json.dumps(
                    {
                        "order_no": order["order_no"],
                        "referee_id": order["user_id"],
                        "days": reward["days"],
                    },
                    ensure_ascii=False,
                ),
                _iso(now),
            ),
        )
        replacement = conn.execute(
            """SELECT * FROM subscription_orders
               WHERE user_id=? AND status='paid'
               ORDER BY paid_at,id LIMIT 1""",
            (order["user_id"],),
        ).fetchone()
        if replacement:
            replacement = dict(replacement)
            replacement_days = int(
                replacement.get("entitlement_days")
                or CYCLE_DAYS.get(replacement["billing_cycle"], 3650)
            )
            reward_days = max(1, replacement_days * REFERRAL_REWARD_PERCENT // 100)
            inserted = conn.execute(
                """INSERT OR IGNORE INTO rewards
                   (user_id,reward_type,days,reference,source_order_no,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    referral["referrer_id"],
                    "REFERRAL_30",
                    reward_days,
                    f"referral:{referral['id']}",
                    replacement["order_no"],
                    _iso(now),
                ),
            )
            if inserted.rowcount:
                grant_subscription_days(
                    conn,
                    referral["referrer_id"],
                    reward_days,
                    replacement["plan_type"],
                    now,
                )
                conn.execute(
                    "UPDATE referrals SET status='qualified' WHERE id=? AND status='registered'",
                    (referral["id"],),
                )

    def process_reversal(
        self,
        event_id: str,
        order_no: str,
        raw_data: dict[str, Any],
        reason: str,
    ) -> bool:
        """Apply a verified provider refund, dispute, or chargeback without voluntary-refund rules."""
        if not event_id or len(event_id) > 128 or not reason or len(reason) > 80:
            raise ValueError("支付逆转事件资料无效。")
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            inserted = conn.execute(
                """INSERT OR IGNORE INTO payment_callbacks
                   (event_id,order_no,raw_data,processed,created_at) VALUES (?,?,?,?,?)""",
                (event_id, order_no, json.dumps(raw_data, ensure_ascii=False), 0, _iso(now)),
            )
            if not inserted.rowcount:
                return False
            row = conn.execute(
                "SELECT * FROM subscription_orders WHERE order_no=?", (order_no,)
            ).fetchone()
            if not row:
                raise ValueError("支付逆转无法匹配订单。")
            order = dict(row)
            if order["status"] == "refunded":
                conn.execute("UPDATE payment_callbacks SET processed=1 WHERE event_id=?", (event_id,))
                return False
            if order["status"] != "paid":
                raise ValueError("只有已支付订单可执行支付平台逆转。")
            if order.get("previous_plan_type") is None:
                raise ValueError("订单缺少付款前订阅快照，需人工核对支付逆转。")
            changed = conn.execute(
                """UPDATE subscription_orders SET status='refunded',refunded_at=?
                   WHERE order_no=? AND status='paid'""",
                (_iso(now), order_no),
            )
            if changed.rowcount != 1:
                raise ValueError("订单状态已变更，请重试支付逆转事件。")
            self._reverse_entitlements(conn, order, now)
            conn.execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (
                    order["user_id"],
                    "PAYMENT_EXTERNAL_REVERSAL",
                    json.dumps({"order_no": order_no, "reason": reason}, ensure_ascii=False),
                    _iso(now),
                ),
            )
            conn.execute("UPDATE payment_callbacks SET processed=1 WHERE event_id=?", (event_id,))
        return True

    def refund_eligibility(self, order_no: str) -> tuple[bool, str]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM subscription_orders WHERE order_no=?", (order_no,)).fetchone()
            if not row:
                raise ValueError("订单不存在。")
            return self._refund_eligibility(conn, dict(row), datetime.now(UTC))

    @staticmethod
    def _refund_eligibility(conn: Any, order: dict[str, Any], now: datetime) -> tuple[bool, str]:
        if order["status"] != "paid" or not order["paid_at"]:
            return False, "只有已支付订单可申请退款。"
        if order.get("previous_plan_type") is None:
            return False, "订单缺少付款前订阅快照，需由客服人工核对。"
        try:
            paid_at = datetime.fromisoformat(order["paid_at"])
            if paid_at.tzinfo is None:
                paid_at = paid_at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return False, "订单付款时间无效，需由客服人工核对。"
        if now - paid_at > timedelta(hours=24):
            return False, "已超过购买后 24 小时退款窗口。"
        latest = conn.execute(
            """SELECT order_no FROM subscription_orders
               WHERE user_id=? AND status='paid' ORDER BY paid_at DESC,id DESC LIMIT 1""",
            (order["user_id"],),
        ).fetchone()
        if not latest or latest["order_no"] != order["order_no"]:
            return False, "只能退款该用户最近一笔已支付订阅订单。"
        placeholders = ",".join("?" for _ in CORE_ACTIONS)
        used = conn.execute(
            f"SELECT 1 FROM strategy_action_logs WHERE user_id=? AND created_at>=? AND action IN ({placeholders}) LIMIT 1",
            (order["user_id"], order["paid_at"], *CORE_ACTIONS),
        ).fetchone()
        if used:
            return False, "账户已使用回测、预警、策略详情、复制信号或券商连接等核心功能。"
        return True, "符合 24 小时且未使用核心功能的退款条件。"

    def mark_refunded(self, admin_id: int, order_no: str) -> None:
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            # ponytail: SQLite-wide write lock; use row locks if callback volume outgrows SQLite.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM subscription_orders WHERE order_no=?", (order_no,)).fetchone()
            if not row:
                raise ValueError("订单不存在。")
            order = dict(row)
            allowed, reason = self._refund_eligibility(conn, order, now)
            if not allowed:
                raise ValueError(reason)
            updated = conn.execute(
                "UPDATE subscription_orders SET status='refunded',refunded_at=? WHERE order_no=? AND status='paid'",
                (_iso(now), order_no),
            )
            if updated.rowcount != 1:
                raise ValueError("订单状态已变更，请刷新后重试。")
            self._reverse_entitlements(conn, order, now)
            conn.execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (admin_id, "ADMIN_REFUND", json.dumps({"order_no": order_no}, ensure_ascii=False), _iso(now)),
            )

    def log_action(self, user_id: int, action: str, details: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
            (user_id, action, json.dumps(details, ensure_ascii=False), _iso()),
        )

    def log_core_action(self, user_id: int, strategy: str, action: str, params: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO strategy_action_logs (user_id,strategy_name,action,params,result,created_at) VALUES (?,?,?,?,?,?)",
            (user_id, strategy, action, json.dumps(params, ensure_ascii=False), "success", _iso()),
        )
