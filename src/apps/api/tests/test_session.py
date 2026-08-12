import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import importlib
import json
from http.cookies import SimpleCookie
import re
import threading
from urllib.parse import urlencode

import pandas as pd
import pytest
from starlette.requests import Request

from core.compat import UTC
from payment.order_service import OrderService
from src.apps.api.app import (
    admin_audit,
    admin_brokers,
    admin_manual_claim_proof,
    admin_manual_claim_review,
    admin_manual_claims,
    admin_overview,
    admin_user_auto_trading,
    admin_users,
    ApiError,
    alert_item,
    alerts,
    REFRESH_COOKIE,
    app,
    bootstrap,
    locale_preference,
    market_candles,
    market_quote,
    market_status,
    market_search,
    opening_pause,
    option_candles,
    options_chain,
    session_login,
    session_logout,
    session_password_reset_confirm,
    session_password_reset_request,
    session_register,
    feedback,
    session_refresh,
    session_verification_request,
    session_verify_email,
    watchlist,
)


def _request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    authorization: str | None = None,
    cookie: str | None = None,
    query: dict[str, str] | None = None,
) -> Request:
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = [(b"content-length", str(len(body)).encode()), (b"user-agent", b"pytest")]
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(query or {}).encode(),
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _payload(response) -> dict:
    return json.loads(response.body.decode())


def _refresh_cookie(response) -> str:
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    return f"{REFRESH_COOKIE}={parsed[REFRESH_COOKIE].value}"


def _login_token() -> str:
    response = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session", method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    return _payload(response)["access_token"]


def _super_admin_token(browser_api) -> str:
    database = browser_api["database"]
    user = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    from core.admin_service import AdminService

    AdminService(database)
    result = browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest-admin"
    )
    return result.access_token


def test_admin_role_migration_is_available_before_first_login(browser_api):
    database = browser_api["database"]
    user = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    database.execute("DELETE FROM admin_roles WHERE user_id=?", (user["id"],))
    database.execute(
        "INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,?,?)",
        (user["id"], "super_admin", datetime.now(UTC).isoformat()),
    )

    result = browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest-first-admin-login"
    )
    identity = app.state.repository.authenticate(result.access_token)
    assert identity.admin_role == "super_admin"


def _captured_auth_token(messages: list[tuple[str, str, str, str]]) -> str:
    text = messages[-1][2]
    return next(line.split("：", 1)[1] for line in text.splitlines() if line.startswith("驗證碼："))


async def _asgi_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    headers: tuple[tuple[str, str], ...] = (),
    query: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict]:
    body = json.dumps(payload).encode() if payload is not None else b""
    request_headers = [
        (b"content-length", str(len(body)).encode()),
        (b"user-agent", b"pytest-asgi"),
    ]
    if payload is not None:
        request_headers.append((b"content-type", b"application/json"))
    request_headers.extend((name.lower().encode(), value.encode()) for name, value in headers)
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": urlencode(query or {}).encode(),
            "headers": request_headers,
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {
        name.decode().lower(): value.decode()
        for name, value in start["headers"]
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, json.loads(response_body.decode())


def _assert_asgi_security_headers(headers: dict[str, str]) -> None:
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_login_uses_http_only_refresh_cookie_and_returns_no_refresh_token(browser_api):
    response = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session",
        method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    payload = _payload(response)

    assert "access_token" in payload
    assert "refresh_token" not in payload
    assert "HttpOnly" in response.headers["set-cookie"]
    assert _refresh_cookie(response).startswith(f"{REFRESH_COOKIE}=")


def test_super_admin_console_is_server_gated_and_projects_safe_fields(browser_api):
    with pytest.raises(ApiError) as unauthenticated:
        asyncio.run(admin_overview(_request("/api/rewrite/v1/admin/overview")))
    assert unauthenticated.value.status == 401

    ordinary_token = _login_token()
    with pytest.raises(ApiError) as forbidden:
        asyncio.run(admin_overview(_request(
            "/api/rewrite/v1/admin/overview",
            authorization=f"Bearer {ordinary_token}",
        )))
    assert forbidden.value.status == 403

    token = _super_admin_token(browser_api)
    authorization = f"Bearer {token}"
    overview = _payload(asyncio.run(admin_overview(_request(
        "/api/rewrite/v1/admin/overview", authorization=authorization,
    ))))
    assert overview["role"] == "super_admin"
    assert overview["official_simulation"]["broker_execution"] is False
    assert overview["shadow_candidates"] == {
        "publication_ceiling": "shadow", "requires_human_review": True,
    }
    users = _payload(asyncio.run(admin_users(_request(
        "/api/rewrite/v1/admin/users", authorization=authorization,
    ))))["items"]
    assert users and set(users[0]) == {
        "id", "email", "display_name", "plan_type", "subscription_expire",
        "last_login", "failed_attempts", "locked_until", "is_active", "is_admin",
        "admin_role", "active_sessions", "active_ips",
    }
    serialized = json.dumps({"overview": overview, "users": users}, ensure_ascii=False)
    assert all(secret not in serialized for secret in (
        "password_hash", "session_token", "refresh_token_hash", "chat_id", "metadata_json",
    ))


def test_super_admin_brokers_and_audit_are_redacted(browser_api, monkeypatch):
    token = _super_admin_token(browser_api)
    authorization = f"Bearer {token}"
    database = browser_api["database"]
    user = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")
    monkeypatch.setenv("TIGER_ACCOUNT", "SECRET-ACCOUNT-1234")
    database.execute(
        """INSERT INTO broker_accounts
           (user_id,provider,account_alias,external_account_id,mode,is_active,status,last_checked,metadata_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (
            user["id"], "Tiger", "primary", "SECRET-ACCOUNT-1234", "live", 1,
            "authorized", datetime.now(UTC).isoformat(),
            json.dumps({"execution_authorized": True, "authorization_verified_at": datetime.now(UTC).isoformat(), "token": "never-return"}),
        ),
    )
    database.execute(
        "INSERT INTO user_action_logs(user_id,action_type,details,created_at) VALUES (?,?,?,?)",
        (user["id"], "ADMIN_UNKNOWN_TEST", json.dumps({"enabled": True, "secret": "never-return"}), datetime.now(UTC).isoformat()),
    )
    brokers = _payload(asyncio.run(admin_brokers(_request(
        "/api/rewrite/v1/admin/brokers", authorization=authorization,
    ))))["items"]
    audit = _payload(asyncio.run(admin_audit(_request(
        "/api/rewrite/v1/admin/audit", authorization=authorization,
    ))))["items"]
    encoded = json.dumps({"brokers": brokers, "audit": audit}, ensure_ascii=False)
    assert brokers[0]["account_masked"].endswith("1234")
    assert brokers[0]["authorized"] is True
    assert "SECRET-ACCOUNT-1234" not in encoded
    assert "never-return" not in encoded
    assert audit[0]["details"] == {}
    assert set(audit[0]) == {
        "id", "created_at", "actor_id", "actor_display", "action_type", "details",
    }


def test_super_admin_auto_trading_requires_password_and_fixed_confirmation(browser_api, monkeypatch):
    token = _super_admin_token(browser_api)
    authorization = f"Bearer {token}"

    def request(payload):
        return _request(
            "/api/rewrite/v1/admin/user-auto-trading", method="PUT",
            payload=payload, authorization=authorization,
        )
    with pytest.raises(ApiError):
        asyncio.run(admin_user_auto_trading(request({
            "enabled": False, "confirmation": "wrong", "password": "StrongPass123",
        })))
    with pytest.raises(ApiError) as bad_password:
        asyncio.run(admin_user_auto_trading(request({
            "enabled": False, "confirmation": "暂停用户实盘服务", "password": "wrong-password",
        })))
    assert bad_password.value.status == 403
    monkeypatch.setattr("notification.telegram_bot.telegram_configured", lambda *_args, **_kwargs: False)
    response = _payload(asyncio.run(admin_user_auto_trading(request({
        "enabled": False, "confirmation": "暂停用户实盘服务", "password": "StrongPass123",
    }))))
    assert response["enabled"] is False
    assert set(response) == {"enabled", "changed", "affected_users", "notified"}


@pytest.mark.parametrize("invalid_enabled", [1, "true", None])
def test_super_admin_auto_trading_rejects_non_boolean_values(browser_api, invalid_enabled):
    token = _super_admin_token(browser_api)
    with pytest.raises(ApiError):
        asyncio.run(admin_user_auto_trading(_request(
            "/api/rewrite/v1/admin/user-auto-trading",
            method="PUT",
            authorization=f"Bearer {token}",
            payload={
                "enabled": invalid_enabled,
                "confirmation": "暂停用户实盘服务",
                "password": "StrongPass123",
            },
        )))


def test_super_admin_writes_recheck_exact_role_inside_transaction(browser_api, monkeypatch):
    _super_admin_token(browser_api)
    database = browser_api["database"]
    admin = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")
    claimant = browser_api["auth"].register(
        "transaction-claimant@example.com", "StrongPass456", "Claimant", True
    )
    database.execute(
        """INSERT INTO subscription_orders
           (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,created_at)
           VALUES (?,?,?,?,?,?,?,'pending',?)""",
        ("TAADMINDOWNGRADE", claimant["id"], "标准版", "monthly", 168, "HKD", "fps", datetime.now(UTC).isoformat()),
    )
    database.execute(
        """INSERT INTO manual_payment_claims
           (order_no,user_id,attempt,status,evidence_file_id,evidence_file_unique_id,
            evidence_message_id,source_update_id,created_at,evidence_source)
           VALUES (?,?,1,'submitted','file-secret','unique-secret','123','update-secret',?,'telegram')""",
        ("TAADMINDOWNGRADE", claimant["id"], datetime.now(UTC).isoformat()),
    )
    from core.admin_service import AdminService

    service = AdminService(database)
    original_require = service._require

    def downgrade_after_entry(actor_id, permission):
        role = original_require(actor_id, permission)
        database.execute("UPDATE admin_roles SET role='finance' WHERE user_id=?", (actor_id,))
        return role

    monkeypatch.setattr(service, "_require", downgrade_after_entry)
    with pytest.raises(PermissionError, match="超级管理员"):
        service.review_manual_payment_claim(
            admin["id"], 1, False, rejection_reason="未核对到账"
        )
    assert database.fetch_one("SELECT status FROM manual_payment_claims WHERE id=1")["status"] == "submitted"

    database.execute("UPDATE admin_roles SET role='finance' WHERE user_id=?", (admin["id"],))
    with pytest.raises(PermissionError, match="超级管理员"):
        service.set_user_auto_trading_enabled(admin["id"], False)
    assert service.control_enabled("user_auto_trading_enabled", False) is False


def test_super_admin_manual_claim_list_and_review_are_redacted_and_reauthenticated(browser_api, monkeypatch):
    token = _super_admin_token(browser_api)
    authorization = f"Bearer {token}"
    database = browser_api["database"]
    claimant = browser_api["auth"].register(
        "claimant@example.com", "StrongPass456", "Claimant", True
    )
    database.execute(
        """INSERT INTO subscription_orders
           (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,created_at)
           VALUES (?,?,?,?,?,?,?,'pending',?)""",
        ("TAADMINCLAIM01", claimant["id"], "标准版", "monthly", 168, "HKD", "fps", datetime.now(UTC).isoformat()),
    )
    database.execute(
        """INSERT INTO manual_payment_claims
           (order_no,user_id,attempt,status,evidence_file_id,evidence_file_unique_id,
            evidence_message_id,source_update_id,created_at,evidence_source)
           VALUES (?,?,1,'submitted','file-secret','unique-secret','123','update-secret',?,'telegram')""",
        ("TAADMINCLAIM01", claimant["id"], datetime.now(UTC).isoformat()),
    )
    claims = _payload(asyncio.run(admin_manual_claims(_request(
        "/api/rewrite/v1/admin/payments/manual-claims", authorization=authorization,
    ))))["items"]
    assert len(claims) == 1
    encoded = json.dumps(claims, ensure_ascii=False)
    assert all(secret not in encoded for secret in (
        "file-secret", "unique-secret", "update-secret", "evidence_storage_key", "evidence_sha256",
    ))
    assert claims[0]["has_evidence"] is True

    path = "/api/rewrite/v1/admin/payments/manual-claims/1/review"
    def review_request(payload):
        request = _request(path, method="POST", authorization=authorization, payload=payload)
        request.scope["path_params"] = {"claim_id": "1"}
        return request

    with pytest.raises(ApiError) as bad_password:
        asyncio.run(admin_manual_claim_review(review_request(
            {"decision": "reject", "password": "wrong-password", "rejection_reason": "未核对到账"}
        )))
    assert bad_password.value.status == 403
    api_module = importlib.import_module("src.apps.api.app")
    def failed_notice(*_args, **_kwargs):
        raise RuntimeError("simulated Telegram outage")

    monkeypatch.setattr(api_module, "queue_manual_payment_review_notice", failed_notice)
    reviewed = _payload(asyncio.run(admin_manual_claim_review(review_request(
        {"decision": "reject", "password": "StrongPass123", "rejection_reason": "未核对到账"}
    ))))
    assert reviewed["status"] == "rejected"
    assert reviewed["notification_queued"] is False
    assert "settlement_reference" not in reviewed
    persisted = database.fetch_one("SELECT status FROM manual_payment_claims WHERE id=1")
    assert persisted["status"] == "rejected"


def test_super_admin_proof_download_fails_closed_on_missing_or_invalid_sha(browser_api):
    token = _super_admin_token(browser_api)
    database = browser_api["database"]
    claimant = browser_api["auth"].register(
        "proof-claimant@example.com", "StrongPass456", "Proof Claimant", True
    )
    database.execute(
        """INSERT INTO subscription_orders
           (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,created_at)
           VALUES (?,?,?,?,?,?,?,'pending',?)""",
        ("TAADMINPROOF01", claimant["id"], "标准版", "monthly", 168, "HKD", "fps", datetime.now(UTC).isoformat()),
    )
    database.execute(
        """INSERT INTO manual_payment_claims
           (order_no,user_id,attempt,status,evidence_storage_key,evidence_sha256,created_at,evidence_source)
           VALUES (?,?,1,'submitted',?,?,?,'web')""",
        ("TAADMINPROOF01", claimant["id"], f"{'0' * 32}.jpg", "0" * 64, datetime.now(UTC).isoformat()),
    )
    request = _request(
        "/api/rewrite/v1/admin/payments/manual-claims/1/proof",
        authorization=f"Bearer {token}",
    )
    request.scope["path_params"] = {"claim_id": "1"}
    with pytest.raises(ApiError) as invalid_proof:
        asyncio.run(admin_manual_claim_proof(request))
    assert invalid_proof.value.status == 409


def test_admin_asgi_errors_keep_private_no_store_security_headers(browser_api):
    ordinary_token = _login_token()
    status, headers, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/admin/overview",
        headers=(("authorization", f"Bearer {ordinary_token}"),),
    ))
    assert status == 403
    assert headers["cache-control"] == "private, no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert payload["code"] == "forbidden"


def test_public_registration_uses_asgi_route_middleware_and_error_mapping(browser_api, monkeypatch):
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    status, headers, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/session/register",
        method="POST",
        payload={
            "email": "asgi-register@example.com",
            "password": "StrongPass123",
            "display_name": "ASGI Register",
            "terms_accepted": True,
        },
    ))

    assert status == 202
    assert payload["accepted"] is True
    _assert_asgi_security_headers(headers)
    invalid_status, invalid_headers, invalid = asyncio.run(_asgi_json(
        "/api/rewrite/v1/session/register", method="POST", payload={}
    ))
    assert invalid_status == 400
    assert invalid["error"] == "注册字段不完整或包含未知字段。"
    assert invalid["code"] == "invalid_request"
    assert re.fullmatch(r"[0-9a-f]{32}", invalid["correlation_id"])
    _assert_asgi_security_headers(invalid_headers)


def test_public_verification_request_uses_asgi_route_and_middleware(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    messages: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(api_module, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        api_module,
        "send_email",
        lambda recipient, subject, text, html: messages.append((recipient, subject, text, html)),
    )

    status, headers, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/session/verification",
        method="POST",
        payload={"email": "missing@example.com"},
    ))

    assert status == 202
    assert payload == {"accepted": True, "message": "如果账户需要验证，邮件已经发送。"}
    assert messages == []
    _assert_asgi_security_headers(headers)


def test_public_email_verification_uses_asgi_route_and_middleware(browser_api, monkeypatch):
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    browser_api["auth"].register(
        "asgi-verify@example.com", "StrongPass123", "ASGI Verify", True
    )
    token = browser_api["auth"].request_email_verification(
        "asgi-verify@example.com", "127.0.0.1"
    )
    assert token is not None

    status, headers, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/session/verify-email", method="POST", payload={"token": token}
    ))

    assert status == 200
    assert payload == {"verified": True}
    _assert_asgi_security_headers(headers)


def test_public_password_reset_request_uses_asgi_route_and_middleware(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    messages: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(api_module, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        api_module,
        "send_email",
        lambda recipient, subject, text, html: messages.append((recipient, subject, text, html)),
    )

    status, headers, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/session/password-reset",
        method="POST",
        payload={"email": "browser@example.com"},
    ))

    assert status == 202
    assert payload == {"accepted": True, "message": "如果账户存在，密码重设邮件已经发送。"}
    assert len(messages) == 1
    _assert_asgi_security_headers(headers)


def test_public_password_reset_confirmation_uses_asgi_route_and_middleware(browser_api):
    token = browser_api["auth"].request_password_reset("browser@example.com", "127.0.0.1")
    assert token is not None

    status, headers, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/session/password-reset/confirm",
        method="POST",
        payload={"token": token, "password": "NewStrongPass456"},
    ))

    assert status == 200
    assert payload == {"reset": True}
    _assert_asgi_security_headers(headers)
    browser_api["auth"].login("browser@example.com", "NewStrongPass456", "127.0.0.1", "pytest")


def test_membership_bootstrap_contract_uses_the_asgi_stack(browser_api):
    browser_api["database"].execute(
        """INSERT INTO manual_payment_receivers
           (method,enabled,receiver_text,version,updated_at)
           VALUES ('fps',1,?,1,?)
           ON CONFLICT(method) DO UPDATE SET enabled=1,
               receiver_text=excluded.receiver_text,version=excluded.version,
               updated_at=excluded.updated_at""",
        ("FPS account for test orders", datetime.now(UTC).isoformat()),
    )
    user = browser_api["database"].fetch_one(
        "SELECT id FROM users WHERE email='browser@example.com'"
    )
    OrderService(browser_api["database"]).create_order(
        user["id"], "标准版", "monthly", "fps", terms_accepted=True,
        idempotency_key="asgi-membership-bootstrap",
    )
    login_status, _, login = asyncio.run(_asgi_json(
        "/api/rewrite/v1/session",
        method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    ))
    assert login_status == 200
    status, headers, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/bootstrap",
        headers=(("authorization", f"Bearer {login['access_token']}"),),
    ))

    assert status == 200
    _assert_asgi_security_headers(headers)
    order = payload["membership"]["orders"][0]
    assert order["can_purchase"] is True
    assert order["purchase_action"] == "upgrade"
    assert order["can_submit_proof"] is True


def test_public_registration_verifies_email_without_disclosing_duplicate_account(
    browser_api, monkeypatch
):
    api_module = importlib.import_module("src.apps.api.app")
    messages: list[tuple[str, str, str, str]] = []
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setattr(api_module, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        api_module,
        "send_email",
        lambda recipient, subject, text, html: messages.append(
            (recipient, subject, text, html)
        ),
    )
    payload = {
        "email": "public-register@example.com",
        "password": "StrongPass123",
        "display_name": "Public Member",
        "terms_accepted": True,
    }

    first = asyncio.run(
        session_register(
            _request("/api/rewrite/v1/session/register", method="POST", payload=payload)
        )
    )
    duplicate = asyncio.run(
        session_register(
            _request("/api/rewrite/v1/session/register", method="POST", payload=payload)
        )
    )

    assert first.status_code == duplicate.status_code == 202
    assert _payload(first) == _payload(duplicate) == {
        "accepted": True,
        "verification_required": True,
        "message": "如果邮箱可用于注册，验证邮件已经发送。",
    }
    assert len(messages) == 1
    with pytest.raises(ApiError, match="完成注册邮箱验证"):
        asyncio.run(
            session_login(
                _request(
                    "/api/rewrite/v1/session",
                    method="POST",
                    payload={
                        "email": payload["email"],
                        "password": payload["password"],
                    },
                )
            )
        )

    verified = asyncio.run(
        session_verify_email(
            _request(
                "/api/rewrite/v1/session/verify-email",
                method="POST",
                payload={"token": _captured_auth_token(messages)},
            )
        )
    )

    assert _payload(verified) == {"verified": True}
    assert "access_token" in _payload(
        asyncio.run(
            session_login(
                _request(
                    "/api/rewrite/v1/session",
                    method="POST",
                    payload={
                        "email": payload["email"],
                        "password": payload["password"],
                    },
                )
            )
        )
    )


def test_public_registration_fails_closed_before_account_creation_without_smtp(
    browser_api, monkeypatch
):
    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setattr(api_module, "smtp_configured", lambda: False)

    with pytest.raises(ApiError) as error:
        asyncio.run(
            session_register(
                _request(
                    "/api/rewrite/v1/session/register",
                    method="POST",
                    payload={
                        "email": "smtp-missing@example.com",
                        "password": "StrongPass123",
                        "display_name": "SMTP Missing",
                        "terms_accepted": True,
                    },
                )
            )
        )

    assert error.value.status == 503
    assert browser_api["database"].fetch_one(
        "SELECT 1 FROM users WHERE email='smtp-missing@example.com'"
    ) is None


def test_feedback_api_is_authenticated_idempotent_and_returns_only_safe_receipts(browser_api):
    token = _login_token()
    payload = {
        "category": "suggestion",
        "message": ("Please add a compact activity digest. " * 8).strip(),
        "context_path": "/dashboard",
        "contact_preference": "none",
    }
    initial = _request(
        "/api/rewrite/v1/feedback", method="POST", payload=payload,
        authorization=f"Bearer {token}",
    )
    initial.scope["headers"].append((b"idempotency-key", b"feedback-replay-01"))
    first = asyncio.run(feedback(initial))
    assert first.status_code == 202
    receipt = _payload(first)
    assert receipt["accepted"] is True and receipt["replayed"] is False
    assert set(receipt["item"]) == {
        "id", "category", "summary", "context_path", "contact_preference", "created_at", "status"
    }
    assert receipt["item"]["id"].startswith("fb_")
    assert "chat" not in json.dumps(receipt)
    assert receipt["item"]["summary"] == payload["message"][:160]
    assert payload["message"] not in json.dumps(receipt)

    repeated = _request(
        "/api/rewrite/v1/feedback", method="POST", payload=payload,
        authorization=f"Bearer {token}",
    )
    repeated.scope["headers"].append((b"idempotency-key", b"feedback-replay-01"))
    replay = asyncio.run(feedback(repeated))
    assert _payload(replay)["replayed"] is True
    listed = asyncio.run(feedback(_request(
        "/api/rewrite/v1/feedback", authorization=f"Bearer {token}",
    )))
    assert _payload(listed)["items"] == [receipt["item"]]


def test_feedback_api_rejects_invalid_fields_conflicting_keys_and_unsafe_paths(browser_api):
    token = _login_token()
    payload = {"category": "bug", "message": "broken", "contact_preference": "email"}
    with pytest.raises(ApiError, match="Idempotency-Key"):
        asyncio.run(feedback(_request(
            "/api/rewrite/v1/feedback", method="POST", payload=payload, authorization=f"Bearer {token}"
        )))
    with pytest.raises(ApiError, match="页面路径"):
        asyncio.run(feedback(_request(
            "/api/rewrite/v1/feedback", method="POST", payload={**payload, "context_path": "/../admin"},
            authorization=f"Bearer {token}",
        )))
    request = _request("/api/rewrite/v1/feedback", method="POST", payload={**payload, "message": "different"}, authorization=f"Bearer {token}")
    request.scope["headers"].append((b"idempotency-key", b"feedback-key-01"))
    # A key conflict is checked after a valid original request with that exact key.
    original = _request("/api/rewrite/v1/feedback", method="POST", payload=payload, authorization=f"Bearer {token}")
    original.scope["headers"].append((b"idempotency-key", b"feedback-key-01"))
    asyncio.run(feedback(original))
    with pytest.raises(ApiError) as error:
        asyncio.run(feedback(request))
    assert error.value.status == 409


def test_feedback_requires_authentication_and_succeeds_without_an_admin_target(browser_api):
    payload = {"category": "other", "message": "No admin target is configured.", "contact_preference": "none"}
    unauthenticated = _request("/api/rewrite/v1/feedback", method="POST", payload=payload)
    unauthenticated.scope["headers"].append((b"idempotency-key", b"feedback-auth-01"))
    with pytest.raises(ApiError) as error:
        asyncio.run(feedback(unauthenticated))
    assert error.value.status == 401
    token = _login_token()
    request = _request("/api/rewrite/v1/feedback", method="POST", payload=payload, authorization=f"Bearer {token}")
    request.scope["headers"].append((b"idempotency-key", b"feedback-no-admin"))
    assert asyncio.run(feedback(request)).status_code == 202
    assert browser_api["database"].fetch_one("SELECT 1 FROM telegram_service_outbox") is None


def test_feedback_outbox_is_committed_with_feedback_and_safely_escapes_notice(browser_api):
    database = browser_api["database"]
    database.execute("UPDATE users SET is_admin=1 WHERE email='browser@example.com'")
    now = datetime.now(UTC).isoformat()
    database.execute(
        "INSERT INTO telegram_accounts(user_id,chat_id,is_active,created_at,updated_at) VALUES (?,?,1,?,?)",
        (1, "10001", now, now),
    )
    from core.admin_service import AdminService
    AdminService(database).set_role(1, 1, "super_admin")
    token = _login_token()
    request = _request("/api/rewrite/v1/feedback", method="POST", authorization=f"Bearer {token}", payload={
        "category": "experience", "message": "<b>unsafe</b> & untrusted", "contact_preference": "telegram",
    })
    request.scope["headers"].append((b"idempotency-key", b"feedback-key-02"))
    response = asyncio.run(feedback(request))
    assert response.status_code == 202
    assert database.fetch_one("SELECT 1 FROM user_feedback WHERE user_id=1") is not None
    notice = database.fetch_one("SELECT message FROM telegram_service_outbox WHERE dedupe_key LIKE 'feedback:%'")
    assert notice and "&lt;b&gt;unsafe&lt;/b&gt; &amp; untrusted" in notice["message"]


def test_feedback_rate_limit_deduplication_and_concurrent_submission(browser_api):
    token = _login_token()
    headers = f"Bearer {token}"
    def submit(index: int):
        request = _request("/api/rewrite/v1/feedback", method="POST", authorization=headers, payload={
            "category": "data", "message": f"feedback item {index}", "contact_preference": "none",
        })
        request.scope["headers"].append((b"idempotency-key", f"feedback-burst-{index:02d}".encode()))
        try:
            return asyncio.run(feedback(request)).status_code
        except ApiError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(submit, range(4)))
    assert statuses.count(202) == 3 and statuses.count(429) == 1


def test_feedback_identical_content_with_a_new_key_replays_within_24_hours(browser_api):
    token = _login_token()
    payload = {"category": "other", "message": "Same message for deduplication.", "contact_preference": "none"}
    first = _request("/api/rewrite/v1/feedback", method="POST", payload=payload, authorization=f"Bearer {token}")
    first.scope["headers"].append((b"idempotency-key", b"feedback-day-one"))
    initial = _payload(asyncio.run(feedback(first)))
    repeated = _request("/api/rewrite/v1/feedback", method="POST", payload=payload, authorization=f"Bearer {token}")
    repeated.scope["headers"].append((b"idempotency-key", b"feedback-day-two"))
    replay = _payload(asyncio.run(feedback(repeated)))
    assert replay["replayed"] is True and replay["item"]["id"] == initial["item"]["id"]


def test_verification_resend_and_password_reset_are_generic_and_rate_limited_by_auth(
    browser_api, monkeypatch
):
    api_module = importlib.import_module("src.apps.api.app")
    messages: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(api_module, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        api_module,
        "send_email",
        lambda recipient, subject, text, html: messages.append(
            (recipient, subject, text, html)
        ),
    )

    resend = asyncio.run(
        session_verification_request(
            _request(
                "/api/rewrite/v1/session/verification",
                method="POST",
                payload={"email": "missing@example.com"},
            )
        )
    )
    assert resend.status_code == 202
    assert messages == []

    requested = asyncio.run(
        session_password_reset_request(
            _request(
                "/api/rewrite/v1/session/password-reset",
                method="POST",
                payload={"email": "browser@example.com"},
            )
        )
    )
    assert requested.status_code == 202
    reset_token = _captured_auth_token(messages)
    confirmed = asyncio.run(
        session_password_reset_confirm(
            _request(
                "/api/rewrite/v1/session/password-reset/confirm",
                method="POST",
                payload={"token": reset_token, "password": "NewStrongPass456"},
            )
        )
    )
    assert _payload(confirmed) == {"reset": True}
    with pytest.raises(ApiError, match="邮箱或密码不正确"):
        asyncio.run(
            session_login(
                _request(
                    "/api/rewrite/v1/session",
                    method="POST",
                    payload={
                        "email": "browser@example.com",
                        "password": "StrongPass123",
                    },
                )
            )
        )
    assert "access_token" in _payload(
        asyncio.run(
            session_login(
                _request(
                    "/api/rewrite/v1/session",
                    method="POST",
                    payload={
                        "email": "browser@example.com",
                        "password": "NewStrongPass456",
                    },
                )
            )
        )
    )


def test_login_refresh_bootstrap_and_logout_flow(browser_api):
    login = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session", method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    login_payload = _payload(login)
    cookie = _refresh_cookie(login)
    read_response = asyncio.run(bootstrap(_request(
        "/api/rewrite/v1/bootstrap",
        authorization=f"Bearer {login_payload['access_token']}",
    )))
    refresh = asyncio.run(session_refresh(_request(
        "/api/rewrite/v1/session/refresh", method="POST", cookie=cookie,
    )))
    refreshed_access = _payload(refresh)["access_token"]
    logout = asyncio.run(session_logout(_request(
        "/api/rewrite/v1/session", method="DELETE",
        authorization=f"Bearer {refreshed_access}", cookie=_refresh_cookie(refresh),
    )))

    assert _payload(read_response)["me"]["display_name"] == "Browser Reader"
    assert _payload(logout)["status"] == "logged_out"
    assert f"{REFRESH_COOKIE}=\"\"" in logout.headers["set-cookie"]


def test_opening_pause_requires_reauthentication_exact_body_and_is_idempotent(browser_api):
    database = browser_api["database"]
    user_id = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")["id"]
    login = browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    )
    authorization = f"Bearer {login.access_token}"
    database.execute("UPDATE user_controls SET opening_paused=1 WHERE user_id=?", (user_id,))
    database.execute(
        """INSERT INTO platform_controls(control_key,control_value,updated_at)
           VALUES ('opening_paused','1',datetime('now'))
           ON CONFLICT(control_key) DO UPDATE SET control_value='1',updated_at=datetime('now')"""
    )
    before_user = database.fetch_one(
        "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
    )["opening_paused"]
    before_platform = database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )["control_value"]
    valid = {"paused": False, "confirmation": "恢复新开仓", "password": "StrongPass123"}

    with pytest.raises(ApiError) as anonymous:
        asyncio.run(opening_pause(_request("/api/rewrite/v1/settings/opening-pause", method="PUT", payload=valid)))
    assert anonymous.value.status == 401

    with pytest.raises(ApiError) as refresh:
        asyncio.run(opening_pause(_request(
            "/api/rewrite/v1/settings/opening-pause", method="PUT", payload=valid,
            authorization=f"Bearer {login.refresh_token}",
        )))
    assert refresh.value.status == 401

    invalid_payloads = (
        {},
        {"paused": False, "confirmation": "恢复新开仓"},
        {"paused": False, "password": "StrongPass123"},
        {"confirmation": "恢复新开仓", "password": "StrongPass123"},
        {**valid, "unexpected": True},
        {**valid, "paused": True},
        {**valid, "paused": 0},
        {**valid, "confirmation": ["恢复新开仓"]},
        {**valid, "confirmation": "恢复"},
        {**valid, "password": 123},
    )
    for payload in invalid_payloads:
        with pytest.raises(ApiError) as invalid:
            asyncio.run(opening_pause(_request(
                "/api/rewrite/v1/settings/opening-pause", method="PUT", payload=payload,
                authorization=authorization,
            )))
        assert invalid.value.status == 400
    assert database.fetch_one(
        "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
    )["opening_paused"] == before_user
    assert database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )["control_value"] == before_platform

    with pytest.raises(ApiError) as incorrect_password:
        asyncio.run(opening_pause(_request(
            "/api/rewrite/v1/settings/opening-pause", method="PUT", payload={**valid, "password": "wrong"},
            authorization=authorization,
        )))
    assert incorrect_password.value.status == 403
    assert database.fetch_one(
        "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
    )["opening_paused"] == 1

    restored = asyncio.run(opening_pause(_request(
        "/api/rewrite/v1/settings/opening-pause", method="PUT", payload=valid, authorization=authorization,
    )))
    repeated = asyncio.run(opening_pause(_request(
        "/api/rewrite/v1/settings/opening-pause", method="PUT", payload=valid, authorization=authorization,
    )))

    assert _payload(restored)["resumed"] is True
    assert _payload(repeated)["resumed"] is False
    assert database.fetch_one(
        "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
    )["opening_paused"] == 0
    assert database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )["control_value"] == "1"
    assert database.fetch_one(
        "SELECT COUNT(*) count FROM user_action_logs WHERE user_id=? AND action_type='USER_RESUME_OPENING'",
        (user_id,),
    )["count"] == 1


def test_bootstrap_execution_accounts_are_user_scoped_redacted_and_display_only(browser_api, monkeypatch):
    database = browser_api["database"]
    user_id = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")["id"]
    expected_account = "tiger-live-account"
    monkeypatch.setenv("TIGER_ACCOUNT", expected_account)
    database.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire='2099-01-01T00:00:00+00:00' WHERE id=?",
        (user_id,),
    )
    database.execute("UPDATE user_controls SET opening_paused=0 WHERE user_id=?", (user_id,))
    database.execute(
        """INSERT INTO platform_controls(control_key,control_value,updated_at)
           VALUES ('opening_paused','0',datetime('now'))
           ON CONFLICT(control_key) DO UPDATE SET control_value='0',updated_at=datetime('now')"""
    )
    database.execute(
        """INSERT INTO platform_controls(control_key,control_value,updated_at)
           VALUES ('user_auto_trading_enabled','1',datetime('now'))
           ON CONFLICT(control_key) DO UPDATE SET control_value='1',updated_at=datetime('now')"""
    )
    now = datetime.now(UTC)
    fresh_verified_at = now.isoformat()
    stale_checked_at = (now - timedelta(hours=1)).isoformat()
    fresh_checked_at = now.isoformat()

    def authorization_metadata(verified_at: str | None) -> str:
        payload = {"credentials": {"token": "never-return"}, "secret": "never-return"}
        if verified_at is not None:
            payload.update({"execution_authorized": True, "authorization_verified_at": verified_at})
        return json.dumps(payload)

    def add_account(account_user_id: int, alias: str) -> int:
        database.execute(
            """INSERT INTO broker_accounts(
                   user_id,provider,account_alias,external_account_id,mode,is_active,status,last_checked,metadata_json,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                account_user_id, "Tiger", alias, expected_account, "live", 1, "authorized",
                fresh_checked_at, authorization_metadata(fresh_verified_at),
            ),
        )
        return int(database.fetch_one(
            "SELECT id FROM broker_accounts WHERE user_id=? AND account_alias=?",
            (account_user_id, alias),
        )["id"])

    account_id = add_account(user_id, "current-tiger")
    other = browser_api["auth"].register("other@example.com", "StrongPass123", "Other", True)
    assert other is not None
    add_account(other["id"], "other-live")
    before_user_pause = database.fetch_one(
        "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
    )["opening_paused"]
    before_platform_pause = database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )["control_value"]

    primary_login = browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    )
    authorization = f"Bearer {primary_login.access_token}"

    def read_bootstrap():
        return asyncio.run(bootstrap(_request("/api/rewrite/v1/bootstrap", authorization=authorization)))

    primary_response = read_bootstrap()
    primary = _payload(primary_response)
    accounts = primary["execution_control"]["accounts"]
    by_alias = {account["alias"]: account for account in accounts}

    assert primary_response.status_code == 200
    assert set(by_alias) == {"current-tiger"}
    assert by_alias["current-tiger"]["authorized"] is True
    assert primary["execution_control"]["has_authorized_broker_account"] is True
    assert primary["execution_control"]["can_increase_exposure"] is True
    assert primary["membership"]["brokerage"]["accounts"] == accounts
    allowed_account_fields = {"id", "provider", "alias", "mode", "status", "authorized", "active", "last_checked"}
    assert all(set(account) == allowed_account_fields for account in accounts)
    serialized = json.dumps(primary, ensure_ascii=False).casefold()
    assert all(value not in serialized for value in ("external_account_id", "metadata_json", "credentials", "token", "secret"))

    invalid_rows = (
        ("Tiger", expected_account, "live", 1, "connected", fresh_checked_at, authorization_metadata(fresh_verified_at)),
        ("Tiger", expected_account, "live", 1, "ready", fresh_checked_at, authorization_metadata(fresh_verified_at)),
        ("Tiger", expected_account, "paper", 1, "authorized", fresh_checked_at, authorization_metadata(fresh_verified_at)),
        ("Tiger", expected_account, "live", 0, "authorized", fresh_checked_at, authorization_metadata(fresh_verified_at)),
        (
            "Tiger", expected_account, "live", 1, "authorized", stale_checked_at,
            authorization_metadata((now - timedelta(hours=1)).isoformat()),
        ),
        (
            "Tiger", expected_account, "live", 1, "authorized", fresh_checked_at,
            authorization_metadata((now + timedelta(minutes=1)).isoformat()),
        ),
        ("Tiger", expected_account, "live", 1, "authorized", fresh_checked_at, authorization_metadata(None)),
        ("Tiger", "different-account", "live", 1, "authorized", fresh_checked_at, authorization_metadata(fresh_verified_at)),
        ("OtherBroker", expected_account, "live", 1, "authorized", fresh_checked_at, authorization_metadata(fresh_verified_at)),
    )
    for provider, external_id, mode, active, status, last_checked, metadata in invalid_rows:
        database.execute(
            """UPDATE broker_accounts SET provider=?,external_account_id=?,mode=?,is_active=?,status=?,
                      last_checked=?,metadata_json=? WHERE id=?""",
            (provider, external_id, mode, active, status, last_checked, metadata, account_id),
        )
        invalid = _payload(read_bootstrap())["execution_control"]
        assert invalid["accounts"][0]["authorized"] is False
        assert invalid["has_authorized_broker_account"] is False
        assert invalid["can_increase_exposure"] is False
        assert invalid["accounts"][0]["last_checked"] == last_checked

    assert database.fetch_one(
        "SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)
    )["opening_paused"] == before_user_pause
    assert database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )["control_value"] == before_platform_pause

    other_login = browser_api["auth"].login("other@example.com", "StrongPass123", "127.0.0.1", "pytest")
    other_response = asyncio.run(bootstrap(_request(
        "/api/rewrite/v1/bootstrap", authorization=f"Bearer {other_login.access_token}",
    )))
    other_execution = _payload(other_response)["execution_control"]
    assert [account["alias"] for account in other_execution["accounts"]] == ["other-live"]
    assert other_execution["accounts"][0]["authorized"] is True


def test_missing_refresh_cookie_is_anonymous_not_an_error(browser_api):
    response = asyncio.run(session_refresh(_request(
        "/api/rewrite/v1/session/refresh", method="POST",
    )))

    assert response.status_code == 200
    assert _payload(response) == {"authenticated": False}


def test_login_rejects_unknown_fields_and_bad_password(browser_api):
    with pytest.raises(ApiError, match="未知字段"):
        asyncio.run(session_login(_request(
            "/api/rewrite/v1/session", method="POST",
            payload={"email": "browser@example.com", "password": "StrongPass123", "admin": True},
        )))
    with pytest.raises(ApiError) as error:
        asyncio.run(session_login(_request(
            "/api/rewrite/v1/session", method="POST",
            payload={"email": "browser@example.com", "password": "incorrect123"},
        )))
    assert error.value.status == 401


def test_authenticated_market_candles_use_bounded_read_adapter(browser_api, monkeypatch):
    login = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session", method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    access_token = _payload(login)["access_token"]

    class StubSource:
        def bars(self, symbol, period, interval):
            if (symbol, period, interval) == ("AAPL", "5d", "1m"):
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            assert (symbol, period, interval) == ("AAPL", "6mo", "1d")
            return pd.DataFrame(
                [{"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1_000}],
                index=pd.to_datetime(["2026-08-08T00:00:00Z"]),
            )

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda: StubSource())
    monkeypatch.setattr(api_module, "public_market_status", lambda **_: {
        "display_source": "测试行情", "is_realtime": False,
        "freshness": "历史行情", "detail": "测试",
    })
    response = asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles",
        authorization=f"Bearer {access_token}",
        query={"symbol": "AAPL", "timeframe": "日线"},
    )))
    payload = _payload(response)

    assert payload["items"][0]["close"] == 101.0
    assert payload["status"]["delivery_delay_minutes"] == 15
    assert payload["status"]["is_realtime"] is False


def test_public_market_error_redacts_provider_details_and_returns_correlation_id(
    browser_api, monkeypatch
):
    from data.datasource import DataSourceError

    api_module = importlib.import_module("src.apps.api.app")
    leaked = r"C:\secret\token.db upstream_response={'access_token':'do-not-leak'} SQL SELECT * FROM users"

    def fail_market_data(*_args, **_kwargs):
        raise DataSourceError(leaked)

    monkeypatch.setattr(api_module, "_market_bars_with_fallback", fail_market_data)
    access_token = _login_token()

    status, _, payload = asyncio.run(_asgi_json(
        "/api/rewrite/v1/market/candles",
        headers=(("authorization", f"Bearer {access_token}"),),
        query={"symbol": "AAPL", "timeframe": "日线"},
    ))

    assert status == 503
    assert payload["code"] == "market_data_unavailable"
    assert payload["error"] == "行情服务暂时不可用，请稍后重试。"
    assert re.fullmatch(r"[0-9a-f]{32}", payload["correlation_id"])
    encoded = json.dumps(payload, ensure_ascii=False)
    assert not any(secret in encoded for secret in (
        "C:\\secret", "access_token", "SELECT * FROM users", "upstream_response",
    ))


def test_authenticated_market_candles_resample_real_hour_bars(browser_api, monkeypatch):
    access_token = _login_token()

    class StubSource:
        def bars(self, symbol, period, interval):
            assert (symbol, period, interval) == ("AAPL", "3mo", "60m")
            return pd.DataFrame(
                [
                    {"Open": 100, "High": 103, "Low": 99, "Close": 102, "Volume": 100},
                    {"Open": 102, "High": 106, "Low": 101, "Close": 105, "Volume": 120},
                    {"Open": 105, "High": 108, "Low": 104, "Close": 107, "Volume": 140},
                ],
                index=pd.to_datetime([
                    "2026-08-08T00:00:00Z",
                    "2026-08-08T01:00:00Z",
                    "2026-08-08T02:00:00Z",
                ]),
            )

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda: StubSource())
    monkeypatch.setattr(api_module, "public_market_status", lambda **_: {
        "display_source": "测试行情", "is_realtime": False,
        "freshness": "历史行情", "detail": "测试",
    })
    response = asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles",
        authorization=f"Bearer {access_token}",
        query={"symbol": "AAPL", "timeframe": "3小时"},
    )))
    payload = _payload(response)

    assert payload["timeframe"] == "3小时"
    assert len(payload["items"]) == 1
    assert payload["items"][0] == {
        "time": 1786147200,
        "open": 100.0,
        "high": 108.0,
        "low": 99.0,
        "close": 107.0,
        "volume": 360.0,
    }


@pytest.mark.parametrize(
    ("timeframe", "period", "interval", "safe_start"),
    (
        ("5分", "5d", "5m", "17:40:00"),
        ("10分", "1mo", "5m", "17:40:00"),
        ("15分", "1mo", "15m", "17:30:00"),
        ("30分", "1mo", "30m", "17:15:00"),
        ("1小时", "3mo", "60m", "16:45:00"),
    ),
)
def test_delayed_resampling_clips_native_bars_by_end_before_aggregation(
    browser_api, monkeypatch, timeframe, period, interval, safe_start
):
    api_module = importlib.import_module("src.apps.api.app")
    cutoff = datetime(2026, 8, 12, 17, 45, tzinfo=UTC)

    class NativeBars:
        name = "Research Feed"
        supports_realtime = False
        delay_minutes = None

        def bars(self, symbol, requested_period, requested_interval):
            assert (symbol, requested_period, requested_interval) == ("AAPL", period, interval)
            return pd.DataFrame(
                [
                    {"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 10},
                    {"Open": 101, "High": 999, "Low": 98, "Close": 999, "Volume": 20},
                ],
                index=pd.to_datetime([
                    f"2026-08-12T{safe_start}Z", "2026-08-12T17:45:00Z",
                ]),
            )

    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_args: NativeBars())
    monkeypatch.setattr(api_module, "_visible_as_of", lambda _delay, _now=None: cutoff)
    payload = _payload(asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL", "timeframe": timeframe},
    ))))

    assert len(payload["items"]) == 1
    assert payload["items"][0]["high"] == 102.0
    assert payload["items"][0]["close"] == 101.0


def test_market_candles_a_share_prefers_akshare_and_reports_actual_source(browser_api, monkeypatch):
    access_token = _login_token()
    calls: list[str | None] = []

    class AKShareSource:
        name = "AKShare"

        def bars(self, symbol, period, interval):
            if (symbol, period, interval) == ("600519", "5d", "1m"):
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            assert (symbol, period, interval) == ("600519", "6mo", "1d")
            return pd.DataFrame(
                [{"Open": 1500, "High": 1510, "Low": 1490, "Close": 1505, "Volume": 20}],
                index=pd.to_datetime(["2026-08-08T00:00:00Z"]),
            )

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(
        api_module, "get_resilient_data_source", lambda name=None: calls.append(name) or AKShareSource()
    )
    monkeypatch.setattr(
        api_module, "public_market_status",
        lambda **context: {"source": context["source"].name, "fallback_from": context["fallback_from"]},
    )
    response = asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles", authorization=f"Bearer {access_token}",
        query={"symbol": "600519", "timeframe": "日线"},
    )))

    payload = _payload(response)
    assert calls == ["akshare"]
    assert payload["items"][0]["close"] == 1505.0
    assert payload["status"]["display_source"] == "真实数据来源"
    assert "source" not in payload["status"] and "fallback_from" not in payload["status"]
    assert payload["status"]["delivery_delay_minutes"] == 15


def test_market_candles_opend_failure_falls_back_to_yahoo_with_actual_status(browser_api, monkeypatch):
    access_token = _login_token()
    calls: list[str | None] = []

    class OpenDSource:
        name = "Futu OpenD"

        def bars(self, *_args):
            from data.datasource import DataSourceError
            raise DataSourceError("OpenD 无权限读取 K 线")

    class YahooSource:
        name = "Yahoo Finance"

        def bars(self, symbol, period, interval):
            if (symbol, period, interval) == ("AAPL", "5d", "1m"):
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            assert (symbol, period, interval) == ("AAPL", "6mo", "1d")
            return pd.DataFrame(
                [{"Open": 200, "High": 203, "Low": 199, "Close": 202, "Volume": 50}],
                index=pd.to_datetime(["2026-08-08T00:00:00Z"]),
            )

    sources = iter([OpenDSource(), YahooSource()])
    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(
        api_module, "get_resilient_data_source", lambda name=None: calls.append(name) or next(sources)
    )
    monkeypatch.setattr(
        api_module, "public_market_status",
        lambda **context: {"source": context["source"].name, "fallback_from": context["fallback_from"]},
    )
    response = asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles", authorization=f"Bearer {access_token}",
        query={"symbol": "AAPL", "timeframe": "日线"},
    )))

    payload = _payload(response)
    assert calls == [None, "yfinance"]
    assert payload["status"]["display_source"] == "真实数据来源"
    assert "source" not in payload["status"] and "fallback_from" not in payload["status"]
    assert payload["status"]["delivery_delay_minutes"] == 15


def test_authenticated_market_quote_marks_unverified_opend_snapshot_non_actionable(browser_api, monkeypatch):
    received = []

    class StubOpenD:
        def stock_quote(self, symbol):
            received.append(symbol)
            return {
                "symbol": symbol, "last": 211.5, "bid": 211.4, "ask": 211.6, "spread": 0.2,
                "open": 208.0, "high": 212.0, "low": 207.5, "prev_close": 209.0,
                "volume": 1_234_567, "quote_at": "2026-08-11 15:59:59", "source": "OpenD",
            }

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", StubOpenD)
    browser_api["database"].execute(
        "UPDATE users SET plan_type='高级版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    response = asyncio.run(market_quote(_request(
        "/api/rewrite/v1/market/quote", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL"},
    )))

    assert response.status_code == 200 and received == ["AAPL"]
    payload = _payload(response)
    assert payload["source"] == "真实数据来源" and payload["last"] == 211.5
    assert payload["bid"] == 211.4 and payload["ask"] == 211.6
    assert payload["status"] == "available" and payload["request_succeeded"] is True
    assert payload["is_realtime"] is False and payload["actionable_quote"] is False
    assert payload["verification"] == "realtime_unverified"
    assert "未验证" in payload["freshness"]


def test_authenticated_market_quote_accepts_verified_opend_realtime_right(browser_api, monkeypatch):
    class VerifiedOpenD:
        def stock_quote(self, symbol):
            quote_at = datetime.now(UTC).isoformat()
            return {
                "symbol": symbol, "last": 211.5, "bid": 211.4, "ask": 211.6, "spread": 0.2,
                "open": 208.0, "high": 212.0, "low": 207.5, "prev_close": 209.0,
                "volume": 1_234_567, "quote_at": quote_at, "source": "OpenD",
                "us_qot_right": "LV2", "us_option_qot_right": "LV1",
                "us_realtime_entitlement": True, "us_option_realtime_entitlement": True,
                "actionable_snapshot": True,
            }

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", VerifiedOpenD)
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    browser_api["database"].execute(
        "UPDATE users SET plan_type='高级版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    response = asyncio.run(market_quote(_request(
        "/api/rewrite/v1/market/quote", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL"},
    )))

    payload = _payload(response)
    assert response.status_code == 200
    assert payload["source"] == "真实数据来源"
    assert set(payload).isdisjoint({"fallback_from", "qot_right", "provider_name"})
    assert payload["is_realtime"] is True and payload["actionable_quote"] is True
    assert payload["verification"] == "realtime_verified"
    assert payload["freshness"] == "实时权限已验证"


def test_authenticated_market_quote_never_labels_a_stale_entitled_snapshot_realtime(
    browser_api, monkeypatch
):
    class StaleEntitledOpenD:
        def stock_quote(self, symbol):
            return {
                "symbol": symbol, "last": 211.5, "bid": 211.4, "ask": 211.6, "spread": 0.2,
                "open": 208.0, "high": 212.0, "low": 207.5, "prev_close": 209.0,
                "volume": 1_234_567, "quote_at": "2020-01-01T00:00:00+00:00", "source": "OpenD",
                "us_qot_right": "LV2", "us_realtime_entitlement": True,
                "actionable_snapshot": True,
            }

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", StaleEntitledOpenD)
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    browser_api["database"].execute(
        "UPDATE users SET plan_type='高级版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    payload = _payload(asyncio.run(market_quote(_request(
        "/api/rewrite/v1/market/quote", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL"},
    ))))

    assert payload["is_realtime"] is False
    assert payload["actionable_quote"] is False
    assert "不新鲜" in payload["freshness"]


def test_free_market_quote_is_built_from_bars_before_the_website_delay_cutoff(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    cutoff = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)

    class UnexpectedOpenD:
        def stock_quote(self, _symbol):
            raise AssertionError("free users must never receive a current OpenD snapshot")

    class MinuteBars:
        name = "Research Feed"
        supports_realtime = False
        delay_minutes = None

        def bars(self, symbol, period, interval):
            assert (symbol, period, interval) == ("AAPL", "5d", "1m")
            return pd.DataFrame(
                [
                    {"Open": 100, "High": 101, "Low": 99, "Close": 100.5, "Volume": 10},
                    {"Open": 200, "High": 201, "Low": 199, "Close": 200.5, "Volume": 20},
                ],
                index=pd.to_datetime([cutoff - timedelta(minutes=1), cutoff + timedelta(minutes=1)]),
            )

    monkeypatch.setattr(api_module, "OpenDAdapter", UnexpectedOpenD)
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_args: MinuteBars())
    def member_cutoff(delay, _now=None):
        assert delay == 15
        return cutoff

    monkeypatch.setattr(api_module, "_visible_as_of", member_cutoff)
    response = asyncio.run(market_quote(_request(
        "/api/rewrite/v1/market/quote", authorization=f"Bearer {_login_token()}", query={"symbol": "AAPL"},
    )))

    payload = _payload(response)
    assert response.status_code == 200
    assert payload["delivery_delay_minutes"] == 15
    assert payload["last"] == 100.5
    assert payload["bid"] is payload["ask"] is payload["spread"] is None
    assert payload["actionable_quote"] is False and payload["is_realtime"] is False
    assert payload["visible_as_of"] == cutoff.isoformat()


def test_delayed_daily_candles_include_the_cutoff_capped_current_day(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    cutoff = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)

    class DailyBars:
        name = "Research Feed"
        supports_realtime = False
        delay_minutes = None

        def bars(self, symbol, period, interval):
            if (symbol, period, interval) == ("AAPL", "5d", "1m"):
                return pd.DataFrame(
                    [
                        {"Open": 205, "High": 210, "Low": 204, "Close": 209, "Volume": 30},
                        {"Open": 211, "High": 212, "Low": 208, "Close": 211, "Volume": 40},
                    ],
                    index=pd.to_datetime(["2026-08-12T17:44:00Z", "2026-08-12T18:01:00Z"]),
                )
            assert (symbol, period, interval) == ("AAPL", "6mo", "1d")
            return pd.DataFrame(
                [
                    {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 10},
                    {"Open": 200, "High": 201, "Low": 199, "Close": 200, "Volume": 20},
                ],
                index=pd.to_datetime(["2026-08-11T00:00:00-04:00", "2026-08-12T00:00:00-04:00"]),
            )

    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_args: DailyBars())
    monkeypatch.setattr(api_module, "_visible_as_of", lambda _delay, _now=None: cutoff)
    response = asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL", "timeframe": "日线"},
    )))

    payload = _payload(response)
    assert [item["close"] for item in payload["items"]] == [100.0, 209.0]
    assert payload["status"]["delivery_delay_minutes"] == 15
    assert payload["status"]["observed_at"] == "2026-08-12T17:44:00+00:00"


@pytest.mark.parametrize(("timeframe", "period"), (("周线", "2y"), ("月线", "5y")))
def test_delayed_coarse_candles_include_the_cutoff_capped_current_period(
    browser_api, monkeypatch, timeframe, period
):
    api_module = importlib.import_module("src.apps.api.app")
    cutoff = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)

    class DailyBars:
        name = "Research Feed"
        supports_realtime = False
        delay_minutes = None

        def bars(self, symbol, requested_period, interval):
            if (symbol, requested_period, interval) == ("AAPL", "5d", "1m"):
                return pd.DataFrame(
                    [{"Open": 205, "High": 210, "Low": 204, "Close": 209, "Volume": 30}],
                    index=pd.to_datetime(["2026-08-12T17:44:00Z"]),
                )
            assert (symbol, requested_period, interval) == ("AAPL", period, "1d")
            return pd.DataFrame(
                [
                    {"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 10},
                    {"Open": 200, "High": 201, "Low": 199, "Close": 200, "Volume": 20},
                ],
                index=pd.to_datetime(["2026-08-03T00:00:00-04:00", "2026-08-12T00:00:00-04:00"]),
            )

    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_args: DailyBars())
    monkeypatch.setattr(api_module, "_visible_as_of", lambda _delay, _now=None: cutoff)
    payload = _payload(asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL", "timeframe": timeframe},
    ))))

    assert payload["items"][-1]["close"] == 209.0
    assert payload["status"]["delivery_delay_minutes"] == 15


@pytest.mark.parametrize(("timeframe", "period"), (("日线", "6mo"), ("周线", "2y"), ("月线", "5y")))
def test_a_share_delayed_coarse_candles_include_cutoff_capped_current_period(
    browser_api, monkeypatch, timeframe, period
):
    api_module = importlib.import_module("src.apps.api.app")
    cutoff = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)

    class AKShareBars:
        name = "Research Feed"
        supports_realtime = False
        delay_minutes = None

        def bars(self, symbol, requested_period, interval):
            if (symbol, requested_period, interval) == ("600519", "5d", "1m"):
                return pd.DataFrame(
                    [
                        {"Open": 1505, "High": 1510, "Low": 1500, "Close": 1509, "Volume": 30},
                        {"Open": 1510, "High": 9999, "Low": 1490, "Close": 9999, "Volume": 40},
                    ],
                    index=pd.to_datetime(["2026-08-12 13:44:00", "2026-08-12 14:01:00"]),
                )
            assert (symbol, requested_period, interval) == ("600519", period, "1d")
            return pd.DataFrame(
                [
                    {"Open": 1400, "High": 1410, "Low": 1390, "Close": 1405, "Volume": 10},
                    {"Open": 1500, "High": 9999, "Low": 1490, "Close": 9999, "Volume": 20},
                ],
                index=pd.to_datetime(["2026-08-03", "2026-08-12"]),
            )

    calls = []
    monkeypatch.setattr(
        api_module, "get_resilient_data_source", lambda name=None: calls.append(name) or AKShareBars()
    )
    monkeypatch.setattr(api_module, "_visible_as_of", lambda _delay, _now=None: cutoff)
    payload = _payload(asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles", authorization=f"Bearer {_login_token()}",
        query={"symbol": "600519", "timeframe": timeframe},
    ))))

    assert calls == ["akshare"]
    assert payload["items"][-1]["close"] == 1509.0
    assert payload["items"][-1]["high"] < 9999


def test_market_status_is_vendor_neutral_cached_and_applies_the_member_boundary(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    calls = {"available": 0, "rights": 0}

    class VerifiedOpenD:
        def available(self):
            calls["available"] += 1
            return True

        def quote_rights(self):
            calls["rights"] += 1
            return {
                "us_realtime_entitlement": True,
                "us_option_realtime_entitlement": True,
            }

    monkeypatch.setattr(api_module, "OpenDAdapter", VerifiedOpenD)
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    monkeypatch.setattr(api_module, "_MARKET_STATUS_CACHE", None)
    free = _payload(asyncio.run(market_status(_request(
        "/api/rewrite/v1/market/status", authorization=f"Bearer {_login_token()}"
    ))))
    assert free["delivery_delay_minutes"] == 15 and free["is_realtime"] is False
    assert "source" not in free and "Futu" not in json.dumps(free, ensure_ascii=False)
    assert set(free).isdisjoint({"qot_right", "provider_name", "fallback_from"})

    browser_api["database"].execute(
        "UPDATE users SET plan_type='高级版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    advanced = _payload(asyncio.run(market_status(_request(
        "/api/rewrite/v1/market/status", authorization=f"Bearer {_login_token()}"
    ))))
    assert advanced["delivery_delay_minutes"] == 0 and advanced["is_realtime"] is True
    assert set(advanced).isdisjoint({"qot_right", "provider_name", "fallback_from"})
    assert calls == {"available": 1, "rights": 1}


def test_market_status_fails_closed_when_the_upstream_probe_is_unavailable(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")

    class UnavailableOpenD:
        def available(self):
            return False

    monkeypatch.setattr(api_module, "OpenDAdapter", UnavailableOpenD)
    monkeypatch.setattr(api_module, "_MARKET_STATUS_CACHE", None)
    payload = _payload(asyncio.run(market_status(_request(
        "/api/rewrite/v1/market/status", authorization=f"Bearer {_login_token()}"
    ))))

    assert payload["status"] == "unavailable"
    assert payload["provider_realtime"] is False
    assert payload["is_realtime"] is False
    assert payload["delivery_delay_minutes"] == 15


def test_market_status_cold_cache_is_single_flight_across_concurrent_requests(browser_api, monkeypatch):
    api_module = importlib.import_module("src.apps.api.app")
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    class SlowOpenD:
        def available(self):
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            release.wait(timeout=2)
            return True

        def quote_rights(self):
            return {
                "us_realtime_entitlement": True,
                "us_option_realtime_entitlement": True,
            }

    monkeypatch.setattr(api_module, "OpenDAdapter", SlowOpenD)
    monkeypatch.setattr(api_module, "_MARKET_STATUS_CACHE", None)
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(api_module._upstream_market_status) for _ in range(6)]
        assert entered.wait(timeout=1)
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert calls == 1
    assert all(result["connected"] is True for result in results)


def test_market_quote_uses_akshare_research_quote_for_a_shares_and_yahoo_for_us_fallback(browser_api, monkeypatch):
    created = False

    class UnexpectedOpenD:
        def __init__(self):
            nonlocal created
            created = True
            raise AssertionError("A-share quote must not construct OpenD")

    class AKShareResearch:
        name = "AKShare"

        def stock_quote(self, symbol):
            assert symbol == "600519"
            return {
                "symbol": symbol, "last": 1500.0, "bid": None, "ask": None, "spread": None,
                "open": 1490.0, "high": 1510.0, "low": 1480.0, "prev_close": 1495.0,
                "volume": 100.0, "quote_at": "2026-08-11T00:00:00", "source": self.name,
            }

    api_module = importlib.import_module("src.apps.api.app")
    browser_api["database"].execute(
        "UPDATE users SET plan_type='高级版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    monkeypatch.setattr(api_module, "OpenDAdapter", UnexpectedOpenD)
    monkeypatch.setattr(
        api_module, "get_resilient_data_source", lambda name: AKShareResearch() if name == "akshare" else AssertionError()
    )
    access_token = _login_token()
    a_share = asyncio.run(market_quote(_request(
        "/api/rewrite/v1/market/quote", authorization=f"Bearer {access_token}", query={"symbol": "600519"},
    )))
    a_share_payload = _payload(a_share)
    assert a_share.status_code == 200 and created is False
    assert a_share_payload["source"] == "真实数据来源" and a_share_payload["last"] == 1500.0
    assert a_share_payload["bid"] is a_share_payload["ask"] is a_share_payload["spread"] is None
    assert a_share_payload["is_realtime"] is False and a_share_payload["actionable_quote"] is False

    class FailingOpenD:
        def stock_quote(self, symbol):
            from data.datasource import DataSourceError
            raise DataSourceError("OpenD 暂时无法连接。")

    monkeypatch.setattr(api_module, "OpenDAdapter", FailingOpenD)
    class DelayedResearch:
        name = "Yahoo Finance"

        def stock_quote(self, symbol):
            assert symbol == "AAPL"
            return {
                "symbol": symbol, "last": 201.5, "bid": None, "ask": None, "spread": None,
                "open": 199.0, "high": 202.0, "low": 198.0, "prev_close": 200.0,
                "volume": 1234.0, "quote_at": "2026-08-11T00:00:00+00:00", "source": self.name,
                "freshness": "约 15 分钟延迟的研究报价", "verification": "delayed_research_quote",
            }

    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda name: DelayedResearch())
    response = asyncio.run(market_quote(_request(
        "/api/rewrite/v1/market/quote", authorization=f"Bearer {access_token}", query={"symbol": "AAPL"},
    )))

    payload = _payload(response)
    assert response.status_code == 200 and payload["status"] == "available"
    assert payload["source"] == "真实数据来源" and "fallback_from" not in payload
    assert payload["last"] == 201.5 and payload["bid"] is payload["ask"] is payload["spread"] is None
    assert payload["is_realtime"] is False and payload["actionable_quote"] is False
    assert payload["verification"] == "delayed_research"


def test_market_quote_a_share_falls_back_to_yahoo_research_without_constructing_opend(browser_api, monkeypatch):
    class UnexpectedOpenD:
        def __init__(self):
            raise AssertionError("A-share fallback must not construct OpenD")

    class FailingAKShare:
        name = "AKShare"

        def stock_quote(self, symbol):
            from data.datasource import DataSourceError
            raise DataSourceError(f"{symbol} AKShare unavailable")

    class YahooResearch:
        name = "Yahoo Finance"

        def stock_quote(self, symbol):
            return {
                "symbol": symbol, "last": 1501.0, "bid": None, "ask": None, "spread": None,
                "open": 1498.0, "high": 1505.0, "low": 1490.0, "prev_close": 1499.0,
                "volume": 88.0, "quote_at": "2026-08-11T00:00:00", "source": self.name,
                "freshness": "约 15 分钟延迟的研究报价", "verification": "delayed_research_quote",
            }

    api_module = importlib.import_module("src.apps.api.app")
    browser_api["database"].execute(
        "UPDATE users SET plan_type='高级版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )
    monkeypatch.setattr(api_module, "OpenDAdapter", UnexpectedOpenD)
    monkeypatch.setattr(
        api_module,
        "get_resilient_data_source",
        lambda name: FailingAKShare() if name == "akshare" else YahooResearch(),
    )
    response = asyncio.run(market_quote(_request(
        "/api/rewrite/v1/market/quote", authorization=f"Bearer {_login_token()}", query={"symbol": "600519"},
    )))

    payload = _payload(response)
    assert response.status_code == 200 and payload["source"] == "真实数据来源"
    assert "fallback_from" not in payload
    assert payload["is_realtime"] is False and payload["actionable_quote"] is False


def _grant_professional_access(browser_api) -> None:
    browser_api["database"].execute(
        "UPDATE users SET plan_type='专业版', subscription_expire=? WHERE email=?",
        ("2099-01-01T00:00:00+00:00", "browser@example.com"),
    )


def test_option_endpoints_reject_non_professional_before_opend_is_created(browser_api, monkeypatch):
    created = False

    class UnexpectedOpenD:
        def __init__(self):
            nonlocal created
            created = True
            raise AssertionError("OpenD must not be constructed for a non-professional member")

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", UnexpectedOpenD)
    access_token = _login_token()
    for handler, query in (
        (options_chain, {"symbol": "AAPL"}),
        (option_candles, {"contract_code": "US.AAPL260918C210000", "timeframe": "日线"}),
    ):
        with pytest.raises(ApiError) as denied:
            asyncio.run(handler(_request(
                "/api/rewrite/v1/options/test", authorization=f"Bearer {access_token}", query=query,
            )))
        assert denied.value.status == 403
    assert created is False


def test_professional_options_chain_exposes_normalized_opend_fields(browser_api, monkeypatch):
    _grant_professional_access(browser_api)
    received = []

    class StubOpenD:
        def option_chain_with_expiries(self, symbol, expiry):
            received.append((symbol, expiry))
            calls = pd.DataFrame([{
                "contractSymbol": "US.AAPL260918C210000", "lastTradeDate": "2026-08-08 16:00:00",
                "strike": 210, "lastPrice": 5.2, "bid": 5.1, "ask": 5.3, "volume": 100,
                "openInterest": 500, "impliedVolatility": 0.31, "delta": 0.5,
                "gamma": 0.02, "theta": -0.1, "vega": 0.2, "rho": 0.05,
            }])
            puts = pd.DataFrame([{
                "contractSymbol": "US.AAPL260918P210000", "lastTradeDate": "2026-08-08 16:00:00",
                "strike": 210, "lastPrice": 4.9, "bid": 4.8, "ask": 5.0, "volume": 90,
                "openInterest": 480, "impliedVolatility": 0.32, "delta": -0.48,
                "gamma": 0.02, "theta": -0.11, "vega": 0.21, "rho": -0.04,
            }])
            return "2026-09-18", ["2026-09-18", "2026-10-16"], calls, puts

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", StubOpenD)
    response = asyncio.run(options_chain(_request(
        "/api/rewrite/v1/options/chain", authorization=f"Bearer {_login_token()}",
        query={"symbol": "AAPL", "expiry": "2026-09-18"},
    )))
    payload = _payload(response)
    call = payload["calls"][0]

    assert response.status_code == 200
    assert received == [("AAPL", "2026-09-18")]
    assert payload["expiry"] == "2026-09-18"
    assert payload["expiries"] == ["2026-09-18", "2026-10-16"] and len(payload["puts"]) == 1
    assert payload["source"] == "真实数据来源"
    assert set(payload).isdisjoint({"fallback_from", "qot_right", "provider_name"})
    assert call == {
        "expiry": "2026-09-18", "option_type": "CALL", "contract_code": "US.AAPL260918C210000",
        "strike": 210.0, "last": 5.2, "bid": 5.1, "ask": 5.3, "spread": pytest.approx(0.2),
        "volume": 100.0, "open_interest": 500.0, "implied_volatility": 0.31,
        "greeks": {"delta": 0.5, "gamma": 0.02, "theta": -0.1, "vega": 0.2, "rho": 0.05},
        "quote_at": "2026-08-08T20:00:00+00:00",
    }


def test_option_chain_validates_us_symbol_and_calendar_expiry_before_opend(browser_api, monkeypatch):
    _grant_professional_access(browser_api)
    created = False

    class UnexpectedOpenD:
        def __init__(self):
            nonlocal created
            created = True
            raise AssertionError("invalid option-chain requests must not construct OpenD")

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", UnexpectedOpenD)
    access_token = _login_token()
    for query in (
        {"symbol": "600519"},
        {"symbol": "AAPL", "expiry": "2026-02-30"},
        {"symbol": "AAPL", "expiry": "20260918"},
    ):
        with pytest.raises(ApiError) as invalid:
            asyncio.run(options_chain(_request(
                "/api/rewrite/v1/options/chain", authorization=f"Bearer {access_token}", query=query,
            )))
        assert invalid.value.status == 400
    assert created is False


def test_option_chain_rejects_unavailable_expiry_instead_of_selecting_another(browser_api, monkeypatch):
    _grant_professional_access(browser_api)

    class StubOpenD:
        def option_chain_with_expiries(self, symbol, expiry):
            from data.opend_adapter import OptionExpiryUnavailableError
            assert (symbol, expiry) == ("AAPL", "2026-10-16")
            raise OptionExpiryUnavailableError("请求的期权到期日不在 OpenD 可用列表中。")

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", StubOpenD)
    with pytest.raises(ApiError) as unavailable:
        asyncio.run(options_chain(_request(
            "/api/rewrite/v1/options/chain", authorization=f"Bearer {_login_token()}",
            query={"symbol": "AAPL", "expiry": "2026-10-16"},
        )))

    assert unavailable.value.status == 404


def test_opend_option_chain_exposes_all_expiries_and_never_reselects(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    from data.opend_adapter import OpenDAdapter, OptionExpiryUnavailableError
    from futu import RET_OK

    class ChainContext:
        def close(self):
            pass

        def get_option_chain(self, code):
            assert code == "US.AAPL"
            return RET_OK, pd.DataFrame({
                "code": [
                    "US.AAPL260918C210000", "US.AAPL260918P210000",
                    "US.AAPL261016C210000", "US.AAPL261016P210000",
                ],
                "strike_time": ["2026-09-18", "2026-09-18", "2026-10-16", "2026-10-16"],
                "strike_price": [210, 210, 210, 210],
                "option_type": ["CALL", "PUT", "CALL", "PUT"],
            })

        def get_market_snapshot(self, codes):
            return RET_OK, pd.DataFrame({"code": codes, "last_price": [5.0] * len(codes)})

    adapter = OpenDAdapter()
    monkeypatch.setattr(adapter, "_context", lambda: ChainContext())
    selected, expiries, calls, puts = adapter.option_chain_with_expiries("AAPL", "2026-10-16")

    assert selected == "2026-10-16"
    assert expiries == ["2026-09-18", "2026-10-16"]
    assert calls["contractSymbol"].tolist() == ["US.AAPL261016C210000"]
    assert puts["contractSymbol"].tolist() == ["US.AAPL261016P210000"]
    with pytest.raises(OptionExpiryUnavailableError):
        adapter.option_chain_with_expiries("AAPL", "2026-12-18")


def test_opend_stock_snapshot_normalizes_quote_fields(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    from data.opend_adapter import OpenDAdapter
    from futu import RET_OK

    class SnapshotContext:
        def close(self):
            pass

        def get_market_snapshot(self, codes):
            assert codes == ["US.AAPL"]
            return RET_OK, pd.DataFrame([{
                "code": "US.AAPL", "last_price": 211.5, "bid_price": 211.4, "ask_price": 211.6,
                "open_price": 208, "high_price": 212, "low_price": 207.5, "prev_close_price": 209,
                "volume": 1_234_567, "update_time": "2026-08-11 15:59:59",
            }])

    adapter = OpenDAdapter()
    monkeypatch.setattr(adapter, "_context", lambda: SnapshotContext())

    quote = adapter.stock_quote("AAPL")
    assert {key: quote[key] for key in (
        "symbol", "last", "bid", "ask", "open", "high", "low", "prev_close", "volume", "quote_at", "source",
    )} == {
        "symbol": "AAPL", "last": 211.5, "bid": 211.4, "ask": 211.6,
        "open": 208.0, "high": 212.0, "low": 207.5, "prev_close": 209.0,
        "volume": 1_234_567.0, "quote_at": "2026-08-11T15:59:59-04:00", "source": "OpenD",
    }
    assert quote["spread"] == pytest.approx(0.2)
    assert quote["us_qot_right"] == "N/A" and quote["actionable_snapshot"] is False


def test_professional_option_candles_validate_request_and_never_fallback(browser_api, monkeypatch):
    _grant_professional_access(browser_api)
    received = []

    class StubOpenD:
        def option_bars(self, contract_code, period, interval):
            received.append((contract_code, period, interval))
            return pd.DataFrame(
                [{"Open": 5, "High": 6, "Low": 4.5, "Close": 5.5, "Volume": 120}],
                index=pd.to_datetime(["2026-08-08T00:00:00Z"]),
            )

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", StubOpenD)
    access_token = _login_token()
    response = asyncio.run(option_candles(_request(
        "/api/rewrite/v1/options/candles", authorization=f"Bearer {access_token}",
        query={"contract_code": "US.AAPL260918C210000", "timeframe": "日线"},
    )))

    assert response.status_code == 200
    assert received == [("US.AAPL260918C210000", "6mo", "1d")]
    assert _payload(response)["items"][0]["close"] == 5.5

    received.clear()
    for query in (
        {"contract_code": "AAPL260918C210000", "timeframe": "日线"},
        {"contract_code": "US.AAPL260918C210000", "timeframe": "3小时"},
    ):
        with pytest.raises(ApiError) as invalid:
            asyncio.run(option_candles(_request(
                "/api/rewrite/v1/options/candles", authorization=f"Bearer {access_token}", query=query,
            )))
        assert invalid.value.status == 400
    assert received == []


def test_options_opend_failure_uses_delayed_yahoo_chain_but_never_guesses_contract_mapping(
    browser_api, monkeypatch
):
    _grant_professional_access(browser_api)

    class FailingOpenD:
        def option_chain_with_expiries(self, symbol, expiry):
            from data.datasource import DataSourceError
            raise DataSourceError("OpenD 暂时无法连接。")

        def option_bars(self, contract_code, period, interval):
            from data.datasource import DataSourceError
            raise DataSourceError("OpenD 暂时无法连接。")

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", FailingOpenD)

    class DelayedYahoo:
        def option_chain_with_expiries(self, symbol, expiry):
            assert (symbol, expiry) == ("AAPL", None)
            calls = pd.DataFrame([{
                "contractSymbol": "AAPL260918C00210000", "lastTradeDate": "2026-08-08 16:00:00",
                "strike": 210, "lastPrice": 5.2, "bid": 5.1, "ask": 5.3,
                "volume": 100, "openInterest": 500, "impliedVolatility": 0.31,
            }])
            return "2026-09-18", ["2026-09-18"], calls, pd.DataFrame()

    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_: DelayedYahoo())
    access_token = _login_token()
    chain = asyncio.run(options_chain(_request(
        "/api/rewrite/v1/options/chain", authorization=f"Bearer {access_token}", query={"symbol": "AAPL"},
    )))
    candles = asyncio.run(option_candles(_request(
        "/api/rewrite/v1/options/candles", authorization=f"Bearer {access_token}",
        query={"contract_code": "US.AAPL260918C210000", "timeframe": "日线"},
    )))

    assert chain.status_code == 200
    chain_payload = _payload(chain)
    assert chain_payload["source"] == "真实数据来源"
    assert "fallback_from" not in chain_payload and "qot_right" not in chain_payload
    assert chain_payload["is_realtime"] is False and chain_payload["actionable_quote"] is False
    assert chain_payload["missing_fields"] == ["delta", "gamma", "theta", "vega", "rho"]
    assert chain_payload["calls"][0]["contract_code"] == "AAPL260918C00210000"
    assert chain_payload["calls"][0]["greeks"] == {
        "delta": None, "gamma": None, "theta": None, "vega": None, "rho": None,
    }

    assert candles.status_code == 503
    assert _payload(candles)["items"] == []
    assert _payload(candles)["error"] == "期权行情暂时不可用，请稍后重试。"
    assert _payload(candles)["code"] == "option_market_data_unavailable"


def test_options_return_explicit_503_when_opend_and_yahoo_are_both_unavailable(browser_api, monkeypatch):
    _grant_professional_access(browser_api)

    class FailingOpenD:
        def option_chain_with_expiries(self, symbol, expiry):
            from data.datasource import DataSourceError
            raise DataSourceError("OpenD 暂时无法连接。")

        def option_bars(self, contract_code, period, interval):
            from data.datasource import DataSourceError
            raise DataSourceError("OpenD 暂时无法连接。")

    class FailingYahoo:
        def option_chain_with_expiries(self, symbol, expiry):
            from data.datasource import DataSourceError
            raise DataSourceError("Yahoo Finance 暂时无法连接。")

        def option_bars(self, contract_code, period, interval):
            from data.datasource import DataSourceError
            raise DataSourceError("Yahoo Finance 暂时无法连接。")

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "OpenDAdapter", FailingOpenD)
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_: FailingYahoo())
    access_token = _login_token()

    chain = asyncio.run(options_chain(_request(
        "/api/rewrite/v1/options/chain", authorization=f"Bearer {access_token}", query={"symbol": "AAPL"},
    )))
    candles = asyncio.run(option_candles(_request(
        "/api/rewrite/v1/options/candles", authorization=f"Bearer {access_token}",
        query={"contract_code": "AAPL260918C00210000", "timeframe": "日线"},
    )))

    assert chain.status_code == candles.status_code == 503
    assert _payload(chain)["expiries"] == _payload(chain)["calls"] == _payload(chain)["puts"] == _payload(chain)["items"] == []
    assert _payload(candles)["items"] == []
    assert _payload(chain)["error"] == _payload(candles)["error"] == "期权行情暂时不可用，请稍后重试。"
    assert _payload(chain)["code"] == _payload(candles)["code"] == "option_market_data_unavailable"
    assert "OpenD" not in json.dumps(_payload(chain), ensure_ascii=False)
    assert "Yahoo" not in json.dumps(_payload(candles), ensure_ascii=False)


def test_authenticated_market_search_finds_arbitrary_symbol(browser_api, monkeypatch):
    access_token = _login_token()

    class StubSource:
        name = "stub"

        def search(self, query, market, max_results):
            assert (query, market, max_results) == ("PLTR", "美股", 8)
            return [{"symbol": "PLTR", "name": "Palantir Technologies", "exchange": "NASDAQ", "type": "股票"}]

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_: StubSource())
    response = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search",
        authorization=f"Bearer {access_token}",
        query={"q": "PLTR", "market": "美股"},
    )))

    assert _payload(response)["items"] == [{
        "symbol": "PLTR", "name": "Palantir Technologies", "exchange": "NASDAQ",
        "type": "股票", "market": "US",
    }]


def test_watchlist_api_post_delete_head_and_bootstrap_are_consistent(browser_api):
    access_token = _login_token()
    auth_header = f"Bearer {access_token}"

    added_us = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="POST", authorization=auth_header,
        payload={"market": "US", "symbol": "pltr"},
    )))
    added_cn = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="POST", authorization=auth_header,
        payload={"market": "CN", "symbol": "600519.SS"},
    )))
    removed = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="DELETE", authorization=auth_header,
        payload={"market": "US", "symbol": "PLTR"},
    )))
    head = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="HEAD", authorization=auth_header,
        payload={"market": "US", "symbol": "MSFT"},
    )))
    bootstrapped = asyncio.run(bootstrap(_request(
        "/api/rewrite/v1/bootstrap", authorization=auth_header,
    )))

    assert _payload(added_us)["watchlists"] == {"us": ["PLTR"], "a_share": []}
    assert _payload(added_cn)["watchlists"] == {"us": ["PLTR"], "a_share": ["600519"]}
    assert _payload(removed)["watchlists"] == {"us": [], "a_share": ["600519"]}
    assert _payload(head)["watchlists"] == {"us": [], "a_share": ["600519"]}
    assert _payload(bootstrapped)["settings"]["watchlists"] == _payload(removed)["watchlists"]


def test_watchlist_api_requires_auth_and_invalid_payload_does_not_write(browser_api):
    with pytest.raises(ApiError) as unauthenticated:
        asyncio.run(watchlist(_request(
            "/api/rewrite/v1/watchlist", method="POST",
            payload={"market": "US", "symbol": "PLTR"},
        )))
    assert unauthenticated.value.status == 401

    access_token = _login_token()
    auth_header = f"Bearer {access_token}"
    before = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", authorization=auth_header,
    )))
    with pytest.raises(ApiError) as invalid:
        asyncio.run(watchlist(_request(
            "/api/rewrite/v1/watchlist", method="POST", authorization=auth_header,
            payload={"market": "US", "symbol": "PLTR", "unexpected": True},
        )))
    after = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", authorization=auth_header,
    )))

    assert invalid.value.status == 400
    assert _payload(after) == _payload(before)


def test_watchlist_api_pins_sort_items_and_returns_metadata(browser_api):
    auth_header = f"Bearer {_login_token()}"
    for symbol in ("PLTR", "AAPL"):
        asyncio.run(watchlist(_request(
            "/api/rewrite/v1/watchlist", method="POST", authorization=auth_header,
            payload={"market": "US", "symbol": symbol},
        )))

    pinned = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="PATCH", authorization=auth_header,
        payload={"market": "US", "symbol": "AAPL", "pinned": True},
    )))
    repeated = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="PATCH", authorization=auth_header,
        payload={"market": "US", "symbol": "AAPL", "pinned": True},
    )))

    assert _payload(pinned) == _payload(repeated) == {
        "watchlists": {"us": ["AAPL", "PLTR"], "a_share": []},
        "pins": {"us": ["AAPL"], "a_share": []},
    }


def test_alert_delete_endpoint_is_idempotent_and_rejects_other_users(browser_api):
    first_token = _login_token()
    created = asyncio.run(alerts(_request(
        "/api/rewrite/v1/alerts", method="POST", authorization=f"Bearer {first_token}",
        payload={
            "symbol": "AAPL",
            "conditions": [{"type": "price", "operator": ">=", "value": 220}],
        },
    )))
    alert_id = _payload(created)["items"][0]["id"]
    other = browser_api["auth"].register(
        "alert-other@example.com", "StrongPass123", "Other", True
    )
    assert other is not None
    other_token = browser_api["auth"].login(
        "alert-other@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token

    denied = _request(
        f"/api/rewrite/v1/alerts/{alert_id}", method="DELETE", authorization=f"Bearer {other_token}"
    )
    denied.scope["path_params"] = {"alert_id": str(alert_id)}
    with pytest.raises(ApiError, match="找不到"):
        asyncio.run(alert_item(denied))

    for _ in range(2):
        request = _request(
            f"/api/rewrite/v1/alerts/{alert_id}", method="DELETE", authorization=f"Bearer {first_token}"
        )
        request.scope["path_params"] = {"alert_id": str(alert_id)}
        deleted = asyncio.run(alert_item(request))
        assert _payload(deleted)["deactivated"] is True

    row = browser_api["database"].fetch_one("SELECT is_active FROM price_alerts WHERE id=?", (alert_id,))
    assert row["is_active"] == 0


def test_alert_create_endpoint_accepts_metadata_and_deduplicates(browser_api):
    auth_header = f"Bearer {_login_token()}"
    payload = {
        "symbol": "AAPL",
        "conditions": [{"type": "price", "operator": "<=", "value": 180}],
        "trigger_mode": "crosses_below",
        "repeat_mode": "repeat",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "channels": ["website"],
        "notify_only": True,
    }
    first = asyncio.run(alerts(_request(
        "/api/rewrite/v1/alerts", method="POST", authorization=auth_header, payload=payload,
    )))
    second = asyncio.run(alerts(_request(
        "/api/rewrite/v1/alerts", method="POST", authorization=auth_header, payload=payload,
    )))

    assert first.status_code == second.status_code == 201
    assert len(_payload(first)["items"]) == len(_payload(second)["items"]) == 1
    item = _payload(first)["items"][0]
    assert item["trigger_mode"] == "crosses_below"
    assert item["repeat_mode"] == "repeat"
    assert item["channels"] == ["website"]
    assert item["notify_only"] is True


def test_watchlist_api_isolates_identities(browser_api):
    second_user = browser_api["auth"].register(
        "other-browser@example.com", "StrongPass123", "Other", True
    )
    assert second_user is not None
    second_login = browser_api["auth"].login(
        "other-browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    )
    first_token = _login_token()
    second_token = second_login.access_token

    first = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="POST", authorization=f"Bearer {first_token}",
        payload={"market": "US", "symbol": "PLTR"},
    )))
    second_before = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", authorization=f"Bearer {second_token}",
    )))
    second_delete = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="DELETE", authorization=f"Bearer {second_token}",
        payload={"market": "US", "symbol": "PLTR"},
    )))

    assert _payload(first)["watchlists"]["us"] == ["PLTR"]
    assert _payload(second_before)["watchlists"] == {"us": [], "a_share": []}
    assert _payload(second_delete)["watchlists"] == {"us": [], "a_share": []}


def test_locale_preference_accepts_supported_values_and_rejects_invalid(browser_api):
    access_token = _login_token()
    auth_header = f"Bearer {access_token}"

    saved = asyncio.run(locale_preference(_request(
        "/api/rewrite/v1/settings/locale", method="PUT", authorization=auth_header,
        payload={"locale": "zh-Hans"},
    )))
    with pytest.raises(ApiError) as invalid:
        asyncio.run(locale_preference(_request(
            "/api/rewrite/v1/settings/locale", method="PUT", authorization=auth_header,
            payload={"locale": "en-US"},
        )))
    bootstrapped = asyncio.run(bootstrap(_request(
        "/api/rewrite/v1/bootstrap", authorization=auth_header,
    )))

    assert _payload(saved) == {"locale": "zh-Hans"}
    assert invalid.value.status == 400
    assert _payload(bootstrapped)["settings"]["ui_locale"] == "zh-Hans"


def test_market_search_a_share_prefers_akshare_and_returns_cn(browser_api, monkeypatch):
    access_token = _login_token()
    calls: list[str | None] = []

    class StubSource:
        name = "AKShare"

        def search(self, query, market, max_results):
            assert (query, market, max_results) == ("600519", "A股", 8)
            return [{"symbol": "600519.SS", "name": "贵州茅台", "exchange": "上海", "type": "股票"}]

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(
        api_module,
        "get_resilient_data_source",
        lambda name=None: (calls.append(name) or StubSource()),
    )
    response = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "600519", "market": "A股"},
    )))

    assert calls == ["akshare"]
    assert _payload(response)["items"][0]["market"] == "CN"


@pytest.mark.parametrize("raises", [False, True])
def test_market_search_us_falls_back_to_yahoo_on_empty_or_error(browser_api, monkeypatch, raises):
    access_token = _login_token()
    calls: list[str | None] = []

    class PrimarySource:
        name = "OpenD"

        def search(self, query, market, max_results):
            if raises:
                from data.datasource import DataSourceError
                raise DataSourceError("primary unavailable")
            return []

    class YahooSource:
        name = "Yahoo Finance"

        def search(self, query, market, max_results):
            return [{"symbol": "PLTR", "name": "Palantir", "exchange": "NASDAQ", "type": "股票"}]

    sources = iter([PrimarySource(), YahooSource()])
    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(
        api_module,
        "get_resilient_data_source",
        lambda name=None: (calls.append(name) or next(sources)),
    )
    response = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "PLTR", "market": "美股"},
    )))

    assert calls == [None, "yfinance"]
    assert _payload(response)["items"][0]["symbol"] == "PLTR"


def test_market_search_direct_yahoo_failure_is_not_retried(browser_api, monkeypatch):
    access_token = _login_token()
    calls = 0

    class YahooSource:
        name = "Yahoo Finance"

        def search(self, query, market, max_results):
            nonlocal calls
            calls += 1
            from data.datasource import DataSourceError
            raise DataSourceError("Yahoo unavailable")

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda name=None: YahooSource())
    with pytest.raises(ApiError) as error:
        asyncio.run(market_search(_request(
            "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
            query={"q": "PLTR", "market": "美股"},
        )))

    assert error.value.status == 503
    assert calls == 1


def test_market_search_invalid_query_does_not_call_provider(browser_api, monkeypatch):
    access_token = _login_token()
    calls = 0
    api_module = importlib.import_module("src.apps.api.app")

    def factory(name=None):
        nonlocal calls
        calls += 1
        raise AssertionError("provider should not be called")

    monkeypatch.setattr(api_module, "get_resilient_data_source", factory)
    for query, market in (("", "美股"), ("x" * 41, "美股"), ("PLTR", "HK")):
        with pytest.raises(ApiError) as error:
            asyncio.run(market_search(_request(
                "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
                query={"q": query, "market": market},
            )))
        assert error.value.status == 400
    assert calls == 0


def test_market_search_rate_limit_and_cache(browser_api, monkeypatch):
    access_token = _login_token()
    provider_calls = 0

    class YahooSource:
        name = "Yahoo Finance"

        def search(self, query, market, max_results):
            nonlocal provider_calls
            provider_calls += 1
            return [{"symbol": query, "name": query, "exchange": "NASDAQ", "type": "股票"}]

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda name=None: YahooSource())
    first = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "PLTR", "market": "美股"},
    )))
    cached = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "pltr", "market": "美股"},
    )))
    assert _payload(first)["items"][0]["symbol"] == "PLTR"
    assert _payload(cached)["cached"] is True
    assert provider_calls == 1

    with api_module._MARKET_SEARCH_LOCK:
        api_module._MARKET_SEARCH_CACHE.clear()
        api_module._MARKET_SEARCH_RATE.clear()
    for index in range(30):
        asyncio.run(market_search(_request(
            "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
            query={"q": f"Q{index}", "market": "美股"},
        )))
    with pytest.raises(ApiError) as limited:
        asyncio.run(market_search(_request(
            "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
            query={"q": "Q30", "market": "美股"},
        )))

    assert provider_calls == 31
    assert limited.value.status == 429
