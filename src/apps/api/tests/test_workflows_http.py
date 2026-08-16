from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from pathlib import Path

from starlette.requests import Request

from core.workflow_registry import WorkflowRegistry
from src.apps.api.workflows import workflow_create


def test_workflow_http_uses_service_factory_without_importing_app():
    app = SimpleNamespace(state=SimpleNamespace())
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript((Path(__file__).parents[4] / "migrations" / "0044_deliberation_workflows.sql").read_text(encoding="utf-8"))
    app.state.workflow_authenticate = lambda request: SimpleNamespace(id=4)
    app.state.workflow_internal_enabled = True
    app.state.workflow_service_factory = lambda request: WorkflowRegistry(conn)
    payload = {"source_kind": "ai", "source_public_id": "task-source", "context": {"symbol": "AAPL"}, "provenance": {"source_sha256": "a" * 64}}
    body = json.dumps(payload).encode()
    delivered = False
    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}
    request = Request({"type": "http", "method": "POST", "path": "/api/rewrite/v1/workflows", "headers": [(b"content-length", str(len(body)).encode())], "app": app}, receive)
    response = asyncio.run(workflow_create(request))
    assert response.status_code == 202
    assert json.loads(response.body)["task_public_id"]
