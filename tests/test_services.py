"""认证、支付、预警、风控与 Backtrader 策略公式检查。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import json
import sys
from types import ModuleType

import pytest
import pandas as pd
import jwt

from backtest.engine import option_payoff
from core.alerts import AlertService
from core.auth import AuthError, AuthService
from core.broker_authorization import broker_execution_authorized
from core.database import DatabaseManager
from core.strategy_registry import StrategyRegistry
from core.user_settings import merge_user_settings
from notification.email_sender import send_email
from payment.order_service import OrderService
from scheduler.jobs import downgrade_expired_subscriptions, notify_expiring_subscriptions, scan_price_alerts
from trading.order_manager import OrderManager, derive_execution_slices, trade_ledger_state
from trading.risk_filter import RiskDecision, validate_order
from trading.tiger_api import (
    TigerAPI,
    TigerAPIRejected,
    TigerSubmissionUnknown,
    _new_tiger_send_claim,
    normalize_portfolio,
)


@pytest.fixture
def db(tmp_path):
    database = DatabaseManager(str(tmp_path / "tradeai-test.db"))
    database.execute(
        "INSERT INTO platform_controls (control_key,control_value,updated_at) VALUES ('opening_paused','0',datetime('now'))"
    )
    return database


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-longer-than-thirty-two-characters")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")


def _user(auth: AuthService, suffix: str = "one"):
    return auth.register(f"{suffix}@example.com", "CorrectHorse123", "Test User", True)


def _set_user_auto_trading(db: DatabaseManager, enabled: bool = True) -> None:
    db.execute(
        """INSERT INTO platform_controls (control_key,control_value,updated_at)
           VALUES ('user_auto_trading_enabled',?,datetime('now'))
           ON CONFLICT(control_key) DO UPDATE SET control_value=excluded.control_value,
           updated_at=excluded.updated_at""",
        (str(int(enabled)),),
    )


def _set_opening_pause(db: DatabaseManager, paused: bool, user_id: int | None = None) -> None:
    if user_id is None:
        db.execute(
            """INSERT INTO platform_controls (control_key,control_value,updated_at)
               VALUES ('opening_paused',?,datetime('now'))
               ON CONFLICT(control_key) DO UPDATE SET control_value=excluded.control_value,
               updated_at=excluded.updated_at""",
            (str(int(paused)),),
        )
        return
    db.execute(
        "UPDATE user_controls SET opening_paused=?,updated_at=datetime('now') WHERE user_id=?",
        (int(paused), user_id),
    )


def _authorize_tiger_execution(
    db: DatabaseManager,
    monkeypatch,
    user_id: int,
    *,
    account_id: str = "TEST-TIGER-LIVE",
    provider: str = "Tiger",
    mode: str = "live",
    active: bool = True,
    status: str = "authorized",
    metadata: dict | None = None,
) -> None:
    monkeypatch.setenv("TIGER_ACCOUNT", account_id)
    proof = metadata if metadata is not None else {
        "execution_authorized": True,
        "authorization_verified_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    db.execute(
        """INSERT INTO broker_accounts
           (user_id,provider,account_alias,external_account_id,mode,is_active,status,metadata_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            provider,
            "测试执行账户",
            account_id,
            mode,
            int(active),
            status,
            json.dumps(proof, ensure_ascii=False),
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )


def test_broker_execution_authorization_ttl_configuration_is_fail_closed(monkeypatch):
    monkeypatch.setenv("TIGER_ACCOUNT", "TTL-ACCOUNT")
    row = {
        "provider": "Tiger",
        "is_active": 1,
        "mode": "live",
        "status": "authorized",
        "external_account_id": "TTL-ACCOUNT",
        "metadata_json": json.dumps({
            "execution_authorized": True,
            "authorization_verified_at": (
                datetime.now(UTC) - timedelta(minutes=50)
            ).isoformat(timespec="seconds"),
        }),
    }

    monkeypatch.setenv("TRADEAI_BROKER_AUTHORIZATION_TTL_SECONDS", "3600")
    assert broker_execution_authorized(row) is True
    for invalid_ttl in ("59", "3601", "invalid"):
        monkeypatch.setenv("TRADEAI_BROKER_AUTHORIZATION_TTL_SECONDS", invalid_ttl)
        assert broker_execution_authorized(row) is False


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
    assert len(first) == 8 and first.isalnum()
    assert any(char.isalpha() for char in first) and any(char.isdigit() for char in first)
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
    auth.reset_password(token, "Replace1", "203.0.113.50")
    with pytest.raises(AuthError, match="无效或已过期"):
        auth.reset_password(token, "Another2", "203.0.113.50")


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
    order = service.create_order(
        user["id"], "标准版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
    assert service.process_callback("event-1", order["order_no"], "paid", {"event": "paid"}) is True
    assert service.process_callback("event-1", order["order_no"], "paid", {"event": "paid"}) is False
    assert service.refund_eligibility(order["order_no"])[0] is False
    assert service.get_order(order["order_no"])["terms_version"]
    service.log_core_action(user["id"], "买入 Call", "BACKTEST", {})
    assert service.refund_eligibility(order["order_no"])[0] is False


def test_broker_connection_requires_user_authorization_and_enforces_member_capacity(db):
    auth = AuthService(db)
    free_user = _user(auth, "free-broker")
    with pytest.raises(ValueError, match="没有自动交易控制账号名额"):
        OrderManager(db).add_broker_account(
            free_user["id"], "Tiger", "模拟账户", "PAPER-1", "paper"
        )
    _set_user_auto_trading(db, False)
    with pytest.raises(ValueError, match="自助连接当前关闭"):
        OrderManager(db).add_broker_account(
            free_user["id"], "Tiger", "第二账户", "PAPER-2", "paper"
        )
    _set_user_auto_trading(db)

    advanced_user = _user(auth, "advanced-broker")
    db.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=? WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), advanced_user["id"]),
    )
    OrderManager(db).add_broker_account(
        advanced_user["id"], "Tiger", "高级账户", "ADV-1", "live"
    )
    with pytest.raises(ValueError, match="高级版最多登记 1 个"):
        OrderManager(db).add_broker_account(
            advanced_user["id"], "IBKR", "第二账户", "ADV-2", "live"
        )

    paid_user = _user(auth, "paid-broker")
    service = OrderService(db)
    order = service.create_order(
        paid_user["id"], "专业版", "monthly", "paypal", terms_accepted=True, source="legacy"
    )
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
    for provider, external_id in (("IBKR", "PRO-3"), ("Futu", "PRO-4"), ("Alpaca", "PRO-5")):
        OrderManager(db).add_broker_account(
            paid_user["id"], provider, provider, external_id, "live"
        )
    with pytest.raises(ValueError, match="专业版最多登记 5 个"):
        OrderManager(db).add_broker_account(
            paid_user["id"], "QMT", "第六账户", "PRO-6", "live"
        )
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM broker_accounts WHERE user_id=? AND is_active=1",
        (paid_user["id"],),
    )["count"] == 5
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


def test_persisted_pauses_cannot_be_bypassed_and_allow_all_pure_reductions(db):
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000}
    auth = AuthService(db)
    long_user = _user(auth, "persisted-long-pause")
    short_user = _user(auth, "persisted-short-pause")
    global_user = _user(auth, "global-pause")

    manager.submit(user_id=long_user["id"], symbol="AAPL", side="BUY", quantity=3, price=100,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    _set_opening_pause(db, True, long_user["id"])
    manager.submit(user_id=long_user["id"], symbol="AAPL", side="SELL", quantity=1, price=101,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    manager.submit(user_id=long_user["id"], symbol="AAPL", side="SELL", quantity=2, price=102,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    with pytest.raises(ValueError, match="暂停"):
        manager.submit(user_id=long_user["id"], symbol="MSFT", side="BUY", quantity=1, price=100,
                       strategy="测试", mode="paper", risk_config=risk, paused=False)

    manager.submit(user_id=short_user["id"], symbol="AAPL", side="SELL", quantity=3, price=100,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    _set_opening_pause(db, True, short_user["id"])
    manager.submit(user_id=short_user["id"], symbol="AAPL", side="BUY", quantity=1, price=99,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    manager.submit(user_id=short_user["id"], symbol="AAPL", side="BUY", quantity=2, price=98,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)

    manager.submit(user_id=global_user["id"], symbol="AAPL", side="BUY", quantity=3, price=100,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    _set_opening_pause(db, True)
    manager.submit(user_id=global_user["id"], symbol="AAPL", side="SELL", quantity=1, price=101,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    manager.submit(user_id=global_user["id"], symbol="AAPL", side="SELL", quantity=2, price=102,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    with pytest.raises(ValueError, match="暂停"):
        manager.submit(user_id=global_user["id"], symbol="NVDA", side="BUY", quantity=1, price=100,
                       strategy="测试", mode="paper", risk_config=risk, paused=False)


def test_opening_pause_rejects_position_reversal_as_one_atomic_order(db):
    user = _user(AuthService(db), "paused-reversal")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000}
    manager.submit(user_id=user["id"], symbol="AAPL", side="BUY", quantity=2, price=100,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    _set_opening_pause(db, True)

    with pytest.raises(ValueError, match="暂停"):
        manager.submit(user_id=user["id"], symbol="AAPL", side="SELL", quantity=3, price=101,
                       strategy="测试", mode="paper", risk_config=risk, paused=False)

    assert manager.current_position(user["id"], "AAPL", "paper") == 2


def test_missing_platform_opening_control_fails_closed_but_still_allows_reduction(db):
    user = _user(AuthService(db), "missing-global-opening-control")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000}
    manager.submit(user_id=user["id"], symbol="AAPL", side="BUY", quantity=2, price=100,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    db.execute("DELETE FROM platform_controls WHERE control_key='opening_paused'")

    with pytest.raises(ValueError, match="暂停"):
        manager.submit(user_id=user["id"], symbol="MSFT", side="BUY", quantity=1, price=100,
                       strategy="测试", mode="paper", risk_config=risk, paused=False)

    reduced = manager.submit(
        user_id=user["id"], symbol="AAPL", side="SELL", quantity=1, price=101,
        strategy="测试", mode="paper", risk_config=risk, paused=False,
    )
    assert reduced["execution_slices"] == [{"action": "close_long", "side": "SELL", "quantity": 1}]


@pytest.mark.parametrize(
    "invalid_proof",
    [
        "missing",
        "other_user",
        "paper",
        "inactive",
        "ready",
        "account_mismatch",
        "metadata_missing_authorization",
        "expired",
        "future",
        "malformed_time",
    ],
)
def test_live_reduce_only_requires_fresh_user_execution_authorization(
    db, monkeypatch, invalid_proof,
):
    user = _user(AuthService(db), f"live-auth-{invalid_proof}")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    monkeypatch.setenv("TIGER_ACCOUNT", "EXPECTED-TIGER-ACCOUNT")
    if invalid_proof != "missing":
        proof_user_id = user["id"]
        kwargs = {"account_id": "EXPECTED-TIGER-ACCOUNT"}
        if invalid_proof == "other_user":
            proof_user_id = _user(AuthService(db), "other-live-auth")["id"]
        elif invalid_proof == "paper":
            kwargs["mode"] = "paper"
        elif invalid_proof == "inactive":
            kwargs["active"] = False
        elif invalid_proof == "ready":
            kwargs["status"] = "ready"
        elif invalid_proof == "account_mismatch":
            kwargs["account_id"] = "OTHER-TIGER-ACCOUNT"
        elif invalid_proof == "metadata_missing_authorization":
            kwargs["metadata"] = {
                "authorization_verified_at": datetime.now(UTC).isoformat(timespec="seconds")
            }
        elif invalid_proof == "expired":
            kwargs["metadata"] = {
                "execution_authorized": True,
                "authorization_verified_at": (
                    datetime.now(UTC) - timedelta(minutes=16)
                ).isoformat(timespec="seconds"),
            }
        elif invalid_proof == "future":
            kwargs["metadata"] = {
                "execution_authorized": True,
                "authorization_verified_at": (
                    datetime.now(UTC) + timedelta(minutes=1)
                ).isoformat(timespec="seconds"),
            }
        elif invalid_proof == "malformed_time":
            kwargs["metadata"] = {
                "execution_authorized": True,
                "authorization_verified_at": "not-a-timestamp",
            }
        _authorize_tiger_execution(db, monkeypatch, proof_user_id, **kwargs)
        monkeypatch.setenv("TIGER_ACCOUNT", "EXPECTED-TIGER-ACCOUNT")

    now = datetime.now(UTC).isoformat(timespec="seconds")
    seed_order_id = f"LIVE-AUTH-SEED-{invalid_proof}"
    db.execute(
        """INSERT INTO orders
           (order_id,symbol,side,order_type,quantity,price,status,strategy_name,reason,created_at,account_mode)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (seed_order_id, "AAPL", "BUY", "LMT", 1, 100, "FILLED", "seed", f"user={user['id']}", now, "live"),
    )
    db.execute(
        """INSERT INTO trades (trade_id,order_id,symbol,side,quantity,price,commission,trade_time)
           VALUES (?,?,?,?,?,?,?,?)""",
        (f"T-{seed_order_id}", seed_order_id, "AAPL", "BUY", 1, 100, 0, now),
    )
    constructed: list[int] = []
    placed: list[int] = []

    class FakeTiger:
        environment = "live"

        def __init__(self):
            constructed.append(1)

        def place_stock_limit(self, *_args, **_kwargs):
            placed.append(1)

    monkeypatch.setattr("trading.order_manager.TigerAPI", FakeTiger)

    with pytest.raises(ValueError, match="执行授权证明"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="SELL", quantity=1, price=99,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )

    assert constructed == []
    assert placed == []


def test_live_reduce_only_bypasses_opening_entitlements_after_final_risk_gate(db, monkeypatch):
    user = _user(AuthService(db), "live-exit")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, False)
    _set_opening_pause(db, True)
    _set_opening_pause(db, True, user["id"])
    merge_user_settings(user["id"], {"live_auto_enabled": False}, db)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO orders
           (order_id,symbol,side,order_type,quantity,price,status,strategy_name,reason,created_at,account_mode)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("LIVE-EXIT-SEED", "AAPL", "BUY", "LMT", 2, 100, "FILLED", "seed", f"user={user['id']}", now, "live"),
    )
    db.execute(
        """INSERT INTO trades (trade_id,order_id,symbol,side,quantity,price,commission,trade_time)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("T-LIVE-EXIT-SEED", "LIVE-EXIT-SEED", "AAPL", "BUY", 2, 100, 0, now),
    )
    constructed: list[int] = []
    placed: list[int] = []
    validated_before_construct: list[int] = []

    class FakeTiger:
        environment = "live"

        def __init__(self):
            constructed.append(1)

        def place_stock_limit(self, *_args, **_kwargs):
            _kwargs["pre_send_check"]("TEST-TIGER-LIVE")
            placed.append(1)
            return type("Result", (), {"status": "FILLED"})()

    def allow_risk(**_kwargs):
        validated_before_construct.append(len(constructed))
        return RiskDecision(True, "PASS", "ok")

    monkeypatch.setattr("trading.order_manager.TigerAPI", FakeTiger)
    monkeypatch.setattr("trading.order_manager.validate_order", allow_risk)
    monkeypatch.setattr("trading.order_manager.StrategyRegistry.check_plan_access", lambda *_args: False)

    result = OrderManager(db).submit(
        user_id=user["id"], symbol="AAPL", side="SELL", quantity=2, price=90,
        strategy="strategy-now-disabled", mode="live",
        risk_config={"max_position_per_symbol": 1, "max_total_position": 1, "max_daily_loss": 1},
        paused=True, live_confirmed=True,
    )

    assert result["status"] == "FILLED"
    assert validated_before_construct == [0]
    assert len(constructed) == 1
    assert placed == [1]


def test_tiger_is_not_constructed_for_paused_opening(db, monkeypatch):
    user = _user(AuthService(db), "tiger-paused-open")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    _set_opening_pause(db, True)
    constructed: list[int] = []

    class FakeTiger:
        environment = "live"

        def __init__(self):
            constructed.append(1)

    monkeypatch.setattr("trading.order_manager.TigerAPI", FakeTiger)

    with pytest.raises(ValueError, match="暂停"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )

    assert constructed == []


def test_live_opening_commits_pending_intent_before_construct_and_places_once(db, monkeypatch):
    user = _user(AuthService(db), "live-valid-opening")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    pending_seen: list[str] = []
    placed: list[int] = []

    class FakeTiger:
        environment = "live"

        def __init__(self):
            row = db.fetch_one(
                "SELECT status FROM orders WHERE reason=? AND account_mode='live' ORDER BY rowid DESC LIMIT 1",
                (f"user={user['id']}",),
            )
            pending_seen.append(str(row["status"]) if row else "missing")

        def place_stock_limit(self, *_args, **_kwargs):
            _kwargs["pre_send_check"]("TEST-TIGER-LIVE")
            placed.append(1)
            return type("Result", (), {"status": "FILLED"})()

    monkeypatch.setattr("trading.order_manager.TigerAPI", FakeTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )

    result = OrderManager(db).submit(
        user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
        strategy="测试", mode="live",
        risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
        paused=False, live_confirmed=True,
    )

    assert pending_seen == ["PENDING"]
    assert placed == [1]
    assert result["status"] == "FILLED"


def test_live_preconstruct_revalidation_rejects_expired_proof_without_constructing(db, monkeypatch):
    user = _user(AuthService(db), "live-preconstruct-expiry")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    constructed: list[int] = []
    real_revalidate = OrderManager._revalidate_live_intent
    revalidation_calls: list[int] = []

    def expire_before_first_revalidation(manager, **kwargs):
        if not revalidation_calls:
            expired = {
                "execution_authorized": True,
                "authorization_verified_at": (
                    datetime.now(UTC) - timedelta(minutes=16)
                ).isoformat(timespec="seconds"),
            }
            db.execute(
                "UPDATE broker_accounts SET metadata_json=? WHERE user_id=?",
                (json.dumps(expired), user["id"]),
            )
        revalidation_calls.append(1)
        return real_revalidate(manager, **kwargs)

    class FakeTiger:
        environment = "live"

        def __init__(self):
            constructed.append(1)

    monkeypatch.setattr(OrderManager, "_revalidate_live_intent", expire_before_first_revalidation)
    monkeypatch.setattr("trading.order_manager.TigerAPI", FakeTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )

    with pytest.raises(ValueError, match="授权已失效"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )

    order = db.fetch_one(
        "SELECT status FROM orders WHERE reason=? AND account_mode='live' ORDER BY rowid DESC LIMIT 1",
        (f"user={user['id']}",),
    )
    assert order["status"] == "REJECTED"
    assert constructed == []
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM risk_log WHERE user_id=? AND event_type='LIVE_SEND_REVALIDATION_REJECTED'",
        (user["id"],),
    )["count"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "proof_expired",
        "broker_inactive",
        "broker_status",
        "tiger_account",
        "platform_pause",
        "user_pause",
        "intent_cancelled",
        "intent_owner_changed",
    ],
)
def test_live_final_revalidation_blocks_constructor_time_mutation(db, monkeypatch, mutation):
    user = _user(AuthService(db), f"live-final-{mutation}")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    constructed: list[int] = []
    placed: list[int] = []
    intent_ids: list[str] = []

    class FakeTiger:
        environment = "live"

        def __init__(self):
            constructed.append(1)
            intent = db.fetch_one(
                "SELECT order_id,status FROM orders WHERE reason=? AND account_mode='live' ORDER BY rowid DESC LIMIT 1",
                (f"user={user['id']}",),
            )
            assert intent and intent["status"] == "PENDING"
            intent_ids.append(str(intent["order_id"]))
            if mutation == "proof_expired":
                expired = {
                    "execution_authorized": True,
                    "authorization_verified_at": (
                        datetime.now(UTC) - timedelta(minutes=16)
                    ).isoformat(timespec="seconds"),
                }
                db.execute(
                    "UPDATE broker_accounts SET metadata_json=? WHERE user_id=?",
                    (json.dumps(expired), user["id"]),
                )
            elif mutation == "broker_inactive":
                db.execute("UPDATE broker_accounts SET is_active=0 WHERE user_id=?", (user["id"],))
            elif mutation == "broker_status":
                db.execute("UPDATE broker_accounts SET status='ready' WHERE user_id=?", (user["id"],))
            elif mutation == "tiger_account":
                monkeypatch.setenv("TIGER_ACCOUNT", "CHANGED-TIGER-ACCOUNT")
            elif mutation == "platform_pause":
                _set_opening_pause(db, True)
            elif mutation == "user_pause":
                _set_opening_pause(db, True, user["id"])
            elif mutation == "intent_cancelled":
                db.execute("UPDATE orders SET status='CANCELLED' WHERE order_id=?", (intent["order_id"],))
            elif mutation == "intent_owner_changed":
                db.execute("UPDATE orders SET reason='user=999999' WHERE order_id=?", (intent["order_id"],))

        def place_stock_limit(self, *_args, **_kwargs):
            placed.append(1)
            return type("Result", (), {"status": "FILLED"})()

    monkeypatch.setattr("trading.order_manager.TigerAPI", FakeTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )

    with pytest.raises(ValueError, match="失效|暂停"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )

    assert constructed == [1]
    assert placed == []
    assert db.fetch_one("SELECT status FROM orders WHERE order_id=?", (intent_ids[0],))["status"] == "REJECTED"


@pytest.mark.parametrize(
    "mutation",
    [
        "proof_expired",
        "platform_pause",
        "user_pause",
        "platform_auto",
        "user_auto",
        "membership",
        "strategy",
        "operator",
        "environment",
        "resolved_account",
        "position_became_opening",
    ],
)
def test_live_send_boundary_callback_blocks_mutable_authority_changes(db, monkeypatch, mutation):
    user = _user(AuthService(db), f"live-boundary-{mutation}")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    strategy_name = "测试"
    if mutation == "strategy":
        strategy_name = StrategyRegistry(db).sync_catalog()[0]["name"]

    side = "BUY"
    if mutation == "position_became_opening":
        side = "SELL"
        now = datetime.now(UTC).isoformat(timespec="seconds")
        db.execute(
            """INSERT INTO orders
               (order_id,symbol,side,order_type,quantity,price,status,strategy_name,reason,created_at,account_mode)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("BOUNDARY-SEED", "AAPL", "BUY", "LMT", 1, 100, "FILLED", "seed", f"user={user['id']}", now, "live"),
        )
        db.execute(
            """INSERT INTO trades (trade_id,order_id,symbol,side,quantity,price,commission,trade_time)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("T-BOUNDARY-SEED", "BOUNDARY-SEED", "AAPL", "BUY", 1, 100, 0, now),
        )
    sent: list[int] = []
    intent_ids: list[str] = []

    class BoundaryTiger:
        environment = "live"

        def place_stock_limit(self, *_args, **kwargs):
            intent = db.fetch_one(
                "SELECT order_id FROM orders WHERE reason=? AND account_mode='live' AND status='PENDING' ORDER BY rowid DESC LIMIT 1",
                (f"user={user['id']}",),
            )
            assert intent
            intent_ids.append(str(intent["order_id"]))
            if mutation == "proof_expired":
                db.execute(
                    "UPDATE broker_accounts SET metadata_json=? WHERE user_id=?",
                    (json.dumps({
                        "execution_authorized": True,
                        "authorization_verified_at": (
                            datetime.now(UTC) - timedelta(minutes=16)
                        ).isoformat(timespec="seconds"),
                    }), user["id"]),
                )
            elif mutation == "platform_pause":
                _set_opening_pause(db, True)
            elif mutation == "user_pause":
                _set_opening_pause(db, True, user["id"])
            elif mutation == "platform_auto":
                _set_user_auto_trading(db, False)
            elif mutation == "user_auto":
                merge_user_settings(user["id"], {"live_auto_enabled": False}, db)
            elif mutation == "membership":
                db.execute(
                    "UPDATE users SET subscription_expire=? WHERE id=?",
                    ((datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds"), user["id"]),
                )
            elif mutation == "strategy":
                db.execute("UPDATE strategy_definitions SET is_active=0 WHERE name=?", (strategy_name,))
            elif mutation == "operator":
                monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", "999999")
            elif mutation == "environment":
                monkeypatch.setenv("TIGER_ENV", "paper")
            elif mutation == "position_became_opening":
                now = datetime.now(UTC).isoformat(timespec="seconds")
                db.execute(
                    """INSERT INTO orders
                       (order_id,symbol,side,order_type,quantity,price,status,strategy_name,reason,created_at,account_mode)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    ("BOUNDARY-FLAT", "AAPL", "SELL", "LMT", 1, 99, "FILLED", "sync", f"user={user['id']}", now, "live"),
                )
                db.execute(
                    """INSERT INTO trades (trade_id,order_id,symbol,side,quantity,price,commission,trade_time)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    ("T-BOUNDARY-FLAT", "BOUNDARY-FLAT", "AAPL", "SELL", 1, 99, 0, now),
                )
                _set_opening_pause(db, True)
            resolved_account = (
                "DIFFERENT-PROPERTIES-ACCOUNT"
                if mutation == "resolved_account"
                else "TEST-TIGER-LIVE"
            )
            kwargs["pre_send_check"](resolved_account)
            sent.append(1)
            return type("Result", (), {"status": "FILLED"})()

    monkeypatch.setattr("trading.order_manager.TigerAPI", BoundaryTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )

    with pytest.raises(ValueError, match="失效|暂停|关闭"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side=side, quantity=1, price=100,
            strategy=strategy_name, mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )

    assert sent == []
    assert db.fetch_one("SELECT status FROM orders WHERE order_id=?", (intent_ids[0],))["status"] == "REJECTED"


def test_live_unknown_submission_blocks_identical_retry(db, monkeypatch):
    user = _user(AuthService(db), "live-submission-unknown")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    attempts: list[int] = []

    class UnknownTiger:
        environment = "live"

        def place_stock_limit(self, *_args, **kwargs):
            kwargs["pre_send_check"]("TEST-TIGER-LIVE")
            attempts.append(1)
            raise TigerSubmissionUnknown("Tiger 提交结果未知，禁止重试并等待订单对账。")

    monkeypatch.setattr("trading.order_manager.TigerAPI", UnknownTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )
    manager = OrderManager(db)
    submit = dict(
        user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
        strategy="测试", mode="live",
        risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
        paused=False, live_confirmed=True,
    )

    with pytest.raises(TigerSubmissionUnknown, match="禁止重试"):
        manager.submit(**submit)
    db.execute(
        "UPDATE orders SET created_at=? WHERE reason=? AND account_mode='live'",
        (
            (datetime.now(UTC) - timedelta(minutes=2)).isoformat(timespec="seconds"),
            f"user={user['id']}",
        ),
    )
    with pytest.raises(ValueError, match="相同未完成订单"):
        manager.submit(**submit)

    order = db.fetch_one(
        "SELECT status FROM orders WHERE reason=? AND account_mode='live' ORDER BY rowid DESC LIMIT 1",
        (f"user={user['id']}",),
    )
    assert order["status"] == "SUBMISSION_UNKNOWN"
    assert attempts == [1]
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM risk_log WHERE user_id=? AND event_type='LIVE_SUBMISSION_UNKNOWN'",
        (user["id"],),
    )["count"] == 1


@pytest.mark.parametrize("broker_status", ["initial", "none", "missing"])
def test_live_nonterminal_broker_status_is_submitted_and_blocks_old_duplicate(
    db, monkeypatch, broker_status,
):
    user = _user(AuthService(db), f"live-submitted-{broker_status}")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)
    attempts: list[int] = []

    class SuccessfulTiger:
        environment = "live"

        def place_stock_limit(self, *_args, **kwargs):
            kwargs["pre_send_check"]("TEST-TIGER-LIVE")
            attempts.append(1)
            if broker_status == "initial":
                return type(
                    "Result",
                    (),
                    {"status": type("OrderStatus", (), {"value": "Initial"})()},
                )()
            if broker_status == "none":
                return type("Result", (), {"status": None})()
            return type("Result", (), {})()

    monkeypatch.setattr("trading.order_manager.TigerAPI", SuccessfulTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )
    manager = OrderManager(db)
    submit = dict(
        user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
        strategy="测试", mode="live",
        risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
        paused=False, live_confirmed=True,
    )

    result = manager.submit(**submit)
    assert result["status"] == "SUBMITTED"
    db.execute(
        "UPDATE orders SET created_at=? WHERE order_id=?",
        (
            (datetime.now(UTC) - timedelta(minutes=2)).isoformat(timespec="seconds"),
            result["order_id"],
        ),
    )

    with pytest.raises(ValueError, match="相同未完成订单"):
        manager.submit(**submit)
    assert attempts == [1]
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM orders WHERE reason=? AND account_mode='live'",
        (f"user={user['id']}",),
    )["count"] == 1


def test_live_explicit_broker_rejection_marks_order_rejected(db, monkeypatch):
    user = _user(AuthService(db), "live-explicit-reject")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)

    class RejectedTiger:
        environment = "live"

        def place_stock_limit(self, *_args, **kwargs):
            kwargs["pre_send_check"]("TEST-TIGER-LIVE")
            raise TigerAPIRejected("broker rejected")

    monkeypatch.setattr("trading.order_manager.TigerAPI", RejectedTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )

    with pytest.raises(TigerAPIRejected, match="broker rejected"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )

    assert db.fetch_one(
        "SELECT status FROM orders WHERE reason=? AND account_mode='live' ORDER BY rowid DESC LIMIT 1",
        (f"user={user['id']}",),
    )["status"] == "REJECTED"


def test_live_success_with_local_status_failure_never_becomes_rejected(db, monkeypatch):
    user = _user(AuthService(db), "live-local-status-failure")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "live")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db, True)
    merge_user_settings(user["id"], {"live_auto_enabled": True}, db)

    class SuccessfulTiger:
        environment = "live"

        def place_stock_limit(self, *_args, **kwargs):
            kwargs["pre_send_check"]("TEST-TIGER-LIVE")
            return type("Result", (), {"status": "FILLED"})()

    monkeypatch.setattr("trading.order_manager.TigerAPI", SuccessfulTiger)
    monkeypatch.setattr(
        "trading.order_manager.validate_order",
        lambda **_kwargs: RiskDecision(True, "PASS", "ok"),
    )
    monkeypatch.setattr(db, "update_order_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(TigerSubmissionUnknown, match="本地状态未能确认"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )

    status = db.fetch_one(
        "SELECT status FROM orders WHERE reason=? AND account_mode='live' ORDER BY rowid DESC LIMIT 1",
        (f"user={user['id']}",),
    )["status"]
    assert status in {"SENDING", "SUBMISSION_UNKNOWN"}


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


def test_us_short_open_close_is_negative_position_and_action_safe(db):
    user = _user(AuthService(db), "short-enabled")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000}

    opened = manager.submit(
        user_id=user["id"], symbol="AAPL", side="SELL", quantity=3, price=200,
        strategy="测试", mode="paper", risk_config=risk, paused=False,
    )
    assert opened["status"] == "FILLED"
    assert opened["execution_slices"] == [{"action": "open_short", "side": "SELL", "quantity": 3}]
    assert trade_ledger_state(db.fetch_all(
        "SELECT t.symbol,t.side,t.quantity,t.price,t.commission,t.trade_time FROM trades t JOIN orders o ON o.order_id=t.order_id WHERE o.reason=?",
        (f"user={user['id']}",),
    ))["positions"]["AAPL"] == -3

    closed = manager.submit(
        user_id=user["id"], symbol="AAPL", side="BUY", quantity=2, price=190,
        strategy="测试", mode="paper", risk_config=risk, paused=False,
    )
    assert closed["status"] == "FILLED"
    assert closed["execution_slices"] == [{"action": "close_short", "side": "BUY", "quantity": 2}]


def test_us_paper_short_is_not_a_membership_capability_and_a_share_stays_long_only(db):
    free_user = _user(AuthService(db), "short-free")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000}
    order = manager.submit(user_id=free_user["id"], symbol="AAPL", side="SELL", quantity=1, price=100,
                           strategy="测试", mode="paper", risk_config=risk, paused=False)
    assert order["execution_slices"][0]["action"] == "open_short"
    assert manager.current_position(free_user["id"], "AAPL") == -1

    with pytest.raises(ValueError, match="A 股暂不支持建立空头仓位"):
        manager.submit(user_id=free_user["id"], symbol="600519", side="SELL", quantity=1, price=100,
                       strategy="测试", mode="paper", risk_config=risk, paused=False)


def test_buy_sell_automatically_split_position_reversals(db):
    user = _user(AuthService(db), "position-reversal")
    manager = OrderManager(db)
    risk = {"max_position_per_symbol": 50_000, "max_total_position": 100_000, "max_daily_loss": 20_000}

    manager.submit(user_id=user["id"], symbol="AAPL", side="BUY", quantity=10, price=100,
                   strategy="测试", mode="paper", risk_config=risk, paused=False)
    short = manager.submit(user_id=user["id"], symbol="AAPL", side="SELL", quantity=20, price=110,
                           strategy="测试", mode="paper", risk_config=risk, paused=False)
    assert short["position_action"] == "reverse_position"
    assert short["execution_slices"] == [
        {"action": "close_long", "side": "SELL", "quantity": 10},
        {"action": "open_short", "side": "SELL", "quantity": 10},
    ]
    assert manager.current_position(user["id"], "AAPL") == -10

    long = manager.submit(user_id=user["id"], symbol="AAPL", side="BUY", quantity=20, price=105,
                          strategy="测试", mode="paper", risk_config=risk, paused=False)
    assert long["execution_slices"] == [
        {"action": "close_short", "side": "BUY", "quantity": 10},
        {"action": "open_long", "side": "BUY", "quantity": 10},
    ]
    assert manager.current_position(user["id"], "AAPL") == 10
    assert derive_execution_slices(10, "SELL", 5) == [
        {"action": "close_long", "side": "SELL", "quantity": 5}
    ]


def test_advanced_live_trade_uses_same_user_switch_as_other_plans(db, monkeypatch):
    user = _user(AuthService(db), "advanced-live")
    db.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=?,is_admin=1 WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", str(user["id"]))
    monkeypatch.setenv("TIGER_ENV", "paper")
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db)
    with pytest.raises(ValueError, match="用户实盘自动交易开关未开启"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )
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
    _authorize_tiger_execution(db, monkeypatch, user["id"])
    _set_user_auto_trading(db)
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
    _authorize_tiger_execution(db, monkeypatch, user["id"])

    with pytest.raises(ValueError, match="用户实盘自动交易开关未开启"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )
    _set_user_auto_trading(db, False)
    with pytest.raises(ValueError, match="总开关当前关闭"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )
    _set_user_auto_trading(db)
    with pytest.raises(ValueError, match="用户实盘自动交易开关未开启"):
        OrderManager(db).submit(
            user_id=user["id"], symbol="AAPL", side="BUY", quantity=1, price=100,
            strategy="测试", mode="live",
            risk_config={"max_position_per_symbol": 5_000, "max_total_position": 50_000, "max_daily_loss": 2_000},
            paused=False, live_confirmed=True,
        )


def test_tiger_paper_snapshot_normalizes_stock_option_and_history():
    from types import SimpleNamespace

    assets = [SimpleNamespace(summary=SimpleNamespace(
        currency="USD", net_liquidation=1_000_000, available_funds=700_000,
        cash=700_000, gross_position_value=300_000,
    ))]
    positions = [
        SimpleNamespace(
            contract=SimpleNamespace(symbol="AAPL", sec_type="STK", currency="USD"),
            quantity=100, average_cost=180, market_price=190, market_value=19_000,
            realized_pnl=20, unrealized_pnl=1_000, today_pnl=150,
        ),
        SimpleNamespace(
            contract=SimpleNamespace(
                symbol="AAPL", sec_type="OPT", currency="USD", expiry="20261218",
                strike=200, put_call="CALL",
            ),
            quantity=2, average_cost=5, market_price=6, market_value=1_200,
            realized_pnl=0, unrealized_pnl=200, today_pnl=40,
        ),
    ]
    orders = [SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL", sec_type="STK"), action="BUY",
        quantity=100, filled=100, avg_fill_price=180, commission=1,
        status="FILLED", order_time=1_750_000_000_000, trade_time=None,
    )]

    snapshot = normalize_portfolio(assets, positions, orders)

    assert snapshot["account"]["total_assets"] == 1_000_000
    assert snapshot["account"]["unrealized_pnl"] == 1_200
    assert [row["instrument_type"] for row in snapshot["positions"]] == ["stock", "option"]
    assert snapshot["orders"][0]["status"] == "FILLED"


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
        lambda message, chat_id=None, **_kwargs: sent.append((message, chat_id)),
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


def test_tiger_send_boundary_requires_claim_sets_user_mark_and_classifies_failures(monkeypatch):
    from types import SimpleNamespace
    from tigeropen.common.exceptions import ApiException

    contract_module = ModuleType("tigeropen.common.util.contract_utils")
    contract_module.stock_contract = lambda **kwargs: SimpleNamespace(**kwargs)
    order_module = ModuleType("tigeropen.common.util.order_utils")
    order_module.limit_order = lambda account, contract, action, quantity, limit_price: SimpleNamespace(
        account=account,
        contract=contract,
        action=action,
        quantity=quantity,
        limit_price=limit_price,
        user_mark=None,
    )
    monkeypatch.setitem(sys.modules, "tigeropen.common.util.contract_utils", contract_module)
    monkeypatch.setitem(sys.modules, "tigeropen.common.util.order_utils", order_module)
    monkeypatch.setenv("TIGER_ENV", "live")
    monkeypatch.setenv("TIGER_REAL_TRADING_ENABLED", "true")
    monkeypatch.setenv("TRADEAI_LIVE_OPERATOR_USER_ID", "7")
    monkeypatch.setenv("TIGER_ACCOUNT", "BOUNDARY-ACCOUNT")

    def claim(account, intent_id, **overrides):
        payload = {
            "user_id": 7,
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 1,
            "price": 100,
        }
        payload.update(overrides)
        return _new_tiger_send_claim(account, intent_id, **payload)

    class Client:
        def __init__(self, failure=None):
            self.failure = failure
            self.orders = []

        def place_order(self, order):
            self.orders.append(order)
            if self.failure:
                raise self.failure
            return 12345

    tiger = TigerAPI()
    tiger._client = Client()
    with pytest.raises(RuntimeError, match="受控发送前授权复验"):
        tiger.place_stock_limit("AAPL", "BUY", 1, 100, user_id=7, intent_id="INTENT-NO-CALLBACK")
    with pytest.raises(TigerAPIRejected, match="一次性执行权证"):
        tiger.place_stock_limit(
            "AAPL", "BUY", 1, 100, user_id=7, intent_id="INTENT-NOOP",
            pre_send_check=lambda _account: None,
        )
    assert tiger._client.orders == []

    one_shot_claim = claim("BOUNDARY-ACCOUNT", "INTENT-VALID")
    placed = tiger.place_stock_limit(
        "AAPL", "BUY", 1, 100, user_id=7, intent_id="INTENT-VALID",
        pre_send_check=lambda _account: one_shot_claim,
    )
    assert placed.user_mark == "INTENT-VALID"
    assert len(tiger._client.orders) == 1
    with pytest.raises(TigerAPIRejected, match="一次性执行权证"):
        tiger.place_stock_limit(
            "AAPL", "BUY", 1, 100, user_id=7, intent_id="INTENT-VALID",
            pre_send_check=lambda _account: one_shot_claim,
        )
    assert len(tiger._client.orders) == 1

    mismatch_cases = [
        ("user", {}, {"user_id": 8}),
        ("symbol", {"symbol": "MSFT"}, {}),
        ("side", {"side": "SELL"}, {}),
        ("quantity", {"quantity": 2}, {}),
        ("price", {"price": 101}, {}),
    ]
    for label, actual_overrides, claim_overrides in mismatch_cases:
        intent_id = f"INTENT-MISMATCH-{label.upper()}"
        mismatch_client = Client()
        tiger._client = mismatch_client
        actual_payload = {
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 1,
            "price": 100,
            **actual_overrides,
        }
        mismatched_claim = claim(
            "BOUNDARY-ACCOUNT",
            intent_id,
            **claim_overrides,
        )
        with pytest.raises(TigerAPIRejected, match="一次性执行权证"):
            tiger.place_stock_limit(
                actual_payload["symbol"],
                actual_payload["side"],
                actual_payload["quantity"],
                actual_payload["price"],
                user_id=7,
                intent_id=intent_id,
                pre_send_check=lambda _account, token=mismatched_claim: token,
            )
        assert mismatch_client.orders == []

    revoked_client = Client()
    tiger._client = revoked_client

    def revoke_environment(account):
        monkeypatch.setenv("TIGER_ENV", "paper")
        return claim(account, "INTENT-REVOKED")

    with pytest.raises(TigerAPIRejected, match="不是 live"):
        tiger.place_stock_limit(
            "AAPL", "BUY", 1, 100, user_id=7, intent_id="INTENT-REVOKED",
            pre_send_check=revoke_environment,
        )
    assert revoked_client.orders == []
    monkeypatch.setenv("TIGER_ENV", "live")

    tiger._client = Client(TimeoutError("timeout"))
    with pytest.raises(TigerSubmissionUnknown, match="禁止重试"):
        tiger.place_stock_limit(
            "AAPL", "BUY", 1, 100, user_id=7, intent_id="INTENT-TIMEOUT",
            pre_send_check=lambda account: claim(account, "INTENT-TIMEOUT"),
        )

    tiger._client = Client(ApiException("ORDER_REJECTED", "rejected"))
    with pytest.raises(TigerAPIRejected, match="ORDER_REJECTED"):
        tiger.place_stock_limit(
            "AAPL", "BUY", 1, 100, user_id=7, intent_id="INTENT-REJECTED",
            pre_send_check=lambda account: claim(account, "INTENT-REJECTED"),
        )


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
    assert removed == [("-1004460522940", "778899"), ("-1003902118990", "778899")]
    assert {message[0] for message in emails} == {user["email"], "support@ciclotrade.com"}


def test_all_eight_option_payoffs_return_finite_values():
    names = (
        "买入 Call", "买入 Put", "牛市价差", "熊市价差",
        "买入跨式", "蝶式", "备兑看涨", "现金担保看跌",
    )
    values = [option_payoff(name, 100, 110, 0, 4, 8) for name in names]
    assert len(values) == 8
    assert all(abs(value) < 1_000 for value in values)
