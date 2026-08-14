from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json

import pytest
from starlette.requests import Request

from core.compat import UTC
from core.referral_coupon import ReferralCouponService
from payment.order_service import _EntitlementCommercePolicy
from src.apps.api.app import app, membership_orders, membership_quote


def _request(path: str, *, payload: dict, token: str, headers: dict[str, str] | None = None) -> Request:
    body = json.dumps(payload).encode()
    raw_headers = [
        (b"content-length", str(len(body)).encode()),
        (b"host", b"testserver"),
        (b"authorization", f"Bearer {token}".encode()),
    ]
    raw_headers.extend((key.lower().encode(), value.encode()) for key, value in (headers or {}).items())
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": raw_headers, "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443), "app": app, "path_params": {},
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


def _token(browser_api) -> str:
    return browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token


def test_membership_quote_http_rejects_unknown_money_and_retired_plan(browser_api):
    token = _token(browser_api)
    quoted = asyncio.run(membership_quote(_request(
        "/api/rewrite/v1/membership/quote",
        payload={"plan": "标准版", "cycle": "monthly"},
        token=token,
    )))
    result = _payload(quoted)
    assert result["list_price_minor"] == 29_800
    assert result["discount_order"] == ["coupon", "referral"]
    assert result["server_reprices_on_order"] is True

    with pytest.raises(Exception) as unknown:
        asyncio.run(membership_quote(_request(
            "/api/rewrite/v1/membership/quote",
            payload={"plan": "标准版", "cycle": "monthly", "amount_minor": 1},
            token=token,
        )))
    assert getattr(unknown.value, "status", None) == 400

    with pytest.raises(Exception) as retired:
        asyncio.run(membership_quote(_request(
            "/api/rewrite/v1/membership/quote",
            payload={"plan": "专业版", "cycle": "monthly"},
            token=token,
        )))
    assert getattr(retired.value, "status", None) == 409

    with pytest.raises(Exception) as invalid_coupon:
        asyncio.run(membership_quote(_request(
            "/api/rewrite/v1/membership/quote",
            payload={"plan": "标准版", "cycle": "monthly", "coupon_code": 123},
            token=token,
        )))
    assert getattr(invalid_coupon.value, "status", None) == 400


def test_membership_order_reprices_after_coupon_policy_changes(browser_api):
    database, auth = browser_api["database"], browser_api["auth"]
    admin = auth.register("quote-admin@example.com", "StrongPass123", "Quote Admin", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (admin["id"], datetime.now(UTC).isoformat()),
    )
    coupons = ReferralCouponService(database, plan_policy=_EntitlementCommercePolicy())
    coupon = coupons.create_coupon(
        admin["id"],
        {
            "code": "HTTP10", "campaign_name": "http quote", "discount_type": "percent",
            "discount_value": 1000, "max_discount_minor": 30_000,
            "starts_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "min_spend_minor": 0, "total_use_limit": 10, "per_user_limit": 1,
            "applicable_plans": ["标准版"], "applicable_cycles": ["monthly"],
            "enabled": True,
        },
        "http-coupon-create-0001",
    )
    token = _token(browser_api)
    quote = _payload(asyncio.run(membership_quote(_request(
        "/api/rewrite/v1/membership/quote",
        payload={"plan": "标准版", "cycle": "monthly", "coupon_code": coupon["code"]},
        token=token,
    ))))
    assert quote["coupon_discount_minor"] == 2_980

    coupons.pause_coupon(
        admin["id"], coupon["public_id"], int(coupon["version"]), "http-coupon-pause-0001"
    )
    with pytest.raises(Exception) as changed:
        asyncio.run(membership_orders(_request(
            "/api/rewrite/v1/membership/orders",
            payload={
                "plan": "标准版", "cycle": "monthly", "method": "fps",
                "terms_accepted": True, "coupon_code": coupon["code"],
            },
            token=token,
            headers={"idempotency-key": "http-order-reprice-0001"},
        )))
    assert getattr(changed.value, "status", None) == 400
    assert database.fetch_one("SELECT COUNT(*) count FROM subscription_orders")["count"] == 0
