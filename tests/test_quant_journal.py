from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC

import pytest

from core.database import DatabaseManager
from core.exceptions import DatabaseError
from core.quant_journal import OfficialPaperJournalV2, QuantJournal


NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(str(tmp_path / "quant-journal.db"))


def stock_leg(
    *,
    symbol: str = "AAPL",
    market: str = "US",
    target: float,
    delta: float,
    price: float,
    **extra,
):
    return {
        "market": market,
        "instrument_type": "stock",
        "symbol": symbol,
        "target_quantity": target,
        "quantity_delta": delta,
        "price": price,
        **extra,
    }


def test_append_is_idempotent_and_sql_rows_are_immutable(db):
    journal = QuantJournal(db)
    payload = dict(
        ledger_key="model/main",
        source="engine-a",
        external_event_id="signal-001",
        strategy_name="trend",
        strategy_version="v1",
        occurred_at=NOW,
        legs=[stock_leg(target=10, delta=10, price=100)],
        metadata={"reason": "daily close"},
    )

    created = journal.append_event(**payload)
    duplicate = journal.append_event(**payload)

    assert created["created"] is True
    assert duplicate["created"] is False
    assert duplicate["id"] == created["id"]
    assert db.fetch_one("SELECT COUNT(*) count FROM quant_events")["count"] == 1
    assert db.fetch_one("SELECT COUNT(*) count FROM quant_event_legs")["count"] == 1
    assert created["occurred_at"].endswith("+00:00")
    assert created["recorded_at"].endswith("+00:00")

    with pytest.raises(ValueError, match="idempotency key"):
        journal.append_event(**{**payload, "legs": [stock_leg(target=11, delta=11, price=100)]})
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute("UPDATE quant_events SET strategy_version='v2' WHERE id=?", (created["id"],))
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute("DELETE FROM quant_event_legs WHERE event_id=?", (created["id"],))
    with pytest.raises(DatabaseError, match="sealed"):
        db.execute(
            """INSERT INTO quant_event_legs
               (event_id,leg_no,market,instrument_type,instrument_key,symbol,currency,
                target_quantity,quantity_delta,price,multiplier,commission)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (created["id"], 1, "US", "stock", "US:STOCK:MSFT", "MSFT", "USD", 1, 1, 1, 1, 0),
        )


def test_replay_keeps_pnl_across_strategy_versions_and_multiple_leg_types(db):
    journal = QuantJournal(db)
    journal.append_event(
        ledger_key="model/global",
        source="engine",
        external_event_id="open-stock",
        strategy_name="momentum",
        strategy_version="1.0",
        occurred_at=NOW,
        legs=[stock_leg(target=10, delta=10, price=100)],
    )
    changed = journal.append_event(
        ledger_key="model/global",
        source="engine",
        external_event_id="strategy-change",
        strategy_name="regime-switch",
        strategy_version="2.0",
        occurred_at="2026-08-06T13:00:00+00:00",
        legs=[
            stock_leg(target=6, delta=-4, price=120),
            {
                "market": "US",
                "instrument_type": "option",
                "symbol": "AAPL",
                "option_expiry": "2026-09-18",
                "option_right": "CALL",
                "option_strike": 130,
                "target_quantity": 2,
                "quantity_delta": 2,
                "price": 5,
                "multiplier": 100,
            },
            {
                "market": "US",
                "instrument_type": "option",
                "symbol": "AAPL",
                "option_expiry": "2026-09-18",
                "option_right": "CALL",
                "option_strike": 150,
                "target_quantity": -2,
                "quantity_delta": -2,
                "price": 2,
                "multiplier": 100,
            },
            stock_leg(symbol="600519", market="CN", target=100, delta=100, price=10),
        ],
    )
    option_keys = [leg["instrument_key"] for leg in changed["legs"] if leg["instrument_type"] == "option"]
    state = journal.replay(
        "model/global",
        marks={
            "US:STOCK:AAPL": 130,
            option_keys[0]: 8,
            option_keys[1]: 1,
            "CN:STOCK:600519": 12,
        },
        initial_cash={"USD": 100_000, "CNY": 500_000},
    )

    assert state["positions"]["US:STOCK:AAPL"]["quantity"] == 6
    assert state["positions"][option_keys[0]]["quantity"] == 2
    assert state["positions"][option_keys[1]]["quantity"] == -2
    assert state["currencies"]["USD"]["cash_flow"] == pytest.approx(-1_120)
    assert state["currencies"]["USD"]["realized_pnl"] == pytest.approx(80)
    assert state["currencies"]["USD"]["unrealized_pnl"] == pytest.approx(980)
    assert state["currencies"]["USD"]["total_pnl"] == pytest.approx(1_060)
    assert state["currencies"]["USD"]["cash"] == pytest.approx(98_880)
    assert state["currencies"]["CNY"]["total_pnl"] == pytest.approx(200)
    assert [item["strategy_version"] for item in state["timeline"]] == ["1.0", "2.0"]
    assert all(item["active"] for item in state["timeline"])


def test_correction_replaces_old_effect_and_reversal_only_appends(db):
    journal = QuantJournal(db)
    wrong = journal.append_event(
        ledger_key="model/corrected",
        source="engine",
        external_event_id="wrong",
        strategy_name="mean-revert",
        strategy_version="1",
        occurred_at=NOW,
        legs=[stock_leg(target=10, delta=10, price=100)],
    )
    corrected = journal.append_event(
        ledger_key="model/corrected",
        source="operator",
        external_event_id="correction-1",
        event_type="correction",
        corrects_event_id=wrong["id"],
        strategy_name="mean-revert",
        strategy_version="1.1",
        occurred_at="2026-08-06T12:05:00Z",
        legs=[stock_leg(target=8, delta=8, price=90)],
        metadata={"reason": "bad quantity and price"},
    )

    corrected_state = journal.replay("model/corrected", marks={"US:STOCK:AAPL": 100})
    assert corrected_state["positions"]["US:STOCK:AAPL"]["quantity"] == 8
    assert corrected_state["positions"]["US:STOCK:AAPL"]["average_cost"] == 90
    assert corrected_state["currencies"]["USD"]["cash_flow"] == -720
    assert corrected_state["currencies"]["USD"]["unrealized_pnl"] == 80
    assert [item["active"] for item in corrected_state["timeline"]] == [False, True]

    reversal = journal.append_reversal(
        source="operator",
        external_event_id="reversal-1",
        corrects_event_id=corrected["id"],
        occurred_at="2026-08-06T12:10:00+00:00",
    )
    final_state = journal.replay("model/corrected")

    assert reversal["event_type"] == "reversal" and reversal["legs"] == []
    assert final_state["positions"] == {}
    assert final_state["currencies"] == {}
    assert final_state["event_count"] == 3
    assert db.fetch_one("SELECT COUNT(*) count FROM quant_events")["count"] == 3
    with pytest.raises(ValueError, match="already been superseded"):
        journal.append_event(
            ledger_key="model/corrected",
            source="operator",
            external_event_id="correction-branch",
            event_type="correction",
            corrects_event_id=wrong["id"],
            strategy_name="mean-revert",
            strategy_version="1.2",
            occurred_at="2026-08-06T12:15:00Z",
            legs=[stock_leg(target=1, delta=1, price=95)],
        )


def test_correction_execution_legs_are_the_held_position_delta(db):
    journal = QuantJournal(db)
    wrong = journal.append_event(
        ledger_key="model/execution-delta",
        source="engine",
        external_event_id="wrong-symbol",
        strategy_name="rotation",
        strategy_version="1",
        occurred_at=NOW,
        legs=[stock_leg(target=10, delta=10, price=100)],
    )
    reduced = journal.append_event(
        ledger_key="model/execution-delta",
        source="operator",
        external_event_id="reduce-aapl",
        event_type="correction",
        corrects_event_id=wrong["id"],
        strategy_name="rotation",
        strategy_version="1.1",
        occurred_at="2026-08-06T12:05:00Z",
        legs=[stock_leg(target=8, delta=8, price=90)],
    )

    reduced_leg = journal.execution_legs(reduced["id"])[0]
    assert reduced_leg["quantity_delta"] == -2
    assert reduced_leg["target_quantity"] == 8

    replacement = journal.append_event(
        ledger_key="model/replacement-symbol",
        source="engine",
        external_event_id="old-aapl",
        strategy_name="rotation",
        strategy_version="1",
        occurred_at=NOW,
        legs=[stock_leg(target=10, delta=10, price=100)],
    )
    corrected = journal.append_event(
        ledger_key="model/replacement-symbol",
        source="operator",
        external_event_id="new-msft",
        event_type="correction",
        corrects_event_id=replacement["id"],
        strategy_name="rotation",
        strategy_version="1.1",
        occurred_at="2026-08-06T12:05:00Z",
        legs=[stock_leg(symbol="MSFT", target=5, delta=5, price=200)],
    )

    legs = {leg["symbol"]: leg for leg in journal.execution_legs(corrected["id"])}
    assert (legs["AAPL"]["quantity_delta"], legs["AAPL"]["target_quantity"], legs["AAPL"]["price"]) == (-10, 0, None)
    assert (legs["MSFT"]["quantity_delta"], legs["MSFT"]["target_quantity"]) == (5, 5)


@pytest.mark.parametrize(
    "leg,match",
    [
        (stock_leg(symbol="AAPL", market="CN", target=1, delta=1, price=1), "symbol"),
        (stock_leg(target=1, delta=0, price=1), "non-zero"),
        (stock_leg(target=1, delta=True, price=1), "finite number"),
        (stock_leg(target=1, delta=1, price=float("nan")), "finite number"),
        (stock_leg(target=1, delta=1, price=1, multiplier=100), "multiplier"),
        ({
            "market": "CN", "instrument_type": "option", "symbol": "510300",
            "option_expiry": "2026-09-18", "option_right": "CALL", "option_strike": 4,
            "target_quantity": 1, "quantity_delta": 1, "price": .1,
        }, "only US option"),
    ],
)
def test_leg_input_boundaries_fail_closed(db, leg, match):
    with pytest.raises(ValueError, match=match):
        QuantJournal(db).append_event(
            ledger_key="model/strict",
            source="engine",
            external_event_id=f"invalid-{match}",
            strategy_name="strict",
            strategy_version="1",
            occurred_at=NOW,
            legs=[leg],
        )


def test_target_time_metadata_and_reopen_boundaries(db):
    journal = QuantJournal(db)
    with pytest.raises(ValueError, match="timezone"):
        journal.append_event(
            ledger_key="model/strict",
            source="engine",
            external_event_id="naive-time",
            strategy_name="strict",
            strategy_version="1",
            occurred_at="2026-08-06T12:00:00",
        )
    with pytest.raises(ValueError, match="JSON"):
        journal.append_event(
            ledger_key="model/strict",
            source="engine",
            external_event_id="bad-metadata",
            strategy_name="strict",
            strategy_version="1",
            occurred_at=NOW,
            metadata={"bad": float("nan")},
        )

    journal.append_event(
        ledger_key="model/strict",
        source="engine",
        external_event_id="valid",
        strategy_name="strict",
        strategy_version="1",
        occurred_at=NOW,
        legs=[stock_leg(target=2, delta=2, price=10)],
    )
    with pytest.raises(ValueError, match="target_quantity"):
        journal.append_event(
            ledger_key="model/strict",
            source="engine",
            external_event_id="bad-target",
            strategy_name="strict",
            strategy_version="2",
            occurred_at=NOW,
            legs=[stock_leg(target=4, delta=1, price=11)],
        )

    reopened = QuantJournal(DatabaseManager(db._db_path))
    state = reopened.replay("model/strict", marks={"US:STOCK:AAPL": 12})
    assert state["event_count"] == 1
    assert state["positions"]["US:STOCK:AAPL"]["quantity"] == 2
    assert state["currencies"]["USD"]["unrealized_pnl"] == 4


def test_ledger_rejects_out_of_order_events_without_losing_history(db):
    journal = QuantJournal(db)
    journal.append_event(
        ledger_key="model/timeline",
        source="engine",
        external_event_id="newer",
        strategy_name="timeline",
        strategy_version="1",
        occurred_at="2026-08-06T12:00:00+00:00",
        legs=[stock_leg(target=1, delta=1, price=100)],
    )

    with pytest.raises(ValueError, match="cannot precede"):
        journal.append_event(
            ledger_key="model/timeline",
            source="engine",
            external_event_id="late-older",
            strategy_name="timeline",
            strategy_version="1",
            occurred_at="2026-08-05T12:00:00+00:00",
            legs=[stock_leg(target=2, delta=1, price=101)],
        )

    assert [event["external_event_id"] for event in journal.list_events("model/timeline")] == ["newer"]


def test_equity_snapshots_are_auditable_idempotent_and_drive_period_windows(db):
    journal = QuantJournal(db)
    first_time = NOW - timedelta(days=370)
    first = journal.append_equity_snapshot(
        ledger_key="model/equity",
        source="engine",
        external_snapshot_id="snapshot-001",
        currency="USD",
        initial_cash=100_000,
        cash=90_000,
        market_value=10_000,
        realized_pnl=0,
        unrealized_pnl=0,
        captured_at=first_time,
    )
    retry = journal.append_equity_snapshot(
        ledger_key="model/equity",
        source="engine",
        external_snapshot_id="snapshot-001",
        currency="USD",
        initial_cash=100_000,
        cash=90_000,
        market_value=10_000,
        realized_pnl=0,
        unrealized_pnl=0,
        captured_at=first_time,
    )
    journal.append_equity_snapshot(
        ledger_key="model/equity",
        source="engine",
        external_snapshot_id="snapshot-002",
        currency="USD",
        initial_cash=100_000,
        cash=91_000,
        market_value=14_000,
        realized_pnl=2_000,
        unrealized_pnl=3_000,
        captured_at=NOW,
    )

    performance = journal.performance_windows("model/equity", "USD")
    assert first["created"] is True and retry["created"] is False
    assert performance["current"]["total_equity"] == 105_000
    assert performance["windows"]["1年"]["pnl"] == 5_000
    assert performance["windows"]["1年"]["return"] == pytest.approx(.05)
    assert performance["windows"]["1周"]["available"] is False
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute("DELETE FROM quant_equity_snapshots")


def test_equity_snapshot_rejects_inconsistent_and_out_of_order_values(db):
    journal = QuantJournal(db)
    with pytest.raises(ValueError, match="equity must equal"):
        journal.append_equity_snapshot(
            ledger_key="model/equity",
            source="engine",
            external_snapshot_id="bad-math",
            currency="USD",
            cash=100_000,
            market_value=10_000,
            realized_pnl=0,
            unrealized_pnl=0,
            captured_at=NOW,
        )
    journal.append_equity_snapshot(
        ledger_key="model/equity",
        source="engine",
        external_snapshot_id="valid-latest",
        currency="USD",
        cash=90_000,
        market_value=10_000,
        realized_pnl=0,
        unrealized_pnl=0,
        captured_at=NOW,
    )
    with pytest.raises(ValueError, match="cannot precede"):
        journal.append_equity_snapshot(
            ledger_key="model/equity",
            source="engine",
            external_snapshot_id="late-older",
            currency="USD",
            cash=90_000,
            market_value=10_000,
            realized_pnl=0,
            unrealized_pnl=0,
            captured_at=NOW - timedelta(days=1),
        )


def test_official_paper_v2_has_three_10000_genesis_accounts_and_is_append_only(db):
    journal = OfficialPaperJournalV2(db)
    accounts = journal.ensure_genesis("tradeai-official-paper-v2")

    assert [(row["market"], row["currency"], row["initial_cash"]) for row in accounts] == [
        ("US", "USD", 10_000),
        ("HK", "HKD", 10_000),
        ("CN", "CNY", 10_000),
    ]
    assert all(row["cash"] == row["total_equity"] == 10_000 for row in accounts)
    assert all(
        len(row["payload_hash"]) == 64
        and set(row["payload_hash"]) <= set("0123456789abcdef")
        for row in accounts
    )
    assert db.fetch_one("SELECT COUNT(*) count FROM quant_events")["count"] == 0
    assert db.fetch_one("SELECT COUNT(*) count FROM quant_equity_snapshots")["count"] == 0

    event = journal.append_event(
        ledger_key="tradeai-official-paper-v2",
        source="pytest",
        external_event_id="hk-stock-open",
        strategy_name="official-paper-v2",
        strategy_version="2",
        occurred_at=NOW,
        legs=[{
            "market": "HK", "instrument_type": "stock", "symbol": "00700",
            "target_quantity": 10, "quantity_delta": 10, "price": 300,
        }],
    )

    assert event["legs"][0]["currency"] == "HKD"
    assert db.fetch_one("SELECT COUNT(*) count FROM quant_events")["count"] == 0
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute("UPDATE official_paper_events_v2 SET strategy_version='3'")
    with pytest.raises(DatabaseError, match="append-only"):
        db.execute("DELETE FROM official_paper_equity_snapshots_v2")


def test_official_paper_v2_database_rejects_correction_forks(db):
    journal = OfficialPaperJournalV2(db)
    original = journal.append_event(
        ledger_key="tradeai-official-paper-v2", source="v2-fork", external_event_id="original",
        strategy_name="trend", strategy_version="1", occurred_at="2026-08-01T10:00:00+00:00",
        legs=[{"market": "US", "instrument_type": "stock", "symbol": "AAPL", "target_quantity": 1, "quantity_delta": 1, "price": 100}],
    )
    journal.append_event(
        ledger_key="tradeai-official-paper-v2", source="v2-fork", external_event_id="correction-one",
        event_type="correction", corrects_event_id=original["id"], strategy_name="trend", strategy_version="2",
        occurred_at="2026-08-01T11:00:00+00:00",
        legs=[{"market": "US", "instrument_type": "stock", "symbol": "AAPL", "target_quantity": 2, "quantity_delta": 2, "price": 101}],
    )

    with pytest.raises(DatabaseError, match="UNIQUE constraint failed"):
        db.execute(
            """INSERT INTO official_paper_events_v2
               (ledger_key,source,external_event_id,event_type,strategy_name,strategy_version,corrects_event_id,
                occurred_at,recorded_at,leg_count,metadata_json,payload_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("tradeai-official-paper-v2", "v2-fork", "correction-two", "reversal", "trend", "2", original["id"],
             "2026-08-01T12:00:00+00:00", "2026-08-01T12:00:00+00:00", 0, "{}", "a" * 64),
        )
