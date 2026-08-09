"""Focused payment, callback, and order API security checks."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from core.compat import UTC
from concurrent.futures import ThreadPoolExecutor
import json
from io import BytesIO
import sqlite3
import threading

import pytest
from PIL import Image

import asgi_app
from core.auth import AuthService
from core.admin_service import AdminService
from core.database import DatabaseManager
from core.plans import referral_code
from payment.order_service import OrderService, grant_subscription_days
from payment.proof_storage import resolve_payment_proof, store_payment_proof
from payment.paddle_client import PaddleClient
from payment.paypal_client import PayPalClient


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "payment-security.db"))


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-longer-than-thirty-two-characters")
    monkeypatch.setenv("FPS_PAYMENT_INSTRUCTIONS", "Test FPS receiver")
    monkeypatch.setenv("ALIPAY_PAYMENT_INSTRUCTIONS", "Test Alipay receiver")
    monkeypatch.setenv("WECHAT_PAYMENT_INSTRUCTIONS", "Test WeChat receiver")


def _user(db: DatabaseManager, name: str = "buyer") -> dict:
    return AuthService(db).register(f"{name}@example.com", "CorrectHorse123", name, True)


def _paypal_headers() -> dict[str, str]:
    return {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
        "paypal-transmission-id": "8e55ea0b-1c3f-4f31-8d0f-1d6f9032a6ee",
        "paypal-transmission-sig": "c2lnbmF0dXJl",
        "paypal-transmission-time": "2026-08-06T00:00:00Z",
    }


def _billing_admin(db: DatabaseManager, name: str = "billing-admin") -> tuple[dict, AdminService]:
    admin = _user(db, name)
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    return admin, AdminService(db)


def test_telegram_order_idempotency_and_owner_boundary(db):
    owner = _user(db, "telegram-owner")
    other = _user(db, "telegram-other")
    orders = OrderService(db)

    order = orders.create_order(
        owner["id"], "标准版", "monthly", "fps", terms_accepted=True,
        source="telegram", idempotency_key="tg-order-1",
    )
    repeated = orders.create_order(
        owner["id"], "标准版", "monthly", "fps", terms_accepted=True,
        source="telegram", idempotency_key="tg-order-1",
    )
    assert repeated["order_no"] == order["order_no"]
    assert order["amount_minor"] == int(round(order["amount"] * 100))
    assert order["expires_at"]
    with pytest.raises(ValueError, match="幂等键"):
        orders.create_order(
            owner["id"], "高级版", "monthly", "fps", terms_accepted=True,
            source="telegram", idempotency_key="tg-order-1",
        )
    with pytest.raises(PermissionError):
        orders.get_order_for_user(other["id"], order["order_no"])


@pytest.mark.parametrize("method", ["fps", "alipay", "wechat"])
def test_new_orders_support_only_manual_payment_methods(db, method):
    user = _user(db, f"manual-{method}")
    order = OrderService(db).create_order(
        user["id"], "标准版", "monthly", method, terms_accepted=True
    )

    assert order["pay_method"] == method
    assert order["status"] == "pending"


def test_same_purchase_is_atomic_across_web_and_telegram(db):
    user = _user(db, "cross-channel-order")
    service = OrderService(db)

    def create(source):
        return service.create_order(
            user["id"],
            "高级版",
            "quarterly",
            "alipay",
            terms_accepted=True,
            idempotency_key=f"{source}-purchase-key",
            source=source,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        web = executor.submit(create, "web")
        telegram = executor.submit(create, "telegram")
        orders = [web.result(), telegram.result()]

    assert orders[0]["order_no"] == orders[1]["order_no"]
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM subscription_orders WHERE user_id=?", (user["id"],)
    )["count"] == 1


def test_same_purchase_is_atomic_across_independent_database_managers(db):
    user = _user(db, "cross-process-order")
    first = OrderService(DatabaseManager(db._db_path))
    second = OrderService(DatabaseManager(db._db_path))

    def create(service, source):
        return service.create_order(
            user["id"], "高级版", "quarterly", "alipay", terms_accepted=True,
            idempotency_key=f"independent-{source}", source=source,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        web = executor.submit(create, first, "web")
        telegram = executor.submit(create, second, "telegram")
        orders = [web.result(), telegram.result()]

    assert orders[0]["order_no"] == orders[1]["order_no"]
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM subscription_orders WHERE user_id=?", (user["id"],)
    )["count"] == 1


def test_distinct_pending_manual_orders_are_bounded_across_channels(db):
    user = _user(db, "cross-channel-limit")
    service = OrderService(db)
    purchases = [
        ("标准版", "monthly", "fps", "web"),
        ("高级版", "monthly", "alipay", "telegram"),
        ("专业版", "monthly", "wechat", "web"),
    ]
    for index, (plan, cycle, method, source) in enumerate(purchases):
        service.create_order(
            user["id"], plan, cycle, method, terms_accepted=True,
            idempotency_key=f"bounded-{index}", source=source,
        )

    with pytest.raises(ValueError, match="待付款订单过多"):
        service.create_order(
            user["id"], "定制版", "project", "fps", terms_accepted=True,
            idempotency_key="bounded-fourth", source="telegram",
        )


@pytest.mark.parametrize("method", ["paypal", "paddle"])
def test_new_provider_orders_are_disabled_but_legacy_import_is_supported(db, method):
    user = _user(db, f"legacy-{method}")
    service = OrderService(db)

    with pytest.raises(ValueError, match="仅支持"):
        service.create_order(user["id"], "标准版", "monthly", method, terms_accepted=True)
    historical = service.create_order(
        user["id"], "标准版", "monthly", method, terms_accepted=True, source="legacy"
    )

    assert historical["pay_method"] == method
    assert service.list_orders(user["id"])[0]["source"] == "legacy"


@pytest.mark.parametrize("method", ["fps", "alipay", "wechat"])
@pytest.mark.parametrize("status", ["paid", "failed", "cancelled"])
def test_manual_provider_callback_cannot_bypass_finance_review(db, method, status):
    user = _user(db, f"callback-blocked-{method}")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", method, terms_accepted=True
    )

    with pytest.raises(ValueError, match="财务审核"):
        service.process_callback(
            f"manual-{status}-{method}", order["order_no"], status, {"capture_id": "forged"}
        )

    assert service.get_order(order["order_no"])["status"] == "pending"
    assert db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    ) == {"plan_type": "免费版", "subscription_expire": None}
    assert db.fetch_one(
        "SELECT 1 FROM payment_callbacks WHERE event_id=?", (f"manual-{status}-{method}",)
    ) is None


def test_manual_claim_evidence_duplicate_and_resubmission(db):
    user = _user(db, "manual-claim")
    orders = OrderService(db)
    order = orders.create_order(
        user["id"], "标准版", "monthly", "fps", terms_accepted=True, source="telegram"
    )
    with pytest.raises(ValueError, match="凭证"):
        orders.submit_manual_payment_claim(user["id"], order["order_no"])
    with pytest.raises(ValueError, match="凭证"):
        orders.submit_manual_payment_claim(
            user["id"], order["order_no"], evidence_file_id="file-only"
        )
    with pytest.raises(ValueError, match="凭证"):
        orders.submit_manual_payment_claim(
            user["id"], order["order_no"], evidence_file_unique_id="unique-only"
        )
    claim = orders.submit_manual_payment_claim(
        user["id"], order["order_no"], evidence_file_id="file-1",
        evidence_file_unique_id="unique-1", source_update_id="9001",
    )
    duplicate = orders.submit_manual_payment_claim(
        user["id"], order["order_no"], evidence_file_id="file-1",
        evidence_file_unique_id="unique-1", source_update_id="9001",
    )
    assert duplicate["id"] == claim["id"]

    admin, admin_service = _billing_admin(db)
    rejected = admin_service.review_manual_payment_claim(admin["id"], claim["id"], False, rejection_reason="Amount not matched")
    assert rejected["status"] == "rejected"
    retry = orders.submit_manual_payment_claim(
        user["id"], order["order_no"], evidence_file_id="file-2",
        evidence_file_unique_id="unique-2", evidence_message_id=12345, source_update_id="9002"
    )
    assert retry["attempt"] == 2 and retry["status"] == "submitted"


def test_payment_proof_content_hash_is_unique_across_web_and_telegram(db):
    user = _user(db, "cross-channel-proof")
    service = OrderService(db)
    web_order = service.create_order(
        user["id"], "标准版", "monthly", "fps", terms_accepted=True, source="web"
    )
    telegram_order = service.create_order(
        user["id"], "高级版", "monthly", "alipay", terms_accepted=True, source="telegram"
    )
    digest = "a" * 64
    web_key = f"{'1' * 32}.jpg"
    service.submit_manual_payment_claim(
        user["id"], web_order["order_no"], evidence_file_id=f"web:{web_key}",
        evidence_file_unique_id=digest, evidence_source="web",
        evidence_storage_key=web_key, evidence_sha256=digest,
    )

    with pytest.raises(ValueError, match="已经用于其他订单"):
        service.submit_manual_payment_claim(
            user["id"], telegram_order["order_no"], evidence_file_id="tg-file",
            evidence_file_unique_id="tg-unique", evidence_source="telegram",
            evidence_storage_key=f"{'2' * 32}.jpg", evidence_sha256=digest,
        )


def test_manual_claim_review_is_idempotent_and_prevents_self_review(db):
    user = _user(db, "self-review")
    orders = OrderService(db)
    order = orders.create_order(
        user["id"], "高级版", "monthly", "fps", terms_accepted=True, source="telegram"
    )
    claim = orders.submit_manual_payment_claim(
        user["id"], order["order_no"], evidence_file_id="self-file",
        evidence_file_unique_id="self-unique", evidence_message_id=123,
    )
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    service = AdminService(db)
    with pytest.raises(PermissionError, match="自己"):
        service.review_manual_payment_claim(user["id"], claim["id"], True, "SELF-123")

    admin, service = _billing_admin(db, "separate-reviewer")
    approved = service.review_manual_payment_claim(admin["id"], claim["id"], True, "SETTLE-123")
    expiry = db.fetch_one("SELECT subscription_expire FROM users WHERE id=?", (user["id"],))["subscription_expire"]
    again = service.review_manual_payment_claim(admin["id"], claim["id"], True, "SETTLE-123")
    retry_without_reference = service.review_manual_payment_claim(admin["id"], claim["id"], True)
    assert approved["status"] == again["status"] == retry_without_reference["status"] == "approved"
    assert db.fetch_one("SELECT subscription_expire FROM users WHERE id=?", (user["id"],))["subscription_expire"] == expiry
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM user_action_logs WHERE action_type='ADMIN_MANUAL_PAYMENT_CLAIM_APPROVED'"
    )["count"] == 1


def test_manual_claim_recheck_blocks_revoked_reviewer_and_duplicate_settlement(db, monkeypatch):
    first_user = _user(db, "settlement-first")
    second_user = _user(db, "settlement-second")
    orders = OrderService(db)
    first_order = orders.create_order(first_user["id"], "标准版", "monthly", "fps", terms_accepted=True, source="telegram")
    second_order = orders.create_order(second_user["id"], "标准版", "monthly", "fps", terms_accepted=True, source="telegram")
    first = orders.submit_manual_payment_claim(
        first_user["id"], first_order["order_no"], evidence_file_id="first-file",
        evidence_file_unique_id="first-unique", evidence_message_id=11,
    )
    second = orders.submit_manual_payment_claim(
        second_user["id"], second_order["order_no"], evidence_file_id="second-file",
        evidence_file_unique_id="second-unique", evidence_message_id=12,
    )
    admin, service = _billing_admin(db, "revoked-reviewer")
    initial_require = service._require

    def revoke_after_initial_check(actor_id, permission):
        role = initial_require(actor_id, permission)
        db.execute("UPDATE users SET is_admin=0 WHERE id=?", (actor_id,))
        return role

    monkeypatch.setattr(service, "_require", revoke_after_initial_check)
    with pytest.raises(PermissionError):
        service.review_manual_payment_claim(admin["id"], first["id"], True, "TOCTOU-123")
    assert db.fetch_one("SELECT status FROM manual_payment_claims WHERE id=?", (first["id"],))["status"] == "submitted"

    monkeypatch.setattr(service, "_require", initial_require)
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    service.review_manual_payment_claim(admin["id"], first["id"], True, "SETTLEMENT-123")
    with pytest.raises(ValueError, match="参考编号"):
        service.review_manual_payment_claim(admin["id"], second["id"], True, "ＳＥＴＴＬＥＭＥＮＴ － １２３")


def test_web_payment_proof_must_pass_integrity_check_before_approval(db, tmp_path, monkeypatch):
    monkeypatch.setenv("PAYMENT_PROOF_DIR", str(tmp_path / "payment-proofs"))
    user = _user(db, "tampered-web-proof")
    order = OrderService(db).create_order(
        user["id"], "标准版", "monthly", "fps", terms_accepted=True, source="web"
    )
    image = BytesIO()
    Image.new("RGB", (96, 96), "white").save(image, format="PNG")
    stored = store_payment_proof(image.getvalue(), "image/png")
    claim = OrderService(db).submit_manual_payment_claim(
        user["id"], order["order_no"], evidence_file_id=f"web:{stored.storage_key}",
        evidence_file_unique_id=stored.sha256, evidence_source="web",
        evidence_storage_key=stored.storage_key, evidence_sha256=stored.sha256,
    )
    resolve_payment_proof(stored.storage_key).write_bytes(b"tampered")
    admin, service = _billing_admin(db, "tampered-proof-reviewer")

    with pytest.raises(ValueError, match="完整性校验失败"):
        service.review_manual_payment_claim(admin["id"], claim["id"], True, "TAMPER-123")
    assert OrderService(db).get_order(order["order_no"])["status"] == "pending"


def test_terminal_callbacks_and_provider_reversal_restore_entitlement(db):
    user = _user(db)
    original_expiry = (datetime.now(UTC) + timedelta(days=12)).isoformat(timespec="seconds")
    db.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (original_expiry, user["id"]),
    )
    service = OrderService(db)

    first = service.create_order(
        user["id"], "高级版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("paid-1", first["order_no"], "paid", {})
    first_expiry = db.fetch_one("SELECT subscription_expire FROM users WHERE id=?", (user["id"],))["subscription_expire"]

    second = service.create_order(
        user["id"], "专业版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback(
        "paid-2", second["order_no"], "paid", {},
        audit_user_id=user["id"], audit_action="PAYMENT_PROVIDER_CALLBACK",
    )
    assert service.refund_eligibility(first["order_no"])[0] is False
    assert service.get_order(second["order_no"])["previous_plan_type"] == "高级版"
    assert service.get_order(second["order_no"])["previous_subscription_expire"] == first_expiry

    service.process_reversal("provider-reversal-2", second["order_no"], {}, "provider_refund")
    restored = db.fetch_one("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],))
    assert restored == {"plan_type": "高级版", "subscription_expire": first_expiry}
    assert service.process_callback("late-paid", second["order_no"], "paid", {}) is False
    assert service.get_order(second["order_no"])["status"] == "refunded"

    failed = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("failed-1", failed["order_no"], "failed", {})
    assert service.process_callback("late-paid-2", failed["order_no"], "paid", {}) is False
    assert service.get_order(failed["order_no"])["status"] == "failed"
    actions = {row["action_type"] for row in db.fetch_all("SELECT action_type FROM user_action_logs")}
    assert {"PAYMENT_PROVIDER_CALLBACK", "PAYMENT_EXTERNAL_REVERSAL"} <= actions


def test_paid_referral_grants_thirty_percent_once(db):
    auth = AuthService(db)
    referrer = _user(db, "referrer")
    referee = auth.register(
        "referee@example.com",
        "CorrectHorse123",
        "Referee",
        True,
        referral_code(referrer["id"]),
    )
    service = OrderService(db)

    first = service.create_order(
        referee["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("referral-paid-1", first["order_no"], "paid", {})

    reward = db.fetch_one(
        "SELECT reward_type,days,reference FROM rewards WHERE user_id=?",
        (referrer["id"],),
    )
    assert reward == {
        "reward_type": "REFERRAL_30",
        "days": 9,
        "reference": db.fetch_one(
            "SELECT 'referral:' || id reference FROM referrals WHERE referee_id=?",
            (referee["id"],),
        )["reference"],
    }
    rewarded_user = db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (referrer["id"],)
    )
    assert rewarded_user["plan_type"] == "标准版"
    first_expiry = rewarded_user["subscription_expire"]

    second = service.create_order(
        referee["id"], "高级版", "quarterly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("referral-paid-2", second["order_no"], "paid", {})

    assert db.fetch_one("SELECT COUNT(*) count FROM rewards WHERE user_id=?", (referrer["id"],))["count"] == 1
    assert db.fetch_one(
        "SELECT subscription_expire FROM users WHERE id=?", (referrer["id"],)
    )["subscription_expire"] == first_expiry
    assert db.fetch_one("SELECT status FROM referrals WHERE referee_id=?", (referee["id"],))["status"] == "qualified"
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM user_action_logs WHERE user_id=? AND action_type='REFERRAL_REWARD_GRANTED'",
        (referrer["id"],),
    )["count"] == 1


def test_refund_revokes_source_referral_reward_and_allows_future_qualification(db):
    auth = AuthService(db)
    referrer = _user(db, "refund-referrer")
    referee = auth.register(
        "refund-referee@example.com",
        "CorrectHorse123",
        "Refund Referee",
        True,
        referral_code(referrer["id"]),
    )
    service = OrderService(db)
    order = service.create_order(
        referee["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("refund-referral-paid", order["order_no"], "paid", {})
    assert db.fetch_one("SELECT COUNT(*) count FROM rewards")["count"] == 1

    service.process_reversal("referral-provider-reversal", order["order_no"], {}, "provider_refund")

    assert db.fetch_one("SELECT COUNT(*) count FROM rewards")["count"] == 0
    assert db.fetch_one("SELECT status FROM referrals WHERE referee_id=?", (referee["id"],))["status"] == "registered"
    assert db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (referrer["id"],)
    ) == {"plan_type": "免费版", "subscription_expire": None}

    replacement = service.create_order(
        referee["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("refund-referral-repaid", replacement["order_no"], "paid", {})
    assert db.fetch_one("SELECT COUNT(*) count FROM rewards")["count"] == 1


def test_refund_preserves_rewards_granted_after_payment(db):
    user = _user(db, "later-reward")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("later-reward-paid", order["order_no"], "paid", {})
    paid_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    db.execute("UPDATE subscription_orders SET paid_at=? WHERE order_no=?", (paid_at, order["order_no"]))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO rewards (user_id,reward_type,days,reference,created_at) VALUES (?,?,?,?,?)",
            (user["id"], "SOCIAL_APPROVED", 7, "YouTube:https://example.com/reward", datetime.now(UTC).isoformat(timespec="seconds")),
        )
        grant_subscription_days(conn, user["id"], 7, "标准版")

    service.process_reversal("later-reward-provider-reversal", order["order_no"], {}, "provider_refund")

    restored = db.fetch_one("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],))
    assert restored["plan_type"] == "标准版"
    assert datetime.fromisoformat(restored["subscription_expire"]) > datetime.now(UTC) + timedelta(days=6)


def test_verified_provider_reversal_bypasses_voluntary_window_and_is_idempotent(db):
    user = _user(db, "provider-reversal")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    service.attach_external_id(order["order_no"], "PAYPAL-REVERSAL")
    assert service.process_callback(
        "provider-paid",
        order["order_no"],
        "paid",
        {"capture_id": "CAPTURE-REVERSAL"},
    )
    db.execute(
        "UPDATE subscription_orders SET paid_at=? WHERE order_no=?",
        ((datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="seconds"), order["order_no"]),
    )
    assert service.refund_eligibility(order["order_no"])[0] is False

    assert service.process_reversal(
        "provider-refunded",
        order["order_no"],
        {"provider": "paypal", "capture_id": "CAPTURE-REVERSAL"},
        "paypal:payment.capture.refunded",
    )
    assert not service.process_reversal(
        "provider-refunded",
        order["order_no"],
        {},
        "paypal:payment.capture.refunded",
    )
    assert service.get_order(order["order_no"])["status"] == "refunded"
    assert db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    ) == {"plan_type": "免费版", "subscription_expire": None}


def test_reversing_older_order_preserves_later_subscription(db):
    user = _user(db, "older-reversal")
    service = OrderService(db)
    first = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    service.attach_external_id(first["order_no"], "PAYPAL-FIRST")
    assert service.process_callback("older-first-paid", first["order_no"], "paid", {})
    second = service.create_order(
        user["id"], "专业版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    service.attach_external_id(second["order_no"], "PAYPAL-SECOND")
    before_second = datetime.now(UTC)
    assert service.process_callback("older-second-paid", second["order_no"], "paid", {})

    assert service.process_reversal(
        "older-first-reversed",
        first["order_no"],
        {"provider": "paypal"},
        "paypal:payment.capture.refunded",
    )

    current = db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )
    assert current["plan_type"] == "专业版"
    remaining = datetime.fromisoformat(current["subscription_expire"]) - before_second
    assert timedelta(days=29) < remaining < timedelta(days=31)


def test_reversing_older_order_only_removes_unused_overlap_and_keeps_manual_extension(db):
    user = _user(db, "overlap-reversal")
    service = OrderService(db)
    anchor = datetime.now(UTC).replace(microsecond=0)
    first = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("overlap-first-paid", first["order_no"], "paid", {})
    db.execute(
        "UPDATE subscription_orders SET paid_at=? WHERE order_no=?",
        ((anchor - timedelta(days=20)).isoformat(timespec="seconds"), first["order_no"]),
    )
    db.execute(
        "UPDATE users SET subscription_expire=? WHERE id=?",
        ((anchor + timedelta(days=10)).isoformat(timespec="seconds"), user["id"]),
    )
    second = service.create_order(
        user["id"], "专业版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("overlap-second-paid", second["order_no"], "paid", {})
    db.execute(
        "UPDATE users SET subscription_expire=datetime(subscription_expire, '+7 days') WHERE id=?",
        (user["id"],),
    )

    service.process_reversal(
        "overlap-first-reversed", first["order_no"], {"provider": "paypal"}, "paypal:refund"
    )

    current = db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )
    assert current["plan_type"] == "专业版"
    remaining = datetime.fromisoformat(current["subscription_expire"]).replace(tzinfo=UTC) - anchor
    assert timedelta(days=36, hours=23) < remaining < timedelta(days=37, minutes=1)


def test_reversing_expired_older_order_does_not_touch_later_purchase(db):
    user = _user(db, "expired-older-reversal")
    service = OrderService(db)
    anchor = datetime.now(UTC).replace(microsecond=0)
    first = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("expired-old-first-paid", first["order_no"], "paid", {})
    db.execute(
        "UPDATE subscription_orders SET paid_at=? WHERE order_no=?",
        ((anchor - timedelta(days=40)).isoformat(timespec="seconds"), first["order_no"]),
    )
    db.execute(
        "UPDATE users SET subscription_expire=? WHERE id=?",
        ((anchor - timedelta(days=10)).isoformat(timespec="seconds"), user["id"]),
    )
    second = service.create_order(
        user["id"], "专业版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("expired-old-second-paid", second["order_no"], "paid", {})
    before = db.fetch_one("SELECT subscription_expire FROM users WHERE id=?", (user["id"],))["subscription_expire"]

    service.process_reversal(
        "expired-old-first-reversed", first["order_no"], {"provider": "paypal"}, "paypal:refund"
    )

    current = db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )
    assert current == {"plan_type": "专业版", "subscription_expire": before}


def test_reversal_preserves_later_manual_subscription_adjustment(db):
    user = _user(db, "manual-after-payment")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("manual-base-paid", order["order_no"], "paid", {})
    db.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=datetime(subscription_expire, '+10 days') WHERE id=?",
        (user["id"],),
    )

    service.process_reversal(
        "manual-base-reversed", order["order_no"], {"provider": "paypal"}, "paypal:refund"
    )

    current = db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    )
    assert current["plan_type"] == "高级版"
    assert datetime.fromisoformat(current["subscription_expire"]).replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(days=9)


def test_reversal_does_not_reactivate_a_manually_downgraded_account(db):
    user = _user(db, "manual-free-after-payment")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("manual-free-paid", order["order_no"], "paid", {})
    db.execute(
        "UPDATE users SET plan_type='免费版',subscription_expire=NULL WHERE id=?", (user["id"],)
    )

    service.process_reversal(
        "manual-free-reversed", order["order_no"], {"provider": "paypal"}, "paypal:refund"
    )

    assert db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    ) == {"plan_type": "免费版", "subscription_expire": None}


def test_reversing_referral_source_moves_reward_to_next_paid_order(db):
    auth = AuthService(db)
    referrer = _user(db, "replacement-referrer")
    referee = auth.register(
        "replacement-referee@example.com",
        "CorrectHorse123",
        "Replacement Referee",
        True,
        referral_code(referrer["id"]),
    )
    service = OrderService(db)
    first = service.create_order(
        referee["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    service.attach_external_id(first["order_no"], "REF-FIRST")
    service.process_callback("ref-source-first", first["order_no"], "paid", {})
    second = service.create_order(
        referee["id"], "高级版", "quarterly", "paypal", terms_accepted=True, source="legacy"
    )
    service.attach_external_id(second["order_no"], "REF-SECOND")
    service.process_callback("ref-source-second", second["order_no"], "paid", {})

    service.process_reversal(
        "ref-source-reversed",
        first["order_no"],
        {"provider": "paypal"},
        "paypal:payment.capture.refunded",
    )

    reward = db.fetch_one("SELECT days,source_order_no FROM rewards WHERE user_id=?", (referrer["id"],))
    assert reward == {"days": 27, "source_order_no": second["order_no"]}
    assert db.fetch_one("SELECT status FROM referrals WHERE referee_id=?", (referee["id"],))["status"] == "qualified"


def test_expired_plan_reward_uses_fallback_and_yearly_is_fifteen_months(db):
    referrer = _user(db, "expired-reward")
    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=? WHERE id=?",
        (expired, referrer["id"]),
    )
    with db.transaction() as conn:
        grant_subscription_days(conn, referrer["id"], 1, "标准版")
    assert db.fetch_one("SELECT plan_type FROM users WHERE id=?", (referrer["id"],))["plan_type"] == "标准版"

    auth = AuthService(db)
    referee = auth.register(
        "yearly-referee@example.com",
        "CorrectHorse123",
        "Yearly Referee",
        True,
        referral_code(referrer["id"]),
    )
    service = OrderService(db)
    order = service.create_order(
        referee["id"], "高级版", "yearly", "paypal", terms_accepted=True, source="legacy"
    )
    before = datetime.now(UTC)
    assert service.process_callback("yearly-paid", order["order_no"], "paid", {})
    buyer_expiry = datetime.fromisoformat(
        db.fetch_one("SELECT subscription_expire FROM users WHERE id=?", (referee["id"],))["subscription_expire"]
    )
    assert timedelta(days=454) < buyer_expiry - before < timedelta(days=456)
    assert db.fetch_one(
        "SELECT days FROM rewards WHERE source_order_no=?", (order["order_no"],)
    )["days"] == 136


def test_annual_bonus_switch_only_changes_new_orders(db):
    user = _user(db, "annual-switch")
    service = OrderService(db)
    promotional = service.create_order(user["id"], "标准版", "yearly", "fps", terms_accepted=True)
    assert promotional["entitlement_days"] == 455

    db.execute(
        """INSERT INTO platform_controls (control_key,control_value,updated_at)
           VALUES ('annual_bonus_enabled','0',?)
           ON CONFLICT(control_key) DO UPDATE SET control_value='0'""",
        (datetime.now(UTC).isoformat(timespec="seconds"),),
    )
    regular = service.create_order(user["id"], "标准版", "yearly", "fps", terms_accepted=True)

    assert service.get_order(promotional["order_no"])["entitlement_days"] == 455
    assert regular["entitlement_days"] == 365


def test_payment_and_refund_roll_back_when_atomic_audit_fails(db):
    user = _user(db, "atomic")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    with pytest.raises(sqlite3.IntegrityError):
        service.process_callback(
            "atomic-paid", order["order_no"], "paid", {},
            audit_user_id=999_999, audit_action="PAYMENT_PROVIDER_CALLBACK",
        )
    assert service.get_order(order["order_no"])["status"] == "pending"
    assert db.fetch_one("SELECT 1 FROM payment_callbacks WHERE event_id='atomic-paid'") is None

    assert service.process_callback("atomic-paid-ok", order["order_no"], "paid", {})
    paid_user = db.fetch_one("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],))
    with db.transaction() as conn:
        conn.executescript(
            """CREATE TRIGGER reject_refund_audit BEFORE INSERT ON user_action_logs
               WHEN NEW.action_type='PAYMENT_EXTERNAL_REVERSAL'
               BEGIN SELECT RAISE(ABORT, 'audit failed'); END;"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.process_reversal("atomic-provider-reversal", order["order_no"], {}, "provider_refund")
    assert service.get_order(order["order_no"])["status"] == "paid"
    assert db.fetch_one("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)) == paid_user


def test_subscription_snapshot_migration_is_idempotent(db):
    with db.transaction() as conn:
        conn.execute("ALTER TABLE subscription_orders DROP COLUMN previous_plan_type")
        conn.execute("ALTER TABLE subscription_orders DROP COLUMN previous_subscription_expire")
        conn.execute("ALTER TABLE user_sessions DROP COLUMN refresh_token_hash")
    DatabaseManager(db._db_path)
    DatabaseManager(db._db_path)
    columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(subscription_orders)")}
    assert {
        "previous_plan_type",
        "previous_subscription_expire",
        "external_price_id",
        "external_capture_id",
        "entitlement_days",
    } <= columns
    reward_columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(rewards)")}
    assert "source_order_no" in reward_columns
    session_columns = {row["name"] for row in db.fetch_all("PRAGMA table_info(user_sessions)")}
    assert "refresh_token_hash" in session_columns


def test_paddle_environment_and_completed_transaction_are_fail_closed(monkeypatch):
    monkeypatch.setenv("PADDLE_API_KEY", "key")
    monkeypatch.setenv("PADDLE_ENV", "typo-production")
    client = PaddleClient()
    assert client.configured is False
    with pytest.raises(RuntimeError, match="sandbox 或 production"):
        client.create_transaction("TA-1", "pri_1")

    order = {
        "pay_method": "paddle",
        "external_id": "txn_1",
        "external_price_id": "pri_1",
        "currency": "HKD",
        "amount": 298.0,
    }
    data = {
        "id": "txn_1",
        "currency_code": "HKD",
        "items": [{"quantity": 1, "price": {"id": "pri_1"}}],
        "details": {"totals": {
            "subtotal": "29800",
            "discount": "0",
            "credit": "0",
            "grand_total": "29800",
            "balance": "0",
            "currency_code": "HKD",
        }},
    }
    assert asgi_app._paddle_transaction_matches(data, order)
    for path, value in (
        (("id",), "txn_other"),
        (("currency_code",), "USD"),
        (("items", 0, "price", "id"), "pri_other"),
        (("details", "totals", "grand_total"), "29799"),
        (("details", "totals", "balance"), "1"),
        (("details", "totals", "currency_code"), "USD"),
    ):
        invalid = deepcopy(data)
        target = invalid
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        assert not asgi_app._paddle_transaction_matches(invalid, order)

    discounted = deepcopy(data)
    discounted["details"]["totals"].update(discount="29800", grand_total="0")
    assert not asgi_app._paddle_transaction_matches(discounted, order)
    zero_order = {**order, "amount": 0}
    assert not asgi_app._paddle_transaction_matches(discounted, zero_order)


@pytest.mark.parametrize("discount,credit", [("29800", "0"), ("0", "29800")])
def test_paddle_zero_total_cannot_activate_subscription(monkeypatch, db, discount, credit):
    user = _user(db, f"paddle-zero-{discount}-{credit}")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", "paddle", terms_accepted=True, source="legacy"
    )
    service.attach_external_id(order["order_no"], "txn_zero", "pri_standard")
    event = {
        "event_id": f"evt-zero-{discount}-{credit}",
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_zero",
            "currency_code": "HKD",
            "custom_data": {"order_no": order["order_no"]},
            "items": [{"quantity": 1, "price": {"id": "pri_standard"}}],
            "details": {"totals": {
                "subtotal": "29800",
                "discount": discount,
                "credit": credit,
                "grand_total": "0",
                "balance": "0",
                "currency_code": "HKD",
            }},
        },
    }

    class Request:
        headers = {"paddle-signature": "ignored"}

        async def stream(self):
            yield json.dumps(event).encode("utf-8")

    monkeypatch.setattr(asgi_app, "_verify_paddle", lambda body, signature: True)
    monkeypatch.setattr(asgi_app, "get_database", lambda: db)
    monkeypatch.setattr(asgi_app, "OrderService", lambda *args: service)

    with pytest.raises(asgi_app.ApiError, match="金额"):
        asyncio.run(asgi_app.paddle_webhook(Request()))

    assert service.get_order(order["order_no"])["status"] == "pending"
    assert db.fetch_one(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)
    ) == {"plan_type": "免费版", "subscription_expire": None}


def test_paypal_client_sets_callbacks_and_captures(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://trade.example/")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "client")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(PayPalClient, "_access_token", lambda self: "access")
    requests = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response({"id": "PAYPAL-1", "status": "CREATED" if len(requests) == 1 else "COMPLETED"})

    monkeypatch.setattr("payment.paypal_client.urlopen", fake_urlopen)
    client = PayPalClient()
    client.create_order("TA-1", 298, "HKD")
    client.capture_order("PAYPAL-1")

    body = json.loads(requests[0][0].data)
    assert body["application_context"] == {
        "return_url": "https://trade.example/payments/paypal/return",
        "cancel_url": "https://trade.example/payments/paypal/cancel",
    }
    assert requests[1][0].full_url.endswith("/v2/checkout/orders/PAYPAL-1/capture")
    assert requests[1][0].method == "POST"


def test_paypal_oauth_token_is_cached(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cache-client")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "cache-secret")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(PayPalClient, "_token_cache", None)
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @staticmethod
        def read():
            return json.dumps({"access_token": "cached-access", "expires_in": 3600}).encode()

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr("payment.paypal_client.urlopen", fake_urlopen)

    assert PayPalClient()._access_token() == "cached-access"
    assert PayPalClient()._access_token() == "cached-access"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "headers",
    [{}, {**_paypal_headers(), "paypal-cert-url": "https://[invalid"}],
)
def test_paypal_webhook_rejects_bad_headers_without_outbound(monkeypatch, headers):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "client")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "webhook")
    monkeypatch.setattr(PayPalClient, "_token_cache", None)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        raise AssertionError("missing headers must not reach PayPal")

    class Request:
        def __init__(self):
            self.headers = headers

        async def stream(self):
            yield b'{"id":"WH-1"}'

    monkeypatch.setattr("payment.paypal_client.urlopen", fake_urlopen)

    with pytest.raises(asgi_app.ApiError) as exc:
        asyncio.run(asgi_app.paypal_webhook(Request()))

    assert exc.value.status == 401
    assert not requests


def test_production_callbacks_hosts_headers_and_body_limits_fail_closed(monkeypatch):
    monkeypatch.setenv("PAYPAL_ENV", "live")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "http://trade.example")
    with pytest.raises(RuntimeError, match="HTTPS"):
        PayPalClient()._callback_urls()

    monkeypatch.setenv("APP_BASE_URL", "https://trade.example")
    monkeypatch.delenv("APP_ALLOWED_HOSTS", raising=False)
    assert asgi_app._trusted_hosts() == ["trade.example"]
    monkeypatch.setenv("APP_ALLOWED_HOSTS", "trade.example,api.trade.example")
    assert asgi_app._trusted_hosts() == ["trade.example", "api.trade.example"]

    class Request:
        headers = {"content-length": str(asgi_app.MAX_REQUEST_BODY_BYTES + 1)}

    called = []

    async def call_next(request):
        called.append(request)
        return asgi_app.JSONResponse({"status": "ok"})

    middleware = asgi_app.SecurityHeadersMiddleware(lambda *_: None)
    response = asyncio.run(middleware.dispatch(Request(), call_next))
    assert response.status_code == 413 and not called
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert "https://static.cloudflareinsights.com" in response.headers["Content-Security-Policy"]
    assert response.headers["Strict-Transport-Security"].startswith("max-age=31536000")

    class ChunkedRequest:
        headers = {}

        async def stream(self):
            yield b"1234"
            yield b"56"

    with pytest.raises(asgi_app.ApiError) as error:
        asyncio.run(asgi_app._limited_body(ChunkedRequest(), 5))
    assert error.value.status == 413


def test_health_and_external_script_alert_do_not_expose_diagnostics(monkeypatch):
    response = asyncio.run(asgi_app.health(None))
    assert response.body == b'{"status":"ok","app":"CicloTrade"}'

    sent = []
    logged = []
    monkeypatch.setenv("EXTERNAL_ALERTS_ENABLED", "true")
    monkeypatch.setattr(asgi_app, "telegram_configured", lambda: True)
    monkeypatch.setattr(asgi_app, "send_telegram", lambda message: sent.append(message))
    monkeypatch.setattr(
        asgi_app,
        "get_database",
        lambda: type("DB", (), {"log_system_event": lambda self, *args: logged.append(args)})(),
    )

    asgi_app.on_script_error(RuntimeError("secret user input"))

    assert sent and "secret user input" not in sent[0]
    assert "RuntimeError" in sent[0] and "事件編號" in sent[0]
    assert logged and "secret user input" in logged[0][-1]


def test_streamlit_deep_link_resources_are_rewritten_for_http_and_websocket():
    seen = []

    async def downstream(scope, receive, send):
        del receive, send
        seen.append((scope["type"], scope["path"], scope["raw_path"]))

    async def no_op():
        return {}

    middleware = asgi_app.StreamlitDeepLinkMiddleware(downstream)
    asyncio.run(middleware({"type": "http", "path": "/admin/static/app.js", "raw_path": b"/admin/static/app.js"}, no_op, no_op))
    asyncio.run(middleware({"type": "websocket", "path": "/markets/_stcore/stream", "raw_path": b"/markets/_stcore/stream"}, no_op, no_op))
    asyncio.run(middleware({"type": "http", "path": "/app/static/tradeai_locale.js", "raw_path": b"/app/static/tradeai_locale.js"}, no_op, no_op))
    asyncio.run(middleware({"type": "http", "path": "/static/media/font.woff2", "raw_path": b"/static/media/font.woff2"}, no_op, no_op))
    asyncio.run(middleware({"type": "http", "path": "/payments/paypal/return", "raw_path": b"/payments/paypal/return"}, no_op, no_op))

    assert seen == [
        ("http", "/static/app.js", b"/static/app.js"),
        ("websocket", "/_stcore/stream", b"/_stcore/stream"),
        ("http", "/app/static/tradeai_locale.js", b"/app/static/tradeai_locale.js"),
        ("http", "/static/media/font.woff2", b"/static/media/font.woff2"),
        ("http", "/payments/paypal/return", b"/payments/paypal/return"),
    ]


def test_streamlit_browser_deep_links_redirect_through_root_login():
    seen = []
    sent = []

    async def downstream(scope, receive, send):
        del receive, send
        seen.append(scope)

    async def no_op():
        return {}

    async def capture(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/subscription",
        "raw_path": b"/subscription",
        "query_string": b"payment=success&next=ignored",
        "headers": [(b"accept", b"text/html,application/xhtml+xml")],
    }
    asyncio.run(asgi_app.StreamlitDeepLinkMiddleware(downstream)(scope, no_op, capture))

    assert not seen
    assert sent[0]["status"] == 307
    assert dict(sent[0]["headers"])[b"location"] == b"/?next=subscription&payment=success"


def test_paypal_capture_verifies_reference_status_amount_and_currency():
    order = {"order_no": "TA-1", "external_id": "PAYPAL-1", "amount": 298.0, "currency": "HKD"}
    capture = {
        "id": "PAYPAL-1",
        "status": "COMPLETED",
        "purchase_units": [{
            "reference_id": "TA-1",
            "amount": {"currency_code": "HKD", "value": "298.00"},
            "payments": {"captures": [{
                "id": "CAPTURE-1", "status": "COMPLETED",
                "amount": {"currency_code": "HKD", "value": "298.00"},
            }]},
        }],
    }
    assert asgi_app._verified_paypal_capture_id(capture, order) == "CAPTURE-1"
    for path, value in (
        (("status",), "APPROVED"),
        (("purchase_units", 0, "reference_id"), "OTHER"),
        (("purchase_units", 0, "amount", "value"), "297.99"),
        (("purchase_units", 0, "amount", "currency_code"), "USD"),
    ):
        invalid = deepcopy(capture)
        target = invalid
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        assert asgi_app._verified_paypal_capture_id(invalid, order) is None


def test_paypal_return_captures_pending_order_and_redirects(monkeypatch):
    order = {
        "order_no": "TA-1", "external_id": "PAYPAL-1", "amount": 298.0,
        "currency": "HKD", "pay_method": "paypal", "status": "pending",
    }
    capture = {
        "id": "PAYPAL-1", "status": "COMPLETED",
        "purchase_units": [{
            "reference_id": "TA-1", "amount": {"currency_code": "HKD", "value": "298.00"},
            "payments": {"captures": [{
                "id": "CAPTURE-1", "status": "COMPLETED",
                "amount": {"currency_code": "HKD", "value": "298.00"},
            }]},
        }],
    }

    class Database:
        def fetch_one(self, sql, params=()):
            return order

        def log_system_event(self, *args):
            raise AssertionError("valid capture must not log an error")

    callbacks = []

    class Service:
        def process_callback(self, *args):
            callbacks.append(args)
            return True

    class Request:
        query_params = {"token": "PAYPAL-1"}

    monkeypatch.setattr(asgi_app, "get_database", Database)
    monkeypatch.setattr(asgi_app, "PayPalClient", lambda: type("Client", (), {"capture_order": lambda self, value: capture})())
    monkeypatch.setattr(asgi_app, "OrderService", lambda database: Service())

    response = asyncio.run(asgi_app.paypal_return(Request()))

    assert response.status_code == 303 and response.headers["location"] == "/subscription?payment=success"
    assert callbacks[0][:3] == ("paypal-capture-CAPTURE-1", "TA-1", "paid")


def test_paypal_refund_webhook_forces_reversal(monkeypatch, db):
    user = _user(db, "paypal-refund")
    service = OrderService(db)
    order = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    service.attach_external_id(order["order_no"], "PAYPAL-ORDER")
    service.process_callback(
        "paypal-refund-paid",
        order["order_no"],
        "paid",
        {"capture_id": "PAYPAL-CAPTURE"},
    )
    event = {
        "id": "WH-REFUND-1",
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {
            "id": "PAYPAL-REFUND",
            "supplementary_data": {
                "related_ids": {"order_id": "PAYPAL-ORDER", "capture_id": "PAYPAL-CAPTURE"}
            },
        },
    }

    class Request:
        headers = _paypal_headers()

        async def stream(self):
            yield json.dumps(event).encode("utf-8")

    monkeypatch.setattr(asgi_app, "get_database", lambda: db)
    monkeypatch.setattr(asgi_app, "OrderService", lambda *args: service)
    verification_threads = []
    request_thread = threading.get_ident()

    class Client:
        webhook_headers_valid = staticmethod(PayPalClient.webhook_headers_valid)

        def verify_webhook(self, headers, payload):
            verification_threads.append(threading.get_ident())
            return True

    monkeypatch.setattr(asgi_app, "PayPalClient", Client)

    dispute_opened = deepcopy(event)
    dispute_opened["id"] = "WH-DISPUTE-OPEN"
    dispute_opened["event_type"] = "CUSTOMER.DISPUTE.CREATED"
    event = dispute_opened
    ignored = asyncio.run(asgi_app.paypal_webhook(Request()))
    assert json.loads(ignored.body) == {"status": "ignored"}
    assert service.get_order(order["order_no"])["status"] == "paid"

    seller_won = deepcopy(event)
    seller_won["id"] = "WH-DISPUTE-SELLER"
    seller_won["event_type"] = "CUSTOMER.DISPUTE.RESOLVED"
    seller_won["resource"]["dispute_outcome"] = {"outcome_code": "RESOLVED_SELLER_FAVOUR"}
    event = seller_won
    ignored = asyncio.run(asgi_app.paypal_webhook(Request()))
    assert json.loads(ignored.body) == {"status": "ignored"}
    assert service.get_order(order["order_no"])["status"] == "paid"

    event = {
        "id": "WH-REFUND-1",
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {
            "id": "PAYPAL-REFUND",
            "supplementary_data": {
                "related_ids": {"order_id": "PAYPAL-ORDER", "capture_id": "PAYPAL-CAPTURE"}
            },
        },
    }

    response = asyncio.run(asgi_app.paypal_webhook(Request()))

    assert response.status_code == 200
    assert service.get_order(order["order_no"])["status"] == "refunded"
    assert verification_threads and all(thread != request_thread for thread in verification_threads)


def test_order_api_rejects_bad_inputs_and_honors_global_pause(monkeypatch):
    monkeypatch.setattr(asgi_app, "_api_user", lambda request: {"id": 7})

    class Request:
        method = "POST"
        headers = {}

        def __init__(self, payload=None, error=False):
            self.payload, self.error = payload, error

        async def stream(self):
            yield b"{" if self.error else json.dumps(self.payload).encode("utf-8")

    bad_payloads = (
        Request(error=True),
        Request({"symbol": "AAPL", "side": "HOLD", "quantity": 1, "price": 100}),
        Request({"symbol": "AAPL", "side": "BUY", "quantity": 1, "price": 100, "mode": "demo"}),
        Request({"symbol": "AAPL", "side": "BUY", "quantity": 1, "price": float("nan")}),
        Request({"symbol": "AAPL", "side": "BUY", "quantity": 1, "price": 100, "mode": "live"}),
    )
    for request in bad_payloads:
        with pytest.raises(asgi_app.ApiError):
            asyncio.run(asgi_app.api_orders(request))

    class Database:
        def fetch_one(self, sql, params=()):
            if "user_controls" in sql:
                return {"opening_paused": 0}
            if "platform_controls" in sql:
                return {"control_value": "1"}
            return None

    submitted = {}

    class Manager:
        def submit(self, **kwargs):
            submitted.update(kwargs)
            return {"order_id": "LIVE-1"}

    monkeypatch.setattr(asgi_app, "get_database", Database)
    monkeypatch.setattr(asgi_app, "OrderManager", Manager)
    response = asyncio.run(asgi_app.api_orders(Request({
        "symbol": "aapl", "side": "buy", "quantity": 1, "price": 100,
        "mode": "live", "confirm_live": True,
    })))
    assert response.status_code == 201
    assert submitted["paused"] is True and submitted["live_confirmed"] is True
