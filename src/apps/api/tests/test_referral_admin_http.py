from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from core.compat import UTC
from src.apps.api.app import (
    admin_referral_analytics,
    admin_referral_coupon_pause,
    admin_referral_coupons,
    admin_referral_policy,
    app,
)


def _request(
    path: str,
    *,
    method: str = "GET",
    token: str,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    path_params: dict | None = None,
    query: dict[str, str] | None = None,
) -> Request:
    body = json.dumps(payload).encode() if payload is not None else b""
    raw_headers = [
        (b"content-length", str(len(body)).encode()),
        (b"host", b"testserver"),
        (b"authorization", f"Bearer {token}".encode()),
    ]
    raw_headers.extend((key.lower().encode(), value.encode()) for key, value in (headers or {}).items())
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": urlencode(query or {}).encode(), "headers": raw_headers,
        "client": ("127.0.0.1", 50000), "server": ("testserver", 443), "app": app,
        "path_params": path_params or {},
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _payload(response):
    return json.loads(response.body.decode())


def _super_admin(browser_api):
    database, auth = browser_api["database"], browser_api["auth"]
    user = auth.register("referral-admin@example.com", "StrongPass123", "Referral Admin", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (user["id"], datetime.now(UTC).isoformat()),
    )
    token = auth.login(
        "referral-admin@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token
    return user, token


def test_referral_policy_and_coupon_admin_http_are_reauthenticated_and_auditable(browser_api):
    database = browser_api["database"]
    _, token = _super_admin(browser_api)
    current = _payload(asyncio.run(admin_referral_policy(_request(
        "/api/rewrite/v1/admin/referrals/policy", token=token,
    ))))
    policy = {
        **current["policy"],
        "withdrawal_min_minor": 10_000,
        "withdrawal_max_minor": 300_000,
        "bonus_enabled": True,
        "bonus_tiers": [
            {"qualified_count": 3, "cumulative_amount_minor": 5_000},
            {"qualified_count": 10, "cumulative_amount_minor": 25_000},
        ],
    }
    updated = _payload(asyncio.run(admin_referral_policy(_request(
        "/api/rewrite/v1/admin/referrals/policy", method="PUT", token=token,
        payload={"password": "StrongPass123", "expected_version": current["version"], "policy": policy},
        headers={"idempotency-key": "admin-policy-http-0001"},
    ))))
    assert updated["version"] == current["version"] + 1
    assert updated["policy"]["bonus_tiers"][1]["cumulative_amount_minor"] == 25_000

    coupon_payload = {
        "code": "LAUNCH10", "campaign_name": "launch", "discount_type": "percent",
        "discount_value": 1000, "max_discount_minor": 30_000,
        "starts_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "min_spend_minor": 0, "total_use_limit": 100, "per_user_limit": 1,
        "applicable_plans": ["标准版", "高级版"],
        "applicable_cycles": ["monthly", "quarterly", "yearly"],
        "enabled": True,
    }
    created = asyncio.run(admin_referral_coupons(_request(
        "/api/rewrite/v1/admin/referrals/coupons", method="POST", token=token,
        payload={"password": "StrongPass123", "coupon": coupon_payload},
        headers={"idempotency-key": "admin-coupon-http-0001"},
    )))
    coupon = _payload(created)
    assert created.status_code == 201
    assert coupon["code"] == "LAUNCH10"
    assert set(coupon).isdisjoint({"id", "created_by", "updated_by", "applicable_plans_json"})

    listed = _payload(asyncio.run(admin_referral_coupons(_request(
        "/api/rewrite/v1/admin/referrals/coupons", token=token,
    ))))
    assert listed["items"][0]["coupon_id"] == coupon["coupon_id"]
    paused = _payload(asyncio.run(admin_referral_coupon_pause(_request(
        f"/api/rewrite/v1/admin/referrals/coupons/{coupon['coupon_id']}/pause",
        method="POST", token=token,
        payload={"password": "StrongPass123", "expected_version": coupon["version"]},
        headers={"idempotency-key": "admin-coupon-pause-0001"},
        path_params={"coupon_id": coupon["coupon_id"]},
    ))))
    assert paused["enabled"] is False
    assert database.fetch_one(
        "SELECT COUNT(*) count FROM membership_promotion_admin_events"
    )["count"] == 3


def test_referral_analytics_http_uses_public_attribution_fields(browser_api):
    _, token = _super_admin(browser_api)
    payload = _payload(asyncio.run(admin_referral_analytics(_request(
        "/api/rewrite/v1/admin/referrals/analytics", token=token,
        query={"promotion_type": "all"},
    ))))
    assert set(payload) == {"items", "summary"}
    assert set(payload["summary"]) == {
        "orders", "customers", "coupon_only_orders", "referral_only_orders",
        "stacked_orders", "unattributed_orders", "list_price_minor", "coupon_cost_minor",
        "referral_cost_minor", "commission_cost_minor", "bonus_cost_minor",
        "promotion_cost_minor", "paid_revenue_minor", "refund_or_chargeback_minor",
        "net_revenue_minor",
    }
    assert "user_id" not in json.dumps(payload)
    assert "email" not in json.dumps(payload)


def test_referral_policy_rejects_non_super_admin(browser_api):
    database, auth = browser_api["database"], browser_api["auth"]
    finance = auth.register("finance-policy@example.com", "StrongPass123", "Finance", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (finance["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'finance',?)",
        (finance["id"], datetime.now(UTC).isoformat()),
    )
    token = auth.login(
        "finance-policy@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token
    with pytest.raises(Exception) as caught:
        asyncio.run(admin_referral_policy(_request(
            "/api/rewrite/v1/admin/referrals/policy", token=token,
        )))
    assert getattr(caught.value, "status", None) == 403
