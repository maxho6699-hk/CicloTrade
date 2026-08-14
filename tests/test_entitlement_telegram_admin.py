"""Published-policy checks owned by the Telegram/Admin cutover."""

from __future__ import annotations

import pytest

from core.admin_service import AdminService
from core.auth import AuthService
from core.database import DatabaseManager
from core.entitlement_policy import seed_canonical_policy
from notification.entitlement_adapter import commerce_plan_allowed, public_commerce_plans
from ui.pages.admin import _grantable_plan_options


def test_expired_or_removed_review_fails_closed_for_commerce_and_admin_grants(tmp_path):
    database = DatabaseManager(str(tmp_path / "reviewed-policy.db"))
    auth = AuthService(database)
    admin = auth.register("owner@example.com", "CorrectHorse123", "Owner", True)
    customer = auth.register("customer@example.com", "CorrectHorse123", "Customer", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    with database.transaction() as conn:
        seed_canonical_policy(conn)

    with database.transaction() as conn:
        assert commerce_plan_allowed(conn, "标准版", "admin_grant") is True
    assert [item["key"] for item in public_commerce_plans(database, "purchasable")] == ["标准版", "高级版"]
    assert _grantable_plan_options(database) == ["标准版", "高级版"]

    database.execute("DELETE FROM membership_entitlement_readiness_reviews")
    with database.transaction() as conn:
        assert commerce_plan_allowed(conn, "标准版", "admin_grant") is False
    assert public_commerce_plans(database, "purchasable") == ()
    assert _grantable_plan_options(database) == []
    with pytest.raises(PermissionError, match="当前会员策略"):
        AdminService(database).grant_trial(admin["id"], customer["id"], "标准版", 7, "测试", idempotency_key="review-removed-001")
