from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import importlib
import json

import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.entitlement_policy import seed_canonical_policy
from core.expanded_research_contracts import AUTHORITY, UNIVERSE_SHA256, canonical_json, receiver_signature
from core.expanded_research_store import ExpandedResearchStore
from src.apps.api.expanded_research_read_model import ExpandedResearchReadModel
from src.apps.api.expanded_research_receiver import ExpandedResearchReceiver
from src.apps.api.feature_catalog_adapter import FeatureCatalogAdapter


SECRET = "s" * 32
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


async def _asgi(
    path: str,
    headers: dict[str, str] | None = None,
    *,
    method: str = "GET",
    body: bytes = b"",
) -> tuple[int, dict[str, str], dict]:
    app = importlib.import_module("src.apps.api.app").app
    raw_path, _, query = path.partition("?")
    messages: list[dict] = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "https", "path": raw_path,
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


def _result() -> dict:
    evidence = {"runner": "equity-research-v1", "code_bundle_sha256": "b" * 64}
    return {
        "schema_version": 1,
        "kind": "tradeai.expanded-local-research.v1",
        "result_id": "expanded-AAPL-aaaaaaaaaaaaaaaaaaaaaaaa",
        "symbol": "AAPL",
        "tier": "A",
        "source_sha256": "a" * 64,
        "universe_sha256": UNIVERSE_SHA256,
        "dataset_end": "2026-08-13",
        "equity": {key: evidence for key in (
            "equity.trend.long_flat.v1",
            "equity.mean_reversion.long_flat.v1",
            "equity.breakout.long_flat.v1",
        )},
        "option_proxy": {"decision": "WAIT", "actionable": False},
        "authority": AUTHORITY,
    }


def _record(receiver: ExpandedResearchReceiver) -> None:
    value = _result()
    raw = canonical_json(value)
    body_sha = hashlib.sha256(raw).hexdigest()
    sent_at = "2026-08-14T12:00:00Z"
    headers = {
        "content-type": "application/json",
        "idempotency-key": "expanded-aapl-http-0001",
        "x-ciclotrade-research-worker-id": "expanded-research-worker",
        "x-ciclotrade-research-fencing-epoch": "1",
        "x-ciclotrade-research-sent-at": sent_at,
        "x-ciclotrade-research-sha256": body_sha,
        "x-ciclotrade-research-signature": receiver_signature(
            SECRET,
            worker_id="expanded-research-worker",
            fencing_epoch=1,
            idempotency_key="expanded-aapl-http-0001",
            sent_at=sent_at,
            body_sha256=body_sha,
        ),
    }
    receiver.accept(raw, headers)


@pytest.fixture
def expanded_receiver(tmp_path, monkeypatch):
    module = importlib.import_module("src.apps.api.app")
    store = ExpandedResearchStore(BacktestQueueDatabase(tmp_path / "expanded.db"), clock=lambda: NOW)
    receiver = ExpandedResearchReceiver(store, shared_secret=SECRET, enabled=True, clock=lambda: NOW)
    previous = getattr(module.app.state, "expanded_research_receiver", None)
    monkeypatch.setattr(module.app.state, "expanded_research_receiver", receiver)
    try:
        yield receiver
    finally:
        module.app.state.expanded_research_receiver = previous


@pytest.fixture
def feature_catalog_adapter(browser_api, monkeypatch):
    module = importlib.import_module("src.apps.api.app")
    database = browser_api["database"]
    database.execute(
        "UPDATE users SET plan_type='标准版',subscription_expire='2099-08-14T00:00:00+00:00' WHERE email='browser@example.com'"
    )
    with database.transaction() as connection:
        seed_canonical_policy(connection)
    adapter = FeatureCatalogAdapter(database)
    previous = getattr(module.app.state, "feature_catalog_adapter", None)
    monkeypatch.setattr(module.app.state, "feature_catalog_adapter", adapter)
    try:
        yield adapter
    finally:
        module.app.state.feature_catalog_adapter = previous


def _token(browser_api) -> str:
    return browser_api["auth"].login(
        "browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    ).access_token


def test_expanded_routes_require_login_and_fail_closed_to_waiting(browser_api, feature_catalog_adapter, monkeypatch):
    module = importlib.import_module("src.apps.api.app")
    status, _, _ = asyncio.run(_asgi("/api/rewrite/v1/strategy-research/expanded/status"))
    assert status == 401

    monkeypatch.setattr(module.app.state, "expanded_research_receiver", None)
    token = _token(browser_api)
    status, headers, payload = asyncio.run(_asgi(
        "/api/rewrite/v1/strategy-research/expanded/status",
        {"authorization": f"Bearer {token}"},
    ))
    assert status == 200
    assert payload["available"] is False and payload["state"] == "waiting"
    assert payload["coverage_count"] == 0 and payload["no_data_count"] == 97
    assert headers["cache-control"] == "no-store"
    catalog_code, _, catalog = asyncio.run(_asgi(
        "/api/rewrite/v1/features/catalog",
        {"authorization": f"Bearer {token}"},
    ))
    strategy = next(item for item in catalog["items"] if item["key"] == "strategy-research")
    assert catalog_code == 200 and strategy["availability"] == "unavailable"


def test_expanded_routes_return_one_consistent_sanitized_projection(browser_api, expanded_receiver, feature_catalog_adapter):
    _record(expanded_receiver)
    token = _token(browser_api)
    headers = {"authorization": f"Bearer {token}"}

    status_code, _, status = asyncio.run(_asgi("/api/rewrite/v1/strategy-research/expanded/status", headers))
    latest_code, _, latest = asyncio.run(_asgi("/api/rewrite/v1/strategy-research/expanded/latest", headers))
    history_code, _, history = asyncio.run(_asgi("/api/rewrite/v1/strategy-research/expanded/history?limit=20", headers))

    assert (status_code, latest_code, history_code) == (200, 200, 200)
    assert status["coverage_count"] == 1 and status["no_data_count"] == 96
    assert latest["available"] is True
    assert latest["cycle"]["evidence"]["universe_sha256"] == status["universe"]["sha256"]
    assert len(latest["cycle"]["symbols"]) == 97
    assert sum(item["data_state"] == "missing" for item in latest["cycle"]["symbols"]) == 96
    assert history["items"][0]["cycle_id"] == latest["cycle"]["cycle_id"]
    assert latest["authority"]["projection_scope"] == "authenticated_research"
    assert latest["authority"]["source_user_visible"] is False
    assert all(key not in json.dumps(latest) for key in ("secret", "worker_id", "fencing_epoch"))
    catalog_code, _, catalog = asyncio.run(_asgi(
        "/api/rewrite/v1/features/catalog", headers,
    ))
    strategy = next(item for item in catalog["items"] if item["key"] == "strategy-research")
    assert catalog_code == 200 and strategy["availability"] == "degraded"
    assert strategy["pin_allowed"] is False
    assert strategy["actions"]["research_url"].endswith("research_scope=expanded")


@pytest.mark.parametrize(
    ("state", "coverage_count", "no_data_count", "expected_availability", "expected_data_state"),
    [
        ("stale", 97, 0, "degraded", "stale"),
        ("degraded", 21, 76, "degraded", "delayed"),
        ("healthy", 21, 76, "degraded", "delayed"),
    ],
)
def test_feature_catalog_never_marks_partial_or_stale_expanded_research_healthy(
    browser_api,
    feature_catalog_adapter,
    monkeypatch,
    state,
    coverage_count,
    no_data_count,
    expected_availability,
    expected_data_state,
):
    module = importlib.import_module("src.apps.api.app")

    class StubReadModel:
        def status(self, _identity):
            return {
                **ExpandedResearchReadModel.unavailable_status(),
                "available": True,
                "state": state,
                "coverage_count": coverage_count,
                "no_data_count": no_data_count,
            }

    monkeypatch.setattr(module, "_expanded_research_read_model", lambda _request: StubReadModel())
    token = _token(browser_api)
    code, _, catalog = asyncio.run(_asgi(
        "/api/rewrite/v1/features/catalog",
        {"authorization": f"Bearer {token}"},
    ))
    strategy = next(item for item in catalog["items"] if item["key"] == "strategy-research")
    assert code == 200
    assert strategy["availability"] == expected_availability
    assert strategy["data_state"] == expected_data_state
    assert strategy["health"] == "degraded"
    assert strategy["pin_allowed"] is False
    assert strategy["actions"]["research_url"].endswith("research_scope=expanded")


def test_expanded_latest_history_and_limit_remain_authenticated(browser_api, expanded_receiver):
    for path in (
        "/api/rewrite/v1/strategy-research/expanded/latest",
        "/api/rewrite/v1/strategy-research/expanded/history?limit=20",
    ):
        status, _, _ = asyncio.run(_asgi(path))
        assert status == 401

    token = _token(browser_api)
    status, _, payload = asyncio.run(_asgi(
        "/api/rewrite/v1/strategy-research/expanded/history?limit=0",
        {"authorization": f"Bearer {token}"},
    ))
    assert status == 400 and payload["code"] == "invalid_request"


def test_expanded_internal_http_accepts_signed_replay_and_rejects_bad_or_large_body(expanded_receiver):
    raw = canonical_json(_result())
    body_sha = hashlib.sha256(raw).hexdigest()
    sent_at = "2026-08-14T12:00:00Z"
    key = "expanded-aapl-http-post-0001"
    headers = {
        "content-type": "application/json",
        "content-length": str(len(raw)),
        "idempotency-key": key,
        "x-ciclotrade-research-worker-id": "expanded-research-worker",
        "x-ciclotrade-research-fencing-epoch": "1",
        "x-ciclotrade-research-sent-at": sent_at,
        "x-ciclotrade-research-sha256": body_sha,
        "x-ciclotrade-research-signature": receiver_signature(
            SECRET,
            worker_id="expanded-research-worker",
            fencing_epoch=1,
            idempotency_key=key,
            sent_at=sent_at,
            body_sha256=body_sha,
        ),
    }
    path = "/api/rewrite/internal/v1/expanded-research/results"
    first_code, _, first = asyncio.run(_asgi(path, headers, method="POST", body=raw))
    replay_code, _, replay = asyncio.run(_asgi(path, headers, method="POST", body=raw))
    assert first_code == 201 and first["created"] is True
    assert replay_code == 200 and replay["created"] is False

    bad_headers = {**headers, "x-ciclotrade-research-signature": "sha256=" + "0" * 64}
    bad_code, _, bad = asyncio.run(_asgi(path, bad_headers, method="POST", body=raw))
    assert bad_code == 401 and "signature" in bad["error"]

    oversized = b"x" * (1024 * 1024 + 1)
    large_code, _, large = asyncio.run(_asgi(
        path,
        {"content-type": "application/json", "content-length": str(len(oversized))},
        method="POST",
        body=oversized,
    ))
    assert large_code == 413 and "too large" in large["error"]


def test_expanded_internal_receiver_route_is_registered():
    module = importlib.import_module("src.apps.api.app")
    assert "/api/rewrite/internal/v1/expanded-research/results" in {route.path for route in module.routes}
