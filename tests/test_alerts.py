"""Focused checks for persisted multi-condition alerts and background delivery."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC

import pandas as pd
import pytest

from core.alerts import AlertService
from core.auth import AuthService
from core.database import DatabaseManager
from core.user_settings import merge_user_settings
from scheduler.jobs import dispatch_price_alert_deliveries, scan_price_alerts


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "alerts.db"))


def _user(db: DatabaseManager, suffix: str, plan: str = "免费版") -> dict:
    user = AuthService(db).register(
        f"alerts-{suffix}@example.com", "CorrectHorse123", "Alert User", True
    )
    if plan != "免费版":
        db.execute(
            "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?",
            (
                plan,
                (datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds"),
                user["id"],
            ),
        )
    return user


def test_and_or_conditions_are_evaluated_and_persisted(db):
    user = _user(db, "logic", "标准版")
    alerts = AlertService(db)
    alerts.create(
        user["id"],
        "免费版",
        "aapl",
        conditions=[
            {"type": "price", "operator": ">=", "value": 100},
            {"type": "rsi", "operator": "<=", "value": 30},
        ],
        logic="AND",
    )
    alerts.create(
        user["id"],
        "定制版",
        "MSFT",
        conditions=[
            {"type": "price", "operator": ">=", "value": 500},
            {"type": "macd", "value": "golden_cross"},
        ],
        logic="OR",
    )

    assert alerts.evaluate(user["id"], {"AAPL": 110}, {"AAPL": {"rsi": 40}}) == []
    and_hit = alerts.evaluate(user["id"], {"aapl": 110}, {"aapl": {"rsi": 25}})
    or_hit = alerts.evaluate(user["id"], {"MSFT": 100}, {"MSFT": {"macd_golden_cross": True}})

    assert and_hit[0]["preview"] == "AAPL 价格 >= 100 AND RSI <= 30"
    assert or_hit[0]["logic"] == "OR"
    assert alerts.evaluate(user["id"], {"AAPL": 110, "MSFT": 600}) == []


def test_database_plan_cannot_be_spoofed(db):
    user = _user(db, "spoof")
    alerts = AlertService(db)

    with pytest.raises(ValueError, match="最多 1 个条件"):
        alerts.create(
            user["id"],
            "定制版",
            "AAPL",
            conditions=[
                {"type": "price", "operator": ">=", "value": 100},
                {"type": "rsi", "operator": "<=", "value": 30},
            ],
        )
    alerts.create(user["id"], "定制版", "AAPL", ">=", 100)
    with pytest.raises(ValueError, match="最多可启用 1 条"):
        alerts.create(user["id"], "定制版", "MSFT", ">=", 100)
    with pytest.raises(ValueError, match="不存在或已停用"):
        alerts.create(999_999, "定制版", "MSFT", ">=", 100)


def test_malformed_legacy_rows_fall_back_to_price_condition(db):
    user = _user(db, "legacy")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO price_alerts
           (user_id,symbol,operator,target_price,conditions,logic,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (user["id"], "AAPL", ">=", 200, '{"type":"price"}', "XOR", now),
    )

    row = AlertService(db).list(user["id"])[0]
    assert row["conditions_list"] == [{"type": "price", "operator": ">=", "value": 200.0}]
    assert row["logic"] == "AND"
    assert AlertService(db).evaluate(user["id"], {"AAPL": 201})[0]["id"] == row["id"]


def test_background_scan_handles_empty_and_single_row_history(db):
    user = _user(db, "short-history")
    alerts = AlertService(db)
    alerts.create(user["id"], None, "AAPL", ">=", 200)

    class EmptyHistory:
        def history(self, symbols, period):
            return pd.DataFrame(), pd.DataFrame()

    class SingleRowHistory:
        def history(self, symbols, period):
            return pd.DataFrame({"AAPL": [201.0]}), pd.DataFrame(index=[0])

    assert scan_price_alerts(db, EmptyHistory()) == 0
    assert alerts.list(user["id"])[0]["is_active"] == 1
    assert scan_price_alerts(db, SingleRowHistory()) == 1


def test_background_scan_uses_five_day_volume_ratio(db):
    user = _user(db, "volume-ratio", "高级版")
    AlertService(db).create(
        user["id"],
        None,
        "AAPL",
        conditions=[{"type": "volume_ratio", "operator": ">=", "value": 5}],
    )

    class VolumeHistory:
        def history(self, symbols, period):
            return (
                pd.DataFrame({"AAPL": [100.0] * 20}),
                pd.DataFrame({"AAPL": [100.0] * 14 + [1.0, 1.0, 1.0, 1.0, 1.0, 10.0]}),
            )

    # The threshold only passes when the current spike is excluded from the
    # preceding five-day average.
    assert scan_price_alerts(db, VolumeHistory()) == 1


def test_background_scan_handles_zero_loss_rsi(db):
    user = _user(db, "rsi", "高级版")
    AlertService(db).create(
        user["id"], None, "AAPL",
        conditions=[{"type": "rsi", "operator": ">=", "value": 90}],
    )

    class RisingHistory:
        def history(self, symbols, period):
            values = list(range(100, 115))
            return pd.DataFrame({"AAPL": values}), pd.DataFrame({"AAPL": [1] * len(values)})

    assert scan_price_alerts(db, RisingHistory()) == 1


def test_price_alert_telegram_requires_advanced_plan_and_verified_consent(db, monkeypatch):
    users = {
        plan: _user(db, plan, plan)
        for plan in ("免费版", "标准版", "高级版")
    }
    for index, user in enumerate(users.values(), start=1):
        AlertService(db).create(user["id"], "定制版", "AAPL", ">=", 200)
        merge_user_settings(
            user["id"],
            {
                "tg_events": {"price_alert": True},
                "telegram": {"consent": True, "verified": True, "chat_id": str(1000 + index)},
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

    assert scan_price_alerts(db, Prices()) == 3
    assert [chat_id for _, chat_id in sent] == ["1003"]


def test_price_alert_delivery_retries_without_reactivating_alert(db, monkeypatch):
    user = _user(db, "retry", "高级版")
    merge_user_settings(
        user["id"],
        {
            "tg_events": {"price_alert": True},
            "telegram": {"consent": True, "verified": True, "chat_id": "2001"},
        },
        db,
    )
    service = AlertService(db)
    service.create(user["id"], None, "AAPL", ">=", 200)
    assert len(service.evaluate(user["id"], {"AAPL": 201})) == 1

    monkeypatch.setattr("scheduler.jobs.telegram_configured", lambda *_: True)
    monkeypatch.setattr(
        "scheduler.jobs.send_telegram",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary outage")),
    )
    assert dispatch_price_alert_deliveries(db) == 0
    failed = db.fetch_one("SELECT status,attempts,next_attempt_at FROM price_alert_deliveries")
    assert failed["status"] == "failed" and failed["attempts"] == 1
    assert service.list(user["id"])[0]["is_active"] == 0

    db.execute(
        "UPDATE price_alert_deliveries SET next_attempt_at='2000-01-01T00:00:00+00:00'"
    )
    sent = []
    monkeypatch.setattr(
        "scheduler.jobs.send_telegram",
        lambda message, chat_id=None, **_kwargs: sent.append((message, chat_id)),
    )
    assert dispatch_price_alert_deliveries(db) == 1
    assert sent and sent[0][1] == "2001"
    assert db.fetch_one("SELECT status FROM price_alert_deliveries")["status"] == "sent"
