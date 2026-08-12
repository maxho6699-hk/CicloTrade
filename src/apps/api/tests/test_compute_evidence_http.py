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
from src.apps.api.compute_evidence_http import (
    ADMIN_HISTORY_PATH,
    ADMIN_LATEST_PATH,
    ADMIN_STATUS_PATH,
    INTERNAL_PATH,
)
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
    assert {ADMIN_STATUS_PATH, ADMIN_LATEST_PATH, ADMIN_HISTORY_PATH} <= paths

    previous = getattr(app.state, "compute_evidence_receiver", None)
    app.state.compute_evidence_receiver = None
    try:
        status, headers, payload = asyncio.run(_asgi(INTERNAL_PATH, method="POST", body=b"{}"))
    finally:
        app.state.compute_evidence_receiver = previous
    assert status == 404 and "unavailable" in payload["error"]
    _assert_security(headers)


@pytest.mark.parametrize("path", [ADMIN_STATUS_PATH, ADMIN_LATEST_PATH, ADMIN_HISTORY_PATH])
def test_admin_reads_require_an_exact_super_admin_role(compute_http, path):
    status, headers, payload = asyncio.run(_asgi(path))
    assert status == 401 and "Bearer" in payload["error"]
    _assert_admin_error_security(headers)
    ordinary = compute_http["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token
    status, headers, payload = asyncio.run(
        _asgi(path, headers=(("authorization", f"Bearer {ordinary}"),))
    )
    assert status == 403 and "后台权限" in payload["error"]
    _assert_admin_error_security(headers)

    database = compute_http["database"]
    user = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    database.execute(
        """INSERT INTO admin_roles(user_id,role,updated_at) VALUES (?,'research',?)
           ON CONFLICT(user_id) DO UPDATE SET role='research',updated_at=excluded.updated_at""",
        (user["id"], NOW.isoformat()),
    )
    status, headers, payload = asyncio.run(
        _asgi(path, headers=(("authorization", f"Bearer {ordinary}"),))
    )
    assert status == 403 and "仅超级管理员" in payload["error"]
    _assert_admin_error_security(headers)


def test_super_admin_status_latest_and_history_are_sanitized(compute_http, tmp_path):
    package = package_fixture()
    body, signed_headers = _signed(package, tmp_path / "admin-read", nonce="a" * 43)
    accepted, _, _ = asyncio.run(
        _asgi(INTERNAL_PATH, method="POST", body=body, headers=tuple(signed_headers.items()))
    )
    assert accepted == 201
    authorization = _super_admin_authorization(compute_http)

    status, headers, summary = asyncio.run(
        _asgi(ADMIN_STATUS_PATH, headers=(("authorization", authorization),))
    )
    assert status == 200
    assert summary == {
        "available": True,
        "publication_ceiling": "shadow",
        "research_only": True,
        "actionable": False,
        "user_visible": False,
        "counts": {"quarantine": 1, "shadow": 0},
        "last_received_at": "2026-08-12T12:00:00Z",
    }
    _assert_admin_security(headers)

    for path in (ADMIN_LATEST_PATH, ADMIN_HISTORY_PATH):
        status, headers, payload = asyncio.run(
            _asgi(path, headers=(("authorization", authorization),), query={"limit": "100"})
        )
        assert status == 200
        assert payload["research_only"] is True
        assert payload["actionable"] is payload["user_visible"] is False
        serialized = json.dumps(payload, sort_keys=True)
        assert all(forbidden not in serialized for forbidden in (
            "payload_json", "storage_path", "artifact_path", "lease_token",
            "token_hash", "shared_secret", "source_worker_id", "job_id",
            "receipt_key", "package_id", "compute_fencing_epoch",
        ))
        assert package["job_id"] not in serialized
        _assert_admin_security(headers)
    latest = asyncio.run(
        _asgi(ADMIN_LATEST_PATH, headers=(("authorization", authorization),))
    )[2]["evidence"]
    assert package["job_id"] not in json.dumps(latest, sort_keys=True)
    assert set(latest) == {
        "publication_state", "received_at", "completed_at",
        "candidate_id", "candidate_version", "market", "instrument_family",
        "symbols", "candidate_status", "manifest_sha256", "result_sha256",
        "package_sha256", "artifact_count", "research_only", "actionable",
        "user_visible",
    }


@pytest.mark.parametrize("limit", ["0", "101", "bad"])
def test_admin_history_limit_fails_closed(compute_http, limit):
    authorization = _super_admin_authorization(compute_http)
    status, headers, payload = asyncio.run(
        _asgi(
            ADMIN_HISTORY_PATH,
            headers=(("authorization", authorization),),
            query={"limit": limit},
        )
    )
    assert status == 400 and "between 1 and 100" in payload["error"]
    _assert_admin_security(headers)


@pytest.mark.parametrize("path", [ADMIN_STATUS_PATH, ADMIN_LATEST_PATH, ADMIN_HISTORY_PATH])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_admin_compute_evidence_routes_are_read_only(compute_http, path, method):
    authorization = _super_admin_authorization(compute_http)
    status, _, _ = asyncio.run(
        _asgi(path, method=method, headers=(("authorization", authorization),))
    )
    assert status == 405


def test_disabled_admin_reads_return_only_safe_empty_contract(browser_api):
    authorization = _super_admin_authorization(browser_api)
    previous = getattr(app.state, "compute_evidence_receiver", None)
    app.state.compute_evidence_receiver = None
    try:
        for path, empty_field in (
            (ADMIN_STATUS_PATH, "counts"),
            (ADMIN_LATEST_PATH, "evidence"),
            (ADMIN_HISTORY_PATH, "items"),
        ):
            status, headers, payload = asyncio.run(
                _asgi(path, headers=(("authorization", authorization),))
            )
            assert status == 200 and payload["available"] is False
            assert payload["research_only"] is True
            assert payload["actionable"] is payload["user_visible"] is False
            assert payload[empty_field] in ({"quarantine": 0, "shadow": 0}, None, [])
            _assert_admin_security(headers)
    finally:
        app.state.compute_evidence_receiver = previous


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


def _super_admin_authorization(context: dict) -> str:
    database = context["database"]
    user = database.fetch_one("SELECT id FROM users WHERE email='browser@example.com'")
    database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    from core.admin_service import AdminService

    AdminService(database).set_role(user["id"], user["id"], "super_admin")
    token = context["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token
    return f"Bearer {token}"


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
    decoded = response_body.decode()
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        payload = {"raw": decoded}
    return start["status"], response_headers, payload


def _assert_security(headers: dict[str, str]) -> None:
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"


def _assert_admin_security(headers: dict[str, str]) -> None:
    assert headers["cache-control"] == "private, no-store"
    assert headers["pragma"] == "no-cache"
    assert headers["vary"] == "Authorization, Cookie"
    assert headers["x-content-type-options"] == "nosniff"


def _assert_admin_error_security(headers: dict[str, str]) -> None:
    assert headers["cache-control"] == "private, no-store"
    assert headers["x-content-type-options"] == "nosniff"
