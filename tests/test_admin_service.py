"""Focused checks for persisted admin permissions and operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import json
import sqlite3

import pytest

from core.admin_service import AdminService
from core.auth import AuthError, AuthService
from core.database import DatabaseManager
from core.user_settings import load_user_settings, merge_user_settings
from payment.order_service import OrderService
from ui.pages.growth import _canonical_social_url, _claim_daily_checkin, _submit_social_share


def _register(auth: AuthService, name: str) -> dict:
    return auth.register(f"{name}@example.com", "CorrectHorse123", name.title(), True)


@pytest.fixture
def services(tmp_path):
    db = DatabaseManager(str(tmp_path / "admin.db"))
    auth = AuthService(db)
    admin = _register(auth, "owner")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    return db, auth, admin, AdminService(db)


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-longer-than-thirty-two-characters")


def test_rbac_user_subscription_and_global_controls(services):
    db, auth, admin, service = services
    support = _register(auth, "support")
    customer = _register(auth, "customer")
    service.set_role(admin["id"], support["id"], "support")

    with pytest.raises(PermissionError):
        service.adjust_subscription(support["id"], customer["id"], "标准版", 30)
    with pytest.raises(ValueError, match="自己的超级管理员"):
        service.set_role(admin["id"], admin["id"], "support")

    expiry = service.adjust_subscription(admin["id"], customer["id"], "标准版", 30)
    assert expiry
    service.add_ip(admin["id"], customer["id"], "203.0.113.10")
    assert service.list_ips(admin["id"], customer["id"])[0]["ip_address"] == "203.0.113.10"
    service.set_recommendations_published(admin["id"], False)
    assert service.control_enabled("recommendations_published", True) is False
    assert service.control_enabled("user_auto_trading_enabled", False) is True
    with pytest.raises(PermissionError, match="超级管理员"):
        service.set_user_auto_trading_enabled(support["id"], False)
    service.set_user_auto_trading_enabled(admin["id"], False)
    assert service.control_enabled("user_auto_trading_enabled", True) is False
    service.set_user_auto_trading_enabled(admin["id"], True)
    assert service.control_enabled("user_auto_trading_enabled", False) is True
    service.set_global_opening_paused(admin["id"], True)
    future = _register(auth, "future")
    assert db.fetch_one("SELECT opening_paused FROM user_controls WHERE user_id=?", (future["id"],))["opening_paused"] == 1

    service.set_user_active(admin["id"], customer["id"], False)
    assert db.fetch_one("SELECT is_active FROM users WHERE id=?", (customer["id"],))["is_active"] == 0
    assert any(row["action_type"] == "ADMIN_USER_AUTO_TRADING_STATUS" for row in service.list_audit(admin["id"]))
    assert any(row["action_type"] == "ADMIN_USER_STATUS" for row in service.list_audit(admin["id"]))


def test_data_source_verification_is_system_only_and_never_stores_captcha(services):
    _db, auth, admin, service = services
    finance = _register(auth, "finance-verification")
    service.set_role(admin["id"], finance["id"], "finance")

    service.record_data_source_verification(
        admin["id"], "opend", "submit_captcha", True
    )
    service.record_data_source_verification(
        admin["id"], "opend", "request_phone_code", True
    )
    service.record_data_source_verification(
        admin["id"], "opend", "submit_phone_code", True
    )
    records = [
        json.loads(row["details"])
        for row in service.list_audit(admin["id"])
        if row["action_type"] == "ADMIN_DATA_SOURCE_VERIFICATION"
    ]
    assert {record["action"] for record in records} >= {
        "submit_captcha",
        "request_phone_code",
        "submit_phone_code",
    }
    assert all(set(record) == {"provider", "action", "success"} for record in records)
    assert all(record["provider"] == "opend" for record in records)

    with pytest.raises(PermissionError):
        service.record_data_source_verification(
            finance["id"], "opend", "request_captcha", True
        )


def test_live_platform_pause_requires_manual_user_resume(services, monkeypatch):
    db, auth, admin, service = services
    customer = _register(auth, "live-customer")
    merge_user_settings(
        customer["id"],
        {
            "live_auto_enabled": True,
            "telegram": {"chat_id": "123456789", "consent": True, "verified": True},
        },
        db,
    )
    sent = []
    monkeypatch.setattr("notification.telegram_bot.telegram_configured", lambda _target: True)
    monkeypatch.setattr("notification.telegram_bot.send_telegram", lambda message, target: sent.append((message, target)))

    paused = service.set_user_auto_trading_enabled(admin["id"], False)
    settings = load_user_settings(customer["id"], db)
    assert paused == {"affected": 1, "notified": 1}
    assert settings["live_auto_enabled"] is False
    assert settings["live_auto_platform_suspended"] is True

    resumed = service.set_user_auto_trading_enabled(admin["id"], True)
    settings = load_user_settings(customer["id"], db)
    assert resumed == {"affected": 1, "notified": 1}
    assert settings["live_auto_enabled"] is False
    assert settings["live_auto_platform_suspended"] is True
    assert len(sent) == 2


def test_admin_ip_removal_revokes_only_matching_sessions(services):
    db, auth, admin, service = services
    customer = _register(auth, "ip-session-customer")
    session = auth.login(customer["email"], "CorrectHorse123", "203.0.113.11", "pytest")
    ip_id = service.list_ips(admin["id"], customer["id"])[0]["id"]

    service.remove_ip(admin["id"], customer["id"], ip_id)

    with pytest.raises(AuthError, match="其他设备"):
        auth.verify(session.access_token)
    with pytest.raises(AuthError, match="IP 已被停用"):
        auth.login(customer["email"], "CorrectHorse123", "203.0.113.11", "pytest")
    assert db.fetch_one(
        "SELECT is_active FROM user_sessions WHERE user_id=? AND ip_address=?",
        (customer["id"], "203.0.113.11"),
    )["is_active"] == 0


def test_admin_unlock_clears_account_login_throttle(services):
    db, _auth, admin, service = services
    customer = _register(_auth, "unlock-throttle")
    key = AuthService._rate_key("login-account", customer["email"], "*")
    db.execute(
        "INSERT INTO auth_rate_limits (rate_key,attempts,window_started,blocked_until) VALUES (?,?,?,?)",
        (key, 12, datetime.now(UTC).isoformat(), (datetime.now(UTC) + timedelta(minutes=15)).isoformat()),
    )

    service.unlock_user(admin["id"], customer["id"])

    assert db.fetch_one("SELECT 1 FROM auth_rate_limits WHERE rate_key=?", (key,)) is None


def test_dashboard_metrics_follow_role_permissions(services):
    _db, auth, admin, service = services
    support = _register(auth, "metrics-support")
    finance = _register(auth, "metrics-finance")
    researcher = _register(auth, "metrics-research")
    auditor = _register(auth, "metrics-risk")
    service.set_role(admin["id"], support["id"], "support")
    service.set_role(admin["id"], finance["id"], "finance")
    service.set_role(admin["id"], researcher["id"], "research")
    service.set_role(admin["id"], auditor["id"], "risk_audit")

    assert set(service.dashboard_metrics(support["id"])) == {"users", "active_users"}
    assert set(service.dashboard_metrics(finance["id"])) == {
        "subscribers",
        "pending_orders",
        "paid_amount",
    }
    assert service.dashboard_metrics(researcher["id"]) == {}
    assert set(service.dashboard_metrics(auditor["id"])) == {"critical_risk"}
    assert set(service.dashboard_metrics(admin["id"])) == {
        "users",
        "active_users",
        "subscribers",
        "pending_orders",
        "paid_amount",
        "critical_risk",
    }


def test_super_admin_and_support_can_grant_trial_but_finance_cannot(services):
    db, auth, admin, service = services
    support = _register(auth, "trial-support")
    finance = _register(auth, "trial-finance")
    customer = _register(auth, "trial-customer")
    service.set_role(admin["id"], support["id"], "support")
    service.set_role(admin["id"], finance["id"], "finance")

    assert service.has_permission(service.role_for(admin["id"]), "membership_grant")
    first_expiry = service.grant_trial(admin["id"], customer["id"], "标准版", 7, "新用户体验")
    second_expiry = service.grant_trial(support["id"], customer["id"], "标准版", 3, "客服补赠")
    assert datetime.fromisoformat(second_expiry) > datetime.fromisoformat(first_expiry)
    with pytest.raises(PermissionError):
        service.grant_trial(finance["id"], customer["id"], "标准版", 3, "越权尝试")
    logs = service.list_membership_logs(support["id"])
    assert [row["operation_type"] for row in logs[:2]] == ["grant_trial", "grant_trial"]
    assert db.fetch_one("SELECT plan_type FROM users WHERE id=?", (customer["id"],))["plan_type"] == "标准版"


def test_finance_can_reconcile_fps_but_support_cannot(services):
    db, auth, admin, service = services
    finance = _register(auth, "finance")
    support = _register(auth, "helper")
    customer = _register(auth, "buyer")
    service.set_role(admin["id"], finance["id"], "finance")
    service.set_role(admin["id"], support["id"], "support")
    order = OrderService(db).create_order(customer["id"], "标准版", "monthly", "fps", terms_accepted=True)

    with pytest.raises(PermissionError):
        service.confirm_fps(support["id"], order["order_no"])
    service.confirm_fps(finance["id"], order["order_no"])
    assert OrderService(db).get_order(order["order_no"])["status"] == "paid"
    assert service.reconciliation_rows(finance["id"])[0]["matched"] == 1


def test_social_share_review_is_idempotent_audited_and_atomic(services):
    db, auth, admin, service = services
    researcher = _register(auth, "researcher")
    customer = _register(auth, "creator")
    service.set_role(admin["id"], researcher["id"], "research")
    now = "2026-08-06T00:00:00+00:00"
    db.execute(
        "INSERT INTO rewards (user_id,reward_type,days,reference,created_at) VALUES (?,?,?,?,?)",
        (customer["id"], "SOCIAL_PENDING", 0, "YouTube:https://example.com/one", now),
    )
    reward_id = service.list_social_share_requests(researcher["id"])[0]["id"]

    expiry = service.review_social_share(researcher["id"], reward_id, True, 7)

    assert db.fetch_one("SELECT reward_type,days FROM rewards WHERE id=?", (reward_id,)) == {
        "reward_type": "SOCIAL_APPROVED",
        "days": 7,
    }
    assert db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (customer["id"],)
    ) == {"plan_type": "标准版", "subscription_expire": expiry}
    with pytest.raises(ValueError, match="已经审核"):
        service.review_social_share(researcher["id"], reward_id, True, 7)
    assert db.fetch_one(
        "SELECT subscription_expire FROM users WHERE id=?", (customer["id"],)
    )["subscription_expire"] == expiry

    db.execute(
        "INSERT INTO rewards (user_id,reward_type,days,reference,created_at) VALUES (?,?,?,?,?)",
        (customer["id"], "SOCIAL_PENDING", 0, "Instagram:https://example.com/two", now),
    )
    rejected_id = service.list_social_share_requests(researcher["id"])[0]["id"]
    service.review_social_share(researcher["id"], rejected_id, False)
    assert db.fetch_one("SELECT reward_type,days FROM rewards WHERE id=?", (rejected_id,)) == {
        "reward_type": "SOCIAL_REJECTED",
        "days": 0,
    }
    actions = {row["action_type"] for row in service.list_audit(researcher["id"])}
    assert {"ADMIN_SOCIAL_SHARE_APPROVE", "ADMIN_SOCIAL_SHARE_REJECT"} <= actions

    db.execute(
        "INSERT INTO rewards (user_id,reward_type,days,reference,created_at) VALUES (?,?,?,?,?)",
        (customer["id"], "SOCIAL_PENDING", 0, "Facebook:https://example.com/three", now),
    )
    pending_id = service.list_social_share_requests(researcher["id"])[0]["id"]
    with db.transaction() as conn:
        conn.executescript(
            """CREATE TRIGGER reject_social_audit BEFORE INSERT ON user_action_logs
               WHEN NEW.action_type='ADMIN_SOCIAL_SHARE_APPROVE'
               BEGIN SELECT RAISE(ABORT, 'audit failed'); END;"""
        )
    before = db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (customer["id"],)
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.review_social_share(researcher["id"], pending_id, True, 5)
    assert db.fetch_one("SELECT reward_type,days FROM rewards WHERE id=?", (pending_id,)) == {
        "reward_type": "SOCIAL_PENDING",
        "days": 0,
    }
    assert db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (customer["id"],)
    ) == before


def test_reviewed_social_link_cannot_be_resubmitted(services):
    db, auth, admin, service = services
    customer = _register(auth, "social-repeat")
    url = _canonical_social_url("https://Example.com/post/1#comments")
    assert url == "https://example.com/post/1"
    assert _submit_social_share(db, customer["id"], "YouTube", url)
    reward_id = db.fetch_one("SELECT id FROM rewards WHERE user_id=?", (customer["id"],))["id"]
    service.review_social_share(admin["id"], reward_id, True, 3)
    assert not _submit_social_share(db, customer["id"], "其他", url)
    assert _canonical_social_url("http://example.com/post/1") is None
    assert _canonical_social_url("https://127.0.0.1/private") is None


def test_streak_reward_extends_subscription_once_per_seven_days(services):
    db, auth, _, _ = services
    customer = _register(auth, "streak-user")
    start = datetime(2026, 7, 1, tzinfo=UTC).date()
    for offset in range(7):
        claimed, streak = _claim_daily_checkin(
            db, customer["id"], (start + timedelta(days=offset)).isoformat()
        )
        assert claimed
        assert streak is (offset == 6)
    first_expiry = db.fetch_one(
        "SELECT subscription_expire FROM users WHERE id=?", (customer["id"],)
    )["subscription_expire"]
    assert first_expiry
    for offset in range(7, 14):
        _, streak = _claim_daily_checkin(
            db, customer["id"], (start + timedelta(days=offset)).isoformat()
        )
        assert streak is (offset == 13)
    rewards = db.fetch_one(
        "SELECT COUNT(*) count,SUM(days) days FROM rewards WHERE user_id=? AND reward_type='STREAK_7'",
        (customer["id"],),
    )
    assert rewards == {"count": 2, "days": 2}
