"""Telegram service desk billing and entitlement security checks."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3

import pytest
from PIL import Image

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
from notification.telegram_security import consume_telegram_timeline_quota
from notification.telegram_outbox import (
    dispatch_telegram_service_outbox,
    enqueue_telegram_outbound,
)
from payment.order_service import OrderService


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "telegram-desk.db"))


@pytest.fixture(autouse=True)
def billing_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FPS_PAYMENT_INSTRUCTIONS", "FPS 123-456-789; use the order number as reference.")
    monkeypatch.setenv("ALIPAY_PAYMENT_INSTRUCTIONS", "Alipay merchant account; use the order number.")
    monkeypatch.setenv("WECHAT_PAYMENT_INSTRUCTIONS", "WeChat merchant account; use the order number.")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-longer-than-thirty-two-characters")
    monkeypatch.setenv("PAYMENT_PROOF_DIR", str(tmp_path / "payment-proofs"))
    monkeypatch.setenv("PAYMENT_RECEIVER_ASSET_DIR", str(tmp_path / "payment-receiver-assets"))

    def fake_download(file_id: str) -> bytes:
        color = tuple(hashlib.sha256(file_id.encode("utf-8")).digest()[:3])
        output = BytesIO()
        Image.new("RGB", (96, 96), color).save(output, format="JPEG")
        return output.getvalue()

    monkeypatch.setattr("notification.telegram_billing.download_telegram_file", fake_download)
    monkeypatch.setattr("notification.telegram_payment_receivers.download_telegram_file", fake_download)


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


def _bound_admin(db: DatabaseManager, name: str, chat_id: str) -> dict:
    user = _bound_user(db, name, chat_id)
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    AdminService(db)
    return user


def test_tg_finance_admin_sets_receiver_text_and_qr_while_non_admin_is_denied(db):
    _bound_user(db, "ordinary-config", "830001")
    denied = telegram_desk_response(db, "830001", "/payconfig")
    assert "后台权限" in denied.message

    admin = _bound_admin(db, "receiver-admin", "830002")
    home = telegram_desk_response(db, "830002", "/start")
    assert any(
        button.get("callback_data") == "desk:receiving"
        for row in home.keyboard for button in row
    )

    waiting_text = telegram_desk_response(db, "830002", "paycfg:settext:alipay", callback=True)
    assert "直接发送收款 ID" in waiting_text.message
    cancelled = telegram_desk_response(db, "830002", "paycfg:cancel", callback=True)
    assert "收款资料管理" in cancelled.message
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM telegram_payment_receiver_sessions WHERE chat_id='830002'"
    )["count"] == 0
    telegram_desk_response(db, "830002", "paycfg:settext:alipay", callback=True)
    saved_text = telegram_desk_response(db, "830002", "Alipay ID: maxho@example.com")
    assert "Alipay ID: maxho@example.com" in saved_text.message

    waiting_qr = telegram_desk_response(db, "830002", "paycfg:setqr:alipay", callback=True)
    assert "二维码图片" in waiting_qr.message
    saved_qr = telegram_desk_response(
        db,
        "830002",
        "photo",
        message_id=701,
        update_id=701,
        photo={"file_id": "alipay-receiver-file", "file_unique_id": "alipay-receiver-unique"},
    )
    assert "二维码：已设置" in saved_qr.message
    profile = db.fetch_one("SELECT * FROM manual_payment_receivers WHERE method='alipay'")
    assert profile["receiver_text"] == "Alipay ID: maxho@example.com"
    assert profile["qr_telegram_file_id"] == "alipay-receiver-file"
    assert profile["updated_by"] == admin["id"]
    assert db.fetch_one("SELECT COUNT(*) count FROM manual_payment_claims")["count"] == 0


def test_receiver_order_snapshot_survives_later_tg_admin_changes(db):
    _bound_admin(db, "snapshot-admin", "830003")
    telegram_desk_response(db, "830003", "paycfg:settext:fps", callback=True)
    telegram_desk_response(db, "830003", "FPS ID OLD")
    telegram_desk_response(db, "830003", "paycfg:setqr:fps", callback=True)
    telegram_desk_response(
        db,
        "830003",
        "photo",
        message_id=702,
        update_id=702,
        photo={"file_id": "fps-old-file", "file_unique_id": "fps-old-unique"},
    )

    _bound_user(db, "snapshot-buyer", "830004")
    first = telegram_desk_response(
        db, "830004", "buy:create:standard:monthly:fps", callback=True
    )
    first_order = db.fetch_one(
        "SELECT order_no FROM subscription_orders WHERE user_id=(SELECT user_id FROM telegram_accounts WHERE chat_id='830004')"
    )["order_no"]
    assert "FPS ID OLD" in first.message
    assert first.followups[0].photo_file_id == "fps-old-file"

    telegram_desk_response(db, "830003", "paycfg:settext:fps", callback=True)
    telegram_desk_response(db, "830003", "FPS ID NEW")
    telegram_desk_response(db, "830003", "paycfg:setqr:fps", callback=True)
    telegram_desk_response(
        db,
        "830003",
        "photo",
        message_id=703,
        update_id=703,
        photo={"file_id": "fps-new-file", "file_unique_id": "fps-new-unique"},
    )

    snapshot = db.fetch_one(
        "SELECT receiver_text,qr_telegram_file_id FROM subscription_order_payment_receivers WHERE order_no=?",
        (first_order,),
    )
    assert snapshot == {"receiver_text": "FPS ID OLD", "qr_telegram_file_id": "fps-old-file"}
    repeated = telegram_desk_response(
        db, "830004", "buy:create:standard:monthly:fps", callback=True
    )
    assert "FPS ID OLD" in repeated.message
    assert repeated.followups[0].photo_file_id == "fps-old-file"


def test_receiver_clear_fields_support_qr_only_text_only_and_disabled(db):
    _bound_admin(db, "clear-admin", "830005")
    telegram_desk_response(db, "830005", "paycfg:settext:wechat", callback=True)
    telegram_desk_response(db, "830005", "WeChat receiver ID")
    telegram_desk_response(db, "830005", "paycfg:setqr:wechat", callback=True)
    telegram_desk_response(
        db,
        "830005",
        "photo",
        message_id=704,
        update_id=704,
        photo={"file_id": "wechat-file", "file_unique_id": "wechat-unique"},
    )

    qr_only = telegram_desk_response(db, "830005", "paycfg:cleartext:wechat", callback=True)
    assert "ID / 说明：尚未设置" in qr_only.message and "二维码：已设置" in qr_only.message
    text_state = telegram_desk_response(db, "830005", "paycfg:settext:wechat", callback=True)
    assert "等待输入" in text_state.message
    telegram_desk_response(db, "830005", "WeChat restored ID")
    text_only = telegram_desk_response(db, "830005", "paycfg:clearqr:wechat", callback=True)
    assert "WeChat restored ID" in text_only.message and "二维码：尚未设置" in text_only.message
    disabled = telegram_desk_response(db, "830005", "paycfg:cleartext:wechat", callback=True)
    assert "状态：未启用" in disabled.message


def test_expired_or_revoked_admin_receiver_session_never_becomes_payment_claim(db):
    admin = _bound_admin(db, "revoked-config", "830006")
    buyer_order = OrderService(db).create_order(
        admin["id"], "标准版", "monthly", "fps", terms_accepted=True, source="web"
    )
    telegram_desk_response(db, "830006", "paycfg:setqr:fps", callback=True)
    db.execute("UPDATE users SET is_admin=0 WHERE id=?", (admin["id"],))
    stopped = telegram_desk_response(
        db,
        "830006",
        "photo",
        message_id=705,
        update_id=705,
        photo={"file_id": "must-not-claim", "file_unique_id": "must-not-claim-unique"},
    )
    assert "已没有财务权限" in stopped.message
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM manual_payment_claims WHERE order_no=?", (buyer_order["order_no"],)
    )["count"] == 0


def _create_manual_order(
    db: DatabaseManager,
    chat_id: str,
    slug: str = "standard",
    method: str = "fps",
) -> str:
    response = telegram_desk_response(
        db, chat_id, f"buy:create:{slug}:monthly:{method}", callback=True
    )
    assert "待付款" in response.message
    return db.fetch_one(
        "SELECT order_no FROM subscription_orders WHERE user_id=(SELECT user_id FROM telegram_accounts WHERE chat_id=?) "
        "ORDER BY id DESC LIMIT 1",
        (chat_id,),
    )["order_no"]


def _create_fps_order(db: DatabaseManager, chat_id: str, slug: str = "standard") -> str:
    return _create_manual_order(db, chat_id, slug, "fps")


def _submit_fps_claim(db: DatabaseManager, chat_id: str, order_no: str, update_id: int = 1) -> dict:
    before = db.fetch_one(
        "SELECT COUNT(*) count FROM manual_payment_claims WHERE order_no=?", (order_no,)
    )["count"]
    prompt = telegram_desk_response(
        db,
        chat_id,
        f"pay:claimed:{order_no}",
        callback=True,
        message_id=100 + update_id,
        update_id=update_id,
    )
    assert "上传付款凭证" in prompt.message
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM manual_payment_claims WHERE order_no=?", (order_no,)
    )["count"] == before
    response = telegram_desk_response(
        db,
        chat_id,
        f"付款订单 {order_no}",
        message_id=100 + update_id,
        update_id=update_id,
        photo={"file_id": f"proof-{update_id}", "file_unique_id": f"unique-{update_id}"},
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
    method_buttons = {
        button.get("callback_data")
        for row in cycle.keyboard
        for button in row
        if button.get("callback_data", "").startswith("buy:method:")
    }
    assert method_buttons == {
        "buy:method:standard:monthly:fps",
        "buy:method:standard:monthly:alipay",
        "buy:method:standard:monthly:wechat",
    }
    assert not any("paypal" in value or "paddle" in value for value in method_buttons)
    confirmation = telegram_desk_response(db, "810001", "buy:method:standard:monthly:fps", callback=True)
    assert any(button.get("callback_data") == "buy:create:standard:monthly:fps" for row in confirmation.keyboard for button in row)


@pytest.mark.parametrize(("method", "chat_id"), [("alipay", "820001"), ("wechat", "820002")])
def test_alipay_and_wechat_require_proof_and_wait_for_finance_review(db, method, chat_id):
    user = _bound_user(db, f"manual-{method}", chat_id)
    order_no = _create_manual_order(db, chat_id, method=method)

    prompt = telegram_desk_response(
        db, chat_id, f"pay:claimed:{order_no}",
        callback=True, message_id=90, update_id=90,
    )
    assert "上传付款凭证" in prompt.message
    assert db.fetch_one("SELECT COUNT(*) count FROM manual_payment_claims")["count"] == 0
    submitted = telegram_desk_response(
        db, chat_id, "photo", message_id=91, update_id=91,
        photo={"file_id": f"{method}-proof", "file_unique_id": f"{method}-unique"},
    )

    assert "付款申报已提交" in submitted.message
    assert db.fetch_one("SELECT pay_method,status FROM subscription_orders WHERE order_no=?", (order_no,)) == {
        "pay_method": method,
        "status": "pending",
    }
    assert db.fetch_one("SELECT plan_type FROM users WHERE id=?", (user["id"],))["plan_type"] == "免费版"


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


def test_photo_claim_stores_telegram_references_and_private_content_hash(db):
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
        """SELECT user_id,order_no,evidence_file_id,evidence_file_unique_id,evidence_message_id,
                  evidence_storage_key,evidence_sha256
           FROM manual_payment_claims WHERE order_no=?""",
        (order_no,),
    )
    assert claim["user_id"] == user["id"] and claim["order_no"] == order_no
    assert claim["evidence_file_id"] == "AgACAgQAAxkBAAI"
    assert claim["evidence_file_unique_id"] == "AQADunique"
    assert claim["evidence_message_id"] == "33"
    assert len(claim["evidence_storage_key"]) == 36
    assert len(claim["evidence_sha256"]) == 64
    columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(manual_payment_claims)")}
    assert not {"photo", "image", "file_path", "blob_data"} & columns


def test_web_order_can_submit_payment_proof_through_bound_telegram(db):
    user = _bound_user(db, "web-proof", "810030")
    order = OrderService(db).create_order(
        user["id"], "标准版", "monthly", "alipay", terms_accepted=True, source="web"
    )

    response = telegram_desk_response(
        db,
        "810030",
        f"付款订单 {order['order_no']}",
        message_id=34,
        update_id=3030,
        photo={"file_id": "web-proof-file", "file_unique_id": "web-proof-unique"},
    )

    assert "付款申报已提交" in response.message
    assert db.fetch_one(
        "SELECT order_no,status FROM manual_payment_claims WHERE order_no=?", (order["order_no"],)
    ) == {"order_no": order["order_no"], "status": "submitted"}


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
    assert "會員功能" in actions.message
    assert "即時正股建議" in actions.message
    assert any(button.get("callback_data") == "desk:plans" for row in actions.keyboard for button in row)
    assert db.fetch_one("SELECT plan_type FROM users WHERE id=?", (member["id"],))["plan_type"] == "免费版"


def test_compact_menu_routes_to_queries_membership_and_delayed_timeline(db, monkeypatch):
    member = _bound_user(db, "timeline-free", "810020")
    updated = (datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)).isoformat()
    cycle = {
        "sequence": 1,
        "instrument_key": "US:STOCK:AAPL",
        "instrument_type": "stock",
        "symbol": "AAPL",
        "currency": "USD",
        "direction": "long",
        "opened_at": updated,
        "updated_at": updated,
        "closed_at": updated,
        "opened_quantity": 10,
        "current_quantity": 0,
        "average_cost": 100,
        "realized_pnl": 50,
        "return": 0.05,
    }
    monkeypatch.setattr("notification.telegram_timeline._cycles", lambda *_args: ([cycle], None))

    queries = telegram_desk_response(db, "810020", "desk:queries", callback=True)
    assert "查詢中心" in queries.message
    assert any(button.get("callback_data") == "desk:timeline" for row in queries.keyboard for button in row)

    membership = telegram_desk_response(db, "810020", "desk:membership", callback=True)
    assert "免費會員" in membership.message and "免費頻道" in membership.message
    assert "正股建議延遲 1 小時" in membership.message

    home = telegram_desk_response(db, "810020", "/timeline")
    assert "交易時間線" in home.message
    assert any("專業會員" in button["text"] for row in home.keyboard for button in row)
    result = telegram_desk_response(db, "810020", "timeline:show:stock:10:0", callback=True)
    assert "正股建議" in result.message and "延遲 1 小時" in result.message
    assert "AAPL" in result.message and "交易回報" in result.message

    option = telegram_desk_response(db, "810020", "timeline:choose:option", callback=True)
    assert "需要升級會員" in option.message
    assert db.fetch_one("SELECT plan_type FROM users WHERE id=?", (member["id"],))["plan_type"] == "免费版"


def test_professional_timeline_allows_realtime_options_and_custom_limit(db, monkeypatch):
    member = _bound_user(db, "timeline-pro", "810021")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire='2099-01-01T00:00:00+00:00' WHERE id=?",
        (member["id"],),
    )
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    cycle = {
        "sequence": 1,
        "instrument_key": "US:OPTION:AAPL:20261218:CALL:200",
        "instrument_type": "option",
        "symbol": "AAPL",
        "currency": "USD",
        "option_expiry": "2026-12-18",
        "option_right": "CALL",
        "option_strike": 200,
        "direction": "long",
        "opened_at": now,
        "updated_at": now,
        "closed_at": None,
        "opened_quantity": 1,
        "current_quantity": 1,
        "average_cost": 5,
        "unrealized_pnl": 120,
    }
    monkeypatch.setattr("notification.telegram_timeline._cycles", lambda *_args: ([cycle], now))

    picker = telegram_desk_response(db, "810021", "timeline:choose:option", callback=True)
    assert "專業會員" in picker.message and "最多查詢 100 筆" in picker.message
    result = telegram_desk_response(db, "810021", "/timeline option 1")
    assert "期權建議" in result.message and "延遲" not in result.message
    assert "浮動損益" in result.message and "+$120.00" in result.message


def test_timeline_pagination_is_bounded_and_never_exceeds_telegram_limit(db, monkeypatch):
    member = _bound_user(db, "timeline-pages", "810022")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire='2099-01-01T00:00:00+00:00' WHERE id=?",
        (member["id"],),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    cycles = []
    for index in range(12):
        occurred = (now - timedelta(days=index + 1)).isoformat()
        cycles.append(
            {
                "sequence": index + 1,
                "instrument_key": f"US:STOCK:TEST{index}",
                "instrument_type": "stock",
                "symbol": f"TEST{index}",
                "currency": "USD",
                "direction": "long",
                "opened_at": occurred,
                "updated_at": occurred,
                "closed_at": occurred,
                "opened_quantity": 10,
                "current_quantity": 0,
                "average_cost": 100,
                "realized_pnl": 10 + index,
                "return": 0.01,
            }
        )
    monkeypatch.setattr("notification.telegram_timeline._cycles", lambda *_args: (cycles, None))

    first = telegram_desk_response(db, "810022", "timeline:show:stock:10:0", callback=True)
    second = telegram_desk_response(db, "810022", "timeline:show:stock:10:1", callback=True)
    assert first.message.count("<blockquote>") == 5
    assert second.message.count("<blockquote>") == 5
    assert any(button.get("callback_data") == "timeline:show:stock:10:1" for row in first.keyboard for button in row)
    assert any(button.get("callback_data") == "timeline:show:stock:10:0" for row in second.keyboard for button in row)
    assert len(first.message.encode("utf-16-le")) // 2 < 4096
    assert len(second.message.encode("utf-16-le")) // 2 < 4096


def test_timeline_has_dedicated_minute_and_daily_rate_limits(db):
    assert consume_telegram_timeline_quota(
        db, "810023", per_minute=2, per_day=20, count_daily=False
    )
    assert consume_telegram_timeline_quota(
        db, "810023", per_minute=2, per_day=20, count_daily=False
    )
    assert not consume_telegram_timeline_quota(
        db, "810023", per_minute=2, per_day=20, count_daily=False
    )

    assert consume_telegram_timeline_quota(
        db, "810024", per_minute=10, per_day=2, count_daily=True
    )
    assert consume_telegram_timeline_quota(
        db, "810024", per_minute=10, per_day=2, count_daily=True
    )
    assert not consume_telegram_timeline_quota(
        db, "810024", per_minute=10, per_day=2, count_daily=True
    )


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
