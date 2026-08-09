"""Telegram Bot menus, API payload validation, and private webhook behaviour."""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

import asgi_app
from core.auth import AuthService
from core.database import DatabaseManager
from core.user_settings import merge_user_settings
from notification.telegram_bot import (
    configure_telegram_bot,
    download_telegram_file,
    send_telegram,
    send_telegram_photo,
    telegram_bot_response,
    telegram_callback_allowed,
)


class _TelegramResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _TelegramFileResponse(_TelegramResponse):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self.body = body
        self.headers = headers or {}

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]


def _telegram_request(payload: dict, secret: str = "webhook-secret") -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-telegram-bot-api-secret-token", secret.encode("utf-8")),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        },
        receive,
    )


def test_send_telegram_accepts_native_keyboard_and_rejects_untrusted_actions(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALERTS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    captured = []

    def capture(request, **_kwargs):
        captured.append(json.loads(request.data.decode("utf-8")))
        return _TelegramResponse()

    monkeypatch.setattr("notification.telegram_bot.urlopen", capture)
    send_telegram(
        "menu",
        chat_id="123456789",
        buttons=[[{"text": "設定", "callback_data": "menu:settings"}, {"text": "網站", "url": "https://ciclotrade.com/settings"}]],
        protect_content=True,
    )

    assert captured[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "menu:settings"
    assert captured[0]["protect_content"] is True
    with pytest.raises(RuntimeError, match="callback_data"):
        send_telegram("menu", chat_id="123456789", buttons=[[{"text": "bad", "callback_data": "user:1"}]])
    with pytest.raises(RuntimeError, match="HTTPS"):
        send_telegram("menu", chat_id="123456789", buttons=[[{"text": "bad", "url": "https://user@example.com"}]])
    with pytest.raises(RuntimeError, match="長度"):
        send_telegram("🚀" * 2049, chat_id="123456789")


def test_configure_telegram_bot_installs_commands_and_menu(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook_secret-123")
    monkeypatch.setenv("APP_BASE_URL", "https://ciclotrade.com")
    captured = []

    def capture(request, **_kwargs):
        captured.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _TelegramResponse()

    monkeypatch.setattr("notification.telegram_bot.urlopen", capture)
    configure_telegram_bot()

    assert captured[0][0].endswith("/setMyCommands")
    assert [item["command"] for item in captured[0][1]["commands"]] == [
        "start", "timeline", "plans", "orders", "id", "settings", "payconfig", "help",
    ]
    assert captured[1] == (captured[1][0], {"menu_button": {"type": "commands"}})
    assert captured[1][0].endswith("/setChatMenuButton")
    assert captured[2][0].endswith("/setWebhook")
    assert captured[2][1] == {
        "url": "https://ciclotrade.com/webhooks/telegram",
        "secret_token": "webhook_secret-123",
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    }


def test_start_returns_chat_id_and_main_menu_without_binding(tmp_path):
    db = DatabaseManager(str(tmp_path / "start.db"))
    reply, keyboard = telegram_bot_response(db, "123456789", "/start")

    assert "Chat ID" in reply and "123456789" in reply
    assert any(button.get("callback_data") == "desk:settings" for row in keyboard for button in row)
    assert telegram_callback_allowed("notify:stock:toggle")
    assert telegram_callback_allowed("timeline:show:stock:30:2")
    assert telegram_callback_allowed("timeline:pnl:7d:0")
    assert not telegram_callback_allowed("timeline:show:stock:1000:0")
    assert not telegram_callback_allowed("timeline:show:crypto:10:0")
    assert not telegram_callback_allowed("timeline:pnl:2026-08-09:0")
    assert not telegram_callback_allowed("notify:stock:on")
    assert telegram_callback_allowed("buy:create:advanced:yearly:alipay")
    assert telegram_callback_allowed("buy:method:professional:monthly:wechat")
    assert not telegram_callback_allowed("buy:create:advanced:yearly:paypal")
    assert telegram_callback_allowed("paycfg:setqr:wechat")
    assert telegram_callback_allowed("paycfg:home")
    assert not telegram_callback_allowed("paycfg:setqr:paypal")


def test_send_telegram_photo_uses_validated_same_bot_file_id(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALERTS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    captured = []

    def capture(request, **_kwargs):
        captured.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _TelegramResponse()

    monkeypatch.setattr("notification.telegram_bot.urlopen", capture)
    send_telegram_photo("FPS 收款二维码", "telegram-qr-file-id", "123456789")
    assert captured[0][0].endswith("/sendPhoto")
    assert captured[0][1]["photo"] == "telegram-qr-file-id"
    assert captured[0][1]["protect_content"] is True
    with pytest.raises(RuntimeError, match="图片标识"):
        send_telegram_photo("QR", "bad file id", "123456789")


def test_download_telegram_file_enforces_metadata_and_stream_limits(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    calls = []

    def oversized(request, **_kwargs):
        calls.append(request.full_url)
        return _TelegramFileResponse(json.dumps({
            "ok": True,
            "result": {"file_path": "photos/proof.jpg", "file_size": 4 * 1024 * 1024 + 1},
        }).encode("utf-8"))

    monkeypatch.setattr("notification.telegram_bot.urlopen", oversized)
    with pytest.raises(ValueError, match="小于 4 MB"):
        download_telegram_file("telegram-file")
    assert len(calls) == 1 and calls[0].endswith("/getFile")

    responses = iter((
        _TelegramFileResponse(json.dumps({
            "ok": True,
            "result": {"file_path": "photos/proof.jpg", "file_size": 5},
        }).encode("utf-8")),
        _TelegramFileResponse(b"proof", {"Content-Length": "5"}),
    ))
    monkeypatch.setattr("notification.telegram_bot.urlopen", lambda *_args, **_kwargs: next(responses))
    assert download_telegram_file("telegram-file") == b"proof"


def test_telegram_webhook_replies_to_private_start_and_ignores_group(monkeypatch, tmp_path):
    db = DatabaseManager(str(tmp_path / "webhook-start.db"))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setattr(asgi_app, "get_database", lambda: db)
    sent = []
    monkeypatch.setattr(asgi_app, "send_telegram", lambda *args, **kwargs: sent.append((args, kwargs)))

    response = asyncio.run(
        asgi_app.telegram_webhook(
            _telegram_request({"message": {"message_id": 7, "text": "/start", "chat": {"id": 123456789, "type": "private"}}})
        )
    )
    assert response.status_code == 200
    assert sent[0][0][1] == "123456789" and sent[0][1]["buttons"]
    assert sent[0][1]["protect_content"] is True

    asyncio.run(
        asgi_app.telegram_webhook(
            _telegram_request({"message": {"message_id": 8, "text": "/settings", "chat": {"id": -100123, "type": "group"}}})
        )
    )
    assert len(sent) == 1


def test_telegram_webhook_rejects_invalid_secret(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected-secret")

    with pytest.raises(asgi_app.ApiError) as exc:
        asyncio.run(
            asgi_app.telegram_webhook(
                _telegram_request(
                    {"message": {"message_id": 7, "text": "/start", "chat": {"id": 123456789, "type": "private"}}},
                    secret="wrong-secret",
                )
            )
        )

    assert exc.value.status == 401


def test_telegram_webhook_answers_and_edits_private_callback(monkeypatch, tmp_path):
    db = DatabaseManager(str(tmp_path / "webhook-callback.db"))
    user = AuthService(db).register("callback@example.com", "CorrectHorse123", "Callback", True)
    db.execute("UPDATE users SET plan_type='高级版',subscription_expire='2099-01-01T00:00:00+00:00' WHERE id=?", (user["id"],))
    merge_user_settings(
        user["id"],
        {"telegram": {"chat_id": "123456789", "consent": True, "verified": True}, "tg_events": {}},
        db,
    )
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setattr(asgi_app, "get_database", lambda: db)
    answered, edited = [], []
    monkeypatch.setattr(asgi_app, "answer_telegram_callback", lambda value: answered.append(value))
    monkeypatch.setattr(asgi_app, "edit_telegram_message", lambda *args, **kwargs: edited.append((args, kwargs)))

    asyncio.run(
        asgi_app.telegram_webhook(
            _telegram_request(
                {"callback_query": {"id": "callback-1", "data": "notify:stock:toggle", "from": {"id": 123456789}, "message": {"message_id": 9, "chat": {"id": 123456789, "type": "private"}}}}
            )
        )
    )
    assert answered == ["callback-1"]
    assert edited[0][0][:3] == ("123456789", 9, edited[0][0][2])
    assert "已開啟" in edited[0][0][2]


def test_telegram_webhook_rejects_callback_from_another_user(monkeypatch, tmp_path):
    db = DatabaseManager(str(tmp_path / "webhook-callback-owner.db"))
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setattr(asgi_app, "get_database", lambda: db)
    answered = []
    monkeypatch.setattr(asgi_app, "answer_telegram_callback", lambda value: answered.append(value))

    response = asyncio.run(
        asgi_app.telegram_webhook(
            _telegram_request(
                {"callback_query": {"id": "callback-2", "data": "menu:home", "from": {"id": 987654321}, "message": {"message_id": 10, "chat": {"id": 123456789, "type": "private"}}}}
            )
        )
    )

    assert response.status_code == 200
    assert not answered
