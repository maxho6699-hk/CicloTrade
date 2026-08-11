from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.strategy_evaluation import SYSTEM_UNIVERSE
from src.apps.worker.system_cycle_producer import (
    cycle_date_at,
    cycle_slot_at,
    produce_once,
)
from src.apps.worker.system_cycle_spool import PersistentSystemCycleSpool


class Source:
    def __init__(self, *, missing: set[str] | None = None):
        self.calls = 0
        self.missing = missing or set()

    def history(self, symbols, period="3y", interval="1d"):
        self.calls += 1
        assert period == "3y" and interval == "1d"
        index = pd.date_range("2025-05-01", periods=330, freq="B")
        close = {
            symbol: pd.Series(80.0 + offset + np.arange(len(index)) * .2, index=index)
            for offset, symbol in enumerate(symbols)
            if symbol not in self.missing
        }
        frame = pd.DataFrame(close, index=index)
        return frame, frame * 0 + 1_000


class DisabledSource:
    def history(self, *_args, **_kwargs):
        raise RuntimeError("MARKET_DATA_ENABLED is false")


@pytest.fixture
def spool(tmp_path):
    return PersistentSystemCycleSpool(BacktestQueueDatabase(tmp_path / "system-cycle.db"))


def test_producer_enqueues_exactly_thirteen_shadow_records_and_no_product_tables(spool):
    now = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)  # 10:00 New York, intraday
    outcome = produce_once(spool=spool, data_source=Source(), now=now)
    row = spool.database.fetch_one("SELECT payload_json FROM system_cycle_research_spool")
    payload = __import__("json").loads(row["payload_json"])

    assert outcome["created"] is True
    assert payload["authority"]["research_only"] is True
    assert len(payload["stocks"]) == 13
    assert [stock["symbol"] for stock in payload["stocks"]] == [symbol for market in ("US", "CN") for symbol in SYSTEM_UNIVERSE[market]]
    tables = {entry["name"] for entry in spool.database.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "quant_events" not in tables and "strategy_scores" not in tables


def test_partial_source_failure_generates_one_no_data_record_without_fake_price(spool):
    outcome = produce_once(
        spool=spool,
        data_source=Source(missing={"MSFT"}),
        now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
    )
    payload = spool.database.fetch_one("SELECT payload_json FROM system_cycle_research_spool")
    record = next(item for item in __import__("json").loads(payload["payload_json"])["stocks"] if item["symbol"] == "MSFT")

    assert outcome["created"] is True
    assert record["status"] == "no_data"
    assert record["latest_price"] is None and record["target_quantity"] == 0


def test_disabled_or_failed_market_data_fails_closed_to_all_no_data(spool):
    outcome = produce_once(
        spool=spool,
        data_source=DisabledSource(),
        now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
    )
    payload = spool.database.fetch_one("SELECT payload_json FROM system_cycle_research_spool")
    stocks = __import__("json").loads(payload["payload_json"])["stocks"]

    assert outcome["created"] is True
    assert all(item["status"] == "no_data" and item["latest_price"] is None for item in stocks)


def test_same_slot_is_idempotent_before_reaching_source(spool):
    source = Source()
    now = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)

    first = produce_once(spool=spool, data_source=source, now=now)
    second = produce_once(spool=spool, data_source=source, now=now.replace(minute=14))

    assert first["created"] is True
    assert second["created"] is False and second["skipped"] is True
    assert source.calls == 1
    assert spool.database.fetch_one("SELECT COUNT(*) count FROM system_cycle_research_spool")["count"] == 1


@pytest.mark.parametrize(
    ("moment", "slot"),
    [
        (datetime(2026, 8, 12, 8, 0, tzinfo=UTC), "premarket"),
        (datetime(2026, 8, 12, 14, 0, tzinfo=UTC), "intraday"),
        (datetime(2026, 8, 12, 21, 0, tzinfo=UTC), "after_close"),
        (datetime(2026, 8, 12, 6, 0, tzinfo=UTC), "overnight"),
    ],
)
def test_new_york_slot_mapping_is_stable_for_all_four_slots(moment, slot):
    assert cycle_slot_at(moment) == slot
    assert cycle_date_at(moment).isoformat() == "2026-08-12"


def test_four_slots_create_four_distinct_cycle_records(spool):
    source = Source()
    moments = [
        datetime(2026, 8, 12, 8, 0, tzinfo=UTC),
        datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
        datetime(2026, 8, 12, 21, 0, tzinfo=UTC),
        datetime(2026, 8, 12, 6, 0, tzinfo=UTC),
    ]

    outcomes = [produce_once(spool=spool, data_source=source, now=moment) for moment in moments]

    assert all(item["created"] for item in outcomes)
    assert spool.database.fetch_one("SELECT COUNT(*) count FROM system_cycle_research_spool")["count"] == 4
