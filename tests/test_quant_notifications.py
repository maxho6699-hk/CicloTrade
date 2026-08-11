"""Persisted, entitlement-aware quant signal delivery checks."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC

from core.auth import AuthService
from core.database import DatabaseManager
from core.quant_journal import QuantJournal
from core.user_settings import merge_user_settings
from notification.telegram_bot import TelegramDeliveryUncertain
from scheduler.jobs import (
    dispatch_quant_group_deliveries,
    dispatch_delayed_free_group_deliveries,
    dispatch_quant_signal_deliveries,
    enqueue_delayed_free_group_deliveries,
    enqueue_quant_group_deliveries,
    enqueue_quant_signal_deliveries,
    publish_daily_group_summary,
    publish_free_daily_group_summary,
)


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
        metadata={
            "risk_levels": {
                "US:STOCK:AAPL": {"stop_loss": 180, "target_price": 240},
                "US:OPTION:AAPL:2026-09-18:CALL:210": {"stop_loss": 3.5, "target_price": 8},
            }
        },
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
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda message, chat_id=None, **kwargs: sent.append((message, chat_id, kwargs)))
    assert dispatch_quant_signal_deliveries(db) == 3
    assert dispatch_quant_signal_deliveries(db) == 0
    assert {target for _, target, _ in sent} == {"10002", "10003"}
    assert all("#1" in message and kwargs["parse_mode"] == "HTML" for message, _, kwargs in sent)
    urls = [button["url"] for _, _, kwargs in sent for row in kwargs["buttons"] for button in row]
    assert any(url.endswith("/opportunities") for url in urls)
    assert any(url.endswith("/portfolio") for url in urls)
    assert any(url.endswith("/markets") for url in urls)
    assert any(url.endswith("/notifications") for url in urls)
    assert all(
        not url.endswith(("/recommendations", "/dashboard", "/terminal", "/settings", "/subscription"))
        for url in urls
    )


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


def test_telegram_dispatcher_does_not_claim_future_discord_rows(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-channel-isolation.db"))
    user = _paid_user(db, "channel-isolation", "高级版", "20003")
    event = _event(db)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with db.transaction() as conn:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute(
            """INSERT INTO quant_event_deliveries
               (event_id,user_id,channel,instrument_type,symbol,status,attempts,
                next_attempt_at,last_error,created_at,updated_at,sent_at)
               VALUES (?,?,'discord','stock','AAPL','pending',0,?,NULL,?,?,NULL)""",
            (event["id"], user["id"], now, now, now),
        )
    sent = []
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda *args, **kwargs: sent.append((args, kwargs)))

    assert dispatch_quant_signal_deliveries(db) == 0
    assert not sent
    row = db.fetch_one("SELECT channel,status,attempts FROM quant_event_deliveries")
    assert row == {"channel": "discord", "status": "pending", "attempts": 0}


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
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda message, chat_id=None, **kwargs: sent.append(message))
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
    assert any("建议已失效 · 做多" in message and "美股 AAPL" in message and "數量　10 股" in message for message in sent)
    assert any("推荐入场 · 做多" in message and "美股 MSFT" in message and "數量　5 股" in message for message in sent)
    assert all("US:STOCK" not in message and "catalog" not in message.lower() for message in sent)
    assert enqueue_quant_signal_deliveries(db) == 0


def test_noop_correction_is_skipped_as_no_material_change(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-noop-correction.db"))
    _paid_user(db, "noop-correction", "高级版", "40002")
    original = _event(db)
    assert enqueue_quant_signal_deliveries(db) == 1

    sent = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr(
        "scheduler.jobs.send_telegram",
        lambda message, chat_id=None, **kwargs: sent.append((message, chat_id, kwargs)),
    )
    assert dispatch_quant_signal_deliveries(db) == 1
    sent.clear()

    QuantJournal(db).append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="mixed-signal-noop-correction",
        event_type="correction",
        corrects_event_id=original["id"],
        strategy_name="趋势交叉",
        strategy_version="v1",
        occurred_at="2026-08-06T02:00:00+00:00",
        metadata={
            "risk_levels": {
                "US:STOCK:AAPL": {"stop_loss": 180, "target_price": 240},
                "US:OPTION:AAPL:2026-09-18:CALL:210": {"stop_loss": 3.5, "target_price": 8},
            }
        },
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

    assert enqueue_quant_signal_deliveries(db) == 1
    assert dispatch_quant_signal_deliveries(db) == 0
    assert not sent
    latest = db.fetch_one(
        "SELECT status,last_error FROM quant_event_deliveries ORDER BY id DESC LIMIT 1"
    )
    assert latest == {"status": "skipped", "last_error": "no_material_change"}


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


def test_group_signal_routes_follow_membership_superset(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-group-delivery.db"))
    _event(db)
    monkeypatch.setenv("TELEGRAM_GROUP_SIGNALS_ENABLED", "true")
    assert enqueue_quant_group_deliveries(db) == 2
    assert enqueue_quant_group_deliveries(db) == 0
    sent = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda message, chat_id=None, **kwargs: sent.append((message, chat_id, kwargs)))
    assert dispatch_quant_group_deliveries(db) == 2
    assert {target for _, target, _ in sent} == {"-1004460522940", "-1003902118990"}
    advanced = next(message for message, target, _ in sent if target == "-1004460522940")
    professional = next(message for message, target, _ in sent if target == "-1003902118990")
    assert "美股 AAPL" in advanced and "Call" not in advanced
    assert "美股 AAPL" in professional and "🟢 AAPL" in professional
    assert "止損 $180.00" in advanced and "目標 $240.00" in advanced
    assert "止損 $3.50" in professional and "目標 $8.00" in professional
    assert all(
        kwargs["parse_mode"] == "HTML"
        and len(kwargs["buttons"]) == 2
        and all(len(row) == 2 for row in kwargs["buttons"])
        and all("callback_data" not in button for row in kwargs["buttons"] for button in row)
        for _, _, kwargs in sent
    )


def test_free_group_signals_are_queued_with_stock_and_option_delays(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-free-delivery.db"))
    _event(db)
    monkeypatch.setenv("TELEGRAM_FREE_DELAYED_SIGNALS_ENABLED", "true")
    assert enqueue_delayed_free_group_deliveries(db) == 2
    assert enqueue_delayed_free_group_deliveries(db) == 0
    rows = db.fetch_all(
        "SELECT instrument_type,delay_minutes FROM telegram_delayed_group_deliveries ORDER BY instrument_type"
    )
    assert rows == [
        {"instrument_type": "option", "delay_minutes": 15},
        {"instrument_type": "stock", "delay_minutes": 60},
    ]
    db.execute(
        "UPDATE telegram_delayed_group_deliveries SET next_attempt_at=?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds"),),
    )
    sent = []
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda message, chat_id=None, **kwargs: sent.append((message, chat_id, kwargs)))
    assert dispatch_delayed_free_group_deliveries(db) == 2
    assert {target for _, target, _ in sent} == {"-1003794694425"}
    assert any("期權建議延遲 15 分鐘" in message and "🟢 AAPL" in message for message, _, _ in sent)
    assert any("正股建議延遲 1 小時" in message and "美股 AAPL" in message for message, _, _ in sent)
    assert all("建議" in message for message, _, _ in sent)
    assert all(kwargs["protect_content"] is True for _, _, kwargs in sent)
    assert all(
        len(kwargs["buttons"]) == 2
        and all(len(row) == 2 for row in kwargs["buttons"])
        and any(button["text"] == "💎 升級會員" for row in kwargs["buttons"] for button in row)
        for _, _, kwargs in sent
    )


def test_daily_group_summary_requires_new_persisted_snapshot(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-daily-summary.db"))
    event = _event(db)
    event_recorded_at = datetime.fromisoformat(
        str(event["recorded_at"]).replace("Z", "+00:00")
    )

    class SummaryClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = event_recorded_at + timedelta(hours=2)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr("scheduler.jobs.datetime", SummaryClock)
    journal = QuantJournal(db)
    journal.append_equity_snapshot(
        ledger_key="tradeai-system", source="pytest", external_snapshot_id="usd-1",
        currency="USD", initial_cash=100_000, cash=90_000, market_value=12_000,
        realized_pnl=1_000, unrealized_pnl=1_000, captured_at="2026-08-07T12:00:00+00:00",
    )
    journal.append_equity_snapshot(
        ledger_key="tradeai-system", source="pytest", external_snapshot_id="cny-1",
        currency="CNY", initial_cash=500_000, cash=450_000, market_value=49_000,
        realized_pnl=-500, unrealized_pnl=-500, captured_at="2026-08-07T12:00:00+00:00",
    )
    monkeypatch.setenv("TELEGRAM_DAILY_SUMMARY_ENABLED", "true")
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    sent = []
    monkeypatch.setattr("scheduler.jobs.send_telegram", lambda message, chat_id=None, **kwargs: sent.append((message, chat_id, kwargs)))

    assert publish_daily_group_summary(db) == 2
    assert publish_daily_group_summary(db) == 0
    assert publish_free_daily_group_summary(db) == 1
    assert publish_free_daily_group_summary(db) == 0
    assert {target for _, target, _ in sent} == {
        "-1004460522940", "-1003902118990", "-1003794694425",
    }
    advanced = next(message for message, target, _ in sent if target == "-1004460522940")
    professional = next(message for message, target, _ in sent if target == "-1003902118990")
    free = next(message for message, target, _ in sent if target == "-1003794694425")
    assert "每日建議總結" in advanced and "美元資產" in advanced and "人民幣資產" in advanced
    assert "美股 AAPL" in advanced and "Call" not in advanced
    assert "美股 AAPL" in professional and "🟢 AAPL" in professional
    assert "止損 $180.00" in advanced and "目標 $240.00" in professional
    assert "正股建議延遲 1 小時" in free and "期權建議延遲 15 分鐘" in free and "升級會員" in free
    assert all(kwargs["protect_content"] is True for _, _, kwargs in sent)
    assert all(kwargs["parse_mode"] == "HTML" for _, _, kwargs in sent)


def test_free_daily_summary_waits_for_latest_snapshot_release_time(tmp_path, monkeypatch):
    db = DatabaseManager(str(tmp_path / "quant-daily-summary-release-gate.db"))
    _event(db)
    QuantJournal(db).append_equity_snapshot(
        ledger_key="tradeai-system",
        source="pytest",
        external_snapshot_id="fresh-usd-1",
        currency="USD",
        initial_cash=100_000,
        cash=100_000,
        market_value=0,
        realized_pnl=0,
        unrealized_pnl=0,
        captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    monkeypatch.setenv("TELEGRAM_DAILY_SUMMARY_ENABLED", "true")
    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    sent = []
    monkeypatch.setattr(
        "scheduler.jobs.send_telegram",
        lambda message, chat_id=None, **kwargs: sent.append((message, chat_id, kwargs)),
    )

    assert publish_free_daily_group_summary(db) == 0
    assert sent == []
