from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

from starlette.requests import Request

from src.apps.api.deliberation import deliberation_create


def _request(payload: dict, app):
    body = json.dumps(payload).encode()
    delivered = False
    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/api/rewrite/v1/deliberations", "headers": [(b"content-length", str(len(body)).encode())], "app": app}, receive)


def test_deliberation_http_uses_injected_auth_and_service():
    from types import SimpleNamespace
    from core.deliberation import DeliberationService
    app = SimpleNamespace(state=SimpleNamespace())
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, is_active INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO users(id,is_active) VALUES (1,1)")
    conn.executescript((Path(__file__).parents[4] / "migrations" / "0044_deliberation_workflows.sql").read_text(encoding="utf-8"))
    source_hash = hashlib.sha256(b"event-1").hexdigest()
    snapshot = {"snapshot_public_id": "evidence_1", "snapshot_version": 1, "source_event_id": "ev_1", "source_event_version": 1, "source_event_sha256": source_hash, "market": "US", "symbol": "AAPL", "timeframe": "1d", "seats": {}}
    app.state.deliberation_authenticate = lambda request: SimpleNamespace(id=1)
    app.state.deliberation_service_factory = lambda request: DeliberationService(conn, authorize=lambda *_: True, evidence_loader=lambda *_: snapshot)
    response = asyncio.run(deliberation_create(_request({"market": "US", "symbol": "AAPL", "timeframe": "1d", "question": "x", "source_event_id": "ev_1", "source_event_version": 1, "source_event_sha256": source_hash}, app)))
    assert response.status_code == 202
    assert json.loads(response.body)["status"] == "blocked"
