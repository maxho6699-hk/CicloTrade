from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from urllib.parse import urlencode

import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.compute_evidence_contracts import canonical_json
from src.apps.api.app import app, routes
from src.apps.api.compute_evidence_http import INTERNAL_PATH
from src.apps.api.compute_evidence_receiver import ComputeEvidenceReceiver
from src.apps.api.compute_evidence_receiver import build_compute_evidence_receiver
from src.apps.worker.compute_evidence_spool import PersistentComputeEvidenceSpool
from tests.test_compute_evidence_acceptance import package_fixture


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SECRET = b"h" * 32
SITE_ID = "hk-strategy-worker"
PUBLISHER_ID = "compute-evidence-publisher"


@pytest.fixture
def compute_http(tmp_path, browser_api):
    previous_receiver = getattr(app.state, "compute_evidence_receiver", None)

    def clock():
        return NOW

    receiver = ComputeEvidenceReceiver(
        BacktestQueueDatabase(tmp_path / "receiver.db"),
        shared_secret=SECRET,
        site_id=SITE_ID,
        publisher_id=PUBLISHER_ID,
        enabled=True,
        clock=clock,
    )
    app.state.compute_evidence_receiver = receiver
    try:
        yield {"receiver": receiver, "clock": clock, **browser_api}
    finally:
        app.state.compute_evidence_receiver = previous_receiver


def test_routes_are_registered_and_receiver_is_disabled_by_default():
    paths = {route.path for route in routes}
    assert INTERNAL_PATH in paths
    assert not any(path.startswith("/api/rewrite/v1/admin/compute-evidence") for path in paths)

    previous = getattr(app.state, "compute_evidence_receiver", None)
    app.state.compute_evidence_receiver = None
    try:
        status, headers, payload = asyncio.run(_asgi(INTERNAL_PATH, method="POST", body=b"{}"))
    finally:
        app.state.compute_evidence_receiver = previous
    assert status == 404 and "unavailable" in payload["error"]
    _assert_security(headers)


def test_receiver_database_cannot_alias_system_cycle_database(tmp_path):
    shared = str((tmp_path / "shared.db").resolve())
    with pytest.raises(RuntimeError, match="TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE"):
        build_compute_evidence_receiver(
            {
                "TRADEAI_COMPUTE_EVIDENCE_RECEIVER_ENABLED": "true",
                "TRADEAI_COMPUTE_EVIDENCE_RECEIVER_DATABASE": shared,
                "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET": "h" * 32,
                "TRADEAI_COMPUTE_EVIDENCE_SITE_ID": SITE_ID,
                "TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID": PUBLISHER_ID,
                "TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE": shared,
            }
        )


def test_streaming_body_limit_and_authentication_failure(compute_http):
    status, headers, payload = asyncio.run(
        _asgi(
            INTERNAL_PATH,
            method="POST",
            body=b"x",
            headers=(("content-length", str(512 * 1024 + 1)),),
        )
    )
    assert status == 413 and "too large" in payload["error"]
    _assert_security(headers)

    status, _, payload = asyncio.run(
        _asgi(
            INTERNAL_PATH,
            method="POST",
            body=b"x" * (512 * 1024 + 1),
            add_content_length=False,
        )
    )
    assert status == 413 and "too large" in payload["error"]

    status, headers, payload = asyncio.run(
        _asgi(
            INTERNAL_PATH,
            method="POST",
            body=b"{}",
            headers=(("content-type", "application/json"),),
        )
    )
    assert status == 401 and "identity headers" in payload["error"]
    _assert_security(headers)


def test_signed_acceptance_returns_201_then_idempotent_200(compute_http, tmp_path):
    package = package_fixture()
    body, first_headers = _signed(package, tmp_path / "first", nonce="f" * 43)
    status, headers, receipt = asyncio.run(
        _asgi(INTERNAL_PATH, method="POST", body=body, headers=tuple(first_headers.items()))
    )
    assert status == 201 and receipt["created"] is True
    assert receipt["publication_state"] == "quarantine"
    assert receipt["research_only"] is True
    assert receipt["actionable"] is receipt["user_visible"] is False
    _assert_security(headers)

    body, retry_headers = _signed(package, tmp_path / "retry", nonce="r" * 43)
    status, _, receipt = asyncio.run(
        _asgi(INTERNAL_PATH, method="POST", body=body, headers=tuple(retry_headers.items()))
    )
    assert status == 200 and receipt["created"] is False


def _signed(package: dict, root, *, nonce: str) -> tuple[bytes, dict[str, str]]:
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(root / "spool.db"), clock=lambda: NOW)
    spool.enqueue(package)
    claim = spool.claim(PUBLISHER_ID)
    assert claim
    headers = spool.signed_headers(
        claim,
        SECRET,
        nonce=nonce,
        expires_at=NOW + timedelta(minutes=2),
    )
    return canonical_json(package), headers


async def _asgi(
    path: str,
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: tuple[tuple[str, str], ...] = (),
    query: dict[str, str] | None = None,
    add_content_length: bool = True,
) -> tuple[int, dict[str, str], dict]:
    supplied = [(name.lower().encode(), value.encode()) for name, value in headers]
    names = {name for name, _ in supplied}
    if add_content_length and b"content-length" not in names:
        supplied.append((b"content-length", str(len(body)).encode()))
    messages = []
    chunks = [body[index : index + 65_536] for index in range(0, len(body), 65_536)] or [b""]

    async def receive():
        if not chunks:
            return {"type": "http.disconnect"}
        chunk = chunks.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": urlencode(query or {}).encode(),
            "headers": supplied,
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 443),
            "root_path": "",
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {name.decode().lower(): value.decode() for name, value in start["headers"]}
    response_body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, json.loads(response_body.decode())


def _assert_security(headers: dict[str, str]) -> None:
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
