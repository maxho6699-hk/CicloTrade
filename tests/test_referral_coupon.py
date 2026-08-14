from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.auth import AuthService
from core.admin_service import AdminService
from core.compat import UTC
from core.database import DatabaseManager
from core.referral_affiliate import ReferralProgramService, ReferralService
from core.referral_coupon import ReferralCouponService
from payment.order_service import OrderService
from payment.promotion_adapter import PromotionOrderAdapter
from payment.receiving_profile import ReceivingProfileService


@pytest.fixture
def db(tmp_path):
    database = DatabaseManager(str(tmp_path / "coupon.db"))
    auth = AuthService(database)
    admin = auth.register("coupon-admin@example.com", "CorrectHorse123", "Coupon Admin", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (admin["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    ReferralProgramService(database).enable(admin["id"])
    return database, auth, admin


class _CommercePolicy:
    def __init__(self, purchasable=("标准版", "高级版")):
        self.purchasable = frozenset(purchasable)

    def purchasable_plans(self, _conn, *, at):
        del at
        return tuple(sorted(self.purchasable))

    def assert_purchasable(self, _conn, *, plan, cycle, at):
        del at
        if plan not in self.purchasable or cycle not in {"monthly", "quarterly", "yearly"}:
            raise PermissionError("会员方案当前不可购买。")


_DEFAULT_POLICY = object()


def _coupon(db, admin_id, code="SAVE20", policy=_DEFAULT_POLICY, plans=None):
    now = datetime.now(UTC)
    selected_policy = _CommercePolicy() if policy is _DEFAULT_POLICY else policy
    return ReferralCouponService(db, plan_policy=selected_policy).create_coupon(
        admin_id,
        {
            "code": code, "campaign_name": "Launch", "discount_type": "percent",
            "discount_value": 1000, "max_discount_minor": 30000, "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "min_spend_minor": 0,
            "total_use_limit": 10, "per_user_limit": 1, "applicable_plans": plans or ["标准版"],
            "applicable_cycles": ["monthly"], "enabled": True,
        }, "coupon-create-0001"
    )


def test_coupon_plan_policy_fails_closed_and_allows_future_restored_plan(db):
    database, _auth, admin = db
    with pytest.raises(PermissionError, match="策略"):
        _coupon(database, admin["id"], "NO-POLICY", policy=None, plans=["专业版"])
    with pytest.raises(PermissionError, match="停售|不可购买"):
        _coupon(database, admin["id"], "RETIRED", policy=_CommercePolicy(), plans=["专业版"])
    restored = _coupon(
        database, admin["id"], "RESTORED",
        policy=_CommercePolicy(("标准版", "高级版", "专业版")), plans=["专业版"],
    )
    assert restored["code"] == "RESTORED"


def test_promotion_quote_requires_point_in_time_purchasable_proof(db):
    database, auth, _admin = db
    buyer = auth.register("policy-proof@example.com", "CorrectHorse123", "Policy Proof", True)
    with database.transaction() as conn:
        with pytest.raises(PermissionError, match="策略"):
            PromotionOrderAdapter(None).quote(
                conn, user_id=buyer["id"], plan="标准版", cycle="monthly",
                list_price_minor=29_800, coupon_code=None, now=datetime.now(UTC),
            )


def _web_referred(database, auth, referrer_email: str, buyer_email: str):
    referrer = auth.register(referrer_email, "CorrectHorse123", "Referrer", True)
    invite = ReferralService(database).ensure_profile(referrer["id"])["invite_code"]
    fingerprint = "b" * 64
    claim = ReferralService(database).issue_link_claim(invite, fingerprint)
    buyer = auth.register(
        buyer_email, "CorrectHorse123", "Buyer", True, invite,
        referral_claim=claim, referral_claim_fingerprint=fingerprint,
    )
    return referrer, buyer


def test_coupon_then_referral_discount_and_order_snapshot_are_immutable(db):
    database, auth, admin = db
    referrer = auth.register("referrer-coupon@example.com", "CorrectHorse123", "Referrer", True)
    invite = ReferralService(database).ensure_profile(referrer["id"])["invite_code"]
    buyer = auth.register("buyer-coupon@example.com", "CorrectHorse123", "Buyer", True, invite)
    attribution = database.fetch_one("SELECT id FROM referral_attributions WHERE referred_user_id=?", (buyer["id"],))
    database.execute("INSERT INTO referral_link_claims(attribution_id,claim_hash,issued_at,expires_at,consumed_at) VALUES (?, ?, ?, ?, ?)", (attribution["id"], "a" * 64, datetime.now(UTC).isoformat(timespec="seconds"), (datetime.now(UTC) + timedelta(minutes=5)).isoformat(timespec="seconds"), datetime.now(UTC).isoformat(timespec="seconds")))
    claim = database.fetch_one("SELECT id FROM referral_link_claims WHERE attribution_id=?", (attribution["id"],))
    database.execute("INSERT INTO referral_discount_eligibilities(attribution_id,link_claim_id,eligible_at) VALUES (?,?,?)", (attribution["id"], claim["id"], datetime.now(UTC).isoformat(timespec="seconds")))
    coupon = _coupon(database, admin["id"])

    order = OrderService(database).create_order(
        buyer["id"], "标准版", "monthly", "paypal", terms_accepted=True,
        source="legacy", idempotency_key="coupon-order-0001", coupon_code=coupon["code"],
    )
    assert order["list_price_minor"] == 29800
    assert order["coupon_discount_minor"] == 2980
    assert order["referral_discount_minor"] == 1341
    assert order["final_amount_minor"] == order["amount_minor"] == 25479
    assert order["coupon_code_snapshot"] == "SAVE20"

    ReferralCouponService(database).pause_coupon(admin["id"], coupon["public_id"], 1, "coupon-pause-0001")
    assert OrderService(database).process_callback("coupon-paid", order["order_no"], "paid", {})
    paid = OrderService(database).get_order(order["order_no"])
    assert paid["amount_minor"] == 25479
    commission = database.fetch_one("SELECT gross_amount_minor,rate_bps FROM referral_commissions")
    assert commission == {"gross_amount_minor": 25479, "rate_bps": 1000}


def test_coupon_is_fail_closed_and_referral_never_applies_to_repeat_or_unattributed(db):
    database, auth, admin = db
    ordinary = auth.register("ordinary-coupon@example.com", "CorrectHorse123", "Ordinary", True)
    coupon = _coupon(database, admin["id"])
    service = OrderService(database)
    with pytest.raises(ValueError, match="优惠码"):
        service.create_order(ordinary["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy", coupon_code="MISSING")
    first = service.create_order(ordinary["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy", coupon_code=coupon["code"])
    assert first["referral_discount_minor"] == 0
    assert first["coupon_discount_minor"] == 2980
    assert service.process_callback("ordinary-first", first["order_no"], "paid", {})
    with pytest.raises(ValueError, match="用尽"):
        service.create_order(ordinary["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy", coupon_code=coupon["code"])


def test_coupon_analytics_reconciles_public_customer_order_and_costs(db):
    database, auth, admin = db
    buyer = auth.register("analytics-coupon@example.com", "CorrectHorse123", "Analytics", True)
    coupon = _coupon(database, admin["id"], "ANALYTICS10")
    order = OrderService(database).create_order(
        buyer["id"], "标准版", "monthly", "paypal", terms_accepted=True,
        source="legacy", idempotency_key="analytics-order-0001", coupon_code=coupon["code"],
    )
    assert OrderService(database).process_callback("analytics-paid", order["order_no"], "paid", {})
    result = ReferralCouponService(database).analytics(admin["id"], coupon_code=coupon["code"])
    assert result["summary"]["orders"] == 1
    item = result["items"][0]
    assert item["customer"].startswith("USR")
    assert item["order_id"] == order["order_no"]
    assert item["campaign"] == "Launch"
    assert item["coupon_discount_minor"] == order["coupon_discount_minor"]
    assert item["net_revenue_minor"] == order["final_amount_minor"]


def test_promotion_analytics_includes_referral_only_orders_and_all_acquisition_costs(db):
    database, auth, admin = db
    _referrer, buyer = _web_referred(
        database, auth, "analytics-referrer@example.com", "analytics-referral-only@example.com"
    )
    policy_service = ReferralCouponService(database)
    current = policy_service.policy(admin["id"])
    policy_service.update_policy(
        admin["id"],
        {
            **current["policy"],
            "bonus_enabled": True,
            "bonus_tiers": [{"qualified_count": 1, "cumulative_amount_minor": 1_000}],
        },
        current["version"],
        "analytics-full-policy-0001",
    )
    ReceivingProfileService(database).set_receiver_text(admin["id"], "fps", "test receiver")
    order = OrderService(database).create_order(
        buyer["id"], "标准版", "monthly", "fps", terms_accepted=True,
        source="web", idempotency_key="analytics-referral-only-0001",
    )
    assert order["coupon_code_snapshot"] is None
    assert order["referral_discount_minor"] > 0
    claim = OrderService(database).submit_manual_payment_claim(
        buyer["id"], order["order_no"], evidence_file_id="analytics-proof",
        evidence_file_unique_id="analytics-proof-unique", evidence_message_id=1,
    )
    approved = AdminService(database).review_manual_payment_claim(
        admin["id"], claim["id"], True, "ANALYTICS-PAID-0001"
    )
    assert approved["status"] == "approved"

    result = policy_service.analytics(admin["id"], promotion_type="all")
    assert result["summary"]["orders"] == 1
    assert result["summary"]["customers"] == 1
    assert result["summary"]["referral_only_orders"] == 1
    assert result["summary"]["coupon_only_orders"] == 0
    assert result["summary"]["stacked_orders"] == 0
    item = result["items"][0]
    assert item["promotion_type"] == "referral_only"
    assert item["coupon_code"] is None
    assert item["commission_cost_minor"] == order["final_amount_minor"] // 10
    assert item["bonus_cost_minor"] == 1_000
    assert item["promotion_cost_minor"] == (
        item["coupon_discount_minor"]
        + item["referral_discount_minor"]
        + item["commission_cost_minor"]
        + item["bonus_cost_minor"]
    )
    assert result["summary"]["commission_cost_minor"] == item["commission_cost_minor"]
    assert result["summary"]["bonus_cost_minor"] == item["bonus_cost_minor"]
    assert result["summary"]["promotion_cost_minor"] == item["promotion_cost_minor"]


def test_coupon_analytics_reconciles_partial_then_full_provider_reversal(db):
    database, auth, admin = db
    buyer = auth.register("analytics-reversal@example.com", "CorrectHorse123", "Analytics Reversal", True)
    coupon = _coupon(database, admin["id"], "ANALYTICSREV")
    service = OrderService(database)
    order = service.create_order(buyer["id"], "标准版", "monthly", "paypal", terms_accepted=True,
                                 source="legacy", idempotency_key="analytics-reversal-0001", coupon_code=coupon["code"])
    assert service.process_callback("analytics-reversal-paid", order["order_no"], "paid", {})
    partial = order["amount_minor"] // 2
    assert service.process_reversal("analytics-reversal-partial", order["order_no"], {"verified_refund_amount_minor": partial}, "paypal:refund")
    result = ReferralCouponService(database).analytics(admin["id"], coupon_code=coupon["code"])
    item, summary = result["items"][0], result["summary"]
    assert item["status"] == "paid"
    assert (item["paid_revenue_minor"], item["refund_or_chargeback_minor"], item["net_revenue_minor"]) == (order["amount_minor"], partial, order["amount_minor"] - partial)
    assert (summary["paid_revenue_minor"], summary["refund_or_chargeback_minor"], summary["net_revenue_minor"]) == (order["amount_minor"], partial, order["amount_minor"] - partial)
    assert service.process_reversal("analytics-reversal-full", order["order_no"], {"verified_refund_amount_minor": order["amount_minor"] - partial}, "paypal:refund")
    item = ReferralCouponService(database).analytics(admin["id"], coupon_code=coupon["code"])["items"][0]
    assert (item["status"], item["refund_or_chargeback_minor"], item["net_revenue_minor"]) == ("refunded", order["amount_minor"], 0)


def test_attribution_summary_uses_net_revenue_after_partial_and_full_reversals(db):
    database, auth, admin = db
    buyer = auth.register("summary-reversal@example.com", "CorrectHorse123", "Summary Reversal", True)
    coupon = _coupon(database, admin["id"], "SUMMARYREV")
    service = OrderService(database)
    partial_order = service.create_order(buyer["id"], "标准版", "monthly", "paypal", terms_accepted=True,
                                         source="legacy", idempotency_key="summary-partial-0001", coupon_code=coupon["code"])
    assert service.process_callback("summary-partial-paid", partial_order["order_no"], "paid", {})
    partial_refund = partial_order["amount_minor"] // 2
    assert service.process_reversal("summary-partial-refund", partial_order["order_no"], {"verified_refund_amount_minor": partial_refund}, "paypal:refund")
    second_buyer = auth.register("summary-full@example.com", "CorrectHorse123", "Summary Full", True)
    full_order = service.create_order(second_buyer["id"], "标准版", "monthly", "paypal", terms_accepted=True,
                                      source="legacy", idempotency_key="summary-full-0001", coupon_code=coupon["code"])
    assert service.process_callback("summary-full-paid", full_order["order_no"], "paid", {})
    assert service.process_reversal("summary-full-refund", full_order["order_no"], {"verified_refund_amount_minor": full_order["amount_minor"]}, "paypal:refund")
    summary = ReferralCouponService(database).attribution_summary(admin["id"])
    assert summary["orders"] == 2
    assert summary["revenue_minor"] == partial_order["amount_minor"] - partial_refund


def test_expired_coupon_reservation_blocks_payment_and_customer_analytics_is_stable(db):
    database, auth, admin = db
    buyer = auth.register("expiry-coupon@example.com", "CorrectHorse123", "Expiry", True)
    coupon = _coupon(database, admin["id"], "EXPIRY10")
    service = OrderService(database)
    order = service.create_order(
        buyer["id"], "标准版", "monthly", "paypal", terms_accepted=True,
        source="legacy", idempotency_key="expiry-order-0001", coupon_code=coupon["code"],
    )
    database.execute("UPDATE membership_coupon_redemptions SET expires_at=? WHERE order_no=?", ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), order["order_no"]))
    with pytest.raises(ValueError, match="预留"):
        service.process_callback("expiry-paid", order["order_no"], "paid", {})
    assert service.get_order(order["order_no"])["status"] == "pending"


def test_policy_hash_tampering_fails_closed(db):
    database, _auth, _admin = db
    with database.transaction() as conn:
        conn.execute("DROP TRIGGER trg_referral_coupon_policy_versions_no_update")
        conn.execute("UPDATE referral_coupon_policy_versions SET config_sha256=?", ("0" * 64,))
        with pytest.raises(ValueError, match="审计摘要"):
            ReferralCouponService._policy(conn)


@pytest.mark.parametrize("tiers", [
    [],
    [{"qualified_count": 2, "cumulative_amount_minor": 1}, {"qualified_count": 2, "cumulative_amount_minor": 2}],
    [{"qualified_count": 2, "cumulative_amount_minor": 2}, {"qualified_count": 3, "cumulative_amount_minor": 2}],
    [{"qualified_count": 0, "cumulative_amount_minor": 1}],
    [{"qualified_count": 100_001, "cumulative_amount_minor": 1}],
    [{"qualified_count": 1, "cumulative_amount_minor": 100_000_001}],
    [{"qualified_count": 1, "cumulative_amount_minor": 1, "extra": 0}],
])
def test_policy_rejects_malformed_bonus_tiers(db, tiers):
    database, _auth, admin = db
    service = ReferralCouponService(database)
    current = service.policy(admin["id"])
    value = {**current["policy"], "bonus_tiers": tiers}
    with pytest.raises(ValueError, match="政策字段"):
        service.update_policy(admin["id"], value, current["version"], "policy-tier-invalid-0001")


def test_policy_idempotency_replay_returns_its_original_snapshot(db):
    database, _auth, admin = db
    service = ReferralCouponService(database)
    initial = service.policy(admin["id"])
    first_value = {**initial["policy"], "hold_days": initial["policy"]["hold_days"] + 1}
    first = service.update_policy(
        admin["id"], first_value, initial["version"], "policy-replay-first-0001"
    )
    second_value = {**first["policy"], "hold_days": first["policy"]["hold_days"] + 1}
    second = service.update_policy(
        admin["id"], second_value, first["version"], "policy-replay-second-0001"
    )

    replay = service.update_policy(
        admin["id"], first_value, initial["version"], "policy-replay-first-0001"
    )

    assert first == {"version": initial["version"] + 1, "policy": first_value}
    assert second["version"] == first["version"] + 1
    assert replay == first


@pytest.mark.parametrize(("field", "invalid_value"), [
    ("campaign_name", 42),
    ("campaign_name", "   "),
    ("campaign_name", "x" * 121),
    ("max_discount_minor", True),
    ("max_discount_minor", "30000"),
    ("max_discount_minor", 0),
    ("max_discount_minor", -1),
    ("max_discount_minor", 100_001),
    ("discount_value", True),
    ("discount_value", "1000"),
    ("discount_value", None),
    ("discount_value", []),
])
def test_coupon_validation_rejects_invalid_values_without_persistent_side_effects(db, field, invalid_value):
    database, _auth, admin = db
    now = datetime.now(UTC)
    payload = {
        "code": "SAFEINPUT", "campaign_name": "Launch", "discount_type": "percent",
        "discount_value": 1000, "max_discount_minor": 30_000,
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(), "min_spend_minor": 0,
        "total_use_limit": 10, "per_user_limit": 1, "applicable_plans": ["标准版"],
        "applicable_cycles": ["monthly"], "enabled": True,
    }
    payload[field] = invalid_value
    coupon_count = database.fetch_one("SELECT COUNT(*) count FROM membership_coupons")["count"]
    event_count = database.fetch_one("SELECT COUNT(*) count FROM membership_promotion_admin_events")["count"]

    with pytest.raises(ValueError):
        ReferralCouponService(database, plan_policy=_CommercePolicy()).create_coupon(
            admin["id"], payload, "coupon-invalid-input-0001"
        )

    assert database.fetch_one("SELECT COUNT(*) count FROM membership_coupons")["count"] == coupon_count
    assert database.fetch_one("SELECT COUNT(*) count FROM membership_promotion_admin_events")["count"] == event_count
