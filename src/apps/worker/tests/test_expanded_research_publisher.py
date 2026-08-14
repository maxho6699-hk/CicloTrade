from __future__ import annotations

import sqlite3
from datetime import datetime

from core.compat import UTC
from core.expanded_research_contracts import AUTHORITY, UNIVERSE_SHA256, canonical_json
from src.apps.worker.expanded_research_publisher import ExpandedResearchPublisher


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _payload() -> dict:
    digest = "a" * 64
    evidence = {"runner": "equity-research-v1", "code_bundle_sha256": "b" * 64}
    return {
        "schema_version": 1, "kind": "tradeai.expanded-local-research.v1", "result_id": "expanded-AAPL-aaaaaaaaaaaaaaaaaaaaaaaa",
        "symbol": "AAPL", "tier": "A", "source_sha256": digest, "universe_sha256": UNIVERSE_SHA256,
        "dataset_end": "2026-08-13", "equity": {key: evidence for key in ("equity.trend.long_flat.v1", "equity.mean_reversion.long_flat.v1", "equity.breakout.long_flat.v1")},
        "option_proxy": {"decision": "WAIT", "actionable": False}, "authority": AUTHORITY,
    }


def _source(path, payload):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE expanded_research_results(result_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL,source_sha256 TEXT NOT NULL,universe_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO expanded_research_results VALUES(?,?,?,?,?,?)", (payload["result_id"], canonical_json(payload).decode(), "a" * 64, payload["source_sha256"], payload["universe_sha256"], "2026-08-14T12:00:00Z"))


def test_disabled_first_never_reads_or_sends(tmp_path):
    calls = []
    publisher = ExpandedResearchPublisher(source_spool=tmp_path / "missing.db", state_database=tmp_path / "state.db", base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=False, transport=lambda *args: calls.append(args))
    assert publisher.publish_once() == {"state": "disabled", "published": 0, "outbound": False, "user_visible": False}
    assert calls == []


def test_publisher_is_idempotent_and_uses_independent_state(tmp_path):
    payload = _payload()
    source = tmp_path / "source.db"
    _source(source, payload)
    calls = []
    def transport(url, body, headers):
        calls.append((url, body, dict(headers)))
        return {"accepted": True, "created": True}
    publisher = ExpandedResearchPublisher(source_spool=source, state_database=tmp_path / "state.db", base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=True, clock=lambda: NOW, transport=transport)
    first = publisher.publish_once()
    second = publisher.publish_once()
    assert first["published"] == 1 and second["published"] == 0 and len(calls) == 1
    assert calls[0][0].endswith("/api/rewrite/internal/v1/expanded-research/results")
    assert calls[0][2]["Idempotency-Key"] == "expanded97-expanded-AAPL-aaaaaaaaaaaaaaaaaaaaaaaa"
    assert calls[0][2]["X-Ciclotrade-Research-Fencing-Epoch"] == "1"


def test_publisher_rejects_source_and_state_alias(tmp_path):
    source = tmp_path / "source.db"
    source.touch()
    try:
        ExpandedResearchPublisher(source_spool=source, state_database=source, base_url="https://ciclotrade.com", shared_secret="s" * 32)
    except ValueError as exc:
        assert "isolated" in str(exc)
    else:
        raise AssertionError("publisher accepted shared source/state database")
