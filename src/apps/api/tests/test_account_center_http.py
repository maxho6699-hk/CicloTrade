from __future__ import annotations

import asyncio
import json

from core.account_center import AccountCenterService, request_sha256
from src.apps.api.app import routes


ACCOUNT = "/api/rewrite/v1/account"
NOTIFICATIONS = "/api/rewrite/v1/notifications"


def _token(browser_api) -> tuple[int, str]:
    user = browser_api["database"].fetch_one(
        "SELECT id FROM users WHERE email='browser@example.com'"
    )
    token = browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest-account-center"
    ).access_token
    return int(user["id"]), f"Bearer {token}"


def _manifest() -> dict:
    value = {
        "manifest_key": "ciclo",
        "skin_id": "shell-f0",
        "asset_version": "v1",
        "assets": {"avatar_48": "/assets/ciclo/shell-f0-v1-48.webp"},
    }
    value["manifest_sha256"] = request_sha256(value)
    return value


def test_account_routes_use_the_existing_bearer_identity(browser_api):
    paths = {route.path for route in routes}
    assert ACCOUNT in paths
    assert f"{ACCOUNT}/memory/{{memory_public_id:str}}/delete" in paths
    assert f"{NOTIFICATIONS}/{{item_public_id:str}}/read" in paths

    status, _, payload = asyncio.run(_asgi(ACCOUNT))
    assert status == 401 and "Bearer" in payload["error"]

    _, authorization = _token(browser_api)
    status, headers, payload = asyncio.run(
        _asgi(ACCOUNT, headers=(("authorization", authorization),))
    )
    assert status == 200
    assert headers["cache-control"] == "private, no-store"
    assert payload["runtime"] == {"auto_live": "not_ready"}
    assert all(item["policy_state"] == "not_configured" for item in payload["agent_levels"].values())


def test_account_memory_authorization_content_and_appearance_are_owner_scoped(browser_api):
    user_id, authorization = _token(browser_api)
    service = AccountCenterService(
        browser_api["database"],
        appearance_entitlement_resolver=lambda owner, skin, version, digest: {
            "allowed": owner == user_id and skin == "shell-f0",
            "rank": 0,
        },
    )
    manifest = next(
        item for item in service.list_appearances(user_id)
        if item["skin_id"] == "shell-f0"
    )
    service.index_content(user_id, "research:AAPL", 1, {"title": "AAPL 研究"}, "http-content-01")

    common = (("authorization", authorization),)
    status, _, appearances = asyncio.run(_asgi(f"{ACCOUNT}/appearances", headers=common))
    assert status == 200 and appearances["items"][0]["entitled"] is True
    assert "owner_id" not in json.dumps(appearances)
    status, _, selected = asyncio.run(_asgi(
        f"{ACCOUNT}/appearance/select", method="POST",
        body={"manifest_public_id": manifest["public_id"]},
        headers=common + (("idempotency-key", "http-selection-01"),),
    ))
    assert status == 200 and selected["skin_id"] == "shell-f0"

    status, _, created = asyncio.run(_asgi(
        f"{ACCOUNT}/memory", method="POST",
        body={"memory_key": "risk-style", "value": {"preference": "evidence-first"}},
        headers=common + (("idempotency-key", "http-memory-01"),),
    ))
    assert status == 201
    status, _, memories = asyncio.run(_asgi(f"{ACCOUNT}/memory", headers=common))
    assert status == 200 and memories["items"][0]["public_id"] == created["public_id"]
    status, _, _ = asyncio.run(_asgi(
        f"{ACCOUNT}/memory/{created['public_id']}/delete", method="POST",
        body={"reason": "用户主动删除"},
        headers=common + (("idempotency-key", "http-memory-delete-01"),),
    ))
    assert status == 200

    status, _, authorization_receipt = asyncio.run(_asgi(
        f"{ACCOUNT}/authorizations", method="POST",
        body={"data_kind": "ai_memory", "scope": {"pages": ["research"]}, "action": "granted"},
        headers=common + (("idempotency-key", "http-authorization-01"),),
    ))
    assert status == 201 and authorization_receipt["action"] == "granted"
    status, _, authorization_state = asyncio.run(
        _asgi(f"{ACCOUNT}/authorizations/ai_memory", headers=common)
    )
    assert status == 200 and authorization_state["authorized"] is True
    status, _, content = asyncio.run(_asgi(f"{ACCOUNT}/content", headers=common))
    assert status == 200 and content["items"][0]["content"] == {"title": "AAPL 研究"}


def test_notification_inbox_read_and_deep_link_fail_closed(browser_api):
    user_id, authorization = _token(browser_api)
    service = AccountCenterService(browser_api["database"])
    item = service.create_notification(
        user_id,
        {
            "source_kind": "account",
            "source_public_id": "account_aaaaaaaaaaaaaaaaaaaaaaaa",
            "source_version": 1,
            "kind": "account_update",
            "title": "账户资料已更新",
            "body": "你的账户资料已完成一次受控更新。",
            "severity": "info",
            "target": None,
        },
        "http-notification-01",
    )
    common = (("authorization", authorization),)
    status, _, inbox = asyncio.run(_asgi(NOTIFICATIONS, headers=common))
    assert status == 200 and inbox["items"][0]["read"] is False
    status, _, _ = asyncio.run(_asgi(
        f"{NOTIFICATIONS}/{item['public_id']}/read", method="POST",
        headers=common + (("idempotency-key", "http-notification-read-01"),),
    ))
    assert status == 200
    status, _, resolved = asyncio.run(_asgi(
        f"{NOTIFICATIONS}/resolve", method="POST",
        body={"notification_public_id": item["public_id"]},
        headers=common,
    ))
    assert status == 200 and resolved == {"route": "/notifications", "locator": None, "stale": True}
    status, _, stale = asyncio.run(_asgi(
        f"{NOTIFICATIONS}/resolve", method="POST",
        body={"notification_public_id": "ntf_aaaaaaaaaaaaaaaaaaaaaaaa"},
        headers=common,
    ))
    assert status == 404


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

    query = b""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": query, "headers": supplied, "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443),
    }
    from src.apps.api.app import app
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    response_headers = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], response_headers, json.loads(response_body or b"{}")
