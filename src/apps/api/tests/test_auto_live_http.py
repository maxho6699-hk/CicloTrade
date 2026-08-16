from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from core.compat import UTC
from src.apps.api.app import routes


BASE = "/api/rewrite/v1/auto-live"


def _token(browser_api) -> tuple[int, str]:
    user = browser_api["database"].fetch_one(
        "SELECT id FROM users WHERE email='browser@example.com'"
    )
    token = browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest-auto-live-http"
    ).access_token
    return int(user["id"]), f"Bearer {token}"


def test_auto_live_routes_require_the_existing_bearer_identity(browser_api):
    paths = {route.path for route in routes}
    assert BASE in paths
    assert f"{BASE}/mandates" in paths
    assert f"{BASE}/mandates/{{mandate_public_id:str}}/start" in paths
    assert f"{BASE}/pause" in paths

    status, _, payload = asyncio.run(_asgi(BASE))
    assert status == 401 and "Bearer" in payload["error"]
    _, authorization = _token(browser_api)
    status, headers, snapshot = asyncio.run(_asgi(BASE, headers=(("authorization", authorization),)))
    assert status == 200 and snapshot["mandates"] == []
    assert headers["cache-control"] == "private, no-store"


def test_snapshot_uses_opaque_broker_refs_and_unapproved_contracts_fail_closed(browser_api):
    user_id, authorization = _token(browser_api)
    now = datetime.now(UTC).isoformat()
    browser_api["database"].execute(
        """INSERT INTO broker_accounts
           (user_id,provider,account_alias,external_account_id,mode,is_active,status,metadata_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (user_id, "tiger", "Primary", "SECRET-ACCOUNT", "live", 1, "authorized", "{}", now),
    )
    common = (("authorization", authorization),)
    status, _, snapshot = asyncio.run(_asgi(BASE, headers=common))
    assert status == 200 and len(snapshot["broker_accounts"]) == 1
    broker = snapshot["broker_accounts"][0]
    assert broker["public_id"].startswith("broker_")
    serialized = json.dumps(snapshot, sort_keys=True)
    assert "SECRET-ACCOUNT" not in serialized and "broker_account_id" not in serialized

    start = datetime.now(UTC) + timedelta(minutes=1)
    status, _, blocked = asyncio.run(_asgi(
        f"{BASE}/mandates",
        method="POST",
        body={
            "broker_account_public_id": broker["public_id"],
            "strategy_version": "unapproved.strategy.v1",
            "risk_version": "unapproved.risk.v1",
            "capital_limit_minor": 100_000,
            "frequency_limit": 2,
            "valid_from": start.isoformat(),
            "valid_until": (start + timedelta(days=7)).isoformat(),
        },
        headers=common + (("idempotency-key", "mandate-create-blocked-01"),),
    ))
    assert status == 403 and "未获服务端批准" in blocked["error"]


def test_global_pause_is_idempotent_and_never_claims_unconfirmed_targets(browser_api):
    _, authorization = _token(browser_api)
    headers = (
        ("authorization", authorization),
        ("idempotency-key", "http-auto-pause-aggregate-01"),
    )
    status, _, first = asyncio.run(_asgi(
        f"{BASE}/pause", method="POST", body={"scope": "aggregate"}, headers=headers,
    ))
    status2, _, replay = asyncio.run(_asgi(
        f"{BASE}/pause", method="POST", body={"scope": "aggregate"}, headers=headers,
    ))
    assert status == status2 == 200 and first == replay
    assert first["status"] == "paused" and first["confirmed"] == first["total"] == 0
    assert first["can_reduce_exposure"] is True

    status, _, rejected = asyncio.run(_asgi(
        f"{BASE}/pause", method="POST",
        body={"scope": "broker", "broker_account_id": 1},
        headers=(
            ("authorization", authorization),
            ("idempotency-key", "http-auto-pause-invalid-01"),
        ),
    ))
    assert status == 400 and "暂停范围" in rejected["error"]


async def _asgi(path: str, *, method: str = "GET", body=None, headers=()):
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
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": supplied, "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443),
    }
    from src.apps.api.app import app
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    response_headers = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], response_headers, json.loads(response_body or b"{}")
