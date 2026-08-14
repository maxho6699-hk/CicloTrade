from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import threading

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from core.referral_affiliate import (
    ReferralCommissionService,
    ReferralProgramService,
    ReferralService,
    ReferralWalletService,
    _ledger_batch,
)
from payment.order_service import OrderService
from payment.promotion_adapter import PromotionOrderAdapter

@pytest.fixture
def db(tmp_path):
    database = DatabaseManager(str(tmp_path / "referral-affiliate.db"))
    auth = AuthService(database)
    admin = auth.register(
        "referral-release@example.com", "CorrectHorse123", "Referral Release", True
    )
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (admin["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    ReferralProgramService(database).enable(admin["id"])
    return database


def _user(
    auth: AuthService, name: str, referral: str = "", *, claim: str = "", fingerprint: str = ""
) -> dict:
    user = auth.register(
        f"{name}@example.com", "CorrectHorse123", name.title(), True, referral,
        referral_claim=claim, referral_claim_fingerprint=fingerprint,
    )
    assert user
    return user


def _admin(db: DatabaseManager, auth: AuthService, name: str) -> dict:
    user = _user(auth, name)
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    db.execute(
        "INSERT OR REPLACE INTO admin_roles(user_id,role,updated_at) VALUES (?, 'finance', ?)",
        (user["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    return user


def _eligible_pair(db: DatabaseManager, auth: AuthService, referrer_name: str, referred_name: str) -> tuple[dict, dict]:
    referrer = _user(auth, referrer_name)
    invite = ReferralService(db).ensure_profile(referrer["id"])["invite_code"]
    fingerprint = "f" * 64
    claim = ReferralService(db).issue_link_claim(invite, fingerprint)
    return referrer, _user(auth, referred_name, invite, claim=claim, fingerprint=fingerprint)


def _settle_promotion(
    db: DatabaseManager, user_id: int, order_no: str, *, amount_minor: int = 200_000,
    hold_days: int = 1, eligible: bool = True,
) -> tuple[dict, dict | None]:
    now = datetime.now(UTC)
    quote = {
        "list_price_minor": amount_minor, "coupon_discount_minor": 0,
        "referral_discount_minor": 0, "final_amount_minor": amount_minor,
        "coupon_code_snapshot": None, "coupon_version_snapshot": None,
        "referral_policy_version": "membership-promotions-v2:1",
        "referral_eligible_snapshot": int(eligible), "commission_rate_bps": 1000,
        "commission_cap_minor": 100_000, "hold_days": hold_days, "bonus_policy_snapshot": None,
    }
    with db.transaction() as conn:
        snapshot = PromotionOrderAdapter.bind_order_snapshot(
            conn, quote=quote, order_no=order_no, user_id=user_id,
            plan_type="高级版", billing_cycle="yearly", currency="HKD",
        )
        conn.execute(
            """INSERT INTO subscription_orders(order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,created_at,paid_at,amount_minor,list_price_minor,coupon_discount_minor,referral_discount_minor,final_amount_minor,coupon_code_snapshot,coupon_version_snapshot,referral_policy_version,referral_eligible_snapshot,referral_commission_rate_bps_snapshot,referral_commission_cap_minor_snapshot,referral_hold_days_snapshot,promotion_snapshot_sha256,referral_attribution_id_snapshot,referral_referrer_user_id_snapshot,referral_referred_user_id_snapshot)
               VALUES (?,?,'高级版','yearly',?,'HKD','fps','paid',?,?,?,?,?,?,?,?,?,'membership-promotions-v2:1',?,1000,100000,?,?,?,?,?)""",
            (order_no, user_id, amount_minor / 100, now.isoformat(), now.isoformat(), amount_minor,
             amount_minor, 0, 0, amount_minor, None, None, int(eligible),
             hold_days, snapshot["promotion_snapshot_sha256"],
             snapshot["referral_attribution_id_snapshot"],
             snapshot["referral_referrer_user_id_snapshot"],
             snapshot["referral_referred_user_id_snapshot"]),
        )
        order = dict(conn.execute("SELECT * FROM subscription_orders WHERE order_no=?", (order_no,)).fetchone())
        PromotionOrderAdapter.activate_paid(conn, order=order, pre_membership={"plan_type": "免费版"}, now=now)
        commission = conn.execute(
            "SELECT * FROM referral_commissions WHERE source_order_no=?", (order_no,)
        ).fetchone()
        return order, dict(commission) if commission else None


def _settle(service: OrderService, user_id: int, plan: str, cycle: str, key: str) -> dict:
    order = service.create_order(
        user_id, plan, cycle, "paypal", terms_accepted=True,
        source="legacy", idempotency_key=key,
    )
    assert service.process_callback(f"event-{key}", order["order_no"], "paid", {})
    return service.get_order(order["order_no"])


def test_order_service_settles_only_verified_link_first_paid_order(db):
    auth = AuthService(db)
    referrer, referred = _eligible_pair(db, auth, "referrer", "referred")
    profile = ReferralService(db).ensure_profile(referrer["id"])
    assert len(profile["invite_code"]) >= 20
    assert profile == ReferralService(db).ensure_profile(referrer["id"])

    orders = OrderService(db)
    first = _settle(orders, referred["id"], "标准版", "monthly", "cash-first")
    renewal = _settle(orders, referred["id"], "标准版", "monthly", "cash-renewal")
    upgrade = _settle(orders, referred["id"], "高级版", "monthly", "cash-upgrade")

    commissions = db.fetch_all(
        """SELECT source_order_no,settlement_sequence,order_kind,rate_bps,
                  gross_amount_minor,commission_amount_minor
           FROM referral_commissions ORDER BY settlement_sequence"""
    )
    assert commissions == [{
        "source_order_no": first["order_no"], "settlement_sequence": 1,
        "order_kind": "initial_purchase", "rate_bps": 1000,
        "gross_amount_minor": first["final_amount_minor"],
        "commission_amount_minor": first["final_amount_minor"] * 10 // 100,
    }]
    assert renewal["order_no"] != first["order_no"] != upgrade["order_no"]
    assert db.fetch_one("SELECT COUNT(*) count FROM rewards")["count"] == 0
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM membership_entitlements WHERE source_kind='referral_reward'"
    )["count"] == 0
    with pytest.raises(Exception, match="immutable"):
        db.execute(
            "UPDATE referral_attributions SET referrer_user_id=? WHERE referred_user_id=?",
            (referred["id"], referred["id"]),
        )


def test_hold_release_full_clawback_and_no_requalification(db):
    auth = AuthService(db)
    referrer, referred = _eligible_pair(db, auth, "hold-referrer", "hold-referred")
    first, commission = _settle_promotion(db, referred["id"], "hold-first")
    assert commission
    wallet = ReferralWalletService(db)
    balances = wallet.balances(referrer["id"])
    assert balances["pending"] == commission["commission_amount_minor"]
    assert balances["available"] == 0

    released_at = datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    assert ReferralCommissionService(db).release_due(referrer["id"], released_at) == 1
    assert wallet.balances(referrer["id"])["available"] == commission["commission_amount_minor"]

    with db.transaction() as conn:
        assert ReferralCommissionService.record_reversal(
            conn, event_key="cash-reversal-first", order=first,
            amount_minor=first["amount_minor"], reason="provider_chargeback", now=released_at,
        )
    balances = wallet.balances(referrer["id"])
    assert balances["pending"] == 0
    assert balances["available"] == 0

    replacement, _ = _settle_promotion(db, referred["id"], "hold-replacement", eligible=False)
    next_commission = db.fetch_one(
        "SELECT settlement_sequence,rate_bps FROM referral_commissions WHERE source_order_no=?",
        (replacement["order_no"],),
    )
    assert next_commission is None


def test_withdrawal_reservation_review_paid_and_clawback_debt(db):
    auth = AuthService(db)
    referrer, referred = _eligible_pair(db, auth, "wallet-referrer", "wallet-referred")
    order, commission = _settle_promotion(db, referred["id"], "wallet-first")
    assert commission
    ReferralCommissionService(db).release_due(
        referrer["id"], datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    )
    wallet = ReferralWalletService(db)
    amount = 20_000
    request = wallet.request_withdrawal(referrer["id"], amount, "withdraw-wallet-0001")
    assert wallet.request_withdrawal(referrer["id"], amount, "withdraw-wallet-0001")["id"] == request["id"]
    balances = wallet.balances(referrer["id"])
    assert balances["reserved"] == amount

    approver = _admin(db, auth, "approver")
    payer = _admin(db, auth, "payer")
    approved = wallet.review(approver["id"], request["public_id"], "approve")
    assert approved["status"] == "approved"
    with pytest.raises(PermissionError, match="必须不同"):
        wallet.confirm_paid(approver["id"], request["public_id"], "fps", "PAYOUT-001")
    paid = wallet.confirm_paid(payer["id"], request["public_id"], "fps", "PAYOUT-001")
    assert paid["status"] == "paid"
    balances = wallet.balances(referrer["id"])
    assert balances["reserved"] == 0
    assert balances["paid"] == amount

    with db.transaction() as conn:
        ReferralCommissionService.record_reversal(
            conn, event_key="wallet-chargeback", order=order,
            amount_minor=order["amount_minor"], reason="chargeback", now=datetime.now(UTC),
        )
    assert wallet.balances(referrer["id"])["available"] < 0
    confirmation = db.fetch_one("SELECT payout_method,payout_reference FROM referral_payout_confirmations")
    assert confirmation == {"payout_method": "fps", "payout_reference": "PAYOUT001"}


def test_concurrent_withdrawal_only_reserves_once(db):
    auth = AuthService(db)
    referrer, referred = _eligible_pair(db, auth, "race-referrer", "race-referred")
    _order, commission = _settle_promotion(db, referred["id"], "race-first")
    assert commission
    ReferralCommissionService(db).release_due(
        referrer["id"], datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def request(key: str):
        barrier.wait()
        try:
            ReferralWalletService(db).request_withdrawal(referrer["id"], 20_000, key)
            outcomes.append("created")
        except ValueError:
            outcomes.append("blocked")

    threads = [
        threading.Thread(target=request, args=("race-withdraw-0001",)),
        threading.Thread(target=request, args=("race-withdraw-0002",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["blocked", "created"]
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM referral_withdrawal_requests WHERE status='submitted'"
    )["count"] == 1
    assert ReferralWalletService(db).balances(referrer["id"])["reserved"] == 20_000


def test_append_only_ledger_and_strict_minimum(db):
    auth = AuthService(db)
    user = _user(auth, "minimum")
    with pytest.raises(ValueError, match="最低提款"):
        ReferralWalletService(db).request_withdrawal(user["id"], 9999, "minimum-withdraw-1")
    with db.transaction() as conn:
        _ledger_batch(
            conn, user_id=user["id"], legs=[("available", 10000)], entry_type="test",
            group_key="test", reference_type="test", reference_id="test",
            batch_key="test-ledger", now=datetime.now(UTC),
        )
    with pytest.raises(Exception, match="append-only"):
        db.execute("DELETE FROM referral_ledger_entries WHERE idempotency_key LIKE 'test-ledger:%'")

    with pytest.raises(Exception, match="unbalanced"):
        with db.transaction() as conn:
            batch = conn.execute(
                """INSERT INTO referral_journal_batches(batch_key,group_key,status,created_at)
                   VALUES ('destructive-unbalanced','destructive-unbalanced','open',?)""",
                (datetime.now(UTC).isoformat(timespec="seconds"),),
            )
            conn.execute(
                """INSERT INTO referral_ledger_entries
                   (public_id,batch_id,account_kind,user_id,bucket,amount_minor,currency,entry_type,
                    group_key,reference_type,reference_id,idempotency_key,created_at)
                   VALUES ('LED000000000000000000000099',?,'user',?,'available',1,'HKD',
                           'test','destructive-unbalanced','test','test','destructive-line',?)""",
                (batch.lastrowid, user["id"], datetime.now(UTC).isoformat(timespec="seconds")),
            )
            conn.execute(
                "UPDATE referral_journal_batches SET status='finalized' WHERE id=?",
                (batch.lastrowid,),
            )


def test_order_service_cutover_does_not_backpay_legacy_history(db):
    auth = AuthService(db)
    referrer = _user(auth, "cutover-referrer")
    referred = _user(auth, "cutover-referred")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO referrals(referrer_id,referee_id,status,created_at) VALUES (?,?,'qualified',?)",
        (referrer["id"], referred["id"], now),
    )
    db.execute(
        """INSERT INTO referral_attributions
           (public_id,referrer_user_id,referred_user_id,invite_code_snapshot,source,attributed_at)
           VALUES ('RFR000000000000000000000001',?,?,'TAI00000001','legacy',?)""",
        (referrer["id"], referred["id"], now),
    )
    db.execute(
        """INSERT INTO subscription_orders
           (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,
            created_at,paid_at,amount_minor,entitlement_days)
           VALUES ('LEGACY-PAID-ORDER',?,'标准版','monthly',100,'HKD','paypal','paid',?,?,10000,30)""",
        (referred["id"], now, now),
    )
    db.execute(
        """INSERT INTO rewards(user_id,reward_type,days,reference,source_order_no,created_at)
           VALUES (?,'REFERRAL_30',9,'referral:legacy','LEGACY-PAID-ORDER',?)""",
        (referrer["id"], now),
    )
    order = _settle(OrderService(db), referred["id"], "高级版", "monthly", "cutover-new")
    assert db.fetch_one(
        "SELECT 1 FROM referral_commissions WHERE source_order_no=?", (order["order_no"],)
    ) is None
    assert db.fetch_one("SELECT COUNT(*) count FROM referral_commissions")["count"] == 0
    assert db.fetch_one("SELECT COUNT(*) count FROM rewards")["count"] == 1


def test_fresh_database_applies_0025_profile_schema_and_portal_serializes_hkt(tmp_path):
    database = DatabaseManager(str(tmp_path / "fresh-0025.db"))
    migration = database.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0025_referral_affiliate_cash.sql'"
    )
    assert migration == {"version": "0025_referral_affiliate_cash.sql"}
    profile_columns = {
        row["name"]: row for row in database.fetch_all("PRAGMA table_info(referral_profiles)")
    }
    assert profile_columns["public_id"]["notnull"] == 1
    assert profile_columns["invite_code"]["notnull"] == 1
    auth = AuthService(database)
    user = _user(auth, "fresh-profile")
    profile = ReferralService(database).ensure_profile(user["id"])
    assert profile["public_id"].startswith("USR")
    assert len(profile["public_id"]) == 27
    portal = ReferralService(database).portal(user["id"], base_url="https://ciclotrade.example")
    assert set(portal) == {
        "program", "invite", "balances", "trends", "funnel", "referrals",
        "commissions", "withdrawals", "timeline",
    }
    assert portal["invite"]["invite_link"].startswith("https://ciclotrade.example/login?ref=")
    assert portal["program"]["enabled"] is False
    assert portal["program"]["cutover_at"] is None


def test_portal_all_emitted_timestamps_are_hong_kong_iso(db):
    auth = AuthService(db)
    referrer, referred = _eligible_pair(db, auth, "hkt-referrer", "hkt-referred")
    _settle_promotion(db, referred["id"], "hkt-first")
    portal = ReferralService(db).portal(referrer["id"])
    timestamp_fields = []
    timestamp_fields.extend(item["joined_at"] for item in portal["referrals"])
    timestamp_fields.extend(item["settled_at"] for item in portal["commissions"])
    timestamp_fields.extend(item["available_at"] for item in portal["commissions"])
    timestamp_fields.extend(item["occurred_at"] for item in portal["timeline"])
    assert timestamp_fields
    assert all(value.endswith("+08:00") for value in timestamp_fields)


def test_direct_cycle_insert_is_rejected_by_database(db):
    auth = AuthService(db)
    first = _user(auth, "cycle-first")
    second = _user(auth, "cycle-second")
    db.execute(
        """INSERT INTO referral_attributions
           (public_id,referrer_user_id,referred_user_id,invite_code_snapshot,source,attributed_at)
           VALUES ('RFR000000000000000000000011',?,?,'first-code','legacy',?)""",
        (first["id"], second["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    with pytest.raises(Exception, match="cycle"):
        db.execute(
            """INSERT INTO referral_attributions
               (public_id,referrer_user_id,referred_user_id,invite_code_snapshot,source,attributed_at)
               VALUES ('RFR000000000000000000000012',?,?,'second-code','legacy',?)""",
            (second["id"], first["id"], datetime.now(UTC).isoformat(timespec="seconds")),
        )


def test_journal_reconciles_batches_platform_and_open_withdrawals(db):
    auth = AuthService(db)
    referrer, referred = _eligible_pair(db, auth, "reconcile-referrer", "reconcile-referred")
    _order, commission = _settle_promotion(db, referred["id"], "reconcile-first")
    assert commission
    ReferralCommissionService(db).release_due(
        referrer["id"], datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    )
    ReferralWalletService(db).request_withdrawal(
        referrer["id"], 20_000, "reconcile-withdrawal-1"
    )

    assert db.fetch_one(
        "SELECT COUNT(*) count FROM referral_journal_batches WHERE status='open'"
    )["count"] == 0
    assert db.fetch_one(
        """SELECT COUNT(*) count FROM (
               SELECT batch_id FROM referral_ledger_entries GROUP BY batch_id HAVING SUM(amount_minor)<>0
           )"""
    )["count"] == 0
    assert db.fetch_one(
        """SELECT COUNT(*) count FROM (
               SELECT batch_id,bucket FROM referral_ledger_entries GROUP BY batch_id,bucket
               HAVING COUNT(DISTINCT account_kind)<>2 OR SUM(amount_minor)<>0
           )"""
    )["count"] == 0
    assert db.fetch_one(
        "SELECT COALESCE(SUM(amount_minor),0) net FROM referral_ledger_entries"
    )["net"] == 0
    reserved = db.fetch_one(
        """SELECT COALESCE(SUM(l.amount_minor),0) amount FROM referral_ledger_entries l
           JOIN referral_journal_batches b ON b.id=l.batch_id
           WHERE l.account_kind='user' AND b.status='finalized' AND l.user_id=?
             AND l.bucket='reserved'""",
        (referrer["id"],),
    )["amount"]
    open_requests = db.fetch_one(
        """SELECT COALESCE(SUM(amount_minor),0) amount FROM referral_withdrawal_requests
           WHERE user_id=? AND status IN ('submitted','approved')""",
        (referrer["id"],),
    )["amount"]
    assert reserved == open_requests == 20_000


def test_partial_commission_reversal_is_proportional(db):
    auth = AuthService(db)
    _referrer, referred = _eligible_pair(db, auth, "partial-referrer", "partial-referred")
    order, commission = _settle_promotion(db, referred["id"], "partial-first")
    assert commission
    with db.transaction() as conn:
        assert ReferralCommissionService.record_reversal(
            conn, event_key="partial-reversal", order=order,
            amount_minor=order["amount_minor"] // 2, reason="provider_refund",
            now=datetime.now(UTC),
        )
    after = db.fetch_one("SELECT reversed_amount_minor,clawed_back_minor FROM referral_commissions WHERE source_order_no=?", (order["order_no"],))
    assert after == {
        "reversed_amount_minor": order["amount_minor"] // 2,
        "clawed_back_minor": commission["commission_amount_minor"] // 2,
    }
    with db.transaction() as conn:
        assert not ReferralCommissionService.record_reversal(
            conn, event_key="partial-reversal", order=order,
            amount_minor=order["amount_minor"], reason="provider_refund",
            now=datetime.now(UTC),
        )
    with pytest.raises(ValueError, match="推广佣金逆转金额无效"):
        with db.transaction() as conn:
            ReferralCommissionService.record_reversal(
                conn, event_key="partial-reversal-over-remaining", order=order,
                amount_minor=order["amount_minor"], reason="provider_refund",
                now=datetime.now(UTC),
            )
