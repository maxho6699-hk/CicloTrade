from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
import importlib
import json
from pathlib import Path

import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import CANONICAL_STOCKS, canonical_json, sha256_bytes
from core.system_cycle_research_store import SystemCycleResearchStore
from src.apps.api.system_cycle_receiver import SystemCycleResearchReceiver
from src.apps.api.system_cycle_research_read_model import SystemCycleResearchReadModel
from src.apps.worker.system_cycle_research import (
    build_system_cycle_heartbeat,
    build_system_cycle_research_result,
)


NOW = datetime.now(UTC).replace(microsecond=0)


class Clock:
    def __init__(self, value: datetime = NOW):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _stock_results(*, as_of: datetime = NOW, no_data: tuple[str, ...] = ()) -> dict[str, dict]:
    values: dict[str, dict] = {}
    for index, (_, symbol) in enumerate(CANONICAL_STOCKS):
        if symbol in no_data:
            values[symbol] = {"status": "no_data", "rows": 0, "reason": "private source detail"}
        else:
            selected = index in {0, 6}
            values[symbol] = {
                "status": "coverage",
                "rows": 756,
                "dataset_end": (as_of - timedelta(days=1)).date().isoformat(),
                "selected": selected,
                "signal_state": "long" if selected else "flat",
                "latest_price": 100.0 + index,
                "target_quantity": 10.0 if selected else 0.0,
            }
    return values


def _result(*, evaluated_at: datetime = NOW, cycle_slot: str = "after_close", no_data: tuple[str, ...] = ()) -> dict:
    return build_system_cycle_research_result(
        worker_id="internal-worker-identity",
        fencing_epoch=7,
        evaluation_date=evaluated_at.date().isoformat(),
        cycle_slot=cycle_slot,
        strategy_key="trend-cross",
        strategy_name="Trend Cross",
        strategy_version="catalog-v1",
        source_snapshot_sha256="a" * 64,
        catalog_snapshot_sha256="b" * 64,
        stock_results=_stock_results(as_of=evaluated_at, no_data=no_data),
        evaluated_at=evaluated_at,
    )


def _record(store: SystemCycleResearchStore, result: dict, *, key: str) -> None:
    raw = canonical_json(result)
    store.record_result(
        result,
        receipt_key=key,
        worker_id=result["worker_id"],
        fencing_epoch=result["fencing_epoch"],
        result_sha256=sha256_bytes(raw),
    )


def _heartbeat(store: SystemCycleResearchStore, *, at: datetime = NOW) -> None:
    value = build_system_cycle_heartbeat(
        worker_id="internal-worker-identity",
        fencing_epoch=7,
        counts={"pending": 2, "claimed": 1, "retryable": 3, "delivered": 4},
        last_result_sha256=None,
        heartbeat_at=at,
    )
    raw = canonical_json(value)
    store.record_heartbeat(
        value,
        heartbeat_key=f"heartbeat-{at.strftime('%Y%m%d%H%M%S')}",
        worker_id=value["worker_id"],
        fencing_epoch=value["fencing_epoch"],
        payload_sha256=sha256_bytes(raw),
    )


def _store(tmp_path: Path, clock: Clock | None = None) -> SystemCycleResearchStore:
    return SystemCycleResearchStore(BacktestQueueDatabase(tmp_path / "research.db"), clock=clock)


async def _asgi(path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], dict]:
    app = importlib.import_module("src.apps.api.app").app
    raw_path, _, query = path.partition("?")
    messages: list[dict] = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "https", "path": raw_path,
            "raw_path": raw_path.encode(), "query_string": query.encode(),
            "headers": [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 443), "root_path": "",
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {name.decode().lower(): value.decode() for name, value in start["headers"]}
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], response_headers, json.loads(body or b"{}")


@pytest.fixture
def research_receiver(tmp_path, monkeypatch):
    module = importlib.import_module("src.apps.api.app")
    clock = Clock()
    store = _store(tmp_path, clock)
    receiver = SystemCycleResearchReceiver(store, shared_secret="r" * 32, enabled=True, clock=clock)
    previous = getattr(module.app.state, "system_cycle_research_receiver", None)
    monkeypatch.setattr(module.app.state, "system_cycle_research_receiver", receiver)
    try:
        yield receiver, store, clock
    finally:
        module.app.state.system_cycle_research_receiver = previous


def _token(browser_api) -> str:
    return browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token


def test_read_model_states_cover_waiting_healthy_stale_and_degraded(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    model = SystemCycleResearchReadModel(store, clock=clock)
    assert model.status()["state"] == "waiting"

    _heartbeat(store)
    assert model.status()["state"] == "waiting"

    _record(store, _result(), key="healthy-result-20260812")
    assert model.status()["state"] == "healthy"

    clock.value += timedelta(seconds=601)
    assert model.status()["state"] == "stale"

    fresh_clock = Clock()
    degraded = _store(tmp_path / "degraded", fresh_clock)
    _record(degraded, _result(no_data=tuple(symbol for _, symbol in CANONICAL_STOCKS[:7])), key="degraded-result-20260812")
    assert SystemCycleResearchReadModel(degraded, clock=fresh_clock).status()["state"] == "degraded"


def test_fresh_heartbeat_keeps_an_old_market_cycle_healthy_until_heartbeat_stales(tmp_path):
    clock = Clock()
    store = _store(tmp_path, clock)
    _record(store, _result(evaluated_at=clock.value - timedelta(hours=2)), key="old-cycle-result-20260812")
    _heartbeat(store, at=clock.value - timedelta(minutes=1))
    model = SystemCycleResearchReadModel(store, clock=clock)
    assert model.status()["state"] == "healthy"

    clock.value += timedelta(minutes=10)
    assert model.status()["state"] == "stale"


def test_read_snapshot_explicitly_begins_one_sqlite_read_transaction(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _record(store, _result(), key="snapshot-result-20260812")
    statements: list[str] = []
    database = store.database
    transaction = database.transaction

    @contextmanager
    def traced_transaction():
        with transaction() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, "transaction", traced_transaction)
    snapshot = store.research_read_snapshot(1)

    assert snapshot["latest"] is not None
    begin = next(index for index, statement in enumerate(statements) if statement == "BEGIN")
    select_indexes = [
        index for index, statement in enumerate(statements)
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(select_indexes) == 3
    assert begin < min(select_indexes)


def test_asgi_routes_require_identity_and_return_disabled_waiting_and_healthy(browser_api, research_receiver):
    receiver, store, _ = research_receiver
    status, _, _ = asyncio.run(_asgi("/api/rewrite/v1/system-cycle-research/status"))
    assert status == 401

    token = _token(browser_api)
    receiver.enabled = False
    status, headers, payload = asyncio.run(_asgi(
        "/api/rewrite/v1/system-cycle-research/status", {"authorization": f"Bearer {token}"}
    ))
    assert status == 200 and payload["available"] is False and payload["state"] == "waiting"
    assert headers["cache-control"] == "no-store" and headers["x-content-type-options"] == "nosniff"

    receiver.enabled = True
    status, _, payload = asyncio.run(_asgi(
        "/api/rewrite/v1/system-cycle-research/status", {"authorization": f"Bearer {token}"}
    ))
    assert status == 200 and payload["available"] is False and payload["state"] == "waiting"

    _heartbeat(store)
    _record(store, _result(), key="api-healthy-result-20260812")
    status, _, payload = asyncio.run(_asgi(
        "/api/rewrite/v1/system-cycle-research/status", {"authorization": f"Bearer {token}"}
    ))
    assert status == 200 and payload["available"] is True and payload["state"] == "healthy"
    assert payload["spool"] == {"pending": 2, "claimed": 1, "retryable": 3, "delivered": 4}


def test_latest_is_exactly_thirteen_sanitized_stocks_and_no_product_tables(browser_api, research_receiver):
    _, store, _ = research_receiver
    _record(store, _result(no_data=(CANONICAL_STOCKS[-1][1],)), key="api-sanitized-result-20260812")
    token = _token(browser_api)
    status, _, payload = asyncio.run(_asgi(
        "/api/rewrite/v1/system-cycle-research/latest", {"authorization": f"Bearer {token}"}
    ))
    assert status == 200 and payload["available"] is True
    cycle = payload["cycle"]
    assert len(cycle["stocks"]) == 13 and cycle["coverage_count"] == 12 and cycle["no_data_count"] == 1
    assert set(cycle["stocks"][0]) == {
        "market", "symbol", "status", "rows", "dataset_end", "selected", "signal_state", "latest_price", "target_quantity",
    }
    encoded = json.dumps(payload, sort_keys=True)
    for forbidden in ("worker_id", "fencing_epoch", "authority", "payload", "reason", "database", "secret", "token", "lease"):
        assert forbidden not in encoded
    tables = {row["name"] for row in store.database.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not ({"quant_events", "notifications"} & tables)
    assert not any(name.startswith("official_") for name in tables)


def test_history_validates_limit_and_is_stably_descending_without_payloads(browser_api, research_receiver):
    _, store, _ = research_receiver
    older = NOW - timedelta(minutes=1)
    _record(store, _result(evaluated_at=older, cycle_slot="premarket"), key="history-older-result-20260812")
    _record(store, _result(evaluated_at=NOW, cycle_slot="after_close"), key="history-newer-result-20260812")
    token = _token(browser_api)
    headers = {"authorization": f"Bearer {token}"}
    status, _, payload = asyncio.run(_asgi("/api/rewrite/v1/system-cycle-research/history?limit=1", headers))
    assert status == 200 and payload["limit"] == 1 and len(payload["items"]) == 1
    assert payload["items"][0]["cycle_slot"] == "after_close"
    assert set(payload["items"][0]) == {
        "cycle_id", "evaluation_date", "cycle_slot", "strategy_key", "strategy_name", "strategy_version",
        "evaluated_at", "received_at", "coverage_count", "no_data_count", "selected_count",
    }
    status, _, _ = asyncio.run(_asgi("/api/rewrite/v1/system-cycle-research/history?limit=0", headers))
    assert status == 400
    status, _, _ = asyncio.run(_asgi("/api/rewrite/v1/system-cycle-research/history?limit=101", headers))
    assert status == 400
