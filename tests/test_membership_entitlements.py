"""Membership renewal, upgrade, and fallback contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.auth import AuthService
from core.compat import UTC
from core.database import DatabaseManager
from payment.order_service import OrderService
from scheduler.jobs import downgrade_expired_subscriptions
from src.apps.api.read_model import ReadOnlyLegacyRepository


def _user(database: DatabaseManager, name: str) -> dict:
    return AuthService(database).register(
        f"{name}@example.com", "CorrectHorse123", name, True
    )


def test_upgrade_is_immediate_and_falls_back_to_remaining_lower_tier(tmp_path):
    database = DatabaseManager(str(tmp_path / "membership-fallback.db"))
    user = _user(database, "fallback-member")
    lower_expiry = (datetime.now(UTC) + timedelta(days=45)).isoformat(
        timespec="seconds"
    )
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (lower_expiry, user["id"]),
    )
    service = OrderService(database)
    upgrade = service.create_order(
        user["id"],
        "专业版",
        "monthly",
        "paypal",
        terms_accepted=True,
        source="legacy",
    )
    assert service.process_callback("membership-upgrade-paid", upgrade["order_no"], "paid", {})

    active = database.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )
    assert active["plan_type"] == "专业版"
    assert datetime.fromisoformat(active["subscription_expire"]) < datetime.fromisoformat(
        lower_expiry
    )

    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds")
    database.execute(
        """UPDATE membership_entitlements SET expires_at=?
           WHERE source_kind='payment_order' AND source_ref=?""",
        (expired, upgrade["order_no"]),
    )
    database.execute(
        "UPDATE users SET subscription_expire=? WHERE id=?", (expired, user["id"])
    )

    assert downgrade_expired_subscriptions(database) == 1
    fallback = database.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )
    assert fallback == {"plan_type": "标准版", "subscription_expire": lower_expiry}


def test_same_tier_purchase_extends_continuously_without_duplicate_activation(tmp_path):
    database = DatabaseManager(str(tmp_path / "membership-renewal.db"))
    user = _user(database, "renewal-member")
    before = datetime.now(UTC).replace(microsecond=0)
    current_expiry = (before + timedelta(days=10)).isoformat(timespec="seconds")
    database.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=? WHERE id=?",
        (current_expiry, user["id"]),
    )
    service = OrderService(database)
    renewal = service.create_order(
        user["id"],
        "专业版",
        "monthly",
        "paypal",
        terms_accepted=True,
        source="legacy",
    )
    assert service.process_callback("membership-renewal-paid", renewal["order_no"], "paid", {})
    assert service.process_callback("membership-renewal-repeat", renewal["order_no"], "paid", {}) is False

    state = database.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )
    assert state["plan_type"] == "专业版"
    remaining = datetime.fromisoformat(state["subscription_expire"]) - before
    assert timedelta(days=39, hours=23) < remaining < timedelta(days=40, minutes=1)
    assert database.fetch_one(
        """SELECT COUNT(*) count FROM membership_entitlements
           WHERE source_kind='payment_order' AND source_ref=?""",
        (renewal["order_no"],),
    )["count"] == 1


def test_read_only_api_resolves_fallback_before_scheduler_updates_cache(tmp_path):
    db_path = tmp_path / "membership-read-model.db"
    database = DatabaseManager(str(db_path))
    auth = AuthService(database)
    user = auth.register(
        "read-model-fallback@example.com",
        "CorrectHorse123",
        "Read Model Fallback",
        True,
    )
    lower_expiry = (datetime.now(UTC) + timedelta(days=45)).isoformat(
        timespec="seconds"
    )
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (lower_expiry, user["id"]),
    )
    service = OrderService(database)
    upgrade = service.create_order(
        user["id"],
        "专业版",
        "monthly",
        "paypal",
        terms_accepted=True,
        source="legacy",
    )
    assert service.process_callback("read-model-upgrade-paid", upgrade["order_no"], "paid", {})
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds")
    database.execute(
        """UPDATE membership_entitlements SET expires_at=?
           WHERE source_kind='payment_order' AND source_ref=?""",
        (expired, upgrade["order_no"]),
    )
    database.execute(
        "UPDATE users SET subscription_expire=? WHERE id=?", (expired, user["id"])
    )
    login = auth.login(
        "read-model-fallback@example.com",
        "CorrectHorse123",
        "127.0.0.1",
        "pytest",
    )

    identity = ReadOnlyLegacyRepository(db_path).authenticate(login.access_token)

    assert identity.effective_plan == "标准版"
    assert identity.subscription_expire == lower_expiry


def test_historical_refund_with_only_legacy_snapshot_requires_manual_review(tmp_path):
    database = DatabaseManager(str(tmp_path / "membership-historical-refund.db"))
    user = _user(database, "historical-refund")
    now = datetime.now(UTC).replace(microsecond=0)
    expiry = (now + timedelta(days=30)).isoformat(timespec="seconds")
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (expiry, user["id"]),
    )
    database.execute(
        """INSERT INTO membership_entitlements
           (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
           VALUES (?,?,?,?,NULL,'legacy_cache',?,'active',?)""",
        (
            user["id"],
            "标准版",
            now.isoformat(timespec="seconds"),
            expiry,
            f"user:{user['id']}:legacy",
            now.isoformat(timespec="seconds"),
        ),
    )
    database.execute(
        """INSERT INTO subscription_orders
           (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,
            created_at,paid_at,previous_plan_type,previous_subscription_expire,entitlement_days)
           VALUES ('HISTORICAL-ORDER',?,'标准版','monthly',298,'HKD','paypal','paid',
                   ?,?,'免费版',NULL,30)""",
        (user["id"], now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
    )

    with pytest.raises(ValueError, match="人工核销"):
        OrderService(database).process_reversal(
            "historical-refund-event",
            "HISTORICAL-ORDER",
            {"provider": "paypal"},
            "provider_refund",
        )

    assert OrderService(database).get_order("HISTORICAL-ORDER")["status"] == "paid"


def test_authoritative_ledger_ignores_stale_higher_cache(tmp_path):
    from core.membership import authoritative_membership_user

    database = DatabaseManager(str(tmp_path / "membership-authoritative.db"))
    user = _user(database, "authoritative-member")
    now = datetime.now(UTC).replace(microsecond=0)
    lower_expiry = (now + timedelta(days=20)).isoformat(timespec="seconds")
    stale_higher_expiry = (now + timedelta(days=90)).isoformat(timespec="seconds")
    database.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=? WHERE id=?",
        (stale_higher_expiry, user["id"]),
    )
    database.execute(
        """INSERT INTO membership_entitlements
           (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
           VALUES (?,'标准版',?, ?,20,'payment_order','LOWER-ACTIVE','active',?)""",
        (
            user["id"],
            now.isoformat(timespec="seconds"),
            lower_expiry,
            now.isoformat(timespec="seconds"),
        ),
    )
    cached = database.fetch_one(
        "SELECT id,plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )

    resolved = authoritative_membership_user(database, cached, now)

    assert resolved["plan_type"] == "标准版"
    assert resolved["subscription_expire"] == lower_expiry


def test_authoritative_lookup_failure_does_not_return_cached_high_plan():
    from core.membership import authoritative_membership_user

    class BrokenDatabase:
        def fetch_all(self, *_args, **_kwargs):
            raise RuntimeError("ledger unavailable")

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        authoritative_membership_user(
            BrokenDatabase(),
            {
                "id": 7,
                "plan_type": "专业版",
                "subscription_expire": "2099-01-01T00:00:00+00:00",
            },
        )


def test_read_only_authentication_fails_closed_when_ledger_table_is_missing(tmp_path):
    db_path = tmp_path / "membership-missing-ledger.db"
    database = DatabaseManager(str(db_path))
    auth = AuthService(database)
    user = auth.register(
        "missing-ledger@example.com",
        "CorrectHorse123",
        "Missing Ledger",
        True,
    )
    database.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=? WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(), user["id"]),
    )
    login = auth.login(
        "missing-ledger@example.com",
        "CorrectHorse123",
        "127.0.0.1",
        "pytest",
    )
    database.execute("DROP TABLE membership_entitlements")

    identity = ReadOnlyLegacyRepository(db_path).authenticate(login.access_token)

    assert identity.effective_plan == "免费版"
    assert identity.subscription_expire is None
