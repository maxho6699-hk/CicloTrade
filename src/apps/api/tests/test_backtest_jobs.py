from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from core.backtest_artifacts import ArtifactStore
from core.backtest_queue import BacktestQueue, BacktestQueueError
from core.backtest_queue_database import BacktestQueueDatabase
from src.apps.api.app import app
from src.apps.api.backtest_jobs import backtest_artifact, backtests, worker_claim, worker_output
from src.apps.api.tests.test_session import _login_token, _request


DATA = b"api frozen input"
DIGEST = hashlib.sha256(DATA).hexdigest()


def _manifest():
    return {
        "schema_version": 1,
        "evaluation_date": "2026-08-10",
        "dataset_end": "2026-08-09",
        "code_bundle_sha256": hashlib.sha256(b"api-bundle").hexdigest(),
        "experiment_budget": {"runs": 1, "folds": 1},
        "inputs": [{"artifact_key": "prices.csv", "sha256": DIGEST, "dataset_end": "2026-08-09"}],
    }


def _browser_request():
    return {
        "schema_version": 1,
        "type": "backtest.run.v1",
        "template_key": "equity.trend.long_flat.v1",
        "symbol": "AAPL",
        "timeframe": "1d",
        "sample_years": 1,
        "lookback": 20,
    }


def _add_headers(request, *pairs):
    request.scope["headers"].extend((name.encode(), value.encode()) for name, value in pairs)
    return request


async def _asgi_call(path, *, method="GET", headers=(), body=b""):
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {name.decode().lower(): value.decode() for name, value in start["headers"]}
    response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], response_headers, response_body


def test_routes_are_present():
    paths = {route.path for route in app.routes}
    assert "/api/rewrite/v1/backtests" in paths
    assert "/api/rewrite/internal/v1/backtest-worker/claims" in paths


def test_worker_bearer_fail_closed_and_stream_upload_limit(browser_api, monkeypatch, tmp_path):
    previous = getattr(app.state, "backtest_queue", None)
    previous_preparation = getattr(app.state, "backtest_preparation", None)
    queue = BacktestQueue(BacktestQueueDatabase(tmp_path / "queue.db"), ArtifactStore(tmp_path / "artifacts", max_bytes=1024))

    class Preparation:
        def prepare(self, owner_id, plan, payload, key):
            assert payload == _browser_request()
            job, created = queue.enqueue(
                owner_id,
                {"type": "backtest.run.v1", "manifest": _manifest()},
                idempotency_scope=f"user:{owner_id}",
                idempotency_key=key,
                plan=plan,
            )
            if created:
                queue.register_input(job["id"], "prices.csv", DATA, DIGEST)
            return queue.get(job["id"], owner_id), created

    app.state.backtest_queue = queue
    app.state.backtest_preparation = Preparation()
    try:
        monkeypatch.setenv("TRADEAI_BACKTEST_QUEUE_ENABLED", "false")
        status, headers, _ = asyncio.run(_asgi_call(
            "/api/rewrite/internal/v1/backtest-worker/claims",
            method="POST",
            headers=(("content-type", "application/json"),),
            body=b"{}",
        ))
        assert status == 404 and headers["x-content-type-options"] == "nosniff"
        with pytest.raises(BacktestQueueError) as disabled:
            asyncio.run(worker_claim(_request("/api/rewrite/internal/v1/backtest-worker/claims", method="POST", payload={})))
        assert disabled.value.status == 404
        monkeypatch.setenv("TRADEAI_BACKTEST_QUEUE_ENABLED", "true")
        monkeypatch.setenv("TRADEAI_BACKTEST_WORKER_API_ENABLED", "true")
        monkeypatch.setenv("TRADEAI_BACKTEST_CLAIMS_ENABLED", "true")
        monkeypatch.setenv("TRADEAI_BACKTEST_WORKER_TOKEN", "x" * 32)
        monkeypatch.setenv("TRADEAI_BACKTEST_WORKER_ID", "worker")
        missing = _add_headers(_request("/api/rewrite/internal/v1/backtest-worker/claims", method="POST", payload={}), ("x-ciclotrade-worker-id", "worker"))
        with pytest.raises(BacktestQueueError) as denied:
            asyncio.run(worker_claim(missing))
        assert denied.value.status == 401
        wrong = _add_headers(_request("/api/rewrite/internal/v1/backtest-worker/claims", method="POST", payload={}), ("authorization", "Bearer wrong"), ("x-ciclotrade-worker-id", "worker"))
        with pytest.raises(BacktestQueueError):
            asyncio.run(worker_claim(wrong))
        token = _login_token()
        public = _add_headers(_request("/api/rewrite/v1/backtests", method="POST", authorization=f"Bearer {token}", payload=_browser_request()), ("idempotency-key", "abcdefgh"))
        assert asyncio.run(backtests(public)).status_code == 202
        job = queue.list(1)[0]
        claim = _add_headers(_request("/api/rewrite/internal/v1/backtest-worker/claims", method="POST", payload={"lease_seconds": 120}), ("authorization", "Bearer " + "x" * 32), ("x-ciclotrade-worker-id", "worker"))
        leased = asyncio.run(worker_claim(claim))
        assert leased.status_code == 200
        lease = json.loads(leased.body)["job"]
        assert "lease_token" in lease
        assert "lease_token_sha256" not in lease
        assert "idempotency_key" not in lease
        assert "owner_id" not in lease
        invalid = _request("/api/rewrite/internal/v1/backtest-worker/jobs/x/outputs/invalid", method="PUT", payload={"body": "small"})
        _add_headers(invalid, ("authorization", "Bearer " + "x" * 32), ("x-ciclotrade-worker-id", "worker"), ("x-ciclotrade-lease-token", "invalid-lease"), ("x-ciclotrade-fencing-epoch", str(lease["fencing_epoch"])), ("x-ciclotrade-artifact-sha256", "0" * 64))
        invalid.scope["path_params"] = {"job_id": job["id"], "artifact_key": "invalid"}
        with pytest.raises(BacktestQueueError) as invalid_lease:
            asyncio.run(worker_output(invalid))
        assert invalid_lease.value.status == 409
        assert not list((tmp_path / "artifacts").glob(".upload-*"))
        upload = _request("/api/rewrite/internal/v1/backtest-worker/jobs/x/outputs/large", method="PUT", payload={"body": "x" * 4096})
        upload.scope["headers"] = [item for item in upload.scope["headers"] if item[0] != b"content-length"]
        _add_headers(upload, ("authorization", "Bearer " + "x" * 32), ("x-ciclotrade-worker-id", "worker"), ("x-ciclotrade-lease-token", lease["lease_token"]), ("x-ciclotrade-fencing-epoch", str(lease["fencing_epoch"])), ("x-ciclotrade-artifact-sha256", "0" * 64))
        upload.scope["path_params"] = {"job_id": job["id"], "artifact_key": "large"}
        # The body is streamed and rejected by its actual size, despite no Content-Length.
        with pytest.raises(BacktestQueueError) as oversized:
            asyncio.run(worker_output(upload))
        assert oversized.value.status == 413
        small = _request("/api/rewrite/internal/v1/backtest-worker/jobs/x/outputs/small", method="PUT", payload={})
        small_body = b"{}"
        small.scope["path_params"] = {"job_id": job["id"], "artifact_key": "small.json"}
        _add_headers(small, ("authorization", "Bearer " + "x" * 32), ("x-ciclotrade-worker-id", "worker"), ("x-ciclotrade-lease-token", lease["lease_token"]), ("x-ciclotrade-fencing-epoch", str(lease["fencing_epoch"])), ("x-ciclotrade-artifact-sha256", hashlib.sha256(small_body).hexdigest()))
        stored_response = asyncio.run(worker_output(small))
        assert "storage_key" not in json.loads(stored_response.body)
        html = b"<script>alert(1)</script>"
        html_hash = hashlib.sha256(html).hexdigest()
        queue.upload_output(job["id"], "report.html", html, html_hash, "worker", lease["lease_token"], lease["fencing_epoch"], media_type="text/html")
        queue.complete(job["id"], "worker", lease["lease_token"], lease["fencing_epoch"], {
            "job_id": job["id"],
            "manifest_sha256": lease["manifest_sha256"],
            "code_bundle_sha256": _manifest()["code_bundle_sha256"],
            "fencing_epoch": lease["fencing_epoch"],
            "input_hashes": {"prices.csv": DIGEST},
            "output_hashes": {"report.html": html_hash, "small.json": hashlib.sha256(small_body).hexdigest()},
            "evidence": {"kind": "research"},
        })
        browser_output = _request(f"/api/rewrite/v1/backtests/{job['id']}/artifacts/report.html", authorization=f"Bearer {token}")
        browser_output.scope["path_params"] = {"job_id": job["id"], "artifact_key": "report.html"}
        download = asyncio.run(backtest_artifact(browser_output))
        assert download.media_type == "application/octet-stream"
        assert download.headers["content-disposition"] == 'attachment; filename="report.html"'
        assert download.headers["content-length"] == str(len(html))
        browser_list = asyncio.run(backtests(_request(
            "/api/rewrite/v1/backtests", authorization=f"Bearer {token}"
        )))
        projected = json.loads(browser_list.body)["items"][0]
        assert projected["failure"] is None
        assert projected["artifacts"] == [
            {
                "artifact_key": "report.html",
                "sha256": html_hash,
                "bytes": len(html),
                "verified": True,
            },
            {
                "artifact_key": "small.json",
                "sha256": hashlib.sha256(small_body).hexdigest(),
                "bytes": len(small_body),
                "verified": True,
            },
        ]
        asgi_status, asgi_headers, asgi_body = asyncio.run(_asgi_call(
            f"/api/rewrite/v1/backtests/{job['id']}/artifacts/report.html",
            headers=(("authorization", f"Bearer {token}"),),
        ))
        assert asgi_status == 200 and asgi_body == html
        assert asgi_headers["content-type"] == "application/octet-stream"
        assert asgi_headers["content-disposition"] == 'attachment; filename="report.html"'
        browser_input = _request(f"/api/rewrite/v1/backtests/{job['id']}/artifacts/prices.csv", authorization=f"Bearer {token}")
        browser_input.scope["path_params"] = {"job_id": job["id"], "artifact_key": "prices.csv"}
        with pytest.raises(BacktestQueueError):
            asyncio.run(backtest_artifact(browser_input))
    finally:
        if previous is None:
            del app.state.backtest_queue
        else:
            app.state.backtest_queue = previous
        if previous_preparation is None:
            del app.state.backtest_preparation
        else:
            app.state.backtest_preparation = previous_preparation
