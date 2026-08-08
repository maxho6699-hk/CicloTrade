"""Notification destinations must remain tenant-scoped and fail closed."""

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from notification.telegram_bot import (
    TelegramDeliveryUncertain,
    confirm_verification,
    entitled_user_target,
    issue_verification_token,
    send_telegram,
    telegram_configured,
    update_notification_preference,
    verified_user_target,
)
from core.user_settings import load_user_settings, merge_user_settings


def test_user_telegram_requires_consent_verification_and_enabled_event(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALERTS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999999")
    settings = {
        "tg_events": {"price_alert": True},
        "telegram": {"chat_id": "123456789", "consent": True, "verified": True},
    }

    assert verified_user_target(settings, "price_alert") == "123456789"
    assert telegram_configured("123456789") is True
    assert verified_user_target(settings, "order_filled") is None
    assert verified_user_target({**settings, "telegram": {**settings["telegram"], "verified": False}}) is None
    assert verified_user_target({**settings, "telegram": {**settings["telegram"], "consent": False}}) is None


def test_global_destination_is_never_used_as_a_user_destination(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999999")

    assert verified_user_target({"tg_events": {"price_alert": True}}, "price_alert") is None
    assert verified_user_target(
        {
            "tg_events": {"price_alert": True},
            "telegram": {"chat_id": "not-a-chat", "consent": True, "verified": True},
        },
        "price_alert",
    ) is None


def test_telegram_timeout_is_not_retried_automatically(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALERTS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    calls = []

    def timeout(*args, **kwargs):
        calls.append((args, kwargs))
        raise TimeoutError

    monkeypatch.setattr("notification.telegram_bot.urlopen", timeout)
    with pytest.raises(TelegramDeliveryUncertain, match="TimeoutError"):
        send_telegram("trade action", chat_id="123456789")
    assert len(calls) == 1


def test_telegram_global_switch_fails_closed_before_network(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALERTS_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999999")
    monkeypatch.setattr(
        "notification.telegram_bot.urlopen",
        lambda *args, **kwargs: pytest.fail("Telegram network called"),
    )

    assert telegram_configured("123456789") is False
    with pytest.raises(RuntimeError, match="平台停用"):
        send_telegram("trade action", chat_id="123456789")


def test_private_telegram_target_rechecks_plan_entitlement():
    settings = {
        "tg_events": {"price_alert": True, "option_signal": True},
        "telegram": {"chat_id": "123456789", "consent": True, "verified": True},
    }
    future = "2099-01-01T00:00:00+00:00"

    assert entitled_user_target({"plan_type": "标准版", "subscription_expire": future}, settings, "price_alert") is None
    assert entitled_user_target({"plan_type": "高级版", "subscription_expire": future}, settings, "price_alert") == "123456789"
    assert entitled_user_target({"plan_type": "高级版", "subscription_expire": future}, settings, "option_signal") is None
    assert entitled_user_target({"plan_type": "专业版", "subscription_expire": future}, settings, "option_signal") == "123456789"


def test_telegram_verification_requires_consent_and_is_one_time(tmp_path):
    db = DatabaseManager(str(tmp_path / "telegram.db"))
    user = AuthService(db).register("telegram@example.com", "CorrectHorse123", "Telegram", True)

    with pytest.raises(ValueError, match="同意"):
        issue_verification_token(db, user["id"], "123456789", False)
    first = issue_verification_token(db, user["id"], "123456789", True)
    second = issue_verification_token(db, user["id"], "123456789", True)
    with pytest.raises(ValueError, match="无效或已使用"):
        confirm_verification(db, user["id"], first)
    assert confirm_verification(db, user["id"], second) == "123456789"
    with pytest.raises(ValueError, match="无效或已使用"):
        confirm_verification(db, user["id"], second)


def test_membership_update_uses_system_entitlement_without_trade_event_toggle():
    settings = {"telegram": {"chat_id": "123456789", "consent": True, "verified": True}}
    future = "2099-01-01T00:00:00+00:00"

    assert entitled_user_target({"plan_type": "标准版", "subscription_expire": future}, settings, "membership_update") == "123456789"


def test_private_notify_command_syncs_website_settings_and_enforces_plan(tmp_path):
    db = DatabaseManager(str(tmp_path / "notify-settings.db"))
    user = AuthService(db).register("notify@example.com", "Correct1", "Notify", True)
    db.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire='2099-01-01T00:00:00+00:00' WHERE id=?",
        (user["id"],),
    )
    merge_user_settings(
        user["id"],
        {
            "telegram": {"chat_id": "123456789", "consent": True, "verified": True},
            "tg_events": {},
        },
        db,
    )

    reply = update_notification_preference(db, "123456789", "/notify stock on")
    blocked = update_notification_preference(db, "123456789", "/notify option on")

    assert "已開啟" in reply
    assert load_user_settings(user["id"], db)["tg_events"]["stock_signal"] is True
    assert "目前會員等級" in blocked
    assert load_user_settings(user["id"], db)["tg_events"].get("option_signal") is not True


def test_private_id_command_returns_binding_steps_without_account_lookup(tmp_path):
    db = DatabaseManager(str(tmp_path / "notify-id.db"))
    reply = update_notification_preference(db, "123456789", "/id")
    assert "Chat ID" in reply and "<code>123456789</code>" in reply
    assert "账户与安全" in reply
