from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import ssl

import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.system_cycle_research_contracts import CANONICAL_STOCKS
from core.system_cycle_research_store import SystemCycleResearchStore
from src.apps.api.system_cycle_receiver import SystemCycleResearchReceiver
from src.apps.worker.system_cycle_publisher import (
    HEARTBEAT_PATH,
    HEARTBEAT_WORKER_ID,
    PUBLISHER_WORKER_ID,
    PUBLISH_HOST,
    PUBLISH_PORT,
    RESULT_PATH,
    HttpsPublisherTransport,
    PublisherResponse,
    PublisherUncertainTransportError,
    SystemCyclePublisher,
    SystemCyclePublisherConfigurationError,
    SystemCyclePublisherSettings,
    run_system_cycle_publisher,
)
from src.apps.worker.system_cycle_research import build_system_cycle_research_result
from src.apps.worker.system_cycle_spool import PersistentSystemCycleSpool


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SECRET = b"p" * 32


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


class ReceiverTransport:
    def __init__(self, receiver: SystemCycleResearchReceiver) -> None:
        self.receiver = receiver
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, path, headers, body, **_limits) -> PublisherResponse:
        self.calls.append((path, dict(headers), body))
        if path == RESULT_PATH:
            value = self.receiver.accept_result(body, headers)
        elif path == HEARTBEAT_PATH:
            value = self.receiver.accept_heartbeat(body, headers)
        else:
            raise AssertionError("publisher used an unexpected path")
        status = 201 if value["created"] else 200
        return PublisherResponse(
            status=status,
            headers={"content-type": "application/json"},
            body=json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
        )


class ScriptedTransport:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, path, headers, body, **limits) -> PublisherResponse:
        self.calls.append((path, dict(headers), body, limits))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def stock_results() -> dict[str, dict]:
    return {
        symbol: {
            "status": "coverage", "rows": 756, "dataset_end": "2026-08-11",
            "selected": index == 0, "signal_state": "long" if index == 0 else "flat",
            "latest_price": 100.0 + index, "target_quantity": 10.0 if index == 0 else 0.0,
        }
        for index, (_, symbol) in enumerate(CANONICAL_STOCKS)
    }


def result(epoch: int, *, cycle_slot: str = "after_close") -> dict:
    return build_system_cycle_research_result(
        worker_id="strategy-worker", fencing_epoch=epoch, evaluation_date="2026-08-12",
        cycle_slot=cycle_slot, strategy_key="trend-cross", strategy_name="Trend Cross",
        strategy_version="catalog-20260812", source_snapshot_sha256="a" * 64,
        catalog_snapshot_sha256="b" * 64, stock_results=stock_results(), evaluated_at=NOW,
    )


def settings(tmp_path, **changes) -> SystemCyclePublisherSettings:
    values = {
        "enabled": True,
        "database_path": tmp_path / "spool.db",
        "shared_secret": SECRET,
        "connect_timeout_seconds": 2.0,
        "total_timeout_seconds": 5.0,
        "max_response_bytes": 64 * 1024,
        "lease_seconds": 30,
        "max_retry_after_seconds": 3_600,
    }
    values.update(changes)
    return SystemCyclePublisherSettings(**values)


def publisher(tmp_path, clock: Clock, transport):
    spool = PersistentSystemCycleSpool(BacktestQueueDatabase(tmp_path / "spool.db"), clock=clock)
    return spool, SystemCyclePublisher(spool, settings(tmp_path), transport, clock=clock)


def receiver_transport(tmp_path, clock: Clock) -> tuple[ReceiverTransport, BacktestQueueDatabase]:
    database = BacktestQueueDatabase(tmp_path / "receiver.db")
    receiver = SystemCycleResearchReceiver(
        SystemCycleResearchStore(database, clock=clock),
        shared_secret=SECRET,
        enabled=True,
        clock=clock,
    )
    return ReceiverTransport(receiver), database


def enqueue(spool: PersistentSystemCycleSpool, *, slot="after_close") -> dict:
    epoch = spool.allocate_fencing_epoch("strategy-worker")
    queued, _ = spool.enqueue(result(epoch, cycle_slot=slot), idempotency_key=f"cycle-20260812-{slot}")
    return queued


def response(status: int, body: dict | None = None, **headers: str) -> PublisherResponse:
    return PublisherResponse(
        status=status,
        headers={"content-type": "application/json", **headers},
        body=json.dumps(body or {}, separators=(",", ":")).encode(),
    )


def test_publisher_is_disabled_without_configuration_and_never_constructs_transport():
    assert run_system_cycle_publisher(env={}) == {
        "state": "disabled", "origin": "https://ciclotrade.com",
    }


def test_enabled_publisher_requires_env_only_secret_and_bounded_timeouts(tmp_path):
    env = {
        "TRADEAI_SYSTEM_CYCLE_PUBLISHER_ENABLED": "true",
        "TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE": str((tmp_path / "spool.db").resolve()),
    }
    with pytest.raises(SystemCyclePublisherConfigurationError, match="secret"):
        SystemCyclePublisherSettings.from_env(env)

    env["TRADEAI_SYSTEM_CYCLE_RESEARCH_SHARED_SECRET"] = "s" * 32
    env["TRADEAI_SYSTEM_CYCLE_PUBLISHER_TOTAL_TIMEOUT"] = "60"
    env["TRADEAI_SYSTEM_CYCLE_PUBLISHER_LEASE_SECONDS"] = "60"
    with pytest.raises(SystemCyclePublisherConfigurationError, match="lease"):
        SystemCyclePublisherSettings.from_env(env)


def test_result_round_trip_uses_fixed_path_and_strict_bound_receipt(tmp_path):
    clock = Clock()
    transport, receiver_db = receiver_transport(tmp_path, clock)
    spool, service = publisher(tmp_path, clock, transport)
    enqueue(spool)

    outcome = service.run_once()

    assert outcome["state"] == "delivered" and outcome["http_status"] == 201
    assert [call[0] for call in transport.calls] == [RESULT_PATH]
    assert spool.counts()["delivered"] == 1
    assert receiver_db.fetch_one("SELECT count(*) total FROM system_cycle_research_receipts")["total"] == 1


def test_reclaim_then_separate_heartbeat_fence_does_not_stale_the_result(tmp_path):
    clock = Clock()
    transport, receiver_db = receiver_transport(tmp_path, clock)
    spool, service = publisher(tmp_path, clock, transport)
    enqueue(spool)
    first = spool.claim(PUBLISHER_WORKER_ID, lease_seconds=10)
    assert first
    clock.value += timedelta(seconds=11)
    reclaimed = spool.claim(PUBLISHER_WORKER_ID, lease_seconds=10)
    assert reclaimed and reclaimed["fencing_epoch"] == 2
    spool.fail(
        reclaimed["id"], PUBLISHER_WORKER_ID, reclaimed["lease_token"], 2,
        error="safe pre-request failure", retry_delay_seconds=60,
    )

    heartbeat = service.run_once()
    assert heartbeat["state"] == "heartbeat_delivered"
    clock.value += timedelta(seconds=60)
    delivered = service.run_once()

    assert delivered["state"] == "delivered"
    fences = {
        row["worker_id"]: row["highest_epoch"]
        for row in receiver_db.fetch_all("SELECT worker_id,highest_epoch FROM system_cycle_research_worker_fences")
    }
    assert fences == {HEARTBEAT_WORKER_ID: 1, "strategy-worker": 1}


def test_retryable_result_honors_bounded_retry_after_and_fifo(tmp_path):
    clock = Clock()
    transport = ScriptedTransport(response(429, **{"retry-after": "99999"}))
    spool, service = publisher(tmp_path, clock, transport)
    first = enqueue(spool)
    enqueue(spool, slot="manual")

    outcome = service.run_once()

    assert outcome["state"] == "retryable" and outcome["retry_after_seconds"] == 3_600
    row = spool.database.fetch_one("SELECT state,last_http_status FROM system_cycle_research_spool WHERE id=?", (first["id"],))
    assert row == {"state": "failed", "last_http_status": 429}
    assert spool.claim(PUBLISHER_WORKER_ID) is None


@pytest.mark.parametrize("status", [302, 400, 401, 409, 422])
def test_redirects_and_non_retryable_client_errors_become_terminal_dead(tmp_path, status):
    clock = Clock()
    spool, service = publisher(tmp_path, clock, ScriptedTransport(response(status)))
    enqueue(spool)

    outcome = service.run_once()

    assert outcome["state"] == "dead" and outcome["http_status"] == status
    assert spool.claim(PUBLISHER_WORKER_ID) is None


def test_uncertain_network_and_invalid_success_receipt_are_never_auto_retried(tmp_path):
    clock = Clock()
    uncertain = PublisherUncertainTransportError("response timed out")
    spool, service = publisher(tmp_path, clock, ScriptedTransport(uncertain))
    enqueue(spool)
    assert service.run_once()["state"] == "uncertain"
    assert spool.claim(PUBLISHER_WORKER_ID) is None

    other = tmp_path / "other"
    other.mkdir()
    spool2, service2 = publisher(other, clock, ScriptedTransport(response(201, {"accepted": True})))
    enqueue(spool2)
    assert service2.run_once()["state"] == "uncertain"
    assert spool2.claim(PUBLISHER_WORKER_ID) is None


def test_crashed_sending_lease_is_quarantined_instead_of_reclaimed(tmp_path):
    clock = Clock()
    spool, _ = publisher(tmp_path, clock, ScriptedTransport())
    enqueue(spool)
    claim = spool.claim(PUBLISHER_WORKER_ID, lease_seconds=10)
    assert claim
    spool.begin_delivery(claim["id"], PUBLISHER_WORKER_ID, claim["lease_token"], 1)
    clock.value += timedelta(seconds=11)

    assert spool.quarantine_expired_deliveries() == 1
    assert spool.database.fetch_one("SELECT state FROM system_cycle_research_spool")["state"] == "uncertain"
    assert spool.claim(PUBLISHER_WORKER_ID) is None


def test_heartbeat_retry_after_is_persisted_and_blocks_immediate_resend(tmp_path):
    clock = Clock()
    transport = ScriptedTransport(response(503, **{"retry-after": "120"}))
    _, service = publisher(tmp_path, clock, transport)

    first = service.run_once()
    second = service.run_once()

    assert first["state"] == "heartbeat_retryable"
    assert second["state"] == "heartbeat_deferred"
    assert len(transport.calls) == 1


def test_https_transport_pins_verified_tls_origin_and_never_follows_redirect(monkeypatch):
    captured = {}

    class Socket:
        def settimeout(self, value):
            captured["socket_timeout"] = value

    class Response:
        status = 302
        length = 0

        @staticmethod
        def getheaders():
            return [("Content-Length", str(Response.length)), ("Location", "https://evil.example/")]

        @staticmethod
        def read(_size):
            return b""

    class Connection:
        def __init__(self, host, port, timeout, context):
            captured.update(host=host, port=port, timeout=timeout, context=context)
            self.sock = Socket()

        def connect(self):
            captured["connected"] = True

        def request(self, method, path, body, headers):
            captured.update(method=method, path=path, body=body, headers=headers)

        @staticmethod
        def getresponse():
            return Response()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr("src.apps.worker.system_cycle_publisher.http.client.HTTPSConnection", Connection)
    transport = HttpsPublisherTransport(monotonic=lambda: 1.0)
    result_value = transport.post(
        RESULT_PATH, {"content-type": "application/json"}, b"{}",
        connect_timeout_seconds=2, total_timeout_seconds=5, max_response_bytes=1024,
    )

    assert result_value.status == 302
    assert captured["host"] == PUBLISH_HOST and captured["port"] == PUBLISH_PORT
    assert captured["path"] == RESULT_PATH and captured["method"] == "POST"
    assert captured["context"].verify_mode == ssl.CERT_REQUIRED
    assert captured["context"].check_hostname is True

    Response.status = 200
    Response.length = 2048
    with pytest.raises(PublisherUncertainTransportError, match="size limit") as oversized:
        transport.post(
            RESULT_PATH, {"content-type": "application/json"}, b"{}",
            connect_timeout_seconds=2, total_timeout_seconds=5, max_response_bytes=1024,
        )
    assert oversized.value.status == 200


def test_publisher_systemd_unit_is_separate_single_shot_network_service():
    root = Path(__file__).resolve().parents[4]
    service = (root / "ops" / "ciclotrade-system-cycle-publisher.service").read_text(encoding="utf-8")
    timer = (root / "ops" / "ciclotrade-system-cycle-publisher.timer").read_text(encoding="utf-8")
    compute = (root / "ops" / "ciclotrade-strategy-compute.service").read_text(encoding="utf-8")

    assert "Type=oneshot" in service and "PrivateNetwork=false" in service
    assert "EnvironmentFile=/etc/ciclotrade-worker/publisher.env" in service
    assert "enable-system-cycle-publisher.after-integration" in service
    assert "Restart=" not in service and "[Install]" not in service
    assert "OnUnitActiveSec=1min" in timer and "Persistent=false" in timer
    assert "PrivateNetwork=true" in compute
