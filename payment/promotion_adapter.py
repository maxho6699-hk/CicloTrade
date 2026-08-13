"""Isolated promotion hooks for the subscription-order integration owner.

This module owns no order lifecycle.  It computes and validates immutable
promotion snapshots so the single owner of ``payment.order_service`` can call
it without duplicating coupon, referral, or refund rules.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
from typing import Any, Protocol, Sequence

from core.referral_affiliate import ReferralCommissionService
from core.referral_coupon import ReferralCouponService


class PlanCommercePolicy(Protocol):
    """Point-in-time membership commerce proof supplied by entitlement policy."""

    def purchasable_plans(self, conn: Any, *, at: datetime) -> Sequence[str]: ...

    def assert_purchasable(
        self, conn: Any, *, plan: str, cycle: str, at: datetime
    ) -> None: ...


def _allowed_plans(
    policy: PlanCommercePolicy | None, conn: Any, *, at: datetime
) -> frozenset[str]:
    if policy is None:
        raise PermissionError("会员商业策略尚未接入，推广优惠暂不可用。")
    plans = policy.purchasable_plans(conn, at=at)
    if isinstance(plans, (str, bytes)):
        raise PermissionError("会员商业策略证明无效。")
    normalized = frozenset(str(plan) for plan in plans)
    if not normalized:
        raise PermissionError("当前没有可购买会员方案。")
    return normalized


class PromotionOrderAdapter:
    """Narrow integration seam for pricing, settlement and clawback."""

    def __init__(self, plan_policy: PlanCommercePolicy | None):
        self.plan_policy = plan_policy

    def validate_coupon_plans(
        self, conn: Any, *, plans: Sequence[str], cycles: Sequence[str], now: datetime
    ) -> None:
        allowed = _allowed_plans(self.plan_policy, conn, at=now)
        if not plans or isinstance(plans, (str, bytes)) or len(set(plans)) != len(plans):
            raise ValueError("优惠码适用方案无效。")
        if not cycles or isinstance(cycles, (str, bytes)) or len(set(cycles)) != len(cycles):
            raise ValueError("优惠码适用周期无效。")
        for plan in plans:
            if str(plan) not in allowed:
                raise PermissionError("优惠码不能用于当前停售或不可购买的方案。")
            for cycle in cycles:
                self.plan_policy.assert_purchasable(
                    conn, plan=str(plan), cycle=str(cycle), at=now
                )

    def quote(
        self,
        conn: Any,
        *,
        user_id: int,
        plan: str,
        cycle: str,
        list_price_minor: int,
        coupon_code: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        self.plan_policy.assert_purchasable(
            conn, plan=str(plan), cycle=str(cycle), at=now
        ) if self.plan_policy is not None else _allowed_plans(None, conn, at=now)
        quote = ReferralCouponService.quote_in_transaction(
            conn,
            user_id=int(user_id),
            plan=str(plan),
            cycle=str(cycle),
            list_price_minor=int(list_price_minor),
            coupon_code=coupon_code,
            now=now,
        )
        # The coupon service prices before an order number exists.  A digest at
        # that point cannot bind an owner or order identity, so only the order
        # creation path may produce the persisted digest below.
        quote.pop("promotion_snapshot_sha256", None)
        return quote

    @classmethod
    def bind_order_snapshot(
        cls,
        conn: Any,
        *,
        quote: dict[str, Any],
        order_no: str,
        user_id: int,
        plan_type: str,
        billing_cycle: str,
        currency: str,
    ) -> dict[str, Any]:
        """Bind a quoted promotion to one immutable order and attribution."""
        attribution = conn.execute(
            """SELECT id,referrer_user_id,referred_user_id FROM referral_attributions
               WHERE referred_user_id=?""",
            (int(user_id),),
        ).fetchone()
        eligible = int(quote.get("referral_eligible_snapshot") or 0)
        if eligible and not attribution:
            raise ValueError("推荐首单缺少归因快照。")
        snapshot = {
            **quote,
            "order_no": str(order_no), "user_id": int(user_id),
            "plan_type": str(plan_type), "billing_cycle": str(billing_cycle),
            "currency": str(currency).upper(),
            "referral_attribution_id_snapshot": int(attribution["id"]) if attribution else None,
            "referral_referrer_user_id_snapshot": (
                int(attribution["referrer_user_id"]) if attribution else None
            ),
            "referral_referred_user_id_snapshot": (
                int(attribution["referred_user_id"]) if attribution else None
            ),
        }
        snapshot["promotion_snapshot_sha256"] = cls.promotion_snapshot_sha256(snapshot)
        return snapshot

    @staticmethod
    def promotion_snapshot_sha256(snapshot: dict[str, Any]) -> str:
        """Hash only persisted order facts; mutable policy rows are excluded."""
        def value(name: str, alias: str | None = None) -> Any:
            return snapshot.get(name, snapshot.get(alias)) if alias else snapshot.get(name)

        payload = {
            "order_no": value("order_no"),
            "user_id": value("user_id"),
            "plan_type": value("plan_type"),
            "billing_cycle": value("billing_cycle"),
            "currency": value("currency"),
            "referral_attribution_id_snapshot": value("referral_attribution_id_snapshot"),
            "referral_referrer_user_id_snapshot": value("referral_referrer_user_id_snapshot"),
            "referral_referred_user_id_snapshot": value("referral_referred_user_id_snapshot"),
            "list_price_minor": value("list_price_minor"),
            "coupon_discount_minor": value("coupon_discount_minor"),
            "referral_discount_minor": value("referral_discount_minor"),
            "final_amount_minor": value("final_amount_minor"),
            "coupon_code_snapshot": value("coupon_code_snapshot"),
            "coupon_version_snapshot": value("coupon_version_snapshot"),
            "referral_policy_version": value("referral_policy_version"),
            "referral_eligible_snapshot": value("referral_eligible_snapshot"),
            "referral_commission_rate_bps_snapshot": value(
                "referral_commission_rate_bps_snapshot", "commission_rate_bps"
            ),
            "referral_commission_cap_minor_snapshot": value(
                "referral_commission_cap_minor_snapshot", "commission_cap_minor"
            ),
            "referral_hold_days_snapshot": value("referral_hold_days_snapshot", "hold_days"),
            "referral_bonus_policy_snapshot": value(
                "referral_bonus_policy_snapshot", "bonus_policy_snapshot"
            ),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def assert_snapshot_integrity(cls, order: dict[str, Any]) -> None:
        if not order.get("referral_policy_version"):
            return
        identity = ("order_no", "user_id", "plan_type", "billing_cycle", "currency")
        attribution = (
            "referral_attribution_id_snapshot", "referral_referrer_user_id_snapshot",
            "referral_referred_user_id_snapshot",
        )
        if any(key not in order or order[key] in (None, "") for key in identity):
            raise ValueError("推广订单身份快照不完整。")
        facts = [order.get(key) for key in attribution]
        if any(value is None for value in facts) and any(value is not None for value in facts):
            raise ValueError("推广订单归因快照不完整。")
        if int(order.get("referral_eligible_snapshot") or 0) and any(value is None for value in facts):
            raise ValueError("推荐首单缺少归因快照。")
        supplied = str(order.get("promotion_snapshot_sha256") or "")
        expected = cls.promotion_snapshot_sha256(order)
        if len(supplied) != 64 or not hmac.compare_digest(supplied, expected):
            raise ValueError("推广订单快照审计摘要不一致。")

    @classmethod
    def assert_snapshot_binding(cls, conn: Any, order: dict[str, Any]) -> None:
        cls.assert_snapshot_integrity(order)
        attribution_id = order.get("referral_attribution_id_snapshot")
        if attribution_id is None:
            return
        attribution = conn.execute(
            """SELECT 1 FROM referral_attributions
               WHERE id=? AND referrer_user_id=? AND referred_user_id=? AND referred_user_id=?""",
            (
                int(attribution_id), int(order["referral_referrer_user_id_snapshot"]),
                int(order["referral_referred_user_id_snapshot"]), int(order["user_id"]),
            ),
        ).fetchone()
        if not attribution:
            raise ValueError("推广订单归因快照与订单所有者不一致。")

    @staticmethod
    def reserve_coupon(
        conn: Any, *, quote: dict[str, Any], user_id: int, order_no: str, now: datetime
    ) -> None:
        ReferralCouponService.redeem_in_transaction(
            conn, quote=quote, user_id=int(user_id), order_no=str(order_no), now=now
        )

    @staticmethod
    def activate_paid(
        conn: Any, *, order: dict[str, Any], pre_membership: dict[str, Any], now: datetime
    ) -> None:
        PromotionOrderAdapter.assert_snapshot_binding(conn, order)
        claimed = conn.execute(
            "INSERT OR IGNORE INTO membership_first_paid_orders(user_id,order_no,claimed_at) VALUES (?,?,?)",
            (int(order["user_id"]), str(order["order_no"]), now.isoformat(timespec="seconds")),
        )
        if int(order.get("referral_eligible_snapshot") or 0) and claimed.rowcount != 1:
            raise ValueError("推荐首单资格已由其他订单占用，当前订单不可支付。")
        if order.get("coupon_code_snapshot"):
            consumed = conn.execute(
                """UPDATE membership_coupon_redemptions SET status='consumed'
                   WHERE order_no=? AND status='reserved' AND discount_minor=? AND coupon_version=?
                     AND (datetime(expires_at)>datetime(?) OR EXISTS (
                         SELECT 1 FROM manual_payment_claims c
                         WHERE c.order_no=membership_coupon_redemptions.order_no
                           AND c.status IN ('submitted','approved')
                     ))""",
                (
                    str(order["order_no"]),
                    int(order["coupon_discount_minor"]),
                    int(order["coupon_version_snapshot"]),
                    now.isoformat(timespec="seconds"),
                ),
            ).rowcount
            if consumed != 1:
                raise ValueError("优惠码预留已失效，订单不可支付。")
        ReferralCommissionService.record_settlement(conn, order, pre_membership, now)

    @staticmethod
    def record_reversal(
        conn: Any,
        *,
        event_key: str,
        order: dict[str, Any],
        amount_minor: int,
        reason: str,
        now: datetime,
    ) -> bool:
        PromotionOrderAdapter.assert_snapshot_binding(conn, order)
        return ReferralCommissionService.record_reversal(
            conn,
            event_key=event_key,
            order=order,
            amount_minor=int(amount_minor),
            reason=reason,
            now=now,
        )

    @staticmethod
    def verified_reversal_minor(order: dict[str, Any], payload: dict[str, Any]) -> int:
        """Accept only the provider-normalized partial-reversal amount.

        Provider webhooks must normalize their signed payload before reaching
        this seam.  A missing, floating-point, negative, or over-remaining
        amount is rejected rather than silently turning a partial refund into
        a full clawback.
        """
        if set(payload) != {"verified_refund_amount_minor"}:
            raise ValueError("支付逆转金额证明无效。")
        amount = payload["verified_refund_amount_minor"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
            raise ValueError("支付逆转金额证明无效。")
        total = int(order.get("final_amount_minor") or order.get("amount_minor") or 0)
        already_reversed = int(order.get("refunded_minor") or 0)
        if total < 1 or amount > total - already_reversed:
            raise ValueError("支付逆转金额超过订单剩余实付额。")
        return amount
