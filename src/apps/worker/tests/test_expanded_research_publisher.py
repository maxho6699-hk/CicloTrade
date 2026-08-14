from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from datetime import datetime

import pytest

from core.compat import UTC
from core.expanded_research_contracts import AUTHORITY, UNIVERSE_SHA256, canonical_json, sha256_bytes
from src.apps.worker.expanded_research_publisher import ExpandedResearchPublisher, ExpandedResearchPublisherError


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def _payload(symbol: str = "AAPL", *, result_suffix: str = "a" * 24) -> dict:
    digest = "a" * 64
    evidence = {"runner": "equity-research-v1", "code_bundle_sha256": "b" * 64}
    return {
        "schema_version": 1, "kind": "tradeai.expanded-local-research.v1", "result_id": f"expanded-{symbol}-{result_suffix}",
        "symbol": symbol, "tier": "A", "source_sha256": digest, "universe_sha256": UNIVERSE_SHA256,
        "dataset_end": "2026-08-13", "equity": {key: evidence for key in ("equity.trend.long_flat.v1", "equity.mean_reversion.long_flat.v1", "equity.breakout.long_flat.v1")},
        "option_proxy": {"decision": "WAIT", "actionable": False}, "authority": AUTHORITY,
    }


def _source(path, payloads, *, corrupt_hash: bool = False):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE expanded_research_results(result_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL,source_sha256 TEXT NOT NULL,universe_sha256 TEXT NOT NULL,created_at TEXT NOT NULL)")
        for payload in payloads:
            body = canonical_json(payload)
            connection.execute("INSERT INTO expanded_research_results VALUES(?,?,?,?,?,?)", (payload["result_id"], body.decode(), "0" * 64 if corrupt_hash else sha256_bytes(body), payload["source_sha256"], payload["universe_sha256"], "2026-08-14T12:00:00Z"))


def _receipt(body, headers, *, result_id):
    digest = sha256_bytes(body)
    return {
        "accepted": True, "created": True, "receipt_key": headers["Idempotency-Key"], "result_id": result_id,
        "payload_sha256": digest, "result_sha256": digest, "state": "shadow", "research_only": True,
        "shadow": True, "actionable": False, "outbound": False, "user_visible": False,
        "execution": False, "official": False, "live": False,
    }


def test_disabled_first_never_reads_or_sends(tmp_path):
    calls = []
    publisher = ExpandedResearchPublisher(source_spool=tmp_path / "missing.db", state_database=tmp_path / "state.db", base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=False, transport=lambda *args: calls.append(args))
    assert publisher.publish_once() == {"state": "disabled", "published": 0, "outbound": False, "user_visible": False}
    assert calls == []


def test_publisher_is_idempotent_and_uses_independent_state(tmp_path):
    payload = _payload()
    source = tmp_path / "source.db"
    _source(source, [payload])
    calls = []
    def transport(url, body, headers):
        calls.append((url, body, dict(headers)))
        return _receipt(body, headers, result_id=payload["result_id"])
    publisher = ExpandedResearchPublisher(source_spool=source, state_database=tmp_path / "state.db", base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=True, clock=lambda: NOW, transport=transport)
    first = publisher.publish_once()
    second = publisher.publish_once()
    assert first["published"] == 1 and second["published"] == 0 and len(calls) == 1
    assert calls[0][0].endswith("/api/rewrite/internal/v1/expanded-research/results")
    assert calls[0][2]["Idempotency-Key"] == "expanded97-expanded-AAPL-aaaaaaaaaaaaaaaaaaaaaaaa"
    assert calls[0][2]["X-Ciclotrade-Research-Fencing-Epoch"] == "1"


def test_publisher_scans_past_ninety_eight_sent_rows(tmp_path):
    payloads = [_payload(result_suffix=f"{index:024x}") for index in range(99)]
    source = tmp_path / "source.db"
    state = tmp_path / "state.db"
    _source(source, payloads)
    calls = []

    def transport(_url, body, headers):
        result_id = json.loads(body)["result_id"]
        calls.append(result_id)
        return _receipt(body, headers, result_id=result_id)

    publisher = ExpandedResearchPublisher(source_spool=source, state_database=state, base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=True, clock=lambda: NOW, transport=transport)
    with closing(sqlite3.connect(state)) as connection, connection:
        connection.executemany(
            """INSERT INTO expanded_research_publish_state(
                   result_id,idempotency_key,status,attempts,fencing_epoch,response_json,last_error,updated_at
               ) VALUES(?,?, 'sent',1,1,'{}',NULL,?)""",
            [
                (payload["result_id"], f"expanded97-{payload['result_id']}", "2026-08-14T12:00:00Z")
                for payload in payloads[:98]
            ],
        )
    result = publisher.publish_once()
    assert result["published"] == 1
    assert calls == [payloads[98]["result_id"]]


def test_publisher_rejects_rewritten_source_hash(tmp_path):
    source = tmp_path / "source.db"
    _source(source, [_payload()], corrupt_hash=True)
    publisher = ExpandedResearchPublisher(source_spool=source, state_database=tmp_path / "state.db", base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=True, clock=lambda: NOW, transport=lambda *_: {})
    with pytest.raises(ExpandedResearchPublisherError, match="sealed evidence"):
        publisher.publish_once()


@pytest.mark.parametrize("column", ["source_sha256", "universe_sha256"])
def test_publisher_rejects_source_row_hash_drift(tmp_path, column):
    source = tmp_path / "source.db"
    state = tmp_path / "state.db"
    _source(source, [_payload()])
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute(f"UPDATE expanded_research_results SET {column}=?", ("c" * 64,))
    publisher = ExpandedResearchPublisher(source_spool=source, state_database=state, base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=True, clock=lambda: NOW, transport=lambda *_: {})
    with pytest.raises(ExpandedResearchPublisherError, match="row hashes"):
        publisher.publish_once()
    with closing(sqlite3.connect(state)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM expanded_research_publish_lease").fetchone()[0] == 0


def test_delivery_failure_releases_lease_and_allows_safe_retry(tmp_path):
    payload = _payload()
    source = tmp_path / "source.db"
    state = tmp_path / "state.db"
    _source(source, [payload])
    fail = True

    def transport(_url, body, headers):
        nonlocal fail
        if fail:
            fail = False
            raise TimeoutError("delivery timeout")
        return _receipt(body, headers, result_id=payload["result_id"])

    publisher = ExpandedResearchPublisher(source_spool=source, state_database=state, base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=True, clock=lambda: NOW, transport=transport)
    assert publisher.publish_once()["state"] == "error"
    with closing(sqlite3.connect(state)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM expanded_research_publish_lease").fetchone()[0] == 0
    assert publisher.publish_once()["published"] == 1
    source.unlink()
    state.unlink()


def test_malicious_success_response_remains_unknown(tmp_path):
    payload = _payload()
    source = tmp_path / "source.db"
    state = tmp_path / "state.db"
    _source(source, [payload])

    def transport(_url, body, headers):
        receipt = _receipt(body, headers, result_id=payload["result_id"])
        receipt["receipt_key"] = "attacker-receipt"
        return receipt

    publisher = ExpandedResearchPublisher(source_spool=source, state_database=state, base_url="https://ciclotrade.com", shared_secret="s" * 32, enabled=True, clock=lambda: NOW, transport=transport)
    result = publisher.publish_once()
    assert result["state"] == "error" and result["published"] == 0
    with closing(sqlite3.connect(state)) as connection:
        assert connection.execute("SELECT status FROM expanded_research_publish_state").fetchone()[0] == "unknown"


def test_publisher_rejects_source_and_state_alias(tmp_path):
    source = tmp_path / "source.db"
    source.touch()
    try:
        ExpandedResearchPublisher(source_spool=source, state_database=source, base_url="https://ciclotrade.com", shared_secret="s" * 32)
    except ValueError as exc:
        assert "isolated" in str(exc)
    else:
        raise AssertionError("publisher accepted shared source/state database")


def test_publisher_rejects_any_noncanonical_base_url(tmp_path):
    with pytest.raises(ValueError, match="sealed"):
        ExpandedResearchPublisher(source_spool=tmp_path / "source.db", state_database=tmp_path / "state.db", base_url="https://localhost", shared_secret="s" * 32)


def test_disabled_first_unit_uses_isolated_production_paths():
    root = Path(__file__).resolve().parents[4]
    service = (root / "ops/ciclotrade-expanded-research-publisher.service").read_text(encoding="utf-8")
    timer = (root / "ops/ciclotrade-expanded-research-publisher.timer").read_text(encoding="utf-8")
    env = (root / "config/expanded-research-publisher.env.example").read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/ciclotrade-worker/current" in service
    assert "ExecStart=/opt/ciclotrade-worker/current/.venv/bin/python" in service
    assert "ConditionPathExists=/etc/ciclotrade-worker/enable-expanded-research-publisher.after-integration" in service
    assert "ReadOnlyPaths=/var/lib/ciclotrade-worker/expanded-research/spool" in service
    assert "ReadWritePaths=/var/lib/ciclotrade-worker/expanded-research/publisher" in service
    assert "MemoryMax=192M" in service
    assert "CPUQuota=25%" in service
    assert "OnCalendar=*-*-* *:*:00" in timer
    assert "TRADEAI_EXPANDED_RESEARCH_PUBLISH_ENABLED=false" in env
    assert "expanded-research/spool/results.db" in env
    assert "expanded-research/publisher/publisher-state.db" in env
