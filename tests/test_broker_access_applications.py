from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.auth import AuthService
from core.broker_access_applications import (
    BrokerAccessApplicationError,
    BrokerAccessApplicationService,
    CANONICAL_PROVIDERS,
)
from core.compat import UTC
from core.database import DatabaseManager
from core.entitlement_policy import seed_canonical_policy


@pytest.fixture
def context(tmp_path):
    database = DatabaseManager(str(tmp_path / "broker-access.db"))
    auth = AuthService(database)
    user = auth.register("professional@example.com", "StrongPass123", "Professional", True)
    admin = auth.register("admin@example.com", "StrongPass123", "Admin", True)
    with database.transaction() as connection:
        seed_canonical_policy(connection)
    service = BrokerAccessApplicationService(database)
    return database, service, user, admin


def _eligible(database, user, plan="高级版", chat="700001"):
    now = datetime.now(UTC)
    database.execute(
        "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?",
        (plan, (now + timedelta(days=30)).isoformat(), user["id"]),
    )
    database.execute(
        """INSERT INTO membership_entitlements
           (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
           VALUES (?,?,?,?,30,'test',?,'active',?)""",
        (user["id"], plan, now.isoformat(), (now + timedelta(days=30)).isoformat(), f"eligible-{user['id']}", now.isoformat()),
    )
    database.execute(
        "INSERT INTO telegram_accounts(user_id,chat_id,is_active,created_at,updated_at) VALUES (?,?,1,?,?)",
        (user["id"], chat, now.isoformat(), now.isoformat()),
    )
    database.execute(
        """INSERT INTO user_settings(user_id,settings_json,updated_at)
           VALUES (?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
        (user["id"], '{"telegram":{"verified":true,"consent":true}}', now.isoformat()),
    )


def _super_admin(database, admin):
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (admin["id"], datetime.now(UTC).isoformat()),
    )


def test_only_canonical_five_us_providers_and_eligible_membership_with_telegram(context):
    database, service, user, _ = context
    assert CANONICAL_PROVIDERS == {"futu_moomoo", "tiger", "ibkr", "webull", "longbridge"}
    with pytest.raises(BrokerAccessApplicationError, match="会员策略"):
        service.create(user["id"], {"provider": "ibkr"}, "broker-key-01")
    _eligible(database, user)
    for provider in ("alpaca", "qmt", "ptrade", "a_stock"):
        with pytest.raises(BrokerAccessApplicationError, match="五家美股"):
            service.create(user["id"], {"provider": provider}, f"blocked-{provider}")
    database.execute("UPDATE telegram_accounts SET is_active=0 WHERE user_id=?", (user["id"],))
    with pytest.raises(BrokerAccessApplicationError, match="Telegram"):
        service.create(user["id"], {"provider": "ibkr"}, "broker-key-02")


def test_create_is_idempotent_fingerprinted_and_has_one_active_per_provider(context):
    database, service, user, _ = context
    _eligible(database, user)
    one, replayed = service.create(
        user["id"], {"provider": "ibkr", "request_reason": "需要美股及期权资格"}, "broker-key-10"
    )
    assert replayed is False and one["status"] == "submitted"
    assert one["eligibility_only"] is True
    assert one["broker_account_created"] is one["execution_enabled"] is False
    two, replayed = service.create(
        user["id"], {"provider": "ibkr", "request_reason": "需要美股及期权资格"}, "broker-key-10"
    )
    assert replayed is True and two == one
    with pytest.raises(BrokerAccessApplicationError, match="不同申请"):
        service.create(user["id"], {"provider": "tiger"}, "broker-key-10")
    with pytest.raises(BrokerAccessApplicationError, match="待审核"):
        service.create(user["id"], {"provider": "ibkr"}, "broker-key-11")
    assert database.fetch_one("SELECT COUNT(*) count FROM broker_accounts")["count"] == 0


def test_withdraw_is_owner_scoped_idempotent_and_reopens_provider(context):
    database, service, user, _ = context
    other = AuthService(database).register("other@example.com", "StrongPass123", "Other", True)
    _eligible(database, user)
    _eligible(database, other, chat="700002")
    item, _ = service.create(user["id"], {"provider": "webull"}, "broker-key-20")
    with pytest.raises(BrokerAccessApplicationError, match="不存在"):
        service.withdraw(other["id"], item["id"])
    assert service.withdraw(user["id"], item["id"])["status"] == "withdrawn"
    assert service.withdraw(user["id"], item["id"])["status"] == "withdrawn"
    replacement, _ = service.create(user["id"], {"provider": "webull"}, "broker-key-21")
    assert replacement["status"] == "submitted"


def test_super_admin_review_is_cas_audited_and_has_no_execution_side_effects(context):
    database, service, user, admin = context
    _eligible(database, user)
    _super_admin(database, admin)
    item, _ = service.create(user["id"], {"provider": "longbridge"}, "broker-key-30")
    approved = service.review(
        admin["id"], item["id"], {"decision": "approved", "reason": "会员及TG资格核验通过"}
    )
    assert approved["status"] == "approved"
    assert approved["decision_reason"] == "会员及TG资格核验通过"
    assert approved["execution_enabled"] is False
    with pytest.raises(BrokerAccessApplicationError, match="其他管理员"):
        service.review(admin["id"], item["id"], {"decision": "rejected", "reason": "冲突审核"})
    with pytest.raises(BrokerAccessApplicationError, match="已经审核通过"):
        service.create(user["id"], {"provider": "longbridge"}, "broker-key-31")
    audit = database.fetch_one(
        "SELECT details FROM user_action_logs WHERE action_type='ADMIN_BROKER_ACCESS_APPLICATION_REVIEW'"
    )
    assert "会员及TG资格核验通过" in audit["details"]
    assert database.fetch_one("SELECT COUNT(*) count FROM broker_accounts")["count"] == 0
    assert database.fetch_one("SELECT COUNT(*) count FROM telegram_service_outbox")["count"] == 0


def test_approval_rechecks_membership_and_telegram_inside_write_lock(context):
    database, service, user, admin = context
    _eligible(database, user)
    _super_admin(database, admin)
    item, _ = service.create(user["id"], {"provider": "ibkr"}, "broker-key-35")
    database.execute("UPDATE telegram_accounts SET is_active=0 WHERE user_id=?", (user["id"],))
    with pytest.raises(BrokerAccessApplicationError, match="Telegram"):
        service.review(
            admin["id"], item["id"], {"decision": "approved", "reason": "资格核验"}
        )
    assert service.list_for_user(user["id"])[0]["status"] == "submitted"
    rejected = service.review(
        admin["id"], item["id"], {"decision": "rejected", "reason": "Telegram 已解绑"}
    )
    assert rejected["status"] == "rejected"


@pytest.mark.parametrize(
    ("verified", "consent"),
    [(False, True), (True, False), (False, False)],
)
def test_application_requires_verified_and_consented_telegram(context, verified, consent):
    database, service, user, _ = context
    _eligible(database, user)
    database.execute(
        "UPDATE user_settings SET settings_json=? WHERE user_id=?",
        (f'{{"telegram":{{"verified":{str(verified).lower()},"consent":{str(consent).lower()}}}}}', user["id"]),
    )
    with pytest.raises(BrokerAccessApplicationError, match="验证并同意"):
        service.create(user["id"], {"provider": "ibkr"}, "broker-key-verified")


def test_non_super_admin_cannot_list_or_review(context):
    database, service, user, admin = context
    _eligible(database, user)
    item, _ = service.create(user["id"], {"provider": "futu_moomoo"}, "broker-key-40")
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'support',?)",
        (admin["id"], datetime.now(UTC).isoformat()),
    )
    with pytest.raises(BrokerAccessApplicationError, match="超级管理员"):
        service.list_for_admin(admin["id"])
    with pytest.raises(PermissionError, match="超级管理员"):
        service.review(admin["id"], item["id"], {"decision": "approved", "reason": "无权"})


def test_public_projection_never_exposes_chat_or_internal_idempotency(context):
    database, service, user, _ = context
    _eligible(database, user)
    service.create(user["id"], {"provider": "tiger"}, "broker-key-50")
    encoded = repr(service.list_for_user(user["id"]))
    for forbidden in ("chat_id", "idempotency_key", "request_fingerprint", "credential", "secret"):
        assert forbidden not in encoded
