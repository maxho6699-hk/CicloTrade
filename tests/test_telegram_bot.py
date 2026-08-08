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
    send_telegram,
    telegram_bot_response,
    telegram_callback_allowed,
)


class _TelegramResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


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
    )

    assert captured[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "menu:settings"
    with pytest.raises(RuntimeError, match="callback_data"):
        send_telegram("menu", chat_id="123456789", buttons=[[{"text": "bad", "callback_data": "user:1"}]])
    with pytest.raises(RuntimeError, match="HTTPS"):
        send_telegram("menu", chat_id="123456789", buttons=[[{"text": "bad", "url": "https://user@example.com"}]])
    with pytest.raises(RuntimeError, match="長度"):
        send_telegram("🚀" * 2049, chat_id="123456789")


def test_configure_telegram_bot_installs_commands_and_menu(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    captured = []

    def capture(request, **_kwargs):
        captured.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _TelegramResponse()

    monkeypatch.setattr("notification.telegram_bot.urlopen", capture)
    configure_telegram_bot()

    assert captured[0][0].endswith("/setMyCommands")
    assert [item["command"] for item in captured[0][1]["commands"]] == ["start", "id", "settings", "help"]
    assert captured[1] == (captured[1][0], {"menu_button": {"type": "commands"}})
    assert captured[1][0].endswith("/setChatMenuButton")


def test_start_returns_chat_id_and_main_menu_without_binding(tmp_path):
    db = DatabaseManager(str(tmp_path / "start.db"))
    reply, keyboard = telegram_bot_response(db, "123456789", "/start")

    assert "Chat ID" in reply and "123456789" in reply
    assert any(button.get("callback_data") == "menu:settings" for row in keyboard for button in row)
    assert telegram_callback_allowed("notify:stock:toggle")
    assert not telegram_callback_allowed("notify:stock:on")


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
