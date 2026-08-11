from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.strategy_evaluation import SYSTEM_UNIVERSE
from data.datasource import DataSourceError
import src.apps.worker.system_cycle_producer as producer
from src.apps.worker.system_cycle_producer import (
    ProducerSettings,
    SystemCycleProducerError,
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
        raise DataSourceError("MARKET_DATA_ENABLED is false")


class EmptySource:
    def history(self, *_args, **_kwargs):
        return pd.DataFrame(), pd.DataFrame()


class InvalidSource:
    def history(self, *_args, **_kwargs):
        return ["not", "a", "dataframe"], None


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
    assert len(__import__("json").loads(payload["payload_json"])["stocks"]) == 13
    assert record["status"] == "no_data"
    assert record["latest_price"] is None and record["target_quantity"] == 0


def test_complete_market_data_failure_does_not_allocate_or_occupy_slot(spool):
    with pytest.raises(SystemCycleProducerError, match="unavailable"):
        produce_once(
            spool=spool,
            data_source=DisabledSource(),
            now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
        )

    assert spool.database.fetch_one("SELECT COUNT(*) count FROM system_cycle_research_spool")["count"] == 0
    assert spool.database.fetch_one("SELECT COUNT(*) count FROM system_cycle_research_spool_workers")["count"] == 0


def test_all_no_data_does_not_occupy_slot_and_recovery_can_create(spool):
    now = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)
    with pytest.raises(SystemCycleProducerError, match="no valid canonical stock coverage"):
        produce_once(spool=spool, data_source=EmptySource(), now=now)

    recovered = produce_once(spool=spool, data_source=Source(), now=now)

    assert recovered["created"] is True
    assert spool.database.fetch_one("SELECT COUNT(*) count FROM system_cycle_research_spool")["count"] == 1


def test_non_dataframe_market_data_does_not_occupy_slot(spool):
    with pytest.raises(SystemCycleProducerError, match="pandas DataFrame"):
        produce_once(
            spool=spool,
            data_source=InvalidSource(),
            now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
        )

    assert spool.database.fetch_one("SELECT COUNT(*) count FROM system_cycle_research_spool")["count"] == 0
    assert spool.database.fetch_one("SELECT COUNT(*) count FROM system_cycle_research_spool_workers")["count"] == 0


def test_disabled_cli_has_zero_database_or_network_side_effects(monkeypatch, capsys):
    monkeypatch.delenv("TRADEAI_SYSTEM_CYCLE_PRODUCER_ENABLED", raising=False)
    monkeypatch.setattr(producer, "BacktestQueueDatabase", lambda *_args, **_kwargs: pytest.fail("database opened"))
    monkeypatch.setattr(producer, "YFinanceAdapter", lambda: pytest.fail("network source constructed"))

    assert producer.main(["--once"]) == 0
    assert __import__("json").loads(capsys.readouterr().out)["state"] == "disabled"


def test_enabled_settings_require_an_absolute_spool_and_market_data_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEAI_SYSTEM_CYCLE_PRODUCER_ENABLED", "true")
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    with pytest.raises(SystemCycleProducerError, match="absolute"):
        ProducerSettings.from_environment(spool_path="relative.db")

    settings = ProducerSettings.from_environment(spool_path=str(tmp_path / "spool.db"))
    assert settings.enabled is True and settings.spool_path == tmp_path / "spool.db"


def test_service_has_environment_and_integration_enable_gates():
    body = (Path(__file__).resolve().parents[4] / "ops" / "ciclotrade-system-cycle-producer.service").read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/ciclotrade-worker/producer.env" in body
    assert "ConditionPathExists=/etc/ciclotrade-worker/enable-system-cycle-producer.after-integration" in body
    assert "PrivateNetwork=false" in body


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
