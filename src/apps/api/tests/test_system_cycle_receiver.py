from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import (
    CANONICAL_STOCKS,
    RECEIVER_ENDPOINT_RESULT,
    canonical_json,
    receiver_signature,
    sha256_bytes,
)
from core.system_cycle_research_store import SystemCycleResearchStore
from src.apps.api.system_cycle_receiver import (
    SystemCycleResearchReceiver,
    SystemCycleResearchReceiverError,
    build_system_cycle_research_receiver,
    system_cycle_research_heartbeat,
    system_cycle_research_result,
)
from src.apps.worker.system_cycle_research import build_system_cycle_heartbeat, build_system_cycle_research_result
from src.apps.worker.system_cycle_spool import signed_heartbeat_headers, signed_result_headers


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SECRET = b"s" * 32


def stock_results():
    return {
        symbol: {
            "status": "coverage", "rows": 756, "dataset_end": "2026-08-11",
            "selected": index == 0, "signal_state": "long" if index == 0 else "flat",
            "latest_price": 100.0 + index, "target_quantity": 10.0 if index == 0 else 0.0,
        }
        for index, (_, symbol) in enumerate(CANONICAL_STOCKS)
    }


def result(epoch=1):
    return build_system_cycle_research_result(
        worker_id="strategy-worker", fencing_epoch=epoch, evaluation_date="2026-08-12",
        cycle_slot="after_close", strategy_key="trend-cross", strategy_name="Trend Cross",
        strategy_version="catalog-20260812", source_snapshot_sha256="a" * 64,
        catalog_snapshot_sha256="b" * 64, stock_results=stock_results(), evaluated_at=NOW,
    )


def receiver(tmp_path):
    database = BacktestQueueDatabase(tmp_path / "research.db")
    store = SystemCycleResearchStore(database, clock=lambda: NOW)
    return SystemCycleResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW), database


def result_headers(value, *, key="cycle-result-20260812", sent_at=NOW):
    body = canonical_json(value)
    claim = {
        "payload": value, "result_sha256": sha256_bytes(body), "idempotency_key": key,
    }
    return signed_result_headers(claim, SECRET, sent_at=sent_at)


def test_signed_result_acceptance_is_idempotent_append_only_and_shadow_isolated(tmp_path):
    service, database = receiver(tmp_path)
    value = result()
    body, headers = canonical_json(value), result_headers(value)

    first = service.accept_result(body, headers)
    retry = service.accept_result(body, headers)

    assert first["created"] is True and retry["created"] is False
    assert first["result_sha256"] == sha256_bytes(body)
    assert first["state"] == "shadow" and first["outbound"] is first["user_visible"] is False
    tables = {row["name"] for row in database.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not ({"quant_events", "notifications"} & tables)
    assert not any(name.startswith("official_") for name in tables)
    with database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE system_cycle_research_receipts SET worker_id='changed' WHERE receipt_key=?",
                (first["receipt_key"],),
            )


def test_receiver_rejects_bad_expired_hash_stale_and_variant_requests(tmp_path):
    service, _ = receiver(tmp_path)
    value = result(epoch=2)
    body, headers = canonical_json(value), result_headers(value, key="cycle-result-epoch-2")
    service.accept_result(body, headers)

    bad_signature = dict(headers, **{"x-ciclotrade-research-signature": "sha256=" + "0" * 64})
    with pytest.raises(SystemCycleResearchReceiverError) as invalid:
        service.accept_result(body, bad_signature)
    assert invalid.value.status == 401

    expired = result_headers(value, key="cycle-result-expired", sent_at=NOW - timedelta(minutes=10))
    with pytest.raises(SystemCycleResearchReceiverError) as old:
        service.accept_result(body, expired)
    assert old.value.status == 401

    wrong_hash = "0" * 64
    wrong_hash_headers = {
        "x-ciclotrade-worker-id": "strategy-worker",
        "x-ciclotrade-fencing-epoch": "2",
        "idempotency-key": "cycle-result-wrong-hash",
        "x-ciclotrade-sent-at": "2026-08-12T12:00:00Z",
        "x-ciclotrade-result-sha256": wrong_hash,
        "x-ciclotrade-research-signature": receiver_signature(
            SECRET, endpoint=RECEIVER_ENDPOINT_RESULT, worker_id="strategy-worker", fencing_epoch=2,
            idempotency_key="cycle-result-wrong-hash", sent_at="2026-08-12T12:00:00Z", body_sha256=wrong_hash,
        ),
    }
    with pytest.raises(SystemCycleResearchReceiverError) as mismatch:
        service.accept_result(body, wrong_hash_headers)
    assert mismatch.value.status == 409

    stale = result(epoch=1)
    with pytest.raises(SystemCycleResearchReceiverError) as old_epoch:
        service.accept_result(canonical_json(stale), result_headers(stale, key="cycle-result-stale"))
    assert old_epoch.value.status == 409

    changed = json.loads(body)
    changed["stocks"][0]["latest_price"] = 999.0
    changed_body = canonical_json(changed)
    changed_headers = result_headers(changed, key="cycle-result-epoch-2")
    with pytest.raises(SystemCycleResearchReceiverError) as conflict:
        service.accept_result(changed_body, changed_headers)
    assert conflict.value.status == 409


def test_receiver_rejects_an_alternate_key_or_revision_for_an_existing_worker_cycle(tmp_path):
    service, database = receiver(tmp_path)
    value = result(epoch=1)
    body = canonical_json(value)
    service.accept_result(body, result_headers(value, key="cycle-result-original"))

    with pytest.raises(SystemCycleResearchReceiverError) as duplicate_key:
        service.accept_result(body, result_headers(value, key="cycle-result-alternate"))
    assert duplicate_key.value.status == 409

    revision = json.loads(body)
    revision["stocks"][0]["latest_price"] = 999.0
    with pytest.raises(SystemCycleResearchReceiverError) as changed_cycle:
        service.accept_result(
            canonical_json(revision), result_headers(revision, key="cycle-result-revision")
        )
    assert changed_cycle.value.status == 409
    assert database.fetch_one("SELECT count(*) total FROM system_cycle_research_receipts")["total"] == 1


def test_signed_heartbeat_advances_fence_and_api_routes_return_no_store(tmp_path):
    service, database = receiver(tmp_path)
    heartbeat = build_system_cycle_heartbeat(
        worker_id="strategy-worker", fencing_epoch=3,
        counts={"pending": 1}, last_result_sha256=None, heartbeat_at=NOW,
    )
    headers = signed_heartbeat_headers(
        heartbeat, SECRET, idempotency_key="heartbeat-20260812-120000", sent_at=NOW,
    )
    app = SimpleNamespace(state=SimpleNamespace(system_cycle_research_receiver=service))
    heartbeat_response = asyncio.run(system_cycle_research_heartbeat(_request(
        "/api/rewrite/internal/v1/system-cycle-research/heartbeat", canonical_json(heartbeat), headers, app,
    )))
    assert heartbeat_response.status_code == 201
    assert heartbeat_response.headers["cache-control"] == "no-store"
    assert database.fetch_one("SELECT highest_epoch FROM system_cycle_research_worker_fences")["highest_epoch"] == 3

    value = result(epoch=3)
    result_response = asyncio.run(system_cycle_research_result(_request(
        "/api/rewrite/internal/v1/system-cycle-research/results", canonical_json(value),
        result_headers(value, key="cycle-result-api"), app,
    )))
    assert result_response.status_code == 201


def test_rewrite_app_registers_only_the_two_bounded_internal_routes():
    from src.apps.api.app import routes

    paths = {route.path for route in routes}
    assert "/api/rewrite/internal/v1/system-cycle-research/results" in paths
    assert "/api/rewrite/internal/v1/system-cycle-research/heartbeat" in paths


def test_receiver_builder_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_RECEIVER_ENABLED", raising=False)
    monkeypatch.delenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE", raising=False)
    monkeypatch.delenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_SHARED_SECRET", raising=False)

    assert build_system_cycle_research_receiver() is None


@pytest.mark.parametrize("protected_name", ["DATABASE_URL", "TRADEAI_BACKTEST_DATABASE_URL"])
def test_receiver_database_must_not_alias_product_or_backtest_database(
    tmp_path, monkeypatch, protected_name
):
    database_path = (tmp_path / "shared.db").resolve()
    monkeypatch.setenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_RECEIVER_ENABLED", "true")
    monkeypatch.setenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE", str(database_path))
    monkeypatch.setenv("TRADEAI_SYSTEM_CYCLE_RESEARCH_SHARED_SECRET", "s" * 32)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TRADEAI_BACKTEST_DATABASE_URL", raising=False)
    monkeypatch.setenv(protected_name, f"sqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="isolated"):
        build_system_cycle_research_receiver()


def _request(path: str, body: bytes, headers: dict[str, str], app) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(key.lower().encode("latin1"), value.encode("latin1")) for key, value in headers.items()]
    raw_headers.append((b"content-length", str(len(body)).encode("ascii")))
    return Request({
        "type": "http", "method": "POST", "path": path, "headers": raw_headers,
        "app": app, "scheme": "https", "server": ("test", 443), "client": ("127.0.0.1", 1),
    }, receive)
