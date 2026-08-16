from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from urllib.parse import urlencode

from core.compat import UTC
from core.entitlement_policy import seed_canonical_policy
from src.apps.api.app import app, routes


USER_PATH = "/api/rewrite/v1/broker-access-applications"
READINESS_PATH = f"{USER_PATH}/readiness"
ADMIN_PATH = "/api/rewrite/v1/admin/broker-access-applications"


def _authorize(browser_api, *, admin=False):
    database = browser_api["database"]
    if admin:
        user = database.fetch_one("SELECT id FROM users WHERE email='reviewer@example.com'")
        if user is None:
            user = browser_api["auth"].register(
                "reviewer@example.com", "StrongPass123", "Reviewer", True
            )
        database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
        database.execute(
            """INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'super_admin',?)
               ON CONFLICT(user_id) DO UPDATE SET role='super_admin',updated_at=excluded.updated_at""",
            (user["id"], datetime.now(UTC).isoformat()),
        )
        email = "reviewer@example.com"
    else:
        user = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")
        email = "browser@example.com"
    token = browser_api["auth"].login(
        email, "StrongPass123", "127.0.0.1", "pytest-broker-access"
    ).access_token
    return user, f"Bearer {token}"


def _eligible(browser_api, user):
    database = browser_api["database"]
    now = datetime.now(UTC)
    expires = now + timedelta(days=30)
    with database.transaction() as connection:
        seed_canonical_policy(connection, now=now)
    database.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=? WHERE id=?",
        (expires.isoformat(), user["id"]),
    )
    database.execute(
        """INSERT INTO membership_entitlements
           (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
           VALUES (?,'高级版',?,?,30,'test','broker-http','active',?)""",
        (user["id"], now.isoformat(), expires.isoformat(), now.isoformat()),
    )
    database.execute(
        "INSERT INTO telegram_accounts(user_id,chat_id,is_active,created_at,updated_at) VALUES (?,'812345678',1,?,?)",
        (user["id"], now.isoformat(), now.isoformat()),
    )
    database.execute(
        """INSERT INTO user_settings(user_id,settings_json,updated_at)
           VALUES (?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
        (user["id"], '{"telegram":{"verified":true,"consent":true}}', now.isoformat()),
    )


def test_routes_registered_and_unauthenticated_requests_fail(browser_api):
    paths = {route.path for route in routes}
    assert USER_PATH in paths
    assert READINESS_PATH in paths
    assert f"{USER_PATH}/{{application_id:str}}/withdraw" in paths
    assert ADMIN_PATH in paths
    assert f"{ADMIN_PATH}/{{application_id:str}}/review" in paths
    status, _, payload = asyncio.run(_asgi(USER_PATH))
    assert status == 401 and "Bearer" in payload["error"]


def test_http_submit_replay_withdraw_and_admin_review_are_sanitized(browser_api):
    user, authorization = _authorize(browser_api)
    _eligible(browser_api, user)
    status, readiness_headers, readiness = asyncio.run(
        _asgi(READINESS_PATH, headers=(("authorization", authorization),))
    )
    assert status == 200 and readiness["can_apply"] is True
    assert readiness["providers"] == ["futu_moomoo", "ibkr", "longbridge", "tiger", "webull"]
    assert readiness_headers["cache-control"] == "private, no-store"
    assert readiness["eligibility_only"] is True
    assert readiness["broker_account_created"] is readiness["execution_enabled"] is False
    headers = (("authorization", authorization), ("idempotency-key", "broker-http-key-01"))
    body = {"provider": "ibkr", "request_reason": "需要美股及期权资格"}
    status, response_headers, created = asyncio.run(
        _asgi(USER_PATH, method="POST", body=body, headers=headers)
    )
    assert status == 201 and created["replayed"] is False
    assert response_headers["cache-control"] == "private, no-store"
    application_id = created["application"]["id"]
    serialized = json.dumps(created, sort_keys=True)
    assert all(value not in serialized for value in ("chat_id", "request_fingerprint", "idempotency_key"))
    status, _, replayed = asyncio.run(_asgi(USER_PATH, method="POST", body=body, headers=headers))
    assert status == 200 and replayed["replayed"] is True

    status, _, withdrawn = asyncio.run(
        _asgi(f"{USER_PATH}/{application_id}/withdraw", method="POST", headers=(("authorization", authorization),))
    )
    assert status == 200 and withdrawn["application"]["status"] == "withdrawn"

    status, _, replacement = asyncio.run(
        _asgi(
            USER_PATH,
            method="POST",
            body={"provider": "ibkr"},
            headers=(("authorization", authorization), ("idempotency-key", "broker-http-key-02")),
        )
    )
    assert status == 201
    replacement_id = replacement["application"]["id"]

    _, admin_authorization = _authorize(browser_api, admin=True)
    status, _, queued = asyncio.run(
        _asgi(ADMIN_PATH, headers=(("authorization", admin_authorization),))
    )
    assert status == 200 and [item["id"] for item in queued["items"]] == [replacement_id]
    status, _, reviewed = asyncio.run(
        _asgi(
            f"{ADMIN_PATH}/{replacement_id}/review",
            method="POST",
            body={"decision": "approved", "reason": "资格条件已核验"},
            headers=(("authorization", admin_authorization),),
        )
    )
    assert status == 200 and reviewed["application"]["status"] == "approved"
    database = browser_api["database"]
    assert database.fetch_one("SELECT COUNT(*) count FROM broker_accounts")["count"] == 0
    assert database.fetch_one("SELECT COUNT(*) count FROM telegram_service_outbox")["count"] == 0


def test_http_rejects_noncanonical_provider_and_non_super_admin(browser_api):
    user, authorization = _authorize(browser_api)
    _eligible(browser_api, user)
    status, _, payload = asyncio.run(
        _asgi(
            USER_PATH,
            method="POST",
            body={"provider": "alpaca"},
            headers=(("authorization", authorization), ("idempotency-key", "broker-http-key-03")),
        )
    )
    assert status == 400 and "五家美股" in payload["error"]
    status, _, payload = asyncio.run(
        _asgi(ADMIN_PATH, headers=(("authorization", authorization),))
    )
    assert status == 403 and "后台权限" in payload["error"]


def test_http_requires_verified_and_consented_telegram(browser_api):
    user, authorization = _authorize(browser_api)
    _eligible(browser_api, user)
    browser_api["database"].execute(
        "UPDATE user_settings SET settings_json=? WHERE user_id=?",
        ('{"telegram":{"verified":true,"consent":false}}', user["id"]),
    )
    status, _, payload = asyncio.run(
        _asgi(
            USER_PATH,
            method="POST",
            body={"provider": "ibkr"},
            headers=(("authorization", authorization), ("idempotency-key", "broker-http-key-consent")),
        )
    )
    assert status == 403 and "验证并同意" in payload["error"]


async def _asgi(path, *, method="GET", body=None, headers=(), query=None):
    encoded = json.dumps(body).encode() if body is not None else b""
    supplied = [(name.lower().encode(), value.encode()) for name, value in headers]
    supplied.append((b"content-length", str(len(encoded)).encode()))
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(query or {}).encode(),
        "headers": supplied,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    response_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    return start["status"], response_headers, json.loads(raw.decode())
