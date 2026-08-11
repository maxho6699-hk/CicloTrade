from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from core.auth import AuthService
from core.database import DatabaseManager
from core.user_settings import merge_user_settings
from notification.telegram_bot import verified_account_for_chat
from notification.telegram_desk import telegram_desk_response


def test_telegram_can_only_pause_new_opening_and_is_idempotently_audited(tmp_path):
    db = DatabaseManager(str(tmp_path / "telegram-pause.db"))
    user = AuthService(db).register("pause@example.com", "CorrectHorse123", "Pause", True)
    merge_user_settings(
        user["id"],
        {
            "telegram": {"chat_id": "123456789", "consent": True, "verified": True},
            "tg_events": {},
        },
        db,
    )

    response = telegram_desk_response(db, "123456789", "desk:pause_opening", callback=True)
    assert "新开仓已暂停" in response.message
    assert "TG 不提供恢复按钮" in response.message
    assert db.fetch_one("SELECT opening_paused FROM user_controls WHERE user_id=?", (user["id"],))["opening_paused"] == 1
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM user_action_logs WHERE user_id=? AND action_type='PAUSE_OPENING'",
        (user["id"],),
    )["count"] == 1
    assert all("resume" not in button.get("callback_data", "") for row in response.keyboard for button in row)
    assert any(button.get("url", "").endswith("/account") for row in response.keyboard for button in row)

    telegram_desk_response(db, "123456789", "desk:pause_opening", callback=True)
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM user_action_logs WHERE user_id=? AND action_type='PAUSE_OPENING'",
        (user["id"],),
    )["count"] == 1


def test_concurrent_pause_callbacks_write_one_audit_record(tmp_path):
    db_path = tmp_path / "telegram-pause-concurrent.db"
    db = DatabaseManager(str(db_path))
    user = AuthService(db).register("pause-concurrent@example.com", "CorrectHorse123", "Pause", True)
    merge_user_settings(
        user["id"],
        {
            "telegram": {"chat_id": "123456789", "consent": True, "verified": True},
            "tg_events": {},
        },
        db,
    )
    assert verified_account_for_chat(db, "123456789")["id"] == user["id"]
    databases = (DatabaseManager(str(db_path)), DatabaseManager(str(db_path)))
    gate = Barrier(2)

    def pause(database):
        gate.wait(timeout=5)
        return telegram_desk_response(
            database,
            "123456789",
            "desk:pause_opening",
            callback=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(pause, databases))

    assert all("新开仓已暂停" in response.message for response in responses), [
        response.message for response in responses
    ]
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM user_action_logs WHERE user_id=? AND action_type='PAUSE_OPENING'",
        (user["id"],),
    )["count"] == 1
    assert all(
        "resume" not in button.get("callback_data", "")
        for response in responses
        for row in response.keyboard
        for button in row
    )
    assert all(
        any(button.get("url", "").endswith("/account") for row in response.keyboard for button in row)
        for response in responses
    )


def test_concurrent_legacy_binding_migration_returns_authoritative_account(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "telegram-binding-concurrent.db"))
    user = AuthService(db).register("binding-concurrent@example.com", "CorrectHorse123", "Binding", True)
    merge_user_settings(
        user["id"],
        {
            "telegram": {"chat_id": "987654321", "consent": True, "verified": True},
            "tg_events": {},
        },
        db,
    )
    gate = Barrier(2)
    original_fetch_all = db.fetch_all

    def synchronized_legacy_lookup(sql, params=()):
        rows = original_fetch_all(sql, params)
        if "FROM users u JOIN user_settings s" in sql:
            gate.wait(timeout=5)
        return rows

    monkeypatch.setattr(db, "fetch_all", synchronized_legacy_lookup)
    with ThreadPoolExecutor(max_workers=2) as pool:
        accounts = list(pool.map(lambda _: verified_account_for_chat(db, "987654321"), range(2)))

    assert all(account and account["id"] == user["id"] for account in accounts)
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM telegram_accounts WHERE user_id=? AND chat_id=?",
        (user["id"], "987654321"),
    )["count"] == 1
