from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.workflow_registry import WorkflowNotFound, WorkflowRegistry


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((Path(__file__).parents[1] / "migrations" / "0044_deliberation_workflows.sql").read_text(encoding="utf-8"))
    return conn


def test_registry_projects_public_events_and_retry_creates_new_task():
    registry = WorkflowRegistry(_db())
    task = registry.create(7, source_kind="deliberation", source_public_id="dlb_1", context={"symbol": "AAPL"}, provenance={"source_sha256": "a" * 64})
    assert task["status"] == "queued"
    registry.transition(task["task_public_id"], 7, "running")
    registry.transition(task["task_public_id"], 7, "partial", payload={"missing": ["news_macro"]})
    detail = registry.get(7, task["task_public_id"])
    assert [event["seq"] for event in detail["events"]] == [1, 2, 3]
    assert detail["source_kind"] == "deliberation"
    assert detail["artifacts"] == []
    assert "owner_id" not in detail and "provenance" not in detail
    assert detail["provenance_sha256"]
    assert all("payload_json" not in event for event in detail["events"])
    retry = registry.retry(7, task["task_public_id"])
    assert retry["task_public_id"] != task["task_public_id"]
    assert retry["attempt"] == 2
    assert registry.get(7, task["task_public_id"])["status"] == "partial"


def test_registry_rejects_urls_and_hides_other_owner():
    registry = WorkflowRegistry(_db())
    with pytest.raises(ValueError):
        registry.create(1, source_kind="ai", source_public_id="x", context={"url": "https://example.com"}, provenance={})
    task = registry.create(1, source_kind="ai", source_public_id="x", context={}, provenance={})
    with pytest.raises(WorkflowNotFound):
        registry.get(2, task["task_public_id"])
