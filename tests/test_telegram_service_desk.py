"""Telegram service desk billing and entitlement security checks."""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
from pathlib import Path
import sqlite3

import pytest

from core.admin_service import AdminService
from core.auth import AuthService
from core.database import DatabaseManager
from notification.telegram_desk import (
    claim_telegram_callback,
    claim_telegram_update,
    consume_telegram_quota,
    telegram_desk_response,
)
from notification.telegram_models import TelegramOutbound
from notification.telegram_outbox import (
    dispatch_telegram_service_outbox,
    enqueue_telegram_outbound,
)


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "telegram-desk.db"))


@pytest.fixture(autouse=True)
def billing_environment(monkeypatch):
    monkeypatch.setenv("FPS_PAYMENT_INSTRUCTIONS", "FPS 123-456-789; use the order number as reference.")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-longer-than-thirty-two-characters")


def _user(db: DatabaseManager, name: str) -> dict:
    return AuthService(db).register(f"{name}@example.com", "CorrectHorse123", name.title(), True)


def _bind(db: DatabaseManager, user: dict, chat_id: str) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO telegram_accounts
           (user_id,chat_id,is_active,revoked_at,created_at,updated_at)
           VALUES (?,?,1,NULL,?,?)""",
        (user["id"], chat_id, now, now),
    )


def _bound_user(db: DatabaseManager, name: str, chat_id: str) -> dict:
    user = _user(db, name)
    _bind(db, user, chat_id)
    return user


def _create_fps_order(db: DatabaseManager, chat_id: str, slug: str = "standard") -> str:
    response = telegram_desk_response(
        db, chat_id, f"buy:create:{slug}:monthly:fps", callback=True
    )
    assert "FPS 待付款" in response.message
    return db.fetch_one(
        "SELECT order_no FROM subscription_orders WHERE user_id=(SELECT user_id FROM telegram_accounts WHERE chat_id=?) "
        "ORDER BY id DESC LIMIT 1",
        (chat_id,),
    )["order_no"]


def _submit_fps_claim(db: DatabaseManager, chat_id: str, order_no: str, update_id: int = 1) -> dict:
    response = telegram_desk_response(
        db,
        chat_id,
        f"pay:claimed:{order_no}",
        callback=True,
        message_id=100 + update_id,
        update_id=update_id,
    )
    assert "付款申报已提交" in response.message
    return db.fetch_one(
        "SELECT * FROM manual_payment_claims WHERE order_no=? ORDER BY id DESC LIMIT 1",
        (order_no,),
    )


def test_service_desk_exposes_plan_cycle_and_available_payment_method(db):
    user = _bound_user(db, "plan-picker", "810001")

    plans = telegram_desk_response(db, "810001", "desk:plans", callback=True)
    plan_buttons = [button["callback_data"] for row in plans.keyboard for button in row if "callback_data" in button]
    assert user["email"] not in plans.message
    assert {"buy:plan:standard", "buy:plan:advanced", "buy:plan:professional", "buy:plan:custom"} <= set(plan_buttons)

    detail = telegram_desk_response(db, "810001", "buy:plan:standard", callback=True)
    assert any(button.get("callback_data") == "buy:cycle:standard:monthly" for row in detail.keyboard for button in row)
    cycle = telegram_desk_response(db, "810001", "buy:cycle:standard:monthly", callback=True)
    assert any(button.get("callback_data") == "buy:method:standard:monthly:fps" for row in cycle.keyboard for button in row)
    confirmation = telegram_desk_response(db, "810001", "buy:method:standard:monthly:fps", callback=True)
    assert any(button.get("callback_data") == "buy:create:standard:monthly:fps" for row in confirmation.keyboard for button in row)


def test_fps_creation_is_idempotent_and_manual_claim_does_not_activate(db):
    user = _bound_user(db, "fps-idempotent", "810002")
    first = _create_fps_order(db, "810002")
    second = _create_fps_order(db, "810002")
    assert first == second
    assert db.fetch_one("SELECT COUNT(*) count FROM subscription_orders WHERE user_id=?", (user["id"],))["count"] == 1

    _submit_fps_claim(db, "810002", first)
    order = db.fetch_one("SELECT status FROM subscription_orders WHERE order_no=?", (first,))
    member = db.fetch_one("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],))
    assert order["status"] == "pending"
    assert member == {"plan_type": "免费版", "subscription_expire": None}


def test_photo_claim_stores_telegram_file_references_only(db):
    user = _bound_user(db, "photo-proof", "810003")
    order_no = _create_fps_order(db, "810003")
    response = telegram_desk_response(
        db,
        "810003",
        "photo",
        message_id=33,
        update_id=3003,
        photo={"file_id": "AgACAgQAAxkBAAI", "file_unique_id": "AQADunique"},
    )
    assert "付款申报已提交" in response.message
    claim = db.fetch_one(
        "SELECT user_id,order_no,evidence_file_id,evidence_file_unique_id,evidence_message_id FROM manual_payment_claims WHERE order_no=?",
        (order_no,),
    )
    assert claim == {
        "user_id": user["id"], "order_no": order_no,
        "evidence_file_id": "AgACAgQAAxkBAAI", "evidence_file_unique_id": "AQADunique",
        "evidence_message_id": "33",
    }
    columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(manual_payment_claims)")}
    assert not {"photo", "image", "file_path", "blob_data"} & columns


def test_finance_admin_approves_rejects_and_repeated_approval_is_safe(db):
    customer = _bound_user(db, "review-customer", "810004")
    admin = _bound_user(db, "review-admin", "810005")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    AdminService(db)

    order_no = _create_fps_order(db, "810004")
    _submit_fps_claim(db, "810004", order_no, 4)
    claim = db.fetch_one("SELECT id,attempt FROM manual_payment_claims WHERE order_no=?", (order_no,))
    prompt = telegram_desk_response(
        db,
        "810005",
        f"admin:approve:{claim['id']}:{claim['attempt']}",
        callback=True,
    )
    assert "/approve" in prompt.message
    assert db.fetch_one("SELECT status FROM subscription_orders WHERE order_no=?", (order_no,))["status"] == "pending"
    approved = telegram_desk_response(
        db,
        "810005",
        f"/approve {claim['id']} {claim['attempt']} FPS-SETTLEMENT-0001",
    )
    assert "已核对到账并开通会员" in approved.message
    expiry = db.fetch_one("SELECT subscription_expire FROM users WHERE id=?", (customer["id"],))["subscription_expire"]
    repeated = telegram_desk_response(
        db,
        "810005",
        f"/approve {claim['id']} {claim['attempt']} FPS-SETTLEMENT-0001",
    )
    assert "已核对到账并开通会员" in repeated.message
    assert db.fetch_one("SELECT subscription_expire FROM users WHERE id=?", (customer["id"],))["subscription_expire"] == expiry
    assert db.fetch_one("SELECT status FROM subscription_orders WHERE order_no=?", (order_no,))["status"] == "paid"

    rejected_order = _create_fps_order(db, "810004", "advanced")
    _submit_fps_claim(db, "810004", rejected_order, 5)
    rejected_claim = db.fetch_one("SELECT id,attempt FROM manual_payment_claims WHERE order_no=?", (rejected_order,))
    rejected = telegram_desk_response(
        db,
        "810005",
        f"admin:reject:{rejected_claim['id']}:{rejected_claim['attempt']}",
        callback=True,
    )
    assert "已驳回，未开通会员" in rejected.message
    assert db.fetch_one("SELECT status FROM manual_payment_claims WHERE order_no=?", (rejected_order,))["status"] == "rejected"
    assert db.fetch_one("SELECT status FROM subscription_orders WHERE order_no=?", (rejected_order,))["status"] == "pending"


def test_forged_admin_review_and_cross_user_claim_are_denied(db):
    owner = _bound_user(db, "order-owner", "810006")
    stranger = _bound_user(db, "order-stranger", "810007")
    order_no = _create_fps_order(db, "810006")
    _submit_fps_claim(db, "810006", order_no, 6)

    claim = db.fetch_one("SELECT id,attempt FROM manual_payment_claims WHERE order_no=?", (order_no,))
    forged = telegram_desk_response(
        db,
        "810007",
        f"admin:approve:{claim['id']}:{claim['attempt']}",
        callback=True,
    )
    assert "仅限已验证的财务管理员" in forged.message
    assert db.fetch_one("SELECT status FROM manual_payment_claims WHERE order_no=?", (order_no,))["status"] == "submitted"

    cross_user = telegram_desk_response(
        db, "810007", f"pay:claimed:{order_no}", callback=True, message_id=17, update_id=7007
    )
    assert "不属于当前用户" in cross_user.message
    assert db.fetch_one("SELECT COUNT(*) count FROM manual_payment_claims WHERE order_no=?", (order_no,))["count"] == 1
    assert owner["id"] != stranger["id"]


def test_callback_deduplication_and_service_desk_quota(db):
    assert claim_telegram_callback(db, "callback-unique", "810008") is True
    assert claim_telegram_callback(db, "callback-unique", "810008") is False
    assert db.fetch_one("SELECT COUNT(*) count FROM telegram_callback_receipts")["count"] == 1

    assert [consume_telegram_quota(db, "810008", "buy:create:standard:monthly:fps") for _ in range(4)] == [True] * 4
    assert consume_telegram_quota(db, "810008", "buy:create:standard:monthly:fps") is False


def test_unbound_checkout_is_blocked_and_free_member_sees_upgrade_path(db):
    unbound = telegram_desk_response(db, "810009", "buy:create:standard:monthly:fps", callback=True)
    assert "需要先绑定账户" in unbound.message
    assert db.fetch_one("SELECT COUNT(*) count FROM subscription_orders")["count"] == 0

    member = _bound_user(db, "free-member", "810010")
    actions = telegram_desk_response(db, "810010", "desk:actions", callback=True)
    assert "会员功能" in actions.message
    assert any(button.get("callback_data") == "desk:plans" for row in actions.keyboard for button in row)
    assert db.fetch_one("SELECT plan_type FROM users WHERE id=?", (member["id"],))["plan_type"] == "免费版"


def test_old_admin_button_cannot_review_a_resubmitted_claim(db):
    customer = _bound_user(db, "stale-customer", "810011")
    admin = _bound_user(db, "stale-admin", "810012")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    AdminService(db)
    order_no = _create_fps_order(db, "810011")
    first = _submit_fps_claim(db, "810011", order_no, 11)
    telegram_desk_response(
        db,
        "810012",
        f"admin:reject:{first['id']}:{first['attempt']}",
        callback=True,
    )
    second = _submit_fps_claim(db, "810011", order_no, 12)
    stale = telegram_desk_response(
        db,
        "810012",
        f"admin:approve:{first['id']}:{first['attempt']}",
        callback=True,
    )
    assert "已经处理" in stale.message
    assert db.fetch_one("SELECT status FROM manual_payment_claims WHERE id=?", (second["id"],))["status"] == "submitted"
    assert db.fetch_one("SELECT plan_type FROM users WHERE id=?", (customer["id"],))["plan_type"] == "免费版"


def test_multiple_pending_orders_require_photo_caption_order_number(db):
    _bound_user(db, "multi-photo", "810013")
    first = _create_fps_order(db, "810013", "standard")
    second = _create_fps_order(db, "810013", "advanced")
    ambiguous = telegram_desk_response(
        db,
        "810013",
        "photo",
        message_id=31,
        update_id=1301,
        photo={"file_id": "photo-a", "file_unique_id": "unique-a"},
    )
    assert "填写完整订单号" in ambiguous.message
    assert db.fetch_one("SELECT COUNT(*) count FROM manual_payment_claims")["count"] == 0
    matched = telegram_desk_response(
        db,
        "810013",
        f"付款订单 {second}",
        message_id=32,
        update_id=1302,
        photo={"file_id": "photo-b", "file_unique_id": "unique-b"},
    )
    assert "付款申报已提交" in matched.message
    assert db.fetch_one("SELECT order_no FROM manual_payment_claims")["order_no"] == second
    assert first != second


def test_changing_text_does_not_bypass_quota_and_updates_are_deduplicated(db):
    allowed = [consume_telegram_quota(db, "810014", f"random text {index}") for index in range(12)]
    assert allowed == [True] * 12
    assert consume_telegram_quota(db, "810014", "entirely different content") is False
    assert claim_telegram_update(db, 1401, "810014", "/start") is True
    assert claim_telegram_update(db, 1401, "810014", "/start") is False


def test_service_outbox_retries_definite_failure_and_is_idempotent(db, monkeypatch):
    item = TelegramOutbound("810015", "<b>payment review</b>")
    assert enqueue_telegram_outbound(db, item, "claim:15:admin") is True
    assert enqueue_telegram_outbound(db, item, "claim:15:admin") is False
    monkeypatch.setattr(
        "notification.telegram_outbox.send_telegram",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary network failure")),
    )
    assert dispatch_telegram_service_outbox(db) == 0
    assert db.fetch_one("SELECT status FROM telegram_service_outbox")["status"] == "failed"
    db.execute("UPDATE telegram_service_outbox SET next_attempt_at='2000-01-01T00:00:00+00:00'")
    sent = []
    monkeypatch.setattr(
        "notification.telegram_outbox.send_telegram",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    assert dispatch_telegram_service_outbox(db) == 1
    assert len(sent) == 1
    assert db.fetch_one("SELECT status FROM telegram_service_outbox")["status"] == "sent"


def test_legacy_binding_migration_skips_duplicate_chat_ids(tmp_path):
    path = tmp_path / "legacy-binding.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY);
        CREATE TABLE subscription_orders (
          order_no TEXT PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT, created_at TEXT
        );
        CREATE TABLE user_settings (user_id INTEGER PRIMARY KEY, settings_json TEXT, updated_at TEXT);
        INSERT INTO users(id) VALUES (1),(2);
        INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES
          (1,'{"telegram":{"chat_id":"998877","verified":true,"consent":true}}','2026-01-01'),
          (2,'{"telegram":{"chat_id":"998877","verified":true,"consent":true}}','2026-01-01');
        """
    )
    migration = Path("migrations/0007_telegram_billing_desk.sql").read_text(encoding="utf-8")
    conn.executescript(migration)
    assert conn.execute("SELECT COUNT(*) FROM telegram_accounts").fetchone()[0] == 0
    conn.close()
