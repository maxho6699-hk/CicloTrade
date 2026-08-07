"""认证、支付、预警、风控与 Backtrader 策略公式检查。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC

import pytest
import pandas as pd
import jwt

from backtest.engine import option_payoff
from core.alerts import AlertService
from core.auth import AuthError, AuthService
from core.database import DatabaseManager
from core.user_settings import merge_user_settings
from notification.email_sender import send_email
from payment.order_service import OrderService
from scheduler.jobs import downgrade_expired_subscriptions, notify_expiring_subscriptions, scan_price_alerts
from trading.order_manager import OrderManager, trade_ledger_state
from trading.risk_filter import validate_order
from trading.tiger_api import TigerAPI


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "tradeai-test.db"))


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-longer-than-thirty-two-characters")


def _user(auth: AuthService, suffix: str = "one"):
    return auth.register(f"{suffix}@example.com", "CorrectHorse123", "Test User", True)


def test_auth_single_session_and_three_ip_limit(db):
    auth = AuthService(db)
    user = _user(auth)
    first = auth.login(user["email"], "CorrectHorse123", "10.0.0.1", "pytest")
    assert auth.verify(first.access_token)["id"] == user["id"]
    second = auth.login(user["email"], "CorrectHorse123", "10.0.0.2", "pytest")
    with pytest.raises(AuthError, match="其他设备"):
        auth.verify(first.access_token)
    assert auth.verify(second.access_token)["id"] == user["id"]
    auth.login(user["email"], "CorrectHorse123", "10.0.0.3", "pytest")
    with pytest.raises(AuthError, match="已绑定 3 个 IP"):
        auth.login(user["email"], "CorrectHorse123", "10.0.0.4", "pytest")


def test_auth_rejects_incomplete_tokens_blank_names_and_admin_email_squatting(db, monkeypatch):
    auth = AuthService(db)
    with pytest.raises(AuthError, match="显示名称"):
        auth.register("blank@example.com", "CorrectHorse123", "   ", True)
    user = _user(auth, "claimed-admin")
    monkeypatch.setenv("TRADEAI_ADMIN_EMAIL", user["email"])
    monkeypatch.setenv("TRADEAI_ADMIN_PASSWORD", "DifferentAdmin123")
    with pytest.raises(AuthError, match="普通账户占用"):
        auth.bootstrap_admin()
    assert auth.get_user(user["id"])["is_admin"] == 0

    incomplete = jwt.encode(
        {"sub": str(user["id"]), "type": "access"},
        "test-secret-that-is-longer-than-thirty-two-characters",
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="失效"):
        auth.verify(incomplete)


def test_login_failures_do_not_globally_lock_victim_and_reset_has_cooldown(db):
    auth = AuthService(db)
    user = _user(auth, "rate-limited")
    for index in range(5):
        with pytest.raises(AuthError, match="邮箱或密码"):
            auth.login(user["email"], "WrongPassword123", f"192.0.2.{index}", "pytest")
    assert db.fetch_one("SELECT locked_until FROM users WHERE id=?", (user["id"],))["locked_until"] is None
    assert auth.login(user["email"], "CorrectHorse123", "198.51.100.1", "pytest").user["id"] == user["id"]

    first = auth.request_password_reset(user["email"], "203.0.113.1")
    second = auth.request_password_reset(user["email"], "203.0.113.2")
    assert first and second is None
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM password_resets WHERE user_id=?", (user["id"],)
    )["count"] == 1


def test_login_throttles_distributed_attempts_for_one_account(db, monkeypatch):
    auth = AuthService(db)
    user = _user(auth, "distributed-rate-limit")
    monkeypatch.setattr("core.auth.bcrypt.checkpw", lambda *_: False)

    for index in range(12):
        with pytest.raises(AuthError, match="邮箱或密码"):
            auth.login(user["email"], "WrongPassword123", f"198.51.100.{index}", "pytest")

    with pytest.raises(AuthError, match="尝试次数过多"):
        auth.login(user["email"], "CorrectHorse123", "203.0.113.200", "pytest")


def test_registration_is_ip_limited_and_duplicate_email_is_not_disclosed(db, monkeypatch):
    auth = AuthService(db)
    monkeypatch.setattr("core.auth.bcrypt.hashpw", lambda *_: b"test-password-hash")

    first = auth.register(
        "register-0@example.com",
        "CorrectHorse123",
        "Register 0",
        True,
        ip_address="203.0.113.70",
    )
    assert first
    assert auth.register(
        "register-0@example.com",
        "CorrectHorse123",
        "Register 0",
        True,
        ip_address="203.0.113.70",
    ) is None
    for index in range(1, 4):
        assert auth.register(
            f"register-{index}@example.com",
            "CorrectHorse123",
            f"Register {index}",
            True,
            ip_address="203.0.113.70",
        )
    with pytest.raises(AuthError, match="尝试次数过多"):
        auth.register(
            "register-blocked@example.com",
            "CorrectHorse123",
            "Blocked",
            True,
            ip_address="203.0.113.70",
        )


def test_refresh_tokens_rotate_and_reuse_revokes_the_session(db):
    auth = AuthService(db)
    user = _user(auth, "refresh-rotation")
    first = auth.login(user["email"], "CorrectHorse123", "203.0.113.80", "pytest")

    access_token, refresh_token = auth.refresh(first.refresh_token)
    assert auth.verify(access_token)["id"] == user["id"]
    assert refresh_token != first.refresh_token

    with pytest.raises(AuthError, match="刷新凭证已失效"):
        auth.refresh(first.refresh_token)
    with pytest.raises(AuthError, match="其他设备"):
        auth.verify(access_token)


def test_auth_throttles_one_ip_across_accounts_and_reset_tokens_are_one_use(db, monkeypatch):
    auth = AuthService(db)
    monkeypatch.setattr("core.auth.bcrypt.checkpw", lambda *_: False)
    for index in range(20):
        with pytest.raises(AuthError, match="邮箱或密码"):
            auth.login(f"missing-{index}@example.com", "WrongPassword123", "192.0.2.50", "pytest")
    with pytest.raises(AuthError, match="尝试次数过多"):
        auth.login("blocked@example.com", "WrongPassword123", "192.0.2.50", "pytest")

    for index in range(10):
        assert auth.request_password_reset(f"missing-{index}@example.com", "198.51.100.50") is None
    with pytest.raises(AuthError, match="尝试次数过多"):
        auth.request_password_reset("another@example.com", "198.51.100.50")

    user = _user(auth, "one-use-reset")
    token = auth.request_password_reset(user["email"], "203.0.113.50")
    assert token
    auth.reset_password(token, "ReplacementPassword456")
    with pytest.raises(AuthError, match="无效或已过期"):
        auth.reset_password(token, "AnotherPassword789")


def test_rate_limit_record_is_atomic_after_bucket_is_blocked(db):
    auth = AuthService(db)
    key = auth._rate_key("atomic-rate", "*", "203.0.113.99")
    now = datetime.now(UTC)
    for _ in range(2):
        auth._record_attempt(
            key,
            now,
            limit=2,
            window=timedelta(minutes=1),
            block=timedelta(minutes=1),
        )

    with pytest.raises(AuthError, match="尝试次数过多"):
        auth._record_attempt(
            key,
            now,
            limit=2,
            window=timedelta(minutes=1),
            block=timedelta(minutes=1),
        )

    assert db.fetch_one("SELECT attempts FROM auth_rate_limits WHERE rate_key=?", (key,))["attempts"] == 2


def test_production_requires_one_time_email_verification(db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    auth = AuthService(db)
    user = auth.register("verify@example.com", "CorrectHorse123", "Verify User", True)
    assert user["email_verified_at"] is None
    with pytest.raises(AuthError, match="邮箱验证"):
        auth.login(user["email"], "CorrectHorse123", "192.0.2.90", "pytest")

    token = auth.request_email_verification(user["email"], "192.0.2.90")
    assert token
    auth.verify_email(token)
    with pytest.raises(AuthError, match="无效或已过期"):
        auth.verify_email(token)
    assert auth.login(user["email"], "CorrectHorse123", "192.0.2.90", "pytest").user[
        "email_verified_at"
    ]


def test_payment_requires_terms_and_never_allows_voluntary_refund(db):
    auth = AuthService(db)
    user = _user(auth, "billing")
    service = OrderService(db)
    with pytest.raises(ValueError, match="不退款政策"):
        service.create_order(user["id"], "标准版", "monthly", "fps")
    order = service.create_order(user["id"], "标准版", "monthly", "fps", terms_accepted=True)
    assert service.process_callback("event-1", order["order_no"], "paid", {"event": "paid"}) is True
    assert service.process_callback("event-1", order["order_no"], "paid", {"event": "paid"}) is False
    assert service.refund_eligibility(order["order_no"])[0] is False
    assert service.get_order(order["order_no"])["terms_version"]
    service.log_core_action(user["id"], "买入 Call", "BACKTEST", {})
    assert service.refund_eligibility(order["order_no"])[0] is False


def test_broker_connection_enforces_plan_and_blocks_refund(db):
    auth = AuthService(db)
    free_user = _user(auth, "free-broker")
    with pytest.raises(ValueError, match="暂不支持连接券商"):
        OrderManager(db).add_broker_account(
            free_user["id"], "Tiger", "模拟账户", "PAPER-1", "paper"
        )

    paid_user = _user(auth, "paid-broker")
    service = OrderService(db)
    order = service.create_order(paid_user["id"], "专业版", "monthly", "fps", terms_accepted=True)
    assert service.process_callback("broker-plan-paid", order["order_no"], "paid", {})
    assert service.refund_eligibility(order["order_no"])[0] is False
    OrderManager(db).add_broker_account(
        paid_user["id"], "Tiger", "模拟账户", "PAPER-2", "paper"
    )
    with pytest.raises(ValueError, match="已经登记"):
        OrderManager(db).add_broker_account(
            paid_user["id"], "Tiger", "重复账户", "PAPER-2", "paper"
        )
    OrderManager(db).add_broker_account(
        paid_user["id"], "Tiger", "第二账户", "PAPER-3", "paper"
    )
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM broker_accounts WHERE user_id=? AND is_active=1",
        (paid_user["id"],),
    )["count"] == 2
    assert service.refund_eligibility(order["order_no"])[0] is False


def test_alert_limits_and_risk_filter(db):
    auth = AuthService(db)
    user = _user(auth, "alerts")
    alerts = AlertService(db)
    alerts.create(user["id"], "免费版", "AAPL", ">=", 200)
    with pytest.raises(ValueError, match="最多可启用 1 条"):
        alerts.create(user["id"], "免费版", "MSFT", "<=", 400)
    decision = validate_order(
        symbol="AAPL", quantity=100, price=100, symbol_exposure=0, total_exposure=0, daily_pnl=0,
        config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
        paused=False, require_market_hours=False,
    )
    assert decision.allowed is False
    assert decision.code == "POSITION_LIMIT"


def test_trade_risk_state_uses_realized_pnl_and_loss_streak():
    now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
    trades = [
        {"symbol": "AAPL", "side": "BUY", "quantity": 10, "price": 100, "commission": 0, "trade_time": (now - timedelta(days=1)).isoformat()},
        {"symbol": "AAPL", "side": "SELL", "quantity": 5, "price": 90, "commission": 0, "trade_time": (now - timedelta(minutes=10)).isoformat()},
        {"symbol": "AAPL", "side": "SELL", "quantity": 2, "price": 80, "commission": 0, "trade_time": (now - timedelta(minutes=2)).isoformat()},
    ]

    state = trade_ledger_state(trades, now)

    assert state["daily_pnl"] == -90
    assert state["consecutive_losses"] == 2
    assert state["positions"]["AAPL"] == 3
    assert state["exposures"]["AAPL"] == 240


def test_orders_and_risk_logs_are_isolated_by_user(db):
    auth = AuthService(db)
    first = _user(auth, "trader-one")
    second = _user(auth, "trader-two")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 1_000, "max_total_position": 5_000, "max_daily_loss": 500}

    manager.submit(user_id=first["id"], symbol="AAPL", side="BUY", quantity=1, price=100, strategy="测试", mode="paper", risk_config=risk, paused=False)
    manager.submit(user_id=second["id"], symbol="AAPL", side="BUY", quantity=1, price=100, strategy="测试", mode="paper", risk_config=risk, paused=False)
    manager.submit(user_id=first["id"], symbol="AAPL", side="SELL", quantity=1, price=100, strategy="测试", mode="paper", risk_config=risk, paused=False)

    with pytest.raises(ValueError, match="单标的仓位上限"):
        manager.submit(user_id=first["id"], symbol="NVDA", side="BUY", quantity=20, price=100, strategy="测试", mode="paper", risk_config=risk, paused=False)

    assert len(db.get_risk_logs(user_id=first["id"])) == 1
    assert db.get_risk_logs(user_id=second["id"]) == []


def test_us_and_a_share_risk_ledgers_do_not_mix_currencies(db):
    user = _user(AuthService(db), "cross-market-risk")
    manager = OrderManager(db)
    risk = {
        "max_position_per_symbol": 20_000,
        "max_total_position": 20_000,
        "max_daily_loss": 2_000,
        "max_position_per_symbol_cny": 50_000,
        "max_total_position_cny": 50_000,
        "max_daily_loss_cny": 10_000,
    }

    manager.submit(
        user_id=user["id"], symbol="AAPL", side="BUY", quantity=100, price=100,
        strategy="测试", mode="paper", risk_config=risk, paused=False,
    )
    a_share = manager.submit(
        user_id=user["id"], symbol="600519", side="BUY", quantity=100, price=500,
        strategy="测试", mode="paper", risk_config=risk, paused=False,
    )

    assert a_share["status"] == "FILLED"
    with pytest.raises(ValueError, match="账户总仓位上限"):
        manager.submit(
            user_id=user["id"], symbol="000001", side="BUY", quantity=1, price=1,
            strategy="测试", mode="paper", risk_config=risk, paused=False,
        )


def test_opening_pause_allows_reduction_but_blocks_new_exposure(db):
    auth = AuthService(db)
    user = _user(auth, "paused-trader")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000}

    manager.submit(user_id=user["id"], symbol="AAPL", side="BUY", quantity=2, price=100,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    reduced = manager.submit(user_id=user["id"], symbol="AAPL", side="SELL", quantity=1, price=101,
                             strategy="测试", mode="paper", risk_config=risk, paused=True)
    assert reduced["status"] == "FILLED"
    with pytest.raises(ValueError, match="暂停"):
        manager.submit(user_id=user["id"], symbol="MSFT", side="BUY", quantity=1, price=100,
                       strategy="测试", mode="paper", risk_config=risk, paused=True)


def test_order_manager_rejects_invalid_modes_and_sides(db):
    user = _user(AuthService(db), "invalid-order")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000}

    with pytest.raises(ValueError, match="方向"):
        manager.submit(user_id=user["id"], symbol="AAPL", side="HOLD", quantity=1, price=100,
                       strategy="测试", mode="paper", risk_config=risk, paused=False)
    with pytest.raises(ValueError, match="账户模式"):
        manager.submit(user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
                       strategy="测试", mode="preview", risk_config=risk, paused=False)
    with pytest.raises(ValueError, match="标的"):
        manager.submit(user_id=user["id"], symbol="AAPL<script>", side="BUY", quantity=1, price=100,
                       strategy="测试", mode="paper", risk_config=risk, paused=False)


def test_advanced_live_trade_requires_extra_contract(db, monkeypatch):
    user = _user(AuthService(db), "advanced-live")
    db.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "paper")
    monkeypatch.delenv("TRADEAI_STOCK_AUTO_CONTRACT_USER_IDS", raising=False)
    with pytest.raises(ValueError, match="额外签约"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )
    monkeypatch.setenv("TRADEAI_STOCK_AUTO_CONTRACT_USER_IDS", str(user["id"]))
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    with pytest.raises(ValueError, match="不是 live"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )


def test_professional_live_trade_does_not_require_extra_contract(db, monkeypatch):
    user = _user(AuthService(db), "professional-live")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "paper")
    monkeypatch.delenv("TRADEAI_STOCK_AUTO_CONTRACT_USER_IDS", raising=False)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)

    with pytest.raises(ValueError, match="不是 live"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )


def test_live_trade_requires_user_level_auto_switch(db, monkeypatch):
    user = _user(AuthService(db), "live-switch")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    monkeypatch.setenv("TIGER_REAL_TRADING_ENABLED", "true")

    with pytest.raises(ValueError, match="用户实盘自动交易开关未开启"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )


def test_smtp_sender_can_differ_from_login_user(monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def starttls(self, **_kwargs):
            pass

        def login(self, *_args):
            pass

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("notification.email_sender.smtplib.SMTP", SMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "smtp-login")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "CicloTrade <support@ciclotrade.com>")

    send_email("user@example.com", "subject", "body")

    assert sent[0]["From"] == "CicloTrade <support@ciclotrade.com>"


def test_background_alert_scan_triggers_without_open_page(db, monkeypatch):
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda: False)
    auth = AuthService(db)
    user = _user(auth, "background-alert")
    AlertService(db).create(user["id"], "免费版", "AAPL", ">=", 200)

    class Prices:
        def history(self, symbols, period):
            return pd.DataFrame({"AAPL": [199.0, 201.0]}), pd.DataFrame({"AAPL": [1, 1]})

    assert scan_price_alerts(db, Prices()) == 1
    assert AlertService(db).list(user["id"])[0]["is_active"] == 0
    assert db.fetch_one("SELECT msg_type FROM notifications")["msg_type"] == "PRICE_ALERT"


def test_background_telegram_uses_only_verified_user_destination(db, monkeypatch):
    auth = AuthService(db)
    opted_in = _user(auth, "telegram-opted-in")
    no_consent = _user(auth, "telegram-no-consent")
    for user in (opted_in, no_consent):
        db.execute(
            "UPDATE users SET plan_type='高级版',subscription_expire=? WHERE id=?",
            ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
        )
        AlertService(db).create(user["id"], "免费版", "AAPL", ">=", 200)
    merge_user_settings(
        opted_in["id"],
        {
            "tg_events": {"price_alert": True},
            "telegram": {"consent": True, "verified": True, "chat_id": "123456789"},
        },
        db,
    )

    class Prices:
        def history(self, symbols, period):
            return pd.DataFrame({"AAPL": [199.0, 201.0]}), pd.DataFrame({"AAPL": [1, 1]})

    sent = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr(
        "scheduler.jobs.send_telegram",
        lambda message, chat_id=None: sent.append((message, chat_id)),
    )
    assert scan_price_alerts(db, Prices()) == 2
    assert len(sent) == 1 and sent[0][1] == "123456789"


def test_tiger_live_orders_require_matching_global_account_operator(monkeypatch):
    monkeypatch.setenv("TIGER_ENV", "live")
    monkeypatch.setenv("TIGER_REAL_TRADING_ENABLED", "true")
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", "7")
    tiger = TigerAPI()
    with pytest.raises(RuntimeError, match="实盘操作员"):
        tiger.place_stock_limit("AAPL", "BUY", 1, 100, user_id=8)

    monkeypatch.setenv("TIGER_ENV", "paper")
    with pytest.raises(RuntimeError, match="不是 live"):
        TigerAPI().place_stock_limit("AAPL", "BUY", 1, 100, user_id=7)


def test_renewal_reminder_is_sent_once_per_expiry(db, monkeypatch):
    user = _user(AuthService(db), "renewal")
    expiry = (datetime.now(UTC) + timedelta(days=3)).isoformat(timespec="seconds")
    db.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?", (expiry, user["id"])
    )
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr("scheduler.jobs.smtp_configured", lambda: True)
    monkeypatch.setattr("scheduler.jobs.send_email", lambda *args: sent.append(args))

    assert notify_expiring_subscriptions(db) == 1
    assert notify_expiring_subscriptions(db) == 0
    assert sent[0][0] == user["email"]


def test_expired_membership_notifies_support_and_removes_paid_groups(db, monkeypatch):
    user = _user(AuthService(db), "expired-membership")
    expiry = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=? WHERE id=?",
        (expiry, user["id"]),
    )
    merge_user_settings(
        user["id"],
        {"telegram": {"consent": True, "verified": True, "chat_id": "778899"}},
        db,
    )
    removed = []
    emails = []
    monkeypatch.setenv("TELEGRAM_MEMBERSHIP_SYNC_ENABLED", "true")
    monkeypatch.setattr("scheduler.jobs.remove_group_member", lambda group, member: removed.append((group, member)))
    monkeypatch.setattr("scheduler.jobs.smtp_configured", lambda: True)
    monkeypatch.setattr("scheduler.jobs.send_email", lambda *args: emails.append(args))

    assert downgrade_expired_subscriptions(db) == 1
    assert downgrade_expired_subscriptions(db) == 0
    assert db.fetch_one("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user["id"],)) == {
        "plan_type": "免费版", "subscription_expire": None,
    }
    assert removed == [("-1004460522940", "778899"), ("-5344553813", "778899")]
    assert {message[0] for message in emails} == {user["email"], "support@ciclotrade.com"}


def test_all_eight_option_payoffs_return_finite_values():
    names = (
        "买入 Call", "买入 Put", "牛市价差", "熊市价差",
        "买入跨式", "蝶式", "备兑看涨", "现金担保看跌",
    )
    values = [option_payoff(name, 100, 110, 0, 4, 8) for name in names]
    assert len(values) == 8
    assert all(abs(value) < 1_000 for value in values)
