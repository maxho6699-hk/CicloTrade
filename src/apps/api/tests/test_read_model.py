from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.auth import AuthService, _token
from core.compat import UTC
from core.database import DatabaseManager
from core.quant_journal import QuantJournal
from payment.order_service import OrderService
from src.apps.api.read_model import ReadModelAuthError, ReadOnlyLegacyRepository


@pytest.fixture
def compatibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-that-is-longer-than-32-characters")
    database = DatabaseManager(str(tmp_path / "compatibility.db"))
    auth = AuthService(database)
    user = auth.register("reader@example.com", "StrongPass123", "Reader", True)
    assert user is not None
    login = auth.login("reader@example.com", "StrongPass123", "127.0.0.1", "pytest")
    repository = ReadOnlyLegacyRepository(tmp_path / "compatibility.db")
    return database, user, login, repository


def test_authentication_read_does_not_touch_session_last_active(compatibility):
    database, user, login, repository = compatibility
    before = database.fetch_one(
        "SELECT last_active FROM user_sessions WHERE user_id=? AND is_active=1", (user["id"],)
    )

    identity = repository.authenticate(login.access_token)
    after = database.fetch_one(
        "SELECT last_active FROM user_sessions WHERE user_id=? AND is_active=1", (user["id"],)
    )

    assert identity.id == user["id"]
    assert after == before


def test_settings_without_legacy_table_include_locale_default(compatibility):
    database, _, login, repository = compatibility
    identity = repository.authenticate(login.access_token)
    database.execute("DROP TABLE user_settings")

    assert repository.settings(identity) == {
        "risk": {},
        "telegram_events": {},
        "watchlists": {"us": [], "a_share": []},
        "ui_locale": None,
    }


def test_refresh_token_is_rejected_by_browser_read_api(compatibility):
    _, _, login, repository = compatibility

    with pytest.raises(ReadModelAuthError, match="类型"):
        repository.authenticate(login.refresh_token)


def test_revoked_session_is_rejected(compatibility):
    database, user, login, repository = compatibility
    database.execute("UPDATE user_sessions SET is_active=0 WHERE user_id=?", (user["id"],))

    with pytest.raises(ReadModelAuthError, match="会话"):
        repository.authenticate(login.access_token)


def test_wrong_or_expired_access_token_is_rejected(compatibility):
    _, user, _, repository = compatibility
    expired = _token(user["id"], "missing-session", "access", timedelta(seconds=-1))

    with pytest.raises(ReadModelAuthError):
        repository.authenticate(expired)


def test_option_details_are_filtered_before_response(compatibility):
    database, user, login, repository = compatibility
    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?", (expiry, user["id"])
    )
    QuantJournal(database).append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="option-read-filter",
        strategy_name="call-spread",
        strategy_version="1",
        occurred_at="2026-08-09T12:00:00+00:00",
        legs=[{
            "market": "US", "instrument_type": "option", "symbol": "AAPL",
            "option_expiry": "2026-09-18", "option_right": "CALL", "option_strike": 210,
            "target_quantity": 1, "quantity_delta": 1, "price": 2.5,
        }],
    )

    standard = repository.authenticate(login.access_token)
    locked = repository.timeline(standard)["items"][0]["legs"][0]
    assert locked == {"instrument_type": "option", "locked": True}

    database.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=? WHERE id=?", (expiry, user["id"])
    )
    professional = repository.authenticate(login.access_token)
    visible = repository.timeline(professional)["items"][0]["legs"][0]
    assert visible["option_strike"] == 210
    assert visible["option_expiry"] == "2026-09-18"


def test_stock_recommendations_require_signal_web_entitlement(compatibility):
    database, user, login, repository = compatibility
    QuantJournal(database).append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="stock-read-filter",
        strategy_name="stock-stability",
        strategy_version="3",
        occurred_at="2026-08-09T12:00:00+00:00",
        legs=[{
            "market": "US", "instrument_type": "stock", "symbol": "AAPL",
            "target_quantity": 5, "quantity_delta": 5, "price": 210,
        }],
    )

    free = repository.authenticate(login.access_token)
    assert repository.timeline(free)["items"][0]["legs"][0] == {
        "instrument_type": "stock", "locked": True,
    }
    locked = repository.recommendations(free)["items"][0]
    assert locked["state"] == "locked"
    assert "symbol" not in locked
    assert "reference_price" not in locked

    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (expiry, user["id"]),
    )
    standard = repository.authenticate(login.access_token)
    visible = repository.recommendations(standard)["items"][0]
    assert visible["state"] == "official"
    assert visible["symbol"] == "AAPL"

    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (expired, user["id"]),
    )
    downgraded = repository.authenticate(login.access_token)
    assert repository.recommendations(downgraded)["items"][0]["state"] == "locked"


def test_membership_orders_are_customer_owned_and_provider_fields_are_redacted(compatibility):
    database, user, login, repository = compatibility
    OrderService(database).create_order(
        user["id"], "标准版", "monthly", "paypal",
        terms_accepted=True, idempotency_key="test-membership-order", source="legacy",
    )
    identity = repository.authenticate(login.access_token)

    payload = repository.membership(identity)
    order = payload["orders"][0]

    assert payload["auto_renewal"] is False
    assert order["plan_type"] == "标准版"
    assert order["pay_method"] == "paypal"
    assert order["proof_status"] is None
    assert "external_id" not in order
    assert "external_capture_id" not in order
    assert "request_fingerprint" not in order


def test_telegram_status_masks_chat_identifier(compatibility):
    database, user, login, repository = compatibility
    now = datetime.now(UTC).isoformat()
    database.execute(
        """INSERT INTO telegram_accounts(user_id,chat_id,is_active,created_at,updated_at)
           VALUES (?,?,1,?,?)""",
        (user["id"], "123456789", now, now),
    )
    database.execute(
        "INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES (?,?,?)",
        (user["id"], '{"telegram":{"verified":true,"consent":true,"chat_id":"123456789"},"tg_events":{"stock_signal":true}}', now),
    )
    identity = repository.authenticate(login.access_token)

    payload = repository.telegram_status(identity)

    assert payload["verified"] is True
    assert payload["chat_id_masked"] == "···· 6789"
    assert "123456789" not in str(payload)


def test_portfolio_uses_only_customer_paper_orders(compatibility):
    database, user, login, repository = compatibility
    now = datetime.now(UTC).isoformat()
    database.insert_order({
        "order_id": "PAPER-READ-1", "symbol": "AAPL", "side": "BUY", "quantity": 5,
        "price": 200, "status": "FILLED", "strategy_name": "pytest",
        "reason": f"user={user['id']}", "created_at": now, "account_mode": "paper",
    })
    database.insert_trade({
        "trade_id": "TRADE-READ-1", "order_id": "PAPER-READ-1", "symbol": "AAPL",
        "side": "BUY", "quantity": 5, "price": 200, "commission": 0, "trade_time": now,
    })
    identity = repository.authenticate(login.access_token)

    payload = repository.portfolio(identity)

    assert payload["positions"][0]["quantity"] == 5
    assert payload["mark_source"] == "last_recorded_trade"
    assert payload["fresh_marks"] is False
    assert payload["activity"]["pnl_method"] == "weighted_average"
    assert payload["activity"]["executions"][0]["side"] == "BUY"
    assert payload["activity"]["intervals"][0]["status"] == "OPEN"


def test_portfolio_activity_groups_paper_trades_and_excludes_live_orders(compatibility):
    database, user, login, repository = compatibility
    reason = f"user={user['id']}"
    rows = [
        ("PAPER-CYCLE-1", "BUY", 10, 100, "2026-08-01T10:00:00+00:00", "paper"),
        ("PAPER-CYCLE-2", "BUY", 5, 110, "2026-08-02T10:00:00+00:00", "paper"),
        ("PAPER-CYCLE-3", "SELL", 8, 120, "2026-08-03T10:00:00+00:00", "paper"),
        ("PAPER-CYCLE-4", "SELL", 7, 130, "2026-08-04T10:00:00+00:00", "paper"),
        ("LIVE-CYCLE-1", "BUY", 99, 1, "2026-08-05T10:00:00+00:00", "live"),
    ]
    for order_id, side, quantity, price, created_at, mode in rows:
        database.insert_order({
            "order_id": order_id, "symbol": "AAPL", "side": side, "quantity": quantity,
            "price": price, "status": "FILLED", "strategy_name": "pytest",
            "reason": reason, "created_at": created_at, "account_mode": mode,
        })
        database.insert_trade({
            "trade_id": f"T-{order_id}", "order_id": order_id, "symbol": "AAPL",
            "side": side, "quantity": quantity, "price": price,
            "commission": 1 if mode == "paper" else 0, "trade_time": created_at,
        })
    identity = repository.authenticate(login.access_token)

    payload = repository.portfolio(identity)
    activity = payload["activity"]
    interval = activity["intervals"][0]

    assert {item["order_id"] for item in payload["orders"]} == {
        "PAPER-CYCLE-1", "PAPER-CYCLE-2", "PAPER-CYCLE-3", "PAPER-CYCLE-4",
    }
    assert len(activity["executions"]) == 4
    assert interval["status"] == "CLOSED"
    assert interval["opened_at"] == "2026-08-01T10:00:00+00:00"
    assert interval["closed_at"] == "2026-08-04T10:00:00+00:00"
    assert interval["average_entry_price"] == pytest.approx(103.3333333333)
    assert interval["average_exit_price"] == pytest.approx(124.6666666667)
    assert interval["realized_pnl"] == pytest.approx(316)
    assert interval["result"] == "profit"
    assert interval["execution_ids"] == [item["execution_id"] for item in reversed(activity["executions"])]
