"""Contract checks for legacy surfaces kept closed during the public soft launch."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import asgi_app
from core.admin_service import AdminService
from core.auth import AuthService
from core.database import DatabaseManager


class _PostRequest:
    method = "POST"
    headers: dict[str, str] = {}

    def __init__(self, payload: dict | None = None):
        self.payload = payload or {"symbol": "AAPL", "side": "BUY", "quantity": 1, "price": 100}

    async def stream(self):
        yield json.dumps(self.payload).encode("utf-8")


@pytest.mark.parametrize("enabled", [None, "false"])
def test_legacy_order_post_is_closed_before_auth_database_or_order_manager(monkeypatch, enabled):
    if enabled is None:
        monkeypatch.delenv("TRADEAI_LEGACY_ORDER_WRITE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("TRADEAI_LEGACY_ORDER_WRITE_ENABLED", enabled)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("legacy POST must stop before this dependency")

    monkeypatch.setattr(asgi_app, "_api_user", unexpected)
    monkeypatch.setattr(asgi_app, "_consume_api_quota", unexpected)
    monkeypatch.setattr(asgi_app, "get_database", unexpected)
    monkeypatch.setattr(asgi_app, "OrderManager", unexpected)

    with pytest.raises(asgi_app.ApiError, match="Legacy order writes are disabled") as exc:
        asyncio.run(asgi_app.api_orders(_PostRequest()))
    assert exc.value.status == 503


def test_legacy_order_post_requires_explicit_true_before_existing_validation(monkeypatch):
    monkeypatch.setenv("TRADEAI_LEGACY_ORDER_WRITE_ENABLED", "true")
    calls: list[str] = []
    monkeypatch.setattr(asgi_app, "_api_user", lambda _request: calls.append("auth") or {"id": 7})
    monkeypatch.setattr(asgi_app, "_consume_api_quota", lambda _user: calls.append("quota"))

    with pytest.raises(asgi_app.ApiError, match="side 必须是 BUY 或 SELL"):
        asyncio.run(asgi_app.api_orders(_PostRequest({"symbol": "AAPL", "side": "HOLD", "quantity": 1, "price": 100})))
    assert calls == ["auth", "quota"]


def test_legacy_trading_ui_is_unreachable_by_default(monkeypatch):
    monkeypatch.delenv("TRADEAI_LEGACY_TRADING_UI_ENABLED", raising=False)
    seen: list[dict] = []
    sent: list[dict] = []

    async def downstream(scope, _receive, _send):
        seen.append(scope)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(
        asgi_app.StreamlitDeepLinkMiddleware(downstream)(
            {"type": "http", "method": "GET", "path": "/trading/", "query_string": b"legacy=1", "headers": []},
            receive,
            send,
        )
    )
    assert not seen
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 404

    websocket_messages: list[dict] = []

    async def websocket_send(message):
        websocket_messages.append(message)

    asyncio.run(
        asgi_app.StreamlitDeepLinkMiddleware(downstream)(
            {"type": "websocket", "path": "/trading", "query_string": b"", "headers": []},
            receive,
            websocket_send,
        )
    )
    assert not seen
    assert websocket_messages == [{"type": "websocket.close", "code": 1008}]


def test_admin_initialization_defaults_fail_closed_without_overwriting_existing_values(tmp_path):
    db = DatabaseManager(str(tmp_path / "soft-launch.db"))
    service = AdminService(db)

    assert service.control_enabled("recommendations_published", True) is False
    assert service.control_enabled("opening_paused", False) is True
    assert service.control_enabled("user_auto_trading_enabled", True) is False

    admin = AuthService(db).register("admin@example.com", "CorrectHorse123", "Admin", True)
    db.execute("UPDATE platform_controls SET control_value='1', updated_by=? WHERE control_key='recommendations_published'", (admin["id"],))
    db.execute("UPDATE platform_controls SET control_value='0', updated_by=? WHERE control_key='opening_paused'", (admin["id"],))
    db.execute("UPDATE platform_controls SET control_value='1', updated_by=? WHERE control_key='user_auto_trading_enabled'", (admin["id"],))
    restarted = AdminService(db)

    assert restarted.control_enabled("recommendations_published", False) is True
    assert restarted.control_enabled("opening_paused", True) is False
    assert restarted.control_enabled("user_auto_trading_enabled", False) is True


def test_nginx_soft_launch_contract_is_static_and_preserves_rewrite_routing():
    config = Path("ops/nginx-ciclotrade.conf").read_text(encoding="utf-8")

    assert "location = /api/v1/orders" in config
    assert "if ($request_method != GET)" in config
    assert "location = /trading" in config
    assert "location = /trading/" in config
    assert config.index("location ^~ /api/rewrite/") < config.index("\n    location ^~ /api/ {")
    assert "|feedback|admin|login|membership|promotion|mystic|opportunities|lab|earnings)/?$" in config
    assert "location ^~ /media/" in config
    assert "location = /theme-init.js" in config
    assert "location = /admin {" not in config
