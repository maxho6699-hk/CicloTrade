from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from core.auth import AuthError, AuthService
from core.compat import UTC
from core.database import DatabaseManager
from core.referral_affiliate import (
    ReferralCommissionService,
    ReferralProgramService,
    ReferralService,
    ReferralWalletService,
    _ledger_batch,
)
from core.referral_bonus import ReferralBonusService
from core.referral_coupon import ReferralCouponService
from payment.promotion_adapter import PromotionOrderAdapter


class CommercePolicy:
    def __init__(self, plans: tuple[str, ...] = ("标准版", "高级版")):
        self.plans = plans

    def purchasable_plans(self, _conn, *, at):
        del at
        return self.plans

    def assert_purchasable(self, _conn, *, plan, cycle, at):
        del at
        if plan not in self.plans or cycle != "monthly":
            raise PermissionError("会员方案当前不可购买。")


@pytest.fixture
def promotion_db(tmp_path):
    database = DatabaseManager(str(tmp_path / "promotion-core.db"))
    auth = AuthService(database)
    admin = auth.register("promotion-admin@example.com", "CorrectHorse123", "Admin", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (admin["id"], datetime.now(UTC).isoformat()),
    )
    ReferralProgramService(database).enable(admin["id"])
    return database, auth, admin


def _user(auth, name, referral="", *, claim="", fingerprint=""):
    return auth.register(
        f"{name}@example.com", "CorrectHorse123", name, True, referral,
        referral_claim=claim, referral_claim_fingerprint=fingerprint,
    )


def _eligible_pair(database, auth):
    referrer = _user(auth, "referrer")
    invite = ReferralService(database).ensure_profile(referrer["id"])["invite_code"]
    fingerprint = "f" * 64
    claim = ReferralService(database).issue_link_claim(invite, fingerprint)
    buyer = _user(auth, "buyer", invite, claim=claim, fingerprint=fingerprint)
    return referrer, buyer


def _insert_promotion_order(
    conn, *, order_no, buyer, now, quote, plan_type="标准版", billing_cycle="monthly"
):
    snapshot = PromotionOrderAdapter.bind_order_snapshot(
        conn, quote=quote, order_no=order_no, user_id=buyer["id"], plan_type=plan_type,
        billing_cycle=billing_cycle, currency="HKD",
    )
    amount_minor = int(quote["final_amount_minor"])
    conn.execute(
        """INSERT INTO subscription_orders(order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,created_at,paid_at,amount_minor,list_price_minor,coupon_discount_minor,referral_discount_minor,final_amount_minor,coupon_code_snapshot,coupon_version_snapshot,referral_policy_version,referral_eligible_snapshot,referral_commission_rate_bps_snapshot,referral_commission_cap_minor_snapshot,referral_hold_days_snapshot,referral_bonus_policy_snapshot,promotion_snapshot_sha256,referral_attribution_id_snapshot,referral_referrer_user_id_snapshot,referral_referred_user_id_snapshot)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            order_no, buyer["id"], plan_type, billing_cycle, amount_minor / 100, "HKD", "fps", "paid",
            now.isoformat(), now.isoformat(), amount_minor,
            snapshot["list_price_minor"], snapshot["coupon_discount_minor"],
            snapshot["referral_discount_minor"], amount_minor,
            snapshot["coupon_code_snapshot"], snapshot["coupon_version_snapshot"],
            snapshot["referral_policy_version"], snapshot["referral_eligible_snapshot"],
            snapshot["commission_rate_bps"], snapshot["commission_cap_minor"],
            snapshot["hold_days"], snapshot["bonus_policy_snapshot"],
            snapshot["promotion_snapshot_sha256"], snapshot["referral_attribution_id_snapshot"],
            snapshot["referral_referrer_user_id_snapshot"], snapshot["referral_referred_user_id_snapshot"],
        ),
    )
    return dict(conn.execute("SELECT * FROM subscription_orders WHERE order_no=?", (order_no,)).fetchone())


def _quote(*, amount_minor, eligible=True, hold_days=0, bonus_policy_snapshot=None, **extra):
    return {
        "list_price_minor": extra.get("list_price_minor"),
        "coupon_discount_minor": extra.get("coupon_discount_minor", 0),
        "referral_discount_minor": extra.get("referral_discount_minor", 0),
        "final_amount_minor": amount_minor,
        "coupon_code_snapshot": extra.get("coupon_code_snapshot"),
        "coupon_version_snapshot": extra.get("coupon_version_snapshot"),
        "referral_policy_version": "membership-promotions-v2:1",
        "referral_eligible_snapshot": int(eligible), "commission_rate_bps": 1000 if eligible else 0,
        "commission_cap_minor": 100_000 if eligible else 0, "hold_days": hold_days,
        "bonus_policy_snapshot": bonus_policy_snapshot,
    }


def test_plain_code_never_qualifies_but_verified_web_link_does(promotion_db):
    database, auth, _admin = promotion_db
    referrer = _user(auth, "plain-owner")
    invite = ReferralService(database).ensure_profile(referrer["id"])["invite_code"]
    plain = _user(auth, "plain-buyer", invite)
    assert database.fetch_one(
        "SELECT 1 FROM referral_discount_eligibilities e JOIN referral_attributions a ON a.id=e.attribution_id WHERE a.referred_user_id=?",
        (plain["id"],),
    ) is None
    _owner, linked = _eligible_pair(database, auth)
    assert database.fetch_one(
        "SELECT 1 FROM referral_discount_eligibilities e JOIN referral_attributions a ON a.id=e.attribution_id WHERE a.referred_user_id=?",
        (linked["id"],),
    ) == {"1": 1}
    with pytest.raises(AuthError, match="证明"):
        _user(auth, "forged-buyer", invite, claim="forged", fingerprint="f" * 64)


def test_coupon_precedes_referral_and_rejects_retired_plan(promotion_db):
    database, auth, admin = promotion_db
    _referrer, buyer = _eligible_pair(database, auth)
    service = ReferralCouponService(database, plan_policy=CommercePolicy())
    now = datetime.now(UTC)
    coupon = service.create_coupon(
        admin["id"],
        {
            "code": "SAVE10", "campaign_name": "Launch", "discount_type": "percent",
            "discount_value": 1000, "max_discount_minor": 30000,
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "min_spend_minor": 0,
            "total_use_limit": 2, "per_user_limit": 1, "applicable_plans": ["标准版"],
            "applicable_cycles": ["monthly"], "enabled": True,
        },
        "coupon-create-contract-001",
    )
    with database.transaction() as conn:
        quote = PromotionOrderAdapter(CommercePolicy()).quote(
            conn, user_id=buyer["id"], plan="标准版", cycle="monthly",
            list_price_minor=29_800, coupon_code=coupon["code"], now=now,
        )
    assert quote["coupon_discount_minor"] == 2_980
    assert quote["referral_discount_minor"] == 1_341
    assert quote["final_amount_minor"] == 25_479
    current = ReferralCouponService(database).policy(admin["id"])
    with pytest.raises(ValueError, match="政策字段"):
        ReferralCouponService(database).update_policy(
            admin["id"], {**current["policy"], "referral_discount_bps": 600},
            current["version"], "policy-fixed-discount-contract-001",
        )
    with pytest.raises(PermissionError, match="停售|不可购买"):
        service.create_coupon(
            admin["id"],
            {**{"code": "NO-PRO", "campaign_name": "No", "discount_type": "percent", "discount_value": 100,
                "max_discount_minor": 1000, "starts_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(), "min_spend_minor": 0,
                "total_use_limit": 1, "per_user_limit": 1, "applicable_plans": ["专业版"],
                "applicable_cycles": ["monthly"], "enabled": True}},
            "coupon-retired-contract-001",
        )


def test_first_paid_only_commissions_final_paid_and_reversal_is_proportional(promotion_db):
    database, auth, _admin = promotion_db
    referrer, buyer = _eligible_pair(database, auth)
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    with database.transaction() as conn:
        order = _insert_promotion_order(
            conn, order_no="PROMO-FIRST", buyer=buyer, now=now, quote=_quote(amount_minor=25_479)
        )
        commission = ReferralCommissionService.record_settlement(conn, order, {"plan_type": "免费版"}, now)
        assert commission and commission["commission_amount_minor"] == 2_547
        assert ReferralCommissionService.record_reversal(
            conn, event_key="partial-provider-refund", order=order, amount_minor=12_739,
            reason="provider_refund", now=now,
        )
    result = database.fetch_one("SELECT reversed_amount_minor,clawed_back_minor FROM referral_commissions")
    assert result == {"reversed_amount_minor": 12_739, "clawed_back_minor": 1_273}
    with database.transaction() as conn:
        repeat = _insert_promotion_order(
            conn, order_no="PROMO-RENEW", buyer=buyer, now=now, quote=_quote(amount_minor=29_800)
        )
        assert ReferralCommissionService.record_settlement(conn, repeat, {"plan_type": "标准版"}, now) is None
    assert database.fetch_one("SELECT COUNT(*) count FROM referral_commissions")["count"] == 1
    assert referrer["id"] > 0


def test_bonus_clawback_creates_debt_and_blocks_withdrawal(promotion_db):
    database, auth, _admin = promotion_db
    referrer, buyer = _eligible_pair(database, auth)
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    snapshot = {"enabled": True, "hold_days": 0, "version": 1, "tiers": [{"qualified_count": 1, "cumulative_amount_minor": 30_000}]}
    with database.transaction() as conn:
        order = _insert_promotion_order(
            conn, order_no="PROMO-BONUS", buyer=buyer, now=now,
            quote=_quote(amount_minor=29_800, bonus_policy_snapshot=json.dumps(snapshot)),
        )
        attribution = dict(conn.execute("SELECT * FROM referral_attributions WHERE referred_user_id=?", (buyer["id"],)).fetchone())
        ReferralBonusService.record_qualification(conn, order=order, attribution=attribution, now=now)
        ReferralBonusService.release_due_in_transaction(conn, referrer["id"], now + timedelta(seconds=1))
        _ledger_batch(
            conn, user_id=referrer["id"], legs=[("available", -30_000), ("paid", 30_000)],
            entry_type="withdrawal_paid", group_key="bonus-paid-contract", reference_type="withdrawal",
            reference_id="WDRBONUSCONTRACT000000000001", batch_key="bonus-paid-contract", now=now,
        )
        conn.execute("INSERT INTO referral_withdrawal_requests(public_id,user_id,amount_minor,currency,status,idempotency_key,request_fingerprint,submitted_at) VALUES ('WDRBONUSCONTRACT000000000001',?,30000,'HKD','paid','bonus-paid-contract-001',?,?)", (referrer["id"], "b" * 64, now.isoformat()))
        ReferralBonusService.record_reversal(conn, source_order_no="PROMO-BONUS", now=now + timedelta(minutes=1))
    balances = ReferralWalletService(database).balances(referrer["id"])
    assert balances["available"] < 0
    with pytest.raises(ValueError, match="余额不足"):
        ReferralWalletService(database).request_withdrawal(referrer["id"], 20_000, "blocked-debt-contract-001")


def test_reversal_parser_rejects_forged_or_over_remaining_amount():
    order = {"amount_minor": 10_000, "refunded_minor": 4_000}
    assert PromotionOrderAdapter.verified_reversal_minor(order, {"verified_refund_amount_minor": 6_000}) == 6_000
    for payload in ({}, {"verified_refund_amount_minor": 6_001}, {"verified_refund_amount_minor": 1.0}):
        with pytest.raises(ValueError):
            PromotionOrderAdapter.verified_reversal_minor(order, payload)


def test_snapshot_hash_rejects_any_tampered_order_fact(promotion_db):
    database, auth, _admin = promotion_db
    _referrer, buyer = _eligible_pair(database, auth)
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    quote = _quote(
        amount_minor=25_479, list_price_minor=29_800, coupon_discount_minor=2_980,
        referral_discount_minor=1_341, coupon_code_snapshot="SAVE10",
        coupon_version_snapshot=1, hold_days=30, bonus_policy_snapshot="{}",
    )
    with database.transaction() as conn:
        order = _insert_promotion_order(
            conn, order_no="PROMO-HASH", buyer=buyer, now=now, quote=quote,
        )
        PromotionOrderAdapter.assert_snapshot_integrity(order)
        forged = {**order, "final_amount_minor": 1, "promotion_snapshot_sha256": "f" * 64}
        with pytest.raises(ValueError, match="摘要"):
            PromotionOrderAdapter.assert_snapshot_integrity(forged)
        with pytest.raises(ValueError, match="摘要"):
            ReferralCommissionService.record_settlement(
                conn, forged, {"plan_type": "免费版"}, now
            )
        with pytest.raises(Exception, match="immutable"):
            conn.execute(
                "UPDATE subscription_orders SET promotion_snapshot_sha256=? WHERE order_no='PROMO-HASH'",
                ("e" * 64,),
            )


def test_snapshot_hash_binds_order_identity_and_attribution(promotion_db):
    database, auth, _admin = promotion_db
    _referrer, buyer = _eligible_pair(database, auth)
    other = _user(auth, "other-buyer")
    now = datetime(2026, 8, 14, 12, tzinfo=UTC)
    with database.transaction() as conn:
        order = _insert_promotion_order(
            conn, order_no="PROMO-IDENTITY", buyer=buyer, now=now, quote=_quote(amount_minor=29_800)
        )
        for field, value in {
            "user_id": other["id"], "order_no": "PROMO-SWAPPED", "plan_type": "高级版",
            "billing_cycle": "yearly", "currency": "USD",
            "referral_attribution_id_snapshot": 0,
        }.items():
            with pytest.raises(ValueError, match="摘要"):
                PromotionOrderAdapter.assert_snapshot_integrity({**order, field: value})
        for field, value in {
            "user_id": other["id"], "plan_type": "高级版", "billing_cycle": "yearly",
            "currency": "USD", "referral_attribution_id_snapshot": 0,
        }.items():
            with pytest.raises(Exception, match="immutable"):
                conn.execute(f"UPDATE subscription_orders SET {field}=? WHERE order_no='PROMO-IDENTITY'", (value,))
