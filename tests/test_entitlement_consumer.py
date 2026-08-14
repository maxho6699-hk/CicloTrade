import sqlite3
from datetime import datetime, timedelta

from core.compat import UTC
from core.database import DatabaseManager
from core.entitlement_consumer import (
    commerce_decision,
    policy_account_limit,
    policy_alert_limit,
    policy_market_data_delay,
    policy_recommendation_delay,
    policy_trading_limits,
    verified_can,
    verified_capabilities,
)
from core.entitlement_policy import seed_canonical_policy
from core.plans import CAPABILITIES


def _db(tmp_path):
    database = DatabaseManager(str(tmp_path / "consumer.db"))
    with database.transaction() as connection:
        seed_canonical_policy(connection)
    return database


def test_consumer_uses_published_policy_not_mutable_legacy_capabilities(tmp_path):
    database = _db(tmp_path)
    with database.transaction() as connection:
        before = verified_capabilities(connection, "高级版")
        CAPABILITIES["高级版"].add("option_auto_live")
        try:
            assert verified_can(connection, "高级版", "option_live_beta_apply") is True
            assert verified_can(connection, "高级版", "option_auto_live") is False
            assert verified_capabilities(connection, "高级版") == before
        finally:
            CAPABILITIES["高级版"].discard("option_auto_live")


def test_advanced_has_application_only_and_no_runtime_or_account_control(tmp_path):
    database = _db(tmp_path)
    with database.transaction() as connection:
        assert verified_can(connection, "高级版", "option_live_beta_apply") is True
        assert verified_can(connection, "高级版", "option_auto_live") is False
        assert verified_can(connection, "高级版", "broker_access_apply") is True
        assert policy_account_limit(connection, "高级版") == 0
        assert policy_trading_limits(connection, "高级版")["broker_accounts"] == 0
        assert policy_trading_limits(connection, "高级版")["instruments"] == ("stock",)


def test_published_policy_drives_alert_and_delivery_contracts(tmp_path):
    database = _db(tmp_path)
    with database.transaction() as connection:
        assert policy_alert_limit(connection, "免费版") == 1
        assert policy_alert_limit(connection, "标准版") == 10
        assert policy_alert_limit(connection, "高级版") is None
        assert policy_market_data_delay(connection, "免费版", "stock") == 15
        assert policy_market_data_delay(connection, "标准版", "stock") == 0
        assert policy_recommendation_delay(connection, "标准版", "stock") == 60
        assert policy_recommendation_delay(connection, "高级版", "stock") == 0
        assert policy_recommendation_delay(connection, "高级版", "option") == 15
        assert policy_recommendation_delay(connection, "专业版", "option") == 0


def test_policy_projection_fails_closed_without_verified_policy():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    assert policy_alert_limit(connection, "高级版") == 0
    assert policy_market_data_delay(connection, "高级版", "stock") == 15
    assert policy_recommendation_delay(connection, "高级版", "stock") == 60
    assert policy_trading_limits(connection, "高级版")["instruments"] == ()


def test_retired_plans_keep_compatibility_reads_but_no_commerce(tmp_path):
    database = _db(tmp_path)
    with database.transaction() as connection:
        assert verified_can(connection, "专业版", "option_chain") is True
        assert verified_can(connection, "专业版", "option_auto_live") is False
        assert policy_account_limit(connection, "专业版") == 0
        assert commerce_decision(connection, "专业版", "purchase")["allowed"] is False
        assert commerce_decision(connection, "专业版", "renew")["allowed"] is False
        assert commerce_decision(connection, "专业版", "admin_grant")["allowed"] is False


def test_policy_hash_or_readiness_failure_fails_closed(tmp_path):
    database = _db(tmp_path)
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT id FROM membership_entitlement_policy_versions WHERE policy_key='public_membership_v1'"
        ).fetchone()
        connection.execute("DROP TRIGGER trg_membership_entitlement_policy_no_update")
        connection.execute(
            "UPDATE membership_entitlement_policy_versions SET policy_sha256=? WHERE id=?",
            ("0" * 64, row["id"]),
        )
        assert verified_can(connection, "高级版", "option_live_beta_apply") is False
        assert commerce_decision(connection, "高级版", "purchase")["allowed"] is False


def test_missing_readiness_review_fails_closed(tmp_path):
    database = _db(tmp_path)
    with database.transaction() as connection:
        connection.execute("DELETE FROM membership_entitlement_readiness_reviews")
        assert verified_can(connection, "高级版", "option_live_beta_apply") is False
        assert commerce_decision(connection, "高级版", "purchase")["allowed"] is False


def test_expired_readiness_receipt_fails_closed_at_as_of(tmp_path):
    database = _db(tmp_path)
    checked_at = datetime.now(UTC)
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER trg_membership_entitlement_readiness_receipts_no_update")
        connection.execute(
            "UPDATE membership_entitlement_readiness_receipts SET valid_until=?",
            ((checked_at - timedelta(seconds=1)).isoformat(),),
        )
        assert verified_capabilities(connection, "高级版", as_of=checked_at) == set()
        assert verified_can(connection, "高级版", "option_live_beta_apply", as_of=checked_at) is False


def test_future_as_of_keeps_future_valid_receipt_and_later_expiry_fails(tmp_path):
    database = _db(tmp_path)
    checked_at = datetime.now(UTC)
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER trg_membership_entitlement_readiness_receipts_no_update")
        connection.execute(
            "UPDATE membership_entitlement_readiness_receipts SET valid_until=?",
            ((checked_at + timedelta(days=1)).isoformat(),),
        )
        assert verified_can(connection, "高级版", "option_live_beta_apply", as_of=checked_at) is True
        assert verified_can(
            connection, "高级版", "option_live_beta_apply", as_of=checked_at + timedelta(days=2),
        ) is False


def test_retired_legacy_read_does_not_require_readiness_receipt(tmp_path):
    database = _db(tmp_path)
    with database.transaction() as connection:
        connection.execute("DELETE FROM membership_entitlement_readiness_reviews")
        assert verified_can(connection, "专业版", "option_chain") is True
        assert verified_can(connection, "专业版", "option_auto_live") is False


def test_missing_policy_fails_closed():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    assert verified_can(connection, "高级版", "option_live_beta_apply") is False
    assert commerce_decision(connection, "高级版", "purchase")["reason"] == "policy_unavailable"
