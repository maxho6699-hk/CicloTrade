from __future__ import annotations

import asyncio
import json

from starlette.applications import Starlette

from core.ai_workspace import AIWorkspaceService
from core.auth import AuthService
from core.database import DatabaseManager
from src.apps.api.ai_workspace import AI_WORKSPACE_ROUTES, AIWorkspaceApiError, ai_workspace_error_handler


def _asgi(app, path: str, *, method: str = "GET", body=None, headers=()):
    encoded = json.dumps(body).encode() if body is not None else b""
    supplied = [(key.lower().encode(), value.encode()) for key, value in headers]
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
    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    headers_out = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], headers_out, json.loads(raw or b"{}")


def test_ai_workspace_routes_fail_closed_without_auth_or_service(tmp_path, monkeypatch):
    monkeypatch.setenv("CICLO_AI_ENABLED", "1")
    db = DatabaseManager(str(tmp_path / "http.db"))
    service = AIWorkspaceService(db)
    app = Starlette(routes=AI_WORKSPACE_ROUTES, exception_handlers={AIWorkspaceApiError: ai_workspace_error_handler})
    app.state.ai_workspace_service_factory = lambda _: service
    status, _, payload = _asgi(app, "/api/rewrite/v1/ai/workspace/readiness")
    assert status == 503
    assert payload["code"] == "ai_workspace_error"


def test_ai_workspace_http_uses_injected_owner_and_idempotency(tmp_path, monkeypatch):
    for name, value in {
        "CICLO_AI_ENABLED": "1",
        "CICLO_AI_ENDPOINT": "https://provider.example.test/respond",
        "CICLO_AI_MODEL": "test-model",
        "CICLO_AI_PROVIDER_VERSION": "provider-v1",
        "CICLO_AI_CONTRACT_VERSION": "contract-v1",
    }.items():
        monkeypatch.setenv(name, value)
    db = DatabaseManager(str(tmp_path / "http-owner.db"))
    user = AuthService(db).register("http-ai@example.com", "CorrectHorse123", "HTTP AI", True)
    service = AIWorkspaceService(db)
    app = Starlette(routes=AI_WORKSPACE_ROUTES, exception_handlers={AIWorkspaceApiError: ai_workspace_error_handler})
    app.state.ai_workspace_authenticate = lambda _: user
    app.state.ai_workspace_authorize = lambda _request, owner_id: owner_id == user["id"]
    app.state.ai_workspace_service_factory = lambda _: service
    common = (("authorization", "Bearer test"),)
    status, _, rejected = _asgi(
        app,
        "/api/rewrite/v1/ai/workspace/sessions",
        method="POST",
        body={"title": "伪造来源", "citations": [{"source_public_id": "fake"}]},
        headers=common + (("idempotency-key", "http-ai-forged-1"),),
    )
    assert status == 400 and "引用" in rejected["error"]
    status, _, created = _asgi(
        app,
        "/api/rewrite/v1/ai/workspace/sessions",
        method="POST",
        body={"title": "AAPL 研究"},
        headers=common + (("idempotency-key", "http-ai-session-1"),),
    )
    assert status == 201
    status, _, replay = _asgi(
        app,
        "/api/rewrite/v1/ai/workspace/sessions",
        method="POST",
        body={"title": "AAPL 研究"},
        headers=common + (("idempotency-key", "http-ai-session-1"),),
    )
    assert status == 201 and replay["public_id"] == created["public_id"]
    status, _, listed = _asgi(app, "/api/rewrite/v1/ai/workspace/sessions", headers=common)
    assert status == 200 and listed["items"][0]["public_id"] == created["public_id"]
    monkeypatch.delenv("CICLO_AI_ENDPOINT", raising=False)
    status, _, blocked = _asgi(
        app,
        f"/api/rewrite/v1/ai/workspace/sessions/{created['public_id']}/messages",
        method="POST",
        body={"content": "请分析 AAPL"},
        headers=common + (("idempotency-key", "http-ai-message-1"),),
    )
    assert status == 503 and blocked["blocked"] is True


def test_ai_workspace_http_denies_authenticated_owner_without_entitlement(tmp_path, monkeypatch):
    monkeypatch.setenv("CICLO_AI_ENABLED", "1")
    db = DatabaseManager(str(tmp_path / "http-denied.db"))
    user = AuthService(db).register("http-ai-free@example.com", "CorrectHorse123", "HTTP AI Free", True)
    app = Starlette(routes=AI_WORKSPACE_ROUTES, exception_handlers={AIWorkspaceApiError: ai_workspace_error_handler})
    app.state.ai_workspace_authenticate = lambda _: user
    app.state.ai_workspace_authorize = lambda *_: False
    app.state.ai_workspace_service_factory = lambda _: AIWorkspaceService(db)
    status, _, payload = _asgi(
        app,
        "/api/rewrite/v1/ai/workspace/readiness",
        headers=(("authorization", "Bearer test"),),
    )
    assert status == 403
    assert "会员" in payload["error"]
