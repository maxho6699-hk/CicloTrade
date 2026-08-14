# -*- coding: utf-8 -*-
"""订阅订单、支付回调幂等和退款资格。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
from enum import Enum
import hashlib
import json
import os
import secrets
from typing import Any

from core.database import DatabaseManager, get_database
from core.entitlement_policy import (
    current_plan_commerce_decision,
    published_plan_commerce_decision,
    validate_order_policy_snapshot,
)
from core.membership import (
    MembershipPlanConflict,  # noqa: F401
    add_membership_entitlement,
    assert_plan_not_lower,
    resolve_membership,
    revoke_membership_entitlement,
)
from core.plans import PLAN_ORDER, PLANS
from core.referral_affiliate import ReferralCommissionService
from payment.promotion_adapter import PromotionOrderAdapter


CYCLE_DAYS = {"monthly": 30, "quarterly": 90, "yearly": 365}
YEARLY_PROMO_DAYS = 90
TERMINAL_STATUSES = {"paid", "failed", "cancelled", "refunded"}
LEGACY_REFERRAL_REWARD_PERCENT = 30
TERMS_VERSION = "2026-08-07-no-refund-v1"
ORDER_EXPIRY_HOURS = {"telegram": 1, "web": 24, "legacy": 24}
MAX_PENDING_MANUAL_ORDERS = 3


class _EntitlementCommercePolicy:
    """Expose the published membership policy to promotion pricing."""

    @staticmethod
    def purchasable_plans(conn: Any, *, at: datetime) -> tuple[str, ...]:
        allowed: list[str] = []
        for plan in PLANS:
            if plan == "免费版":
                continue
            _, decision = current_plan_commerce_decision(
                conn, plan, "purchase", as_of=at,
            )
            if decision["allowed"]:
                allowed.append(plan)
        return tuple(allowed)

    @staticmethod
    def assert_purchasable(
        conn: Any, *, plan: str, cycle: str, at: datetime
    ) -> None:
        if plan not in PLANS or cycle not in PLANS[plan]["prices"]:
            raise ValueError("优惠码不适用于此订单。")
        _, decision = current_plan_commerce_decision(
            conn, plan, "purchase", as_of=at,
        )
        if not decision["allowed"]:
            raise PermissionError("优惠码不能用于当前停售或不可购买的方案。")


def _canonical_event_payload(value: dict[str, Any]) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _existing_event_matches(
    conn: Any, event_id: str, order_no: str, event_payload: dict[str, Any]
) -> bool:
    existing = conn.execute(
        "SELECT order_no,raw_data FROM payment_callbacks WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if not existing:
        return False
    try:
        stored = _canonical_event_payload(json.loads(str(existing["raw_data"])))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("支付事件历史记录无法核验。") from exc
    if (
        str(existing["order_no"] or "") != str(order_no)
        or stored != _canonical_event_payload(event_payload)
    ):
        raise ValueError("支付事件编号已用于不同请求。")
    return True


def _purchase_action(current_plan: str, target_plan: str) -> str:
    if current_plan == "免费版":
        return "purchase"
    return "renew" if current_plan == target_plan else "upgrade"


class ManualPaymentMethod(str, Enum):
    FPS = "fps"
    ALIPAY = "alipay"
    WECHAT = "wechat"


MANUAL_PAYMENT_METHODS = frozenset(method.value for method in ManualPaymentMethod)
LEGACY_PROVIDER_METHODS = frozenset({"paypal", "paddle"})
PAYMENT_METHOD_LABELS = {
    ManualPaymentMethod.FPS.value: "FPS 转数快",
    ManualPaymentMethod.ALIPAY.value: "支付宝",
    ManualPaymentMethod.WECHAT.value: "微信支付",
    "paypal": "PayPal（历史）",
    "paddle": "Paddle（历史）",
}
MANUAL_PAYMENT_INSTRUCTION_ENVS = {
    ManualPaymentMethod.FPS.value: "FPS_PAYMENT_INSTRUCTIONS",
    ManualPaymentMethod.ALIPAY.value: "ALIPAY_PAYMENT_INSTRUCTIONS",
    ManualPaymentMethod.WECHAT.value: "WECHAT_PAYMENT_INSTRUCTIONS",
}


def manual_payment_instructions(method: str) -> str:
    """Return configured receiving instructions with safe, predictable line breaks."""
    env_name = MANUAL_PAYMENT_INSTRUCTION_ENVS.get(str(method or "").strip().lower())
    if not env_name:
        return ""
    value = os.getenv(env_name, "")
    return value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").strip()


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="seconds")


def grant_subscription_days(
    conn: Any,
    user_id: int,
    days: int,
    fallback_plan: str,
    now: datetime | None = None,
    *,
    source_kind: str = "membership_grant",
    source_ref: str | None = None,
) -> str:
    """Extend one effective plan through the canonical entitlement ledger."""
    days = int(days)
    if days < 1 or fallback_plan not in PLANS or fallback_plan == "免费版":
        raise ValueError("奖励订阅权益无效。")
    base = now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    current = resolve_membership(conn, user_id, base, sync_cache=True)
    plan = str(current["plan_type"])
    if plan == "免费版":
        plan = fallback_plan
    _, decision = current_plan_commerce_decision(
        conn, plan, "admin_grant", as_of=base,
    )
    if not decision["allowed"]:
        raise ValueError("当前会员策略不允许赠送或延长该方案。")
    state = add_membership_entitlement(
        conn,
        user_id,
        plan,
        days,
        source_kind=source_kind,
        source_ref=source_ref
        or f"grant:{int(user_id)}:{_iso(base)}:{secrets.token_hex(8)}",
        now=base,
    )
    expiry = state["subscription_expire"]
    if not expiry:
        raise ValueError("奖励订阅权益未能生效。")
    return expiry


class OrderService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def annual_bonus_enabled(self) -> bool:
        row = self.db.fetch_one(
            "SELECT control_value FROM platform_controls WHERE control_key='annual_bonus_enabled'"
        )
        return not row or str(row["control_value"]).lower() in {"1", "true", "yes", "on"}

    def create_order(
        self,
        user_id: int,
        plan: str,
        cycle: str,
        method: str,
        *,
        terms_accepted: bool = False,
        idempotency_key: str | None = None,
        source: str = "web",
        coupon_code: str | None = None,
    ) -> dict[str, Any]:
        if terms_accepted is not True:
            raise ValueError("建立訂單前必須同意用戶協議、風險披露與不退款政策。")
        if plan not in PLANS or plan == "免费版":
            raise ValueError("请选择可购买的订阅方案。")
        prices = PLANS[plan]["prices"]
        if cycle not in prices:
            raise ValueError("该方案不支持所选付款周期。")
        source = str(source or "web").strip().lower()
        if source not in ORDER_EXPIRY_HOURS:
            raise ValueError("订单来源无效。")
        method = str(method or "").strip().lower()
        if method not in MANUAL_PAYMENT_METHODS and not (
            source == "legacy" and method in LEGACY_PROVIDER_METHODS
        ):
            raise ValueError("新订单仅支持 FPS、支付宝或微信支付人工付款。")
        if idempotency_key is not None:
            idempotency_key = str(idempotency_key).strip()
            if not 1 <= len(idempotency_key) <= 128:
                raise ValueError("幂等键无效。")
        entitlement_days = CYCLE_DAYS.get(cycle, 3650)
        if cycle == "yearly" and self.annual_bonus_enabled():
            entitlement_days += YEARLY_PROMO_DAYS
        amount = float(prices[cycle])
        list_price_minor = int(round(amount * 100))
        canonical_coupon = str(coupon_code or "").strip().upper() or None
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute(
                "SELECT is_active,plan_type,subscription_expire FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            if not user or not user["is_active"]:
                raise PermissionError("账户不存在或已停用。")
            current = resolve_membership(conn, user_id, now, sync_cache=True)
            assert_plan_not_lower(str(current["plan_type"]), plan)
            current_plan = str(current["plan_type"])
            action = _purchase_action(current_plan, plan)
            entitlement_policy, decision = current_plan_commerce_decision(
                conn, plan, action, as_of=now,
            )
            if not decision["allowed"]:
                raise ValueError("当前会员策略不允许购买、续费或升级至该方案。")
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "plan": plan,
                        "cycle": cycle,
                        "method": method,
                        "coupon_code": canonical_coupon,
                        "list_price_minor": list_price_minor,
                        "entitlement_days": entitlement_days,
                        "terms_version": TERMS_VERSION,
                        "entitlement_policy_key": entitlement_policy.policy_key,
                        "entitlement_policy_version": entitlement_policy.version,
                        "entitlement_policy_sha256": entitlement_policy.policy_sha256,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if method in MANUAL_PAYMENT_METHODS:
                placeholders = ",".join("?" for _ in MANUAL_PAYMENT_METHODS)
                conn.execute(
                    f"""UPDATE subscription_orders SET status='cancelled'
                        WHERE user_id=? AND status='pending' AND pay_method IN ({placeholders})
                          AND expires_at IS NOT NULL AND datetime(expires_at)<=datetime(?)
                          AND NOT EXISTS (
                              SELECT 1 FROM manual_payment_claims c
                              WHERE c.order_no=subscription_orders.order_no AND c.status='submitted'
                          )""",
                    (user_id, *sorted(MANUAL_PAYMENT_METHODS), _iso(now)),
                )
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM subscription_orders WHERE user_id=? AND idempotency_key=?",
                    (user_id, idempotency_key),
                ).fetchone()
                if existing:
                    if existing["request_fingerprint"] != fingerprint:
                        raise ValueError("幂等键已用于不同的订单请求。")
                    if method in MANUAL_PAYMENT_METHODS:
                        from payment.receiving_profile import ReceivingProfileService

                        ReceivingProfileService.snapshot_order(conn, str(existing["order_no"]), method)
                    return dict(existing)
            if method in MANUAL_PAYMENT_METHODS:
                existing_purchase = conn.execute(
                    """SELECT * FROM subscription_orders
                       WHERE user_id=? AND status='pending' AND request_fingerprint=?
                         AND datetime(expires_at)>datetime(?)
                       ORDER BY id DESC LIMIT 1""",
                    (user_id, fingerprint, _iso(now)),
                ).fetchone()
                if existing_purchase:
                    from payment.receiving_profile import ReceivingProfileService

                    ReceivingProfileService.snapshot_order(
                        conn, str(existing_purchase["order_no"]), method
                    )
                    return dict(existing_purchase)
                pending = conn.execute(
                    """SELECT COUNT(*) FROM subscription_orders
                       WHERE user_id=? AND status='pending'
                         AND pay_method IN ({}) AND datetime(expires_at)>datetime(?)""".format(
                        ",".join("?" for _ in MANUAL_PAYMENT_METHODS)
                    ),
                    (user_id, *sorted(MANUAL_PAYMENT_METHODS), _iso(now)),
                ).fetchone()[0]
                if pending >= MAX_PENDING_MANUAL_ORDERS:
                    raise ValueError("待付款订单过多，请先完成现有订单或等待订单到期。")
            order_no = f"TA{now:%Y%m%d%H%M%S}{secrets.token_hex(3).upper()}"
            expires_at = _iso(now + timedelta(hours=ORDER_EXPIRY_HOURS[source]))
            promotion_adapter = PromotionOrderAdapter(_EntitlementCommercePolicy())
            quote = promotion_adapter.quote(
                conn,
                user_id=user_id,
                plan=plan,
                cycle=cycle,
                list_price_minor=list_price_minor,
                coupon_code=canonical_coupon,
                now=now,
            )
            snapshot = promotion_adapter.bind_order_snapshot(
                conn,
                quote=quote,
                order_no=order_no,
                user_id=user_id,
                plan_type=plan,
                billing_cycle=cycle,
                currency="HKD",
            )
            final_amount_minor = int(snapshot["final_amount_minor"])
            amount = final_amount_minor / 100
            conn.execute(
                """INSERT INTO subscription_orders
                   (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,
                    entitlement_days,created_at,terms_version,terms_accepted_at,source,idempotency_key,
                    request_fingerprint,amount_minor,expires_at,
                    entitlement_policy_key_snapshot,entitlement_policy_version_snapshot,
                    entitlement_policy_sha256_snapshot,entitlement_purchase_action_snapshot,
                    list_price_minor,coupon_discount_minor,referral_discount_minor,final_amount_minor,
                    coupon_code_snapshot,coupon_version_snapshot,referral_policy_version,
                    referral_eligible_snapshot,referral_commission_rate_bps_snapshot,
                    referral_commission_cap_minor_snapshot,referral_hold_days_snapshot,
                    referral_bonus_policy_snapshot,promotion_snapshot_sha256,
                    referral_attribution_id_snapshot,referral_referrer_user_id_snapshot,
                    referral_referred_user_id_snapshot)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_no, user_id, plan, cycle, amount, "HKD", method, "pending",
                    entitlement_days, _iso(now), TERMS_VERSION, _iso(now), source, idempotency_key,
                    fingerprint, final_amount_minor, expires_at,
                    entitlement_policy.policy_key, entitlement_policy.version,
                    entitlement_policy.policy_sha256, action,
                    int(snapshot["list_price_minor"]),
                    int(snapshot["coupon_discount_minor"]),
                    int(snapshot["referral_discount_minor"]),
                    final_amount_minor,
                    snapshot.get("coupon_code_snapshot"),
                    snapshot.get("coupon_version_snapshot"),
                    snapshot["referral_policy_version"],
                    int(snapshot["referral_eligible_snapshot"]),
                    int(snapshot.get("commission_rate_bps") or 0),
                    int(snapshot.get("commission_cap_minor") or 0),
                    int(snapshot.get("hold_days") or 0),
                    snapshot.get("bonus_policy_snapshot"),
                    snapshot["promotion_snapshot_sha256"],
                    snapshot.get("referral_attribution_id_snapshot"),
                    snapshot.get("referral_referrer_user_id_snapshot"),
                    snapshot.get("referral_referred_user_id_snapshot"),
                ),
            )
            promotion_adapter.reserve_coupon(
                conn, quote=snapshot, user_id=user_id, order_no=order_no, now=now,
            )
            if method in MANUAL_PAYMENT_METHODS:
                from payment.receiving_profile import ReceivingProfileService

                ReceivingProfileService.snapshot_order(conn, order_no, method)
            conn.execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (
                    user_id,
                    "ORDER_CREATE",
                    json.dumps(
                        {"order_no": order_no, "plan": plan, "method": method,
                         "source": source, "terms_version": TERMS_VERSION},
                        ensure_ascii=False,
                    ),
                    _iso(now),
                ),
            )
            return dict(conn.execute("SELECT * FROM subscription_orders WHERE order_no=?", (order_no,)).fetchone())

    def get_order(self, order_no: str) -> dict[str, Any]:
        order = self.db.fetch_one("SELECT * FROM subscription_orders WHERE order_no=?", (order_no,))
        if not order:
            raise ValueError("订单不存在。")
        return order

    def get_order_for_user(self, user_id: int, order_no: str) -> dict[str, Any]:
        order = self.db.fetch_one(
            "SELECT * FROM subscription_orders WHERE order_no=? AND user_id=?", (order_no, user_id)
        )
        if not order:
            raise PermissionError("订单不存在或不属于当前用户。")
        return order

    def list_orders(self, user_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM subscription_orders WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        )

    def list_pending_orders(self, user_id: int, source: str | None = None) -> list[dict[str, Any]]:
        params: tuple[Any, ...] = (user_id,)
        clause = ""
        if source is not None:
            clause = " AND source=?"
            params = (user_id, source)
        return self.db.fetch_all(
            """SELECT * FROM subscription_orders WHERE user_id=? AND status='pending'""" + clause
            + " ORDER BY created_at DESC", params
        )

    @staticmethod
    def _validated_claim_evidence(
        evidence_file_id: str | None,
        evidence_file_unique_id: str | None,
        evidence_message_id: str | int | None,
    ) -> tuple[str | None, str | None, str | None]:
        file_id = str(evidence_file_id).strip() if evidence_file_id is not None else None
        unique_id = str(evidence_file_unique_id).strip() if evidence_file_unique_id is not None else None
        message_id = str(evidence_message_id).strip() if evidence_message_id is not None else None
        if file_id is None or unique_id is None:
            raise ValueError("必须上传 Telegram 付款凭证截图。")
        for value in (file_id, unique_id):
            if value is not None and not 1 <= len(value) <= 256:
                raise ValueError("Telegram 文件凭证无效。")
        if message_id is not None and (not message_id.isdigit() or not 1 <= len(message_id) <= 64):
            raise ValueError("Telegram 消息凭证无效。")
        return file_id, unique_id, message_id

    def require_payment_claim_capacity(self, user_id: int) -> None:
        recent = self.db.fetch_one(
            """SELECT COUNT(*) count FROM manual_payment_claims
               WHERE user_id=? AND datetime(created_at)>=datetime(?)""",
            (int(user_id), _iso(datetime.now(UTC) - timedelta(hours=1))),
        )
        if int((recent or {}).get("count") or 0) >= 3:
            raise ValueError("付款凭证提交过于频繁，请稍后再试。")

    def submit_manual_payment_claim(
        self,
        user_id: int,
        order_no: str,
        *,
        evidence_file_id: str | None = None,
        evidence_file_unique_id: str | None = None,
        evidence_message_id: str | int | None = None,
        file_id: str | None = None,
        file_unique_id: str | None = None,
        message_id: str | int | None = None,
        source_update_id: str | int | None = None,
        evidence_source: str = "telegram",
        evidence_storage_key: str | None = None,
        evidence_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Submit payment proof metadata without granting entitlement."""
        evidence_file_id = evidence_file_id if evidence_file_id is not None else file_id
        evidence_file_unique_id = evidence_file_unique_id if evidence_file_unique_id is not None else file_unique_id
        evidence_message_id = evidence_message_id if evidence_message_id is not None else message_id
        file_id, unique_id, message_id = self._validated_claim_evidence(
            evidence_file_id, evidence_file_unique_id, evidence_message_id
        )
        evidence_source = str(evidence_source or "").strip().lower()
        storage_key = str(evidence_storage_key or "").strip().lower() or None
        proof_sha256 = str(evidence_sha256 or "").strip().lower() or None
        if evidence_source not in {"telegram", "web"}:
            raise ValueError("付款凭证来源无效。")
        if evidence_source == "web":
            if storage_key is None or file_id != f"web:{storage_key}":
                raise ValueError("网站付款凭证存储资料无效。")
        if (storage_key is None) != (proof_sha256 is None):
            raise ValueError("付款凭证存储资料不完整。")
        if proof_sha256 is not None and (
            len(proof_sha256) != 64 or any(char not in "0123456789abcdef" for char in proof_sha256)
        ):
            raise ValueError("付款凭证内容摘要无效。")
        update_id = str(source_update_id).strip() if source_update_id is not None else None
        if update_id is not None and (not update_id or len(update_id) > 64):
            raise ValueError("Telegram 更新编号无效。")
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute("SELECT is_active FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or not user["is_active"]:
                raise PermissionError("账户不存在或已停用。")
            if update_id:
                receipt = conn.execute(
                    "SELECT claim_id FROM telegram_callback_receipts WHERE update_id=?", (update_id,)
                ).fetchone()
                if receipt and receipt["claim_id"]:
                    claim = conn.execute("SELECT * FROM manual_payment_claims WHERE id=?", (receipt["claim_id"],)).fetchone()
                    if claim:
                        return dict(claim)
            order = conn.execute(
                "SELECT * FROM subscription_orders WHERE order_no=? AND user_id=?", (order_no, user_id)
            ).fetchone()
            if not order:
                raise PermissionError("订单不存在或不属于当前用户。")
            if order["status"] != "pending":
                raise ValueError("只有待付款订单可以提交付款凭证。")
            if str(order["pay_method"]) not in MANUAL_PAYMENT_METHODS:
                raise ValueError("此订单不是人工付款订单，不能提交人工付款凭证。")
            if order["expires_at"]:
                try:
                    expires_at = datetime.fromisoformat(order["expires_at"])
                    expires_at = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
                    if expires_at <= now:
                        raise ValueError("订单已过期，请重新建立订单。")
                except (TypeError, ValueError) as exc:
                    if str(exc) == "订单已过期，请重新建立订单。":
                        raise
                    raise ValueError("订单过期时间无效。") from exc
            existing = conn.execute(
                "SELECT * FROM manual_payment_claims WHERE order_no=? AND status='submitted'",
                (order_no,),
            ).fetchone()
            if existing:
                return dict(existing)
            current = resolve_membership(conn, user_id, now, sync_cache=True)
            assert_plan_not_lower(str(current["plan_type"]), str(order["plan_type"]))
            if proof_sha256:
                duplicate_evidence = conn.execute(
                    """SELECT order_no FROM manual_payment_claims
                       WHERE evidence_sha256=? AND order_no<>? AND status IN ('submitted','approved')
                       LIMIT 1""",
                    (proof_sha256, order_no),
                ).fetchone()
                if duplicate_evidence:
                    raise ValueError("这张付款凭证已经用于其他订单。")
            recent = conn.execute(
                "SELECT COUNT(*) FROM manual_payment_claims WHERE user_id=? AND datetime(created_at)>=datetime(?)",
                (user_id, _iso(now - timedelta(hours=1))),
            ).fetchone()[0]
            if recent >= 3:
                raise ValueError("付款凭证提交过于频繁，请稍后再试。")
            attempt = conn.execute(
                "SELECT COALESCE(MAX(attempt),0)+1 FROM manual_payment_claims WHERE order_no=?", (order_no,)
            ).fetchone()[0]
            inserted = conn.execute(
                """INSERT INTO manual_payment_claims
                   (order_no,user_id,attempt,status,evidence_file_id,evidence_file_unique_id,
                    evidence_message_id,source_update_id,evidence_source,evidence_storage_key,
                    evidence_sha256,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_no, user_id, attempt, "submitted", file_id, unique_id, message_id,
                    update_id, evidence_source, storage_key, proof_sha256, _iso(now),
                ),
            )
            claim_id = inserted.lastrowid
            if update_id:
                conn.execute(
                    """INSERT INTO telegram_callback_receipts
                       (update_id,user_id,claim_id,payload_fingerprint,received_at) VALUES (?,?,?,?,?)""",
                    (
                        update_id, user_id, claim_id,
                        hashlib.sha256(json.dumps([order_no, file_id, unique_id, message_id]).encode("utf-8")).hexdigest(),
                        _iso(now),
                    ),
                )
            conn.execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (user_id, "MANUAL_PAYMENT_CLAIM_SUBMITTED", json.dumps({"order_no": order_no, "claim_id": claim_id}, ensure_ascii=False), _iso(now)),
            )
            return dict(conn.execute("SELECT * FROM manual_payment_claims WHERE id=?", (claim_id,)).fetchone())

    def attach_external_id(self, order_no: str, external_id: str, price_id: str | None = None) -> None:
        if not external_id or len(external_id) > 128 or (price_id is not None and len(price_id) > 128):
            raise ValueError("外部支付交易资料无效。")
        self.db.execute(
            """UPDATE subscription_orders SET external_id=?,external_price_id=?
               WHERE order_no=? AND status='pending' AND external_id IS NULL""",
            (external_id, price_id, order_no),
        )

    @staticmethod
    def _activate_paid_order(
        conn: Any, order: dict[str, Any], now: datetime, capture_id: str | None = None
    ) -> bool:
        """Mark one pending order paid and apply its entitlement in this transaction."""
        PromotionOrderAdapter.assert_snapshot_binding(conn, order)
        current = resolve_membership(
            conn, int(order["user_id"]), now, sync_cache=True
        )
        order_policy = validate_order_policy_snapshot(conn, order)
        action = _purchase_action(str(current["plan_type"]), str(order["plan_type"]))
        snapshot_action = order.get("entitlement_purchase_action_snapshot")
        if snapshot_action is not None and snapshot_action != action:
            raise ValueError("付款时会员状态已变化，订单商业动作不再有效。")
        decision = published_plan_commerce_decision(
            conn, order_policy, str(order["plan_type"]), action, as_of=now,
        )
        if not decision["allowed"]:
            raise ValueError("订单创建时的会员策略不允许该商业动作。")
        assert_plan_not_lower(str(current["plan_type"]), str(order["plan_type"]))
        PromotionOrderAdapter.activate_paid(
            conn, order=order, pre_membership=current, now=now,
        )
        changed = conn.execute(
            """UPDATE subscription_orders
               SET status='paid',paid_at=?,previous_plan_type=?,previous_subscription_expire=?,
                   external_capture_id=COALESCE(external_capture_id,?)
               WHERE order_no=? AND status='pending'""",
            (_iso(now), current["plan_type"], current["subscription_expire"], capture_id, order["order_no"]),
        )
        if changed.rowcount != 1:
            return False
        days = int(order.get("entitlement_days") or CYCLE_DAYS.get(order["billing_cycle"], 3650))
        state = add_membership_entitlement(
            conn,
            int(order["user_id"]),
            str(order["plan_type"]),
            days,
            source_kind="payment_order",
            source_ref=str(order["order_no"]),
            now=now,
        )
        current_rank = PLAN_ORDER.index(str(state["plan_type"]))
        lower_plans = PLAN_ORDER[:current_rank]
        if lower_plans:
            placeholders = ",".join("?" for _ in lower_plans)
            conn.execute(
                f"""UPDATE subscription_orders SET status='cancelled'
                    WHERE user_id=? AND order_no<>? AND status='pending'
                      AND plan_type IN ({placeholders})
                      AND NOT EXISTS (
                          SELECT 1 FROM manual_payment_claims c
                          WHERE c.order_no=subscription_orders.order_no
                            AND c.status='submitted'
                      )""",
                (int(order["user_id"]), str(order["order_no"]), *lower_plans),
            )
        return True

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
        event_payload = {"kind": "payment_callback", "status": status, "data": raw_data}
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _existing_event_matches(conn, event_id, order_no, event_payload):
                return False
            inserted = conn.execute(
                "INSERT OR IGNORE INTO payment_callbacks (event_id,order_no,raw_data,processed,created_at) VALUES (?,?,?,?,?)",
                (event_id, order_no, _canonical_event_payload(event_payload), 0, _iso(now)),
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
            if str(order["pay_method"]) in MANUAL_PAYMENT_METHODS:
                raise ValueError("人工付款订单只能通过财务审核处理。")
            if status == "paid":
                capture_id = raw_data.get("capture_id")
                if capture_id is not None and (
                    not isinstance(capture_id, str) or not capture_id or len(capture_id) > 128
                ):
                    raise ValueError("外部支付捕获编号无效。")
                if not self._activate_paid_order(conn, order, now, capture_id):
                    conn.execute("UPDATE payment_callbacks SET processed=1 WHERE event_id=?", (event_id,))
                    return False
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
        ledger_entitlement = conn.execute(
            """SELECT id FROM membership_entitlements
               WHERE user_id=? AND source_kind='payment_order' AND source_ref=?""",
            (order["user_id"], order["order_no"]),
        ).fetchone()
        if ledger_entitlement:
            revoke_membership_entitlement(
                conn,
                int(order["user_id"]),
                source_kind="payment_order",
                source_ref=str(order["order_no"]),
                now=now,
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
            if referral and reward:
                revoke_membership_entitlement(
                    conn,
                    int(reward["user_id"]),
                    source_kind="referral_reward",
                    source_ref=f"reward:{reward['id']}",
                    now=now,
                )
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
                replacement_row = conn.execute(
                    """SELECT * FROM subscription_orders
                       WHERE user_id=? AND status='paid'
                       ORDER BY paid_at,id LIMIT 1""",
                    (order["user_id"],),
                ).fetchone()
                cash_enabled = str(conn.execute(
                    "SELECT control_value FROM platform_controls WHERE control_key='referral_cash_enabled'"
                ).fetchone()[0]).lower() in {"1", "true", "yes", "on"}
                if replacement_row and not cash_enabled:
                    replacement = dict(replacement_row)
                    replacement_days = int(
                        replacement.get("entitlement_days")
                        or CYCLE_DAYS.get(replacement["billing_cycle"], 3650)
                    )
                    reward_days = max(
                        1, replacement_days * LEGACY_REFERRAL_REWARD_PERCENT // 100
                    )
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
                            source_kind="referral_reward",
                            source_ref=f"reward:{inserted.lastrowid}",
                        )
                        conn.execute(
                            "UPDATE referrals SET status='qualified' WHERE id=? AND status='registered'",
                            (referral["id"],),
                        )
            return

        legacy_snapshot = conn.execute(
            """SELECT 1 FROM membership_entitlements
               WHERE user_id=? AND source_kind='legacy_cache' AND status='active'
               LIMIT 1""",
            (order["user_id"],),
        ).fetchone()
        if legacy_snapshot:
            raise ValueError(
                "历史会员权益缺少可核对的订单来源，不能自动冲正；请转人工核销。"
            )

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
        cash_enabled_row = conn.execute(
            "SELECT control_value FROM platform_controls WHERE control_key='referral_cash_enabled'"
        ).fetchone()
        cash_enabled = bool(cash_enabled_row and str(cash_enabled_row[0]).lower() in {"1", "true", "yes", "on"})
        if replacement and not cash_enabled:
            replacement = dict(replacement)
            replacement_days = int(
                replacement.get("entitlement_days")
                or CYCLE_DAYS.get(replacement["billing_cycle"], 3650)
            )
            reward_days = max(1, replacement_days * LEGACY_REFERRAL_REWARD_PERCENT // 100)
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
                    source_kind="referral_reward",
                    source_ref=f"reward:{inserted.lastrowid}",
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
        event_payload = {"kind": "payment_reversal", "reason": reason, "data": raw_data}
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _existing_event_matches(conn, event_id, order_no, event_payload):
                return False
            inserted = conn.execute(
                """INSERT OR IGNORE INTO payment_callbacks
                   (event_id,order_no,raw_data,processed,created_at) VALUES (?,?,?,?,?)""",
                (event_id, order_no, _canonical_event_payload(event_payload), 0, _iso(now)),
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
            is_v2 = bool(order.get("referral_policy_version"))
            total_minor = int(
                order.get("final_amount_minor")
                or order.get("amount_minor")
                or round(float(order["amount"]) * 100)
            )
            reversal_minor = (
                PromotionOrderAdapter.verified_reversal_minor(order, raw_data)
                if is_v2 else total_minor
            )
            previous_refunded = int(order.get("refunded_minor") or 0)
            refunded_minor = previous_refunded + reversal_minor
            fully_refunded = refunded_minor == total_minor
            changed = conn.execute(
                """UPDATE subscription_orders
                   SET refunded_minor=?,status=CASE WHEN ? THEN 'refunded' ELSE status END,
                       refunded_at=CASE WHEN ? THEN ? ELSE refunded_at END
                   WHERE order_no=? AND status='paid' AND refunded_minor=?""",
                (
                    refunded_minor, int(fully_refunded), int(fully_refunded),
                    _iso(now), order_no, previous_refunded,
                ),
            )
            if changed.rowcount != 1:
                raise ValueError("订单状态已变更，请重试支付逆转事件。")
            if is_v2:
                PromotionOrderAdapter.record_reversal(
                    conn,
                    event_key=event_id,
                    order=order,
                    amount_minor=reversal_minor,
                    reason=reason,
                    now=now,
                )
            else:
                ReferralCommissionService.record_reversal(
                    conn,
                    event_key=event_id,
                    order=order,
                    amount_minor=reversal_minor,
                    reason=reason,
                    now=now,
                )
            if fully_refunded:
                self._reverse_entitlements(conn, order, now)
            conn.execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (
                    order["user_id"],
                    "PAYMENT_EXTERNAL_REVERSAL",
                    json.dumps(
                        {
                            "order_no": order_no,
                            "reason": reason,
                            "reversal_minor": reversal_minor,
                            "refunded_minor": refunded_minor,
                            "fully_refunded": fully_refunded,
                        },
                        ensure_ascii=False,
                    ),
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
        del conn, order, now
        return False, "CicloTrade 數碼服務一經付款概不接受主動退款；支付平台強制逆轉或法定權利另行處理。"

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
