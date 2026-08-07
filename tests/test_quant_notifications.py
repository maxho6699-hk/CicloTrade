"""Persisted, entitlement-aware quant signal delivery checks."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC

from core.auth import AuthService
from core.database import DatabaseManager
from core.quant_journal import QuantJournal
from core.user_settings import merge_user_settings
from notification.telegram_bot import TelegramDeliveryUncertain
from scheduler.jobs import dispatch_quant_signal_deliveries, enqueue_quant_signal_deliveries


def _paid_user(db: DatabaseManager, suffix: str, plan: str, chat_id: str) -> dict:
    user = AuthService(db).register(f"{suffix}@example.com", "CorrectHorse123", suffix, True)
    db.execute(
        "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?",
        (plan, (datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), user["id"]),
    )
    merge_user_settings(
        user["id"],
        {
            "watchlists": {"us": ["AAPL"], "a_share": []},
            "tg_events": {"stock_signal": True, "option_signal": True},
            "telegram": {"consent": True, "verified": True, "chat_id": chat_id},
        },
        db,
    )
    return user


def _event(db: DatabaseManager) -> dict:
    return QuantJournal(db).append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="mixed-signal-1",
        strategy_name="趋势交叉",
        strategy_version="v1",
        occurred_at="2026-08-06T01:30:00+00:00",
        legs=[
            {
                "market": "US",
                "instrument_type": "stock",
                "symbol": "AAPL",
                "target_quantity": 10,
                "quantity_delta": 10,
                "price": 200,
            },
            {
                "market": "US",
                "instrument_type": "option",
                "symbol": "AAPL",
                "option_expiry": "2026-09-18",
                "option_right": "CALL",
                "option_strike": 210,
                "target_quantity": 1,
                "quantity_delta": 1,
                "price": 5,
            },
        ],
    )


def test_signal_outbox_enforces_tiers_and_is_idempotent(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-notifications.db"))
    _paid_user(db, "standard", "标准版", "10001")
    _paid_user(db, "advanced", "高级版", "10002")
    _paid_user(db, "professional", "专业版", "10003")
    _event(db)

    assert enqueue_quant_signal_deliveries(db) == 3
    assert enqueue_quant_signal_deliveries(db) == 0
    rows = db.fetch_all(
        """SELECT u.plan_type,d.instrument_type FROM quant_event_deliveries d
           JOIN users u ON u.id=d.user_id ORDER BY u.plan_type,d.instrument_type"""
    )
    assert {(row["plan_type"], row["instrument_type"]) for row in rows} == {
        ("高级版", "stock"),
        ("专业版", "stock"),
        ("专业版", "option"),
    }

    sent = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda message, chat_id=None: sent.append((message, chat_id)))
    assert dispatch_quant_signal_deliveries(db) == 3
    assert dispatch_quant_signal_deliveries(db) == 0
    assert {target for _, target in sent} == {"10002", "10003"}
    assert all("#1" in message for message, _ in sent)


def test_delivery_rechecks_watchlist_and_subscription_before_sending(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-entitlement-recheck.db"))
    advanced = _paid_user(db, "advanced-recheck", "高级版", "20001")
    professional = _paid_user(db, "professional-recheck", "专业版", "20002")
    _event(db)
    assert enqueue_quant_signal_deliveries(db) == 3

    merge_user_settings(advanced["id"], {"watchlists": {"us": [], "a_share": []}}, db)
    db.execute(
        "UPDATE users SET subscription_expire=? WHERE id=?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds"), professional["id"]),
    )
    sent = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda *args, **kwargs: sent.append((args, kwargs)))

    assert dispatch_quant_signal_deliveries(db) == 0
    assert not sent
    assert db.fetch_one(
        "SELECT COUNT(*) count FROM quant_event_deliveries WHERE status='skipped'"
    )["count"] == 3


def test_failed_delivery_is_persisted_for_retry(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-delivery-retry.db"))
    _paid_user(db, "retry", "高级版", "30001")
    _event(db)
    assert enqueue_quant_signal_deliveries(db) == 1
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr(
        "scheduler.jobs.send_telegram",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary outage")),
    )

    assert dispatch_quant_signal_deliveries(db) == 0
    row = db.fetch_one("SELECT status,attempts,last_error,next_attempt_at FROM quant_event_deliveries")
    assert row["status"] == "failed" and row["attempts"] == 1
    assert row["last_error"] == "temporary outage" and row["next_attempt_at"]


def test_correction_notifies_adjustment_and_removed_symbol(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-correction-delivery.db"))
    user = _paid_user(db, "correction", "高级版", "40001")
    merge_user_settings(user["id"], {"watchlists": {"us": ["AAPL", "MSFT"], "a_share": []}}, db)
    original = _event(db)
    assert enqueue_quant_signal_deliveries(db) == 1

    sent = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda message, chat_id=None: sent.append(message))
    assert dispatch_quant_signal_deliveries(db) == 1
    sent.clear()

    QuantJournal(db).append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="mixed-signal-correction",
        event_type="correction",
        corrects_event_id=original["id"],
        strategy_name="趋势交叉",
        strategy_version="v2",
        occurred_at="2026-08-06T02:00:00+00:00",
        legs=[
            {
                "market": "US",
                "instrument_type": "stock",
                "symbol": "MSFT",
                "target_quantity": 5,
                "quantity_delta": 5,
                "price": 220,
            }
        ],
    )

    assert enqueue_quant_signal_deliveries(db) == 2
    assert dispatch_quant_signal_deliveries(db) == 2
    assert any("US:STOCK:AAPL -10" in message and "目标仓位 0" in message for message in sent)
    assert any("US:STOCK:MSFT +5" in message and "目标仓位 5" in message for message in sent)
    assert enqueue_quant_signal_deliveries(db) == 0


def test_new_eligibility_only_receives_events_recorded_after_activation(tmp_path):
    db = DatabaseManager(str(tmp_path / "quant-eligibility-cutoff.db"))
    upgrade = _paid_user(db, "upgrade", "标准版", "50001")
    watchlist = _paid_user(db, "watchlist", "高级版", "50002")
    notifications = _paid_user(db, "notifications", "高级版", "50003")
    merge_user_settings(watchlist["id"], {"watchlists": {"us": [], "a_share": []}}, db)
    merge_user_settings(
        notifications["id"],
        {"tg_events": {"stock_signal": False, "option_signal": True}},
        db,
    )
    old = _event(db)
    activation = old["recorded_at"]

    db.execute(
        """INSERT INTO subscription_orders
           (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,
            created_at,paid_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("upgrade-paid", upgrade["id"], "高级版", "monthly", 1, "HKD", "paypal", "paid", activation, activation),
    )
    db.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire=? WHERE id=?",
        ((datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"), upgrade["id"]),
    )
    merge_user_settings(watchlist["id"], {"watchlists": {"us": ["AAPL"], "a_share": []}}, db)
    merge_user_settings(
        notifications["id"],
        {"tg_events": {"stock_signal": True, "option_signal": True}},
        db,
    )
    db.execute(
        "UPDATE user_settings SET updated_at=? WHERE user_id IN (?,?)",
        (activation, watchlist["id"], notifications["id"]),
    )
    newcomer = _paid_user(db, "newcomer", "高级版", "50004")
    db.execute("UPDATE users SET created_at=? WHERE id=?", (activation, newcomer["id"]))
    db.execute("UPDATE user_settings SET updated_at=? WHERE user_id=?", (activation, newcomer["id"]))

    fresh = QuantJournal(db).append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="fresh-after-activation",
        strategy_name="趋势交叉",
        strategy_version="v1",
        occurred_at="2026-08-06T02:00:00+00:00",
        legs=[
            {
                "market": "US",
                "instrument_type": "stock",
                "symbol": "AAPL",
                "target_quantity": 11,
                "quantity_delta": 1,
                "price": 201,
            }
        ],
    )

    assert enqueue_quant_signal_deliveries(db) == 4
    rows = db.fetch_all("SELECT DISTINCT event_id FROM quant_event_deliveries")
    assert rows == [{"event_id": fresh["id"]}]


def test_ambiguous_delivery_requires_manual_retry(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-delivery-uncertain.db"))
    _paid_user(db, "uncertain", "高级版", "60001")
    _event(db)
    assert enqueue_quant_signal_deliveries(db) == 1
    calls = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)

    def uncertain(*args, **kwargs):
        calls.append((args, kwargs))
        raise TelegramDeliveryUncertain("timeout")

    monkeypatch.setattr("scheduler.jobs.send_telegram", uncertain)
    assert dispatch_quant_signal_deliveries(db) == 0
    assert dispatch_quant_signal_deliveries(db) == 0
    row = db.fetch_one("SELECT status,attempts,last_error FROM quant_event_deliveries")
    assert row["status"] == "skipped" and row["attempts"] == 1
    assert row["last_error"] == "delivery_uncertain_manual_retry: timeout"
    assert len(calls) == 1
