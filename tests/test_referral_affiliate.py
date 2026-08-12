from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import threading

import pytest

from core.admin_service import AdminService
from core.auth import AuthService
from core.database import DatabaseManager
from core.referral_affiliate import (
    ReferralCommissionService,
    ReferralService,
    ReferralWalletService,
)
from payment.order_service import OrderService


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "referral-affiliate.db"))


def _user(auth: AuthService, name: str, referral: str = "") -> dict:
    user = auth.register(
        f"{name}@example.com", "CorrectHorse123", name.title(), True, referral
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


def _settle(service: OrderService, user_id: int, plan: str, cycle: str, key: str) -> dict:
    order = service.create_order(
        user_id, plan, cycle, "paypal", terms_accepted=True,
        source="legacy", idempotency_key=key,
    )
    assert service.process_callback(f"event-{key}", order["order_no"], "paid", {})
    return service.get_order(order["order_no"])


def test_permanent_random_attribution_and_cash_rates_without_legacy_days(db):
    auth = AuthService(db)
    referrer = _user(auth, "referrer")
    profile = ReferralService(db).ensure_profile(referrer["id"])
    assert len(profile["invite_code"]) >= 20
    assert profile == ReferralService(db).ensure_profile(referrer["id"])
    referred = _user(auth, "referred", profile["invite_code"])

    orders = OrderService(db)
    first = _settle(orders, referred["id"], "标准版", "monthly", "cash-first")
    renewal = _settle(orders, referred["id"], "标准版", "monthly", "cash-renewal")
    upgrade = _settle(orders, referred["id"], "高级版", "monthly", "cash-upgrade")

    commissions = db.fetch_all(
        """SELECT source_order_no,settlement_sequence,order_kind,rate_bps,
                  gross_amount_minor,commission_amount_minor
           FROM referral_commissions ORDER BY settlement_sequence"""
    )
    assert commissions == [
        {
            "source_order_no": first["order_no"], "settlement_sequence": 1,
            "order_kind": "initial_purchase", "rate_bps": 2000,
            "gross_amount_minor": first["amount_minor"],
            "commission_amount_minor": first["amount_minor"] * 20 // 100,
        },
        {
            "source_order_no": renewal["order_no"], "settlement_sequence": 2,
            "order_kind": "renewal", "rate_bps": 1000,
            "gross_amount_minor": renewal["amount_minor"],
            "commission_amount_minor": renewal["amount_minor"] * 10 // 100,
        },
        {
            "source_order_no": upgrade["order_no"], "settlement_sequence": 3,
            "order_kind": "upgrade", "rate_bps": 1000,
            "gross_amount_minor": upgrade["amount_minor"],
            "commission_amount_minor": upgrade["amount_minor"] * 10 // 100,
        },
    ]
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
    referrer = _user(auth, "hold-referrer")
    profile = ReferralService(db).ensure_profile(referrer["id"])
    referred = _user(auth, "hold-referred", profile["invite_code"])
    orders = OrderService(db)
    first = _settle(orders, referred["id"], "标准版", "monthly", "hold-first")
    commission = db.fetch_one("SELECT * FROM referral_commissions WHERE source_order_no=?", (first["order_no"],))
    wallet = ReferralWalletService(db)
    balances = wallet.balances(referrer["id"])
    assert balances["pending"] == commission["commission_amount_minor"]
    assert balances["available"] == 0

    released_at = datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    assert ReferralCommissionService(db).release_due(referrer["id"], released_at) == 1
    assert wallet.balances(referrer["id"])["available"] == commission["commission_amount_minor"]

    assert orders.process_reversal(
        "cash-reversal-first", first["order_no"], {}, "provider_chargeback"
    )
    balances = wallet.balances(referrer["id"])
    assert balances["pending"] == 0
    assert balances["available"] == 0

    replacement = _settle(
        orders, referred["id"], "标准版", "monthly", "hold-replacement"
    )
    next_commission = db.fetch_one(
        "SELECT settlement_sequence,rate_bps FROM referral_commissions WHERE source_order_no=?",
        (replacement["order_no"],),
    )
    assert next_commission == {"settlement_sequence": 2, "rate_bps": 1000}


def test_withdrawal_reservation_review_paid_and_clawback_debt(db):
    auth = AuthService(db)
    referrer = _user(auth, "wallet-referrer")
    profile = ReferralService(db).ensure_profile(referrer["id"])
    referred = _user(auth, "wallet-referred", profile["invite_code"])
    orders = OrderService(db)
    order = _settle(orders, referred["id"], "高级版", "yearly", "wallet-first")
    commission = db.fetch_one("SELECT * FROM referral_commissions WHERE source_order_no=?", (order["order_no"],))
    ReferralCommissionService(db).release_due(
        referrer["id"], datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    )
    wallet = ReferralWalletService(db)
    amount = 10000
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

    orders.process_reversal("wallet-chargeback", order["order_no"], {}, "chargeback")
    assert wallet.balances(referrer["id"])["available"] < 0
    confirmation = db.fetch_one("SELECT payout_method,payout_reference FROM referral_payout_confirmations")
    assert confirmation == {"payout_method": "fps", "payout_reference": "PAYOUT001"}


def test_concurrent_withdrawal_only_reserves_once(db):
    auth = AuthService(db)
    referrer = _user(auth, "race-referrer")
    profile = ReferralService(db).ensure_profile(referrer["id"])
    referred = _user(auth, "race-referred", profile["invite_code"])
    order = _settle(OrderService(db), referred["id"], "高级版", "yearly", "race-first")
    commission = db.fetch_one("SELECT * FROM referral_commissions WHERE source_order_no=?", (order["order_no"],))
    ReferralCommissionService(db).release_due(
        referrer["id"], datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def request(key: str):
        barrier.wait()
        try:
            ReferralWalletService(db).request_withdrawal(referrer["id"], 10000, key)
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
    assert ReferralWalletService(db).balances(referrer["id"])["reserved"] == 10000


def test_append_only_ledger_and_strict_minimum(db):
    auth = AuthService(db)
    user = _user(auth, "minimum")
    with pytest.raises(ValueError, match="最低提款"):
        ReferralWalletService(db).request_withdrawal(user["id"], 9999, "minimum-withdraw-1")
    db.execute(
        """INSERT INTO referral_ledger_entries
           (public_id,user_id,bucket,amount_minor,currency,entry_type,group_key,
            reference_type,reference_id,idempotency_key,created_at)
           VALUES ('LED000000000000000000000001',?,'available',10000,'HKD','test','test','test','test','test-ledger',?)""",
        (user["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    with pytest.raises(Exception, match="append-only"):
        db.execute("DELETE FROM referral_ledger_entries WHERE idempotency_key='test-ledger'")


def test_cutover_does_not_backpay_and_legacy_paid_history_makes_next_cash_order_repeat(db):
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
    cash = db.fetch_one(
        "SELECT settlement_sequence,order_kind,rate_bps FROM referral_commissions WHERE source_order_no=?",
        (order["order_no"],),
    )
    assert cash == {"settlement_sequence": 2, "order_kind": "upgrade", "rate_bps": 1000}
    assert db.fetch_one("SELECT COUNT(*) count FROM referral_commissions")["count"] == 1
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


def test_portal_all_emitted_timestamps_are_hong_kong_iso(db):
    auth = AuthService(db)
    referrer = _user(auth, "hkt-referrer")
    profile = ReferralService(db).ensure_profile(referrer["id"])
    referred = _user(auth, "hkt-referred", profile["invite_code"])
    _settle(OrderService(db), referred["id"], "标准版", "monthly", "hkt-first")
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
