from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import (
    AUTHORITY,
    CANONICAL_STOCKS,
    CANONICAL_SYSTEM_UNIVERSE,
    SYSTEM_UNIVERSE_SHA256,
    SystemCycleResearchConflict,
    SystemCycleResearchError,
    canonical_json,
    sha256_bytes,
    validate_system_cycle_result,
)
from core.strategy_evaluation import SYSTEM_UNIVERSE
from src.apps.worker.system_cycle_research import (
    build_system_cycle_research_result,
    build_system_cycle_heartbeat,
)
from src.apps.worker.system_cycle_spool import PersistentSystemCycleSpool, SystemCycleSpoolError


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value


def stock_results(*, no_data: tuple[str, ...] = ()) -> dict[str, dict]:
    values = {}
    for index, (_, symbol) in enumerate(CANONICAL_STOCKS):
        if symbol in no_data:
            values[symbol] = {"status": "no_data", "rows": 0, "reason": "source history unavailable"}
        else:
            selected = index in {0, 6}
            values[symbol] = {
                "status": "coverage",
                "rows": 756,
                "dataset_end": "2026-08-11",
                "selected": selected,
                "signal_state": "long" if selected else "flat",
                "latest_price": 100.0 + index,
                "target_quantity": 10.0 if selected else 0.0,
            }
    return values


def result(*, worker_id="strategy-worker", epoch=1, no_data=()) -> dict:
    return build_system_cycle_research_result(
        worker_id=worker_id,
        fencing_epoch=epoch,
        evaluation_date="2026-08-12",
        cycle_slot="after_close",
        strategy_key="trend-cross",
        strategy_name="Trend Cross",
        strategy_version="catalog-20260812",
        source_snapshot_sha256="a" * 64,
        catalog_snapshot_sha256="b" * 64,
        stock_results=stock_results(no_data=no_data),
        evaluated_at=NOW,
    )


def test_builder_imports_and_hashes_the_exact_canonical_13_stock_universe():
    built = result(no_data=("TSLA", "300750"))

    assert CANONICAL_SYSTEM_UNIVERSE == {key: list(value) for key, value in SYSTEM_UNIVERSE.items()}
    assert len(CANONICAL_STOCKS) == 13
    assert built["universe"]["sha256"] == SYSTEM_UNIVERSE_SHA256
    assert [(row["market"], row["symbol"]) for row in built["stocks"]] == list(CANONICAL_STOCKS)
    assert {row["status"] for row in built["stocks"]} == {"coverage", "no_data"}
    assert built["authority"] == AUTHORITY
    assert built["cycle"]["selected_symbols"] == ["AAPL", "000001"]


def test_builder_rejects_missing_stock_and_contract_mutation():
    missing = stock_results()
    missing.pop("AAPL")
    with pytest.raises(ValueError, match="exactly"):
        build_system_cycle_research_result(
            worker_id="strategy-worker", fencing_epoch=1, evaluation_date="2026-08-12",
            cycle_slot="after_close", strategy_key="trend", strategy_name="Trend",
            strategy_version="v1", source_snapshot_sha256="a" * 64,
            catalog_snapshot_sha256="b" * 64, stock_results=missing, evaluated_at=NOW,
        )

    changed = result()
    changed["universe"]["markets"]["US"].append("PLTR")
    with pytest.raises(SystemCycleResearchError, match="canonical"):
        validate_system_cycle_result(changed)


def test_spool_is_idempotent_and_uses_leased_fenced_one_at_a_time_delivery(tmp_path):
    clock = Clock()
    spool = PersistentSystemCycleSpool(BacktestQueueDatabase(tmp_path / "spool.db"), clock=clock)
    receiver_epoch = spool.allocate_fencing_epoch("strategy-worker")
    payload = result(epoch=receiver_epoch)

    first, created = spool.enqueue(payload, idempotency_key="cycle-20260812-after-close")
    second, reused = spool.enqueue(payload, idempotency_key="cycle-20260812-after-close")
    assert created is True and reused is False and first["id"] == second["id"]
    changed = json.loads(canonical_json(payload))
    changed["stocks"][0]["latest_price"] = 999.0
    with pytest.raises(SystemCycleResearchConflict):
        spool.enqueue(changed, idempotency_key="cycle-20260812-after-close")

    claim = spool.claim("spool-uploader", lease_seconds=30)
    assert claim and claim["fencing_epoch"] == 1 and spool.claim("spool-uploader") is None
    heartbeat = spool.heartbeat(claim["id"], "spool-uploader", claim["lease_token"], 1, lease_seconds=30)
    assert heartbeat["result_sha256"] == sha256_bytes(canonical_json(payload))
    with pytest.raises(SystemCycleSpoolError):
        spool.heartbeat(claim["id"], "spool-uploader", "wrong", 1)


def test_spool_expiry_retry_and_delivery_are_recoverable(tmp_path):
    clock = Clock()
    spool = PersistentSystemCycleSpool(BacktestQueueDatabase(tmp_path / "spool.db"), clock=clock)
    epoch = spool.allocate_fencing_epoch("strategy-worker")
    payload = result(epoch=epoch)
    queued, _ = spool.enqueue(payload, idempotency_key="cycle-20260812-after-close")
    claim = spool.claim("spool-uploader", lease_seconds=10)
    assert claim
    clock.value += timedelta(seconds=11)
    reclaimed = spool.claim("spool-uploader", lease_seconds=10)
    assert reclaimed and reclaimed["id"] == queued["id"] and reclaimed["fencing_epoch"] == 2
    failed = spool.fail(
        reclaimed["id"], "spool-uploader", reclaimed["lease_token"], 2,
        error="temporary acceptance failure", retry_delay_seconds=30,
    )
    assert failed["state"] == "failed" and spool.claim("spool-uploader") is None
    clock.value += timedelta(seconds=30)
    retry = spool.claim("spool-uploader", lease_seconds=10)
    assert retry and retry["fencing_epoch"] == 3
    receipt = {
        "accepted": True, "created": True, "receipt_key": "cycle-20260812-after-close",
        "result_sha256": retry["result_sha256"], "state": "shadow",
        "outbound": False, "user_visible": False,
    }
    wrong_key = dict(receipt, receipt_key="another-cycle-result")
    with pytest.raises(SystemCycleSpoolError, match="not bound"):
        spool.complete(retry["id"], "spool-uploader", retry["lease_token"], 3, wrong_key)
    delivered = spool.complete(retry["id"], "spool-uploader", retry["lease_token"], 3, receipt)
    assert delivered["state"] == "delivered"
    assert spool.complete(retry["id"], "spool-uploader", retry["lease_token"], 3, receipt)["state"] == "delivered"
    assert spool.counts() == {
        "pending": 0, "claimed": 0, "sending": 0, "retryable": 0,
        "delivered": 1, "dead": 0, "uncertain": 0,
    }


def test_spool_rejects_an_idempotency_key_that_cannot_be_signed(tmp_path):
    spool = PersistentSystemCycleSpool(BacktestQueueDatabase(tmp_path / "spool.db"), clock=lambda: NOW)

    with pytest.raises(SystemCycleSpoolError, match="idempotency_key"):
        spool.enqueue(result(), idempotency_key="invalid key")


def test_heartbeat_builder_remains_shadow_only():
    value = build_system_cycle_heartbeat(
        worker_id="strategy-worker", fencing_epoch=3,
        counts={"pending": 1, "delivered": 2}, last_result_sha256="c" * 64, heartbeat_at=NOW,
    )
    assert value["authority"] == AUTHORITY
    assert value["spool"] == {"claimed": 0, "delivered": 2, "pending": 1, "retryable": 0}
