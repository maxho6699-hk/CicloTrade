from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from core.deliberation import (
    DELIBERATION_METHOD_VERSION,
    DeliberationConflict,
    DeliberationForbidden,
    DeliberationNotFound,
    DeliberationService,
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL DEFAULT 1)")
    conn.executemany("INSERT INTO users(id,is_active) VALUES (?,1)", [(1,), (2,)])
    conn.executescript((Path(__file__).parents[1] / "migrations" / "0044_deliberation_workflows.sql").read_text(encoding="utf-8"))
    return conn


def _snapshot(*, incomplete: bool = False) -> dict:
    now = "2026-08-15T10:00:00+00:00"
    seats = {
        "market_structure": {"support_strength": 72, "counter_evidence_strength": 31, "coverage": 0.9, "source": "quotes-v1", "citation": "quote:close"},
        "fundamentals": {"support_strength": 64, "counter_evidence_strength": 44, "coverage": 0.8, "source": "fundamentals-v1", "citation": "filing:2026q2"},
        "news_macro": {"support_strength": 55, "counter_evidence_strength": 50, "coverage": 0.7, "source": "news-v1", "citation": "macro:2026-08-15"},
        "risk": {"support_strength": 35, "counter_evidence_strength": 78, "coverage": 1.0, "source": "risk-v1", "citation": "risk:volatility"},
    }
    if incomplete:
        seats.pop("news_macro")
    value = {
        "snapshot_public_id": "evidence_20260815_a",
        "snapshot_version": 1,
        "source_event_id": "ev_20260815_a",
        "source_event_version": 3,
        "market": "US",
        "symbol": "AAPL",
        "timeframe": "1d",
        "evidence_version": "evidence.v3",
        "research_version": "research.v5",
        "observed_at": now,
        "available_at": now,
        "as_of": now,
        "calculated_at": now,
        "seats": seats,
    }
    source_material = {"event": "ev_20260815_a", "version": 3, "symbol": "AAPL"}
    value["source_event_sha256"] = hashlib.sha256(
        json.dumps(source_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    value["snapshot_sha256"] = hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "snapshot_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def test_deliberation_is_deterministic_and_exposes_four_seats_without_conclusion():
    conn = _db()
    snapshot = _snapshot()
    service = DeliberationService(conn, authorize=lambda owner, capability: owner == 1 and capability == "multi_agent_deliberation", evidence_loader=lambda owner, binding: snapshot)
    result = service.create(1, {"market": "US", "symbol": "AAPL", "timeframe": "1d", "question": "审阅风险", "source_event_id": "ev_20260815_a", "source_event_version": 3, "source_event_sha256": snapshot["source_event_sha256"]})
    assert result["status"] == "succeeded"
    assert result["method_version"] == DELIBERATION_METHOD_VERSION
    assert set(result["seats"]) == {"market_structure", "fundamentals", "news_macro", "risk"}
    assert 0 <= result["support_strength"] <= 100
    assert 0 <= result["counter_evidence_strength"] <= 100
    assert result["result_sha256"] == hashlib.sha256(json.dumps({k: v for k, v in result.items() if k != "result_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert "win_rate" not in result and "probability" not in result and "conclusion" not in result and "chain_of_thought" not in result
    assert all("source" in seat and "citation" in seat and "weight_bps" in seat and "contribution" in seat for seat in result["seats"].values())


def test_missing_seat_is_partial_and_missing_snapshot_is_blocked():
    conn = _db()
    partial = DeliberationService(conn, authorize=lambda *_: True, evidence_loader=lambda *_: _snapshot(incomplete=True))
    result = partial.create(1, {"market": "US", "symbol": "AAPL", "timeframe": "1d", "question": "资料审阅", "source_event_id": "ev_20260815_a", "source_event_version": 3, "source_event_sha256": _snapshot(incomplete=True)["source_event_sha256"]})
    assert result["status"] == "partial"
    assert "news_macro" in result["missing"]
    blocked = DeliberationService(conn, authorize=lambda *_: True, evidence_loader=lambda *_: None)
    result = blocked.create(1, {"market": "US", "symbol": "AAPL", "timeframe": "1d", "question": "资料审阅", "source_event_id": "ev_missing", "source_event_version": 1, "source_event_sha256": "0" * 64})
    assert result["status"] == "blocked"
    assert set(result["missing"]) == {"market_structure", "fundamentals", "news_macro", "risk"}


def test_readiness_does_not_treat_source_only_seats_as_scored_evidence():
    conn = _db()
    snapshot = _snapshot()
    snapshot["seats"]["market_structure"].pop("support_strength")
    snapshot.pop("snapshot_sha256")
    snapshot["snapshot_sha256"] = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    service = DeliberationService(conn, authorize=lambda *_: True, evidence_loader=lambda *_: snapshot)
    ready = service.readiness(1, {
        "market": "US", "symbol": "AAPL", "timeframe": "1d", "question": "资料审阅",
        "source_event_id": "ev_20260815_a", "source_event_version": 3,
        "source_event_sha256": snapshot["source_event_sha256"],
    })
    assert ready["status"] == "partial"
    assert "market_structure" in ready["missing"]


def test_deliberation_authorization_and_owner_isolation_are_fail_closed():
    conn = _db()
    denied = DeliberationService(conn, authorize=lambda *_: False, evidence_loader=lambda *_: _snapshot())
    with pytest.raises(DeliberationForbidden):
        denied.create(1, {})
    allowed = DeliberationService(conn, authorize=lambda *_: True, evidence_loader=lambda *_: _snapshot())
    result = allowed.create(1, {"market": "US", "symbol": "AAPL", "timeframe": "1d", "question": "x", "source_event_id": "ev_20260815_a", "source_event_version": 3, "source_event_sha256": _snapshot()["source_event_sha256"]})
    with pytest.raises(DeliberationNotFound):
        allowed.get(2, result["deliberation_public_id"])


def test_snapshot_hash_is_independent_from_source_event_hash_and_mismatch_fails_closed():
    conn = _db()
    snapshot = _snapshot(incomplete=True)
    assert snapshot["snapshot_sha256"] != snapshot["source_event_sha256"]
    service = DeliberationService(conn, authorize=lambda *_: True, evidence_loader=lambda *_: snapshot)
    payload = {
        "market": "US",
        "symbol": "AAPL",
        "timeframe": "1d",
        "question": "资料审阅",
        "source_event_id": "ev_20260815_a",
        "source_event_version": 3,
        "source_event_sha256": snapshot["source_event_sha256"],
    }
    first = service.create(1, payload)
    second = service.retry(1, first["deliberation_public_id"])
    assert first["evidence_snapshot_sha256"] == second["evidence_snapshot_sha256"]
    assert first["support_strength"] == second["support_strength"]
    with pytest.raises(DeliberationConflict, match="来源哈希"):
        service.create(1, {**payload, "source_event_sha256": "f" * 64})
