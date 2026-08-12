from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.auth import AuthService, _token
from core.compat import UTC
from core.database import DatabaseManager
from core.membership import MembershipPlanConflict, add_membership_entitlement
from core.plans import PLAN_ORDER, PLANS, plan_display_name
from core.quant_journal import OfficialPaperJournalV2, QuantJournal
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
        "watchlist_pins": {"us": [], "a_share": []},
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
        metadata={"action_contract": {
            "stop_price": 200, "target_price": 225, "max_loss": 50,
            "rationale": "价格站稳后分批验证。",
        }},
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
    recommendation = repository.recommendations(professional)["items"][0]
    assert recommendation["option_right"] == "CALL"
    assert recommendation["option_strike"] == 210
    assert recommendation["actionable"] is False
    assert {"bid", "ask", "current_price", "quote_at"}.issubset(recommendation["missing_fields"])


def test_stock_recommendations_require_signal_web_entitlement(compatibility):
    database, user, login, repository = compatibility
    QuantJournal(database).append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="stock-read-filter",
        strategy_name="stock-stability",
        strategy_version="3",
        occurred_at="2026-08-09T12:00:00+00:00",
        metadata={"action_contract": {
            "stop_price": 200, "target_price": 225, "max_loss": 50,
            "rationale": "价格站稳后分批验证。",
            "current_price": 211,
            "quote_at": datetime.now(UTC).isoformat(),
        }},
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
    assert visible["actionable"] is True
    assert visible["contract_status"] == "complete"
    assert visible["stop_price"] == 200

    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (expired, user["id"]),
    )
    downgraded = repository.authenticate(login.access_token)
    assert repository.recommendations(downgraded)["items"][0]["state"] == "locked"


def test_recommendations_distinguish_short_cover_and_incomplete_contract(compatibility):
    database, user, login, repository = compatibility
    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        (expiry, user["id"]),
    )
    journal = QuantJournal(database)
    journal.append_event(
        ledger_key="tradeai-system", source="pytest", external_event_id="short-open",
        strategy_name="short-cycle", strategy_version="1",
        occurred_at="2026-08-09T12:00:00+00:00",
        legs=[{
            "market": "US", "instrument_type": "stock", "symbol": "TSLA",
            "target_quantity": -10, "quantity_delta": -10, "price": 320,
        }],
    )
    journal.append_event(
        ledger_key="tradeai-system", source="pytest", external_event_id="short-cover",
        strategy_name="short-cycle", strategy_version="1",
        occurred_at="2026-08-09T13:00:00+00:00",
        legs=[{
            "market": "US", "instrument_type": "stock", "symbol": "TSLA",
            "target_quantity": 0, "quantity_delta": 10, "price": 305,
        }],
    )

    identity = repository.authenticate(login.access_token)
    items = repository.recommendations(identity)["items"]

    assert [item["action"] for item in items[:2]] == ["COVER", "SHORT"]
    assert [item["position_action"] for item in items[:2]] == ["close_short", "open_short"]
    assert all(item["actionable"] is False for item in items[:2])
    assert "stop_price" in items[0]["missing_fields"]


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
    assert payload["brokerage"] == {
        "auto_control_account_limit": 0,
        "accounts_used": 0,
        "accounts": [],
        "requires_user_authorization": True,
        "short_eligibility_source": "broker",
        "subscription_auto_connects_broker": False,
        "capability_catalog": [
            {
                "key": "tiger", "display_name": "Tiger Brokers",
                "status": "limited_manual_onboarding",
                "capabilities": ["market_data", "us_stock_limit_orders"],
                "connection_available": False,
            },
            {
                "key": "futu", "display_name": "Futu OpenD",
                "status": "market_data_only", "capabilities": ["market_data"],
                "connection_available": False,
            },
            {
                "key": "alpaca", "display_name": "Alpaca",
                "status": "planned", "capabilities": [],
                "connection_available": False,
            },
            {
                "key": "ibkr", "display_name": "Interactive Brokers",
                "status": "planned", "capabilities": [],
                "connection_available": False,
            },
            {
                "key": "qmt", "display_name": "QMT",
                "status": "evaluating", "capabilities": [],
                "connection_available": False,
            },
            {
                "key": "ptrade", "display_name": "PTrade",
                "status": "evaluating", "capabilities": [],
                "connection_available": False,
            },
        ],
        "us_short": {
            "requires_ciclotrade_manual_approval": False,
            "requires_broker_authorization": True,
            "requires_margin": True,
            "requires_borrowability": True,
        },
    }
    assert order["plan_type"] == "标准版"
    assert order["pay_method"] == "paypal"
    assert order["proof_status"] is None
    assert order["can_purchase"] is True
    assert order["purchase_action"] == "upgrade"
    assert order["can_submit_proof"] is True
    assert order["blocked_reason"] is None
    assert "external_id" not in order
    assert "external_capture_id" not in order
    assert "request_fingerprint" not in order
    unavailable = {
        "planned", "evaluating", "unsupported",
    }
    for provider in payload["brokerage"]["capability_catalog"]:
        if provider["status"] in unavailable:
            assert not any("action" in key for key in provider)


def test_membership_order_behavior_matrix_uses_authoritative_entitlements(compatibility):
    database, user, login, repository = compatibility
    database.execute(
        """INSERT INTO manual_payment_receivers
           (method,enabled,receiver_text,version,updated_at)
           VALUES ('fps',1,?,1,?)
           ON CONFLICT(method) DO UPDATE SET enabled=1,
               receiver_text=excluded.receiver_text,version=excluded.version,
               updated_at=excluded.updated_at""",
        ("FPS account for test orders", datetime.now(UTC).isoformat()),
    )
    orders = OrderService(database)
    old_low_order = orders.create_order(
        user["id"], "标准版", "monthly", "fps", terms_accepted=True,
        idempotency_key="matrix-old-low-order",
    )
    moment = datetime.now(UTC)
    with database.transaction() as connection:
        add_membership_entitlement(
            connection, user["id"], "标准版", 30,
            source_kind="pytest", source_ref="matrix-standard", now=moment,
        )

    upgrade = orders.create_order(
        user["id"], "专业版", "monthly", "fps", terms_accepted=True,
        idempotency_key="matrix-upgrade-order",
    )
    assert upgrade["plan_type"] == "专业版"

    with database.transaction() as connection:
        add_membership_entitlement(
            connection, user["id"], "高级版", 30,
            source_kind="pytest", source_ref="matrix-advanced", now=moment,
        )

    renewal = orders.create_order(
        user["id"], "高级版", "monthly", "fps", terms_accepted=True,
        idempotency_key="matrix-renewal-order",
    )
    assert renewal["plan_type"] == "高级版"
    with pytest.raises(MembershipPlanConflict):
        orders.create_order(
            user["id"], "标准版", "monthly", "fps", terms_accepted=True,
            idempotency_key="matrix-blocked-downgrade",
        )

    membership = repository.membership(repository.authenticate(login.access_token))
    old_low = next(
        order for order in membership["orders"]
        if order["order_no"] == old_low_order["order_no"]
    )
    assert old_low["can_purchase"] is False
    assert old_low["purchase_action"] == "covered"
    assert old_low["can_submit_proof"] is False
    assert {"signal_web", "tg_stock_signal"}.issubset(membership["capabilities"])


def test_membership_plan_contract_projects_the_canonical_plan_matrix(compatibility):
    database, user, login, repository = compatibility
    identity = repository.authenticate(login.access_token)

    payload = repository.membership(identity)

    assert [item["key"] for item in payload["plans"]] == list(PLAN_ORDER)
    for item in payload["plans"]:
        plan = PLANS[item["key"]]
        assert item["display_name"] == plan_display_name(item["key"])
        assert item["prices"] == plan["prices"]
        assert item["summary"] == plan["summary"]
        assert item["features"] == list(plan["features"])

    by_key = {item["key"]: item for item in payload["plans"]}
    assert by_key["免费版"]["purchase_action"] == "unavailable"
    assert by_key["免费版"]["can_purchase"] is False
    assert by_key["标准版"]["purchase_action"] == "upgrade"
    assert by_key["标准版"]["can_purchase"] is True
    assert "1 个自动交易控制账号名额（仍需主动授权券商）" in by_key["高级版"]["features"]
    assert "最多 5 个自动交易控制账号名额（仍需主动授权券商）" in by_key["专业版"]["features"]
    for feature in ("期权链、期权报价 K 线、Greeks 与 IV", "单腿与多腿期权组合研究"):
        assert feature not in by_key["高级版"]["features"]
        assert feature in by_key["专业版"]["features"]

    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    database.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=? WHERE id=?", (expiry, user["id"])
    )
    advanced = repository.membership(repository.authenticate(login.access_token))
    assert advanced["brokerage"]["auto_control_account_limit"] == 1
    advanced_by_key = {item["key"]: item for item in advanced["plans"]}
    assert advanced_by_key["标准版"]["purchase_action"] == "covered"
    assert advanced_by_key["标准版"]["can_purchase"] is False
    assert advanced_by_key["高级版"]["purchase_action"] == "renew"
    assert advanced_by_key["高级版"]["can_purchase"] is True
    assert advanced_by_key["专业版"]["purchase_action"] == "upgrade"
    assert advanced_by_key["专业版"]["can_purchase"] is True
    database.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire=? WHERE id=?", (expiry, user["id"])
    )
    professional = repository.membership(repository.authenticate(login.access_token))
    assert professional["brokerage"]["auto_control_account_limit"] == 5


def test_membership_annual_bonus_uses_the_platform_control(compatibility):
    database, _, login, repository = compatibility
    identity = repository.authenticate(login.access_token)

    assert repository.membership(identity)["annual_bonus_enabled"] is True
    database.execute(
        """INSERT INTO platform_controls (control_key,control_value,updated_at)
           VALUES ('annual_bonus_enabled','0',datetime('now'))
           ON CONFLICT(control_key) DO UPDATE SET control_value='0'"""
    )

    assert repository.membership(identity)["annual_bonus_enabled"] is False


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


def test_portfolio_uses_system_ledger_and_excludes_customer_paper_orders(compatibility):
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
    journal = OfficialPaperJournalV2(database)
    journal.append_event(
        ledger_key="tradeai-official-paper-v2", source="pytest", external_event_id="official-read-1",
        strategy_name="official-validation", strategy_version="1", occurred_at=now,
        legs=[{
            "market": "US", "instrument_type": "stock", "symbol": "MSFT",
            "target_quantity": 3, "quantity_delta": 3, "price": 450,
        }],
    )
    journal.append_equity_snapshot(
        ledger_key="tradeai-official-paper-v2", source="pytest", external_snapshot_id="official-usd-1",
        market="US", currency="USD", initial_cash=10_000, cash=8_650, market_value=1_350,
        realized_pnl=0, unrealized_pnl=0, captured_at=now,
    )
    identity = repository.authenticate(login.access_token)

    payload = repository.portfolio(identity)

    assert payload["account_mode"] == "official"
    assert payload["scope"] == "ciclotrade_system_validation"
    assert payload["positions"][0]["symbol"] == "MSFT"
    assert payload["positions"][0]["quantity"] == 3
    assert payload["positions"][0]["market"] == "US"
    assert payload["positions"][0]["currency"] == "USD"
    assert {item["symbol"] for item in payload["orders"]} == {"MSFT"}
    assert payload["accounts"]["US"]["total_equity"] == 10_000
    assert payload["accounts"]["HK"]["status"] == "recorded"
    assert payload["accounts"]["HK"]["initial_cash"] == 10_000
    assert payload["accounts"]["CN"]["initial_cash"] == 10_000
    assert payload["mark_source"] == "official_paper_v2_last_recorded_price"
    assert payload["fresh_marks"] is False
    assert payload["activity"]["pnl_method"] == "weighted_average"
    assert payload["activity"]["executions"][0]["side"] == "BUY"
    assert payload["activity"]["intervals"][0]["status"] == "OPEN"
    performance = repository.performance(identity)
    assert {item["market"] for item in performance["items"]} == {"US", "HK", "CN"}


def test_recommendations_and_timeline_include_official_paper_v2_events(compatibility):
    database, user, login, repository = compatibility
    now = datetime.now(UTC).isoformat()
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire=? WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(), user["id"]),
    )
    OfficialPaperJournalV2(database).append_event(
        ledger_key="tradeai-official-paper-v2", source="official-read", external_event_id="official-consumer-v2",
        strategy_name="official-validation", strategy_version="2", occurred_at=now,
        metadata={"risk_levels": {"US:STOCK:MSFT": {"stop_loss": 400, "target_price": 500}}},
        legs=[{
            "market": "US", "instrument_type": "stock", "symbol": "MSFT",
            "target_quantity": 3, "quantity_delta": 3, "price": 450,
        }],
    )
    identity = repository.authenticate(login.access_token)

    timeline = repository.timeline(identity)
    recommendations = repository.recommendations(identity)

    assert any(
        item["strategy_version"] == "2" and any(leg.get("symbol") == "MSFT" for leg in item["legs"])
        for item in timeline["items"]
    )
    assert any(item["strategy_version"] == "2" and item["symbol"] == "MSFT" for item in recommendations["items"])


def test_portfolio_activity_groups_official_ledger_events(compatibility):
    database, user, login, repository = compatibility
    rows = [
        ("OFFICIAL-CYCLE-1", 10, 10, 100, "2026-08-01T10:00:00+00:00"),
        ("OFFICIAL-CYCLE-2", 15, 5, 110, "2026-08-02T10:00:00+00:00"),
        ("OFFICIAL-CYCLE-3", 7, -8, 120, "2026-08-03T10:00:00+00:00"),
        ("OFFICIAL-CYCLE-4", 0, -7, 130, "2026-08-04T10:00:00+00:00"),
    ]
    journal = OfficialPaperJournalV2(database)
    for event_id, target, delta, price, occurred_at in rows:
        journal.append_event(
            ledger_key="tradeai-official-paper-v2", source="pytest", external_event_id=event_id,
            strategy_name="official-cycle", strategy_version="1", occurred_at=occurred_at,
            legs=[{
                "market": "US", "instrument_type": "stock", "symbol": "AAPL",
                "target_quantity": target, "quantity_delta": delta, "price": price,
                "commission": 1,
            }],
        )
    identity = repository.authenticate(login.access_token)

    payload = repository.portfolio(identity)
    activity = payload["activity"]
    interval = activity["intervals"][0]

    assert len(payload["orders"]) == 4
    assert all(item["account_mode"] == "official" for item in payload["orders"])
    assert len(activity["executions"]) == 4
    assert interval["status"] == "CLOSED"
    assert datetime.fromisoformat(interval["opened_at"]) == datetime.fromisoformat("2026-08-01T10:00:00+00:00")
    assert datetime.fromisoformat(interval["closed_at"]) == datetime.fromisoformat("2026-08-04T10:00:00+00:00")
    assert interval["average_entry_price"] == pytest.approx(103.3333333333)
    assert interval["average_exit_price"] == pytest.approx(124.6666666667)
    assert interval["realized_pnl"] == pytest.approx(316)
    assert interval["result"] == "profit"
    assert interval["execution_ids"] == [item["execution_id"] for item in reversed(activity["executions"])]


def test_portfolio_activity_keeps_option_prices_per_contract_unit(compatibility):
    database, user, login, repository = compatibility
    del user
    journal = OfficialPaperJournalV2(database)
    for event_id, target, delta, price, occurred_at in (
        ("OPTION-OPEN", 1, 1, 5, "2026-08-01T10:00:00+00:00"),
        ("OPTION-CLOSE", 0, -1, 6, "2026-08-02T10:00:00+00:00"),
    ):
        journal.append_event(
            ledger_key="tradeai-official-paper-v2", source="pytest", external_event_id=event_id,
            strategy_name="official-option-cycle", strategy_version="1", occurred_at=occurred_at,
            legs=[{
                "market": "US", "instrument_type": "option", "symbol": "AAPL",
                "option_expiry": "2026-09-18", "option_right": "CALL", "option_strike": 210,
                "target_quantity": target, "quantity_delta": delta, "price": price, "multiplier": 100,
            }],
        )

    interval = repository.portfolio(repository.authenticate(login.access_token))["activity"]["intervals"][0]

    assert interval["multiplier"] == 100
    assert interval["average_entry_price"] == pytest.approx(5)
    assert interval["average_exit_price"] == pytest.approx(6)
    assert interval["realized_pnl"] == pytest.approx(100)
    assert interval["realized_return_pct"] == pytest.approx(20)


def test_portfolio_activity_reports_full_per_market_execution_counts_before_preview_limit(compatibility):
    database, user, login, repository = compatibility
    del user
    journal = OfficialPaperJournalV2(database)
    journal.append_event(
        ledger_key="tradeai-official-paper-v2", source="pytest", external_event_id="OFFICIAL-CN-1",
        strategy_name="official-volume", strategy_version="1", occurred_at="2026-07-31T10:00:00+00:00",
        legs=[{
            "market": "CN", "instrument_type": "stock", "symbol": "600519",
            "target_quantity": 1, "quantity_delta": 1, "price": 1000,
        }],
    )
    for index in range(501):
        journal.append_event(
            ledger_key="tradeai-official-paper-v2", source="pytest", external_event_id=f"OFFICIAL-US-{index}",
            strategy_name="official-volume", strategy_version="1",
            occurred_at=f"2026-08-01T10:{index // 60:02d}:{index % 60:02d}+00:00",
            legs=[{
                "market": "US", "instrument_type": "stock", "symbol": "AAPL",
                "target_quantity": index + 1, "quantity_delta": 1, "price": 100,
            }],
        )
    activity = repository.portfolio(repository.authenticate(login.access_token))["activity"]

    assert len(activity["executions"]) == 500
    assert activity["truncated"] is True
    assert activity["execution_counts_by_market"] == {"US": 501, "CN": 1, "HK": 0}
    assert len(activity["execution_previews_by_market"]["US"]) == 500
    assert [item["symbol"] for item in activity["execution_previews_by_market"]["CN"]] == ["600519"]
