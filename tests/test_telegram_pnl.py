"""Private Telegram closed-trade P&L queries."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.auth import AuthService
from core.compat import UTC
from core.database import DatabaseManager
from core.quant_journal import OfficialPaperJournalV2
from notification.telegram_desk import telegram_desk_response
from notification.telegram_timeline import _cycles


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "telegram-pnl.db"))


@pytest.fixture(autouse=True)
def auth_environment(monkeypatch):
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


def _hong_kong_midday(days_ago: int = 0) -> str:
    local = datetime.now(ZoneInfo("Asia/Hong_Kong")) - timedelta(days=days_ago)
    return local.replace(hour=12, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()


def test_private_timeline_reads_official_paper_v2_events(db):
    OfficialPaperJournalV2(db).append_event(
        ledger_key="tradeai-official-paper-v2", source="timeline-v2", external_event_id="timeline-v2-open",
        strategy_name="official", strategy_version="2", occurred_at="2026-08-01T10:00:00+00:00",
        legs=[{"market": "US", "instrument_type": "stock", "symbol": "MSFT", "target_quantity": 2, "quantity_delta": 2, "price": 450}],
    )

    cycles, _ = _cycles(db, "stock", include_marks=False)

    assert cycles[0]["symbol"] == "MSFT"


def test_closed_pnl_query_filters_by_hong_kong_close_date_and_renders_fills(db, monkeypatch):
    member = _bound_user(db, "pnl-today", "810025")
    db.execute(
        "UPDATE users SET plan_type='高级版',subscription_expire='2099-01-01T00:00:00+00:00' WHERE id=?",
        (member["id"],),
    )
    today = _hong_kong_midday()
    yesterday = _hong_kong_midday(1)
    cycles = [
        {
            "sequence": 1,
            "instrument_key": "US:STOCK:TODAY",
            "instrument_type": "stock",
            "symbol": "TODAY",
            "currency": "USD",
            "direction": "long",
            "opened_at": today,
            "recorded_at": today,
            "updated_at": today,
            "closed_at": today,
            "opened_quantity": 15,
            "closed_quantity": 15,
            "current_quantity": 0,
            "average_cost": 103.33,
            "commission": 2,
            "realized_pnl": 100,
            "return": 0.06,
            "executions": [
                {"role": "open", "occurred_at": today, "quantity": 10, "price": 100, "commission": 1},
                {"role": "add", "occurred_at": today, "quantity": 5, "price": 110, "commission": 0},
                {"role": "close", "occurred_at": today, "quantity": 15, "price": 110, "commission": 1},
            ],
        },
        {
            "sequence": 2,
            "instrument_key": "US:STOCK:YESTERDAY",
            "instrument_type": "stock",
            "symbol": "YESTERDAY",
            "currency": "USD",
            "direction": "long",
            "opened_at": yesterday,
            "recorded_at": yesterday,
            "updated_at": yesterday,
            "closed_at": yesterday,
            "opened_quantity": 2,
            "closed_quantity": 2,
            "current_quantity": 0,
            "average_cost": 200,
            "commission": 0,
            "realized_pnl": -20,
            "return": -0.05,
            "executions": [],
        },
        {
            "sequence": 3,
            "instrument_key": "US:STOCK:OPEN",
            "instrument_type": "stock",
            "symbol": "OPEN",
            "currency": "USD",
            "direction": "long",
            "opened_at": today,
            "recorded_at": today,
            "updated_at": today,
            "closed_at": None,
            "opened_quantity": 1,
            "closed_quantity": 0,
            "current_quantity": 1,
            "average_cost": 50,
            "executions": [],
        },
    ]

    def fake_cycles(_database, kind, *, include_marks=True):
        return (cycles, None) if kind == "stock" else ([], None)

    monkeypatch.setattr("notification.telegram_timeline._cycles", fake_cycles)
    center = telegram_desk_response(db, "810025", "desk:pnl", callback=True)
    assert any(
        button.get("callback_data") == "timeline:pnl:today:0"
        for row in center.keyboard
        for button in row
    )

    today_result = telegram_desk_response(db, "810025", "timeline:pnl:today:0", callback=True)
    assert "TODAY" in today_result.message and "YESTERDAY" not in today_result.message
    assert "OPEN" not in today_result.message
    assert "補倉" in today_result.message and "平倉" in today_result.message
    assert "已實現淨盈虧" in today_result.message and "交易回報" in today_result.message
    assert "浮動損益" not in today_result.message

    yesterday_result = telegram_desk_response(db, "810025", "timeline:pnl:yesterday:0", callback=True)
    assert "YESTERDAY" in yesterday_result.message and "TODAY" not in yesterday_result.message


def test_closed_pnl_option_delay_is_separate_from_full_option_timeline(db, monkeypatch):
    _bound_user(db, "pnl-option", "810026")
    now = datetime.now(UTC).replace(microsecond=0)
    closed = (now - timedelta(hours=2)).isoformat()
    recent_recorded = (now - timedelta(minutes=10)).isoformat()
    visible = (now - timedelta(minutes=20)).isoformat()
    cycles = [
        {
            "sequence": 1,
            "instrument_key": "US:OPTION:RECENT",
            "instrument_type": "option",
            "symbol": "RECENT",
            "currency": "USD",
            "option_expiry": "2026-09-18",
            "option_right": "CALL",
            "option_strike": 200,
            "direction": "long",
            "opened_at": closed,
            "recorded_at": recent_recorded,
            "updated_at": closed,
            "closed_at": closed,
            "opened_quantity": 1,
            "closed_quantity": 1,
            "realized_pnl": 10,
            "return": 0.1,
            "commission": 0,
            "executions": [],
        },
        {
            "sequence": 2,
            "instrument_key": "US:OPTION:VISIBLE",
            "instrument_type": "option",
            "symbol": "VISIBLE",
            "currency": "USD",
            "option_expiry": "2026-09-18",
            "option_right": "CALL",
            "option_strike": 200,
            "direction": "long",
            "opened_at": visible,
            "recorded_at": visible,
            "updated_at": visible,
            "closed_at": visible,
            "opened_quantity": 1,
            "closed_quantity": 1,
            "realized_pnl": 10,
            "return": 0.1,
            "commission": 0,
            "executions": [],
        },
    ]

    def fake_cycles(_database, kind, *, include_marks=True):
        return ([], None) if kind == "stock" else (cycles, None)

    monkeypatch.setattr("notification.telegram_timeline._cycles", fake_cycles)
    result = telegram_desk_response(db, "810026", "timeline:pnl:today:0", callback=True)
    assert "VISIBLE" in result.message and "RECENT" not in result.message
    assert "timeline:choose:option" not in result.message


def test_professional_closed_pnl_option_is_realtime_and_long_fill_chain_paginates(db, monkeypatch):
    member = _bound_user(db, "pnl-pro", "810027")
    db.execute(
        "UPDATE users SET plan_type='专业版',subscription_expire='2099-01-01T00:00:00+00:00' WHERE id=?",
        (member["id"],),
    )
    now = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0).isoformat()
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
        "recorded_at": now,
        "updated_at": now,
        "closed_at": now,
        "opened_quantity": 9,
        "closed_quantity": 9,
        "average_cost": 5,
        "commission": 0,
        "realized_pnl": 90,
        "return": 0.2,
        "executions": [
            {"role": "open", "occurred_at": now, "quantity": 1, "price": 5, "commission": 0}
        ]
        + [
            {"role": "add", "occurred_at": now, "quantity": 1, "price": 5, "commission": 0}
            for _ in range(7)
        ]
        + [{"role": "close", "occurred_at": now, "quantity": 1, "price": 15, "commission": 0}],
    }

    def fake_cycles(_database, kind, *, include_marks=True):
        return ([], None) if kind == "stock" else ([cycle], None)

    monkeypatch.setattr("notification.telegram_timeline._cycles", fake_cycles)
    first = telegram_desk_response(db, "810027", "timeline:pnl:today:0", callback=True)
    assert "AAPL" in first.message and "成交明細 1-8／9" in first.message
    assert any(
        button.get("callback_data") == "timeline:pnl:today:1"
        for row in first.keyboard
        for button in row
    )
    second = telegram_desk_response(db, "810027", "timeline:pnl:today:1", callback=True)
    assert "成交明細 9-9／9" in second.message and "平倉" in second.message
    assert len(first.message.encode("utf-16-le")) // 2 < 4096
    assert len(second.message.encode("utf-16-le")) // 2 < 4096
