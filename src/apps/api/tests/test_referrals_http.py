from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json

import pytest
from starlette.requests import Request

from core.compat import UTC
from core.referral_affiliate import ReferralCommissionService, ReferralProgramService, ReferralService
from core.referral_coupon import ReferralCouponService
from payment.order_service import OrderService
from src.apps.api.app import (
    admin_referral_withdrawal_paid,
    admin_referral_withdrawal_review,
    admin_referral_withdrawals,
    app,
    referral_portal,
    referral_visit,
    referral_withdrawals,
)


def _request(path: str, *, method="GET", payload=None, token=None, headers=None, path_params=None) -> Request:
    body = json.dumps(payload).encode() if payload is not None else b""
    raw_headers = [(b"content-length", str(len(body)).encode()), (b"host", b"testserver")]
    if token:
        raw_headers.append((b"authorization", f"Bearer {token}".encode()))
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": raw_headers, "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443), "app": app, "path_params": path_params or {},
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
    return json.loads(response.body.decode()) if response.body else None


def _login(browser_api, email="browser@example.com", password="StrongPass123"):
    return browser_api["auth"].login(email, password, "127.0.0.1", "pytest").access_token


def test_portal_and_visit_contract_use_public_fields_hkt_and_no_raw_fingerprint(browser_api, monkeypatch):
    monkeypatch.setenv("REFERRAL_VISIT_HMAC_SECRET", "referral-test-secret-that-is-at-least-32-characters")
    token = _login(browser_api)
    response = asyncio.run(referral_portal(_request("/api/rewrite/v1/referrals/portal", token=token)))
    payload = _payload(response)
    assert set(payload) == {"program", "invite", "balances", "withdrawal_eligibility", "trends", "funnel", "referrals", "commissions", "withdrawals", "timeline"}
    assert payload["program"]["currency"] == "HKD"
    assert payload["program"]["minimum_withdrawal_minor"] > 0
    assert payload["program"]["enabled"] is False
    assert payload["program"]["cutover_at"] is None
    assert payload["invite"]["qr_payload"] == payload["invite"]["invite_link"]
    assert payload["invite"]["invite_link"].startswith("/login?ref=")
    assert "testserver" not in payload["invite"]["invite_link"]
    visit = asyncio.run(referral_visit(_request(
        "/api/rewrite/v1/referrals/visits", method="POST",
        payload={"invite_code": payload["invite"]["invite_code"]},
        headers={"user-agent": "pytest browser"},
    )))
    assert visit.status_code == 204
    row = browser_api["database"].fetch_one("SELECT fingerprint_hash FROM referral_visit_daily")
    assert len(row["fingerprint_hash"]) == 64
    assert "127.0.0.1" not in row["fingerprint_hash"]
    assert "pytest" not in row["fingerprint_hash"]
    rate = browser_api["database"].fetch_one("SELECT rate_key_hash FROM referral_visit_rate_limits")
    assert len(rate["rate_key_hash"]) == 64


def test_withdrawal_http_and_billing_admin_state_machine(browser_api):
    database, auth = browser_api["database"], browser_api["auth"]
    release_admin = auth.register(
        "referral-http-release@example.com", "StrongPass123", "Referral Release", True
    )
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (release_admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)",
        (release_admin["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    ReferralProgramService(database).enable(release_admin["id"])
    policy_service = ReferralCouponService(database)
    current_policy = policy_service.policy(release_admin["id"])
    policy_service.update_policy(
        release_admin["id"],
        {**current_policy["policy"], "withdrawal_min_minor": 5_000},
        current_policy["version"],
        "http-withdrawal-policy-0001",
    )
    referrer = database.fetch_one("SELECT * FROM users WHERE email='browser@example.com'")
    profile = ReferralService(database).ensure_profile(referrer["id"])
    claim_fingerprint = "a" * 64
    referral_claim = ReferralService(database).issue_link_claim(
        profile["invite_code"], claim_fingerprint
    )
    referred = auth.register(
        "ref-http@example.com", "StrongPass123", "Ref HTTP", True, profile["invite_code"],
        referral_claim=referral_claim, referral_claim_fingerprint=claim_fingerprint,
    )
    order = OrderService(database).create_order(
        referred["id"], "高级版", "yearly", "paypal", terms_accepted=True,
        source="legacy", idempotency_key="http-referral-order",
    )
    OrderService(database).process_callback("http-referral-paid", order["order_no"], "paid", {})
    commission = database.fetch_one("SELECT * FROM referral_commissions WHERE source_order_no=?", (order["order_no"],))
    ReferralCommissionService(database).release_due(
        referrer["id"], datetime.fromisoformat(commission["available_at"]) + timedelta(seconds=1)
    )
    token = _login(browser_api)
    created = asyncio.run(referral_withdrawals(_request(
        "/api/rewrite/v1/referrals/withdrawals", method="POST", token=token,
        payload={"amount_minor": 10000, "currency": "HKD"},
        headers={"idempotency-key": "http-withdrawal-0001"},
    )))
    result = _payload(created)
    assert created.status_code == 201
    assert result["withdrawal"]["withdrawal_id"].startswith("WDR")
    assert result["withdrawal"]["submitted_at"].endswith("+08:00")
    assert set(result["balances"]) == {"withdrawable_minor", "reserved_minor", "debt_minor"}
    assert "user_id" not in json.dumps(result)

    admin1 = auth.register("finance1@example.com", "StrongPass123", "Finance One", True)
    admin2 = auth.register("finance2@example.com", "StrongPass123", "Finance Two", True)
    for admin in (admin1, admin2):
        database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
        database.execute(
            "INSERT OR REPLACE INTO admin_roles(user_id,role,updated_at) VALUES (?,'finance',?)",
            (admin["id"], datetime.now(UTC).isoformat(timespec="seconds")),
        )
    admin1_token = _login(browser_api, "finance1@example.com")
    admin2_token = _login(browser_api, "finance2@example.com")
    withdrawal_id = result["withdrawal"]["withdrawal_id"]
    listed = _payload(asyncio.run(admin_referral_withdrawals(_request(
        "/api/rewrite/v1/admin/referrals/withdrawals", token=admin1_token
    ))))
    assert listed["items"][0]["withdrawal_id"] == withdrawal_id
    assert listed["items"][0]["user_reference"].startswith("USR")
    assert "@example.com" not in listed["items"][0]["user_masked"]

    approved = asyncio.run(admin_referral_withdrawal_review(_request(
        f"/api/rewrite/v1/admin/referrals/withdrawals/{withdrawal_id}/review",
        method="POST", token=admin1_token,
        payload={"decision": "approve", "password": "StrongPass123"},
        headers={"idempotency-key": "http-withdrawal-review-0001"},
        path_params={"withdrawal_id": withdrawal_id},
    )))
    assert _payload(approved)["status"] == "approved"
    paid = asyncio.run(admin_referral_withdrawal_paid(_request(
        f"/api/rewrite/v1/admin/referrals/withdrawals/{withdrawal_id}/paid",
        method="POST", token=admin2_token,
        payload={"password": "StrongPass123", "payout_method": "fps", "payout_reference": "HTTP-PAYOUT-1"},
        headers={"idempotency-key": "http-withdrawal-paid-0001"},
        path_params={"withdrawal_id": withdrawal_id},
    )))
    assert _payload(paid)["status"] == "paid"
    assert "payout_reference" not in json.dumps(_payload(paid))


def test_admin_referral_review_wrong_password_is_forbidden(browser_api):
    database, auth = browser_api["database"], browser_api["auth"]
    admin = auth.register("wrong-password-finance@example.com", "StrongPass123", "Finance", True)
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'finance',?)",
        (admin["id"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    token = _login(browser_api, "wrong-password-finance@example.com")
    with pytest.raises(Exception) as caught:
        asyncio.run(admin_referral_withdrawal_review(_request(
            "/api/rewrite/v1/admin/referrals/withdrawals/WDR000000000000000000000000/review",
            method="POST", token=token,
            payload={"decision": "approve", "password": "WrongPass123"},
            path_params={"withdrawal_id": "WDR000000000000000000000000"},
        )))
    assert getattr(caught.value, "status", None) == 403
