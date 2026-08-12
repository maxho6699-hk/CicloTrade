from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import stat

import pytest

from core.backtest_artifacts import ArtifactStore
from core.backtest_contracts import _json, sha256_json
from core.backtest_queue import BacktestQueue
from core.backtest_queue_database import BacktestQueueDatabase
from src.apps.worker.autonomous_candidate_producer import (
    AutonomousCandidateProducer,
    main,
)
from src.apps.worker.backtest_runtime import ResourceSnapshot
from src.apps.worker.candidate_input_contracts import CandidateInputError, validate_candidate_spec
from src.apps.worker.candidate_producer_config import (
    CandidateProducerError,
    CandidateProducerSettings,
    _integration_marker_ready,
)
from src.apps.worker.compute_gate import ComputeGate, ComputeGateSettings


NOW = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)  # 02:00 Hong Kong


class Probe:
    def __init__(self, cpu: float = 10.0, memory: float = 20.0):
        self.value = ResourceSnapshot(cpu, memory)

    def snapshot(self) -> ResourceSnapshot:
        return self.value


class Disk:
    free = 10**12


def prices(symbol: str = "AAPL", rows: int = 260) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow((
        "symbol", "session_date", "session_open_at", "session_close_at", "available_at",
        "open", "high", "low", "close", "volume",
    ))
    start, value = date(2025, 1, 2), 100.0
    for offset in range(rows):
        session = start + timedelta(days=offset)
        close = value * (1 + ((offset % 9) - 4) * 0.001)
        writer.writerow((
            symbol, session.isoformat(), f"{session}T14:30:00Z", f"{session}T21:00:00Z",
            f"{session}T21:05:00Z", f"{value:.8f}", f"{max(value, close) * 1.01:.8f}",
            f"{min(value, close) * .99:.8f}", f"{close:.8f}", str(1_000_000 + offset),
        ))
        value = close
    return target.getvalue().encode("utf-8")


def spec(version: str = "v1", **overrides) -> dict:
    value = {
        "candidate_id": "AAPL.trend",
        "candidate_version": version,
        "template_key": "equity.trend.long_flat.v1",
        "provenance_source": "approved_seed",
        "hypothesis": "A bounded trend lookback may remain robust after independent costs and stress tests.",
        "parent_version": None,
        "parent_job_id": None,
        "parent_manifest_sha256": None,
        "parent_result_sha256": None,
        "search_space": {"lookback": [5]},
        "experiment_budget": {"runs": 1, "folds": 3},
        "parameters": {"lookback": 5},
    }
    value.update(overrides)
    return value


def settings(tmp_path: Path, **overrides) -> CandidateProducerSettings:
    source, drop = tmp_path / "sources", tmp_path / "inbox"
    source.mkdir()
    drop.mkdir()
    values = {
        "enabled": True,
        "source_dir": source,
        "drop_dir": drop,
        "queue_db": tmp_path / "queue.db",
        "artifact_dir": tmp_path / "artifacts",
        "allowed_symbols": frozenset({"AAPL", "MSFT"}),
        "minimum_free_bytes": 1,
        "max_daily_candidates": 4,
        "max_pending_jobs": 4,
    }
    values.update(overrides)
    return CandidateProducerSettings(**values)


def queue_for(config: CandidateProducerSettings) -> BacktestQueue:
    assert config.queue_db and config.artifact_dir
    return BacktestQueue(BacktestQueueDatabase(config.queue_db), ArtifactStore(config.artifact_dir))


def producer(config: CandidateProducerSettings, queue: BacktestQueue, *, probe: Probe | None = None, now: datetime = NOW) -> AutonomousCandidateProducer:
    return AutonomousCandidateProducer(
        queue,
        config,
        resource_probe=probe or Probe(),
        disk_probe=lambda _: Disk(),
        clock=lambda: now,
    )


def test_producer_atomically_drops_frozen_shadow_request_and_compute_gate_consumes_it(tmp_path):
    config = settings(tmp_path)
    body = prices()
    (config.source_dir / "aapl.csv").write_bytes(body)
    queue = queue_for(config)

    outcome = producer(config, queue).produce("aapl.csv", spec())
    request_path = config.drop_dir / f"{outcome['request_id']}.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert outcome["state"] == "produced" and outcome["publication"] == "disabled"
    assert request["schema_version"] == 2
    assert request["universe_sha256"]
    assert request["candidate_spec"]["experiment_budget"] == {"folds": 3, "runs": 1}
    assert not list(config.drop_dir.glob("*.part"))

    gate_config = ComputeGateSettings(
        drop_dir=config.drop_dir,
        queue_db=config.queue_db,
        artifact_dir=config.artifact_dir,
        allowed_symbols=config.allowed_symbols,
        minimum_free_bytes=1,
        max_daily_jobs=4,
        max_daily_runs=4,
        max_pending_jobs=4,
        max_requests_per_run=1,
    )
    result = ComputeGate(
        queue,
        gate_config,
        resource_probe=Probe(),
        disk_probe=lambda _: Disk(),
        clock=lambda: NOW,
    ).run_once()
    manifest = queue.get(result["job_ids"][0])["manifest"]
    assert result["state"] == "produced"
    assert manifest["candidate_id"] == "AAPL.trend"
    assert manifest["provenance"]["source"] == "approved_seed"
    assert manifest["parameters"] == {"lookback": 5}
    assert manifest["authority"]["publication_ceiling"] == "shadow"
    assert manifest["authority"]["user_visible"] is False
    assert (config.drop_dir / "aapl.csv").read_bytes() == body
    assert not list(config.drop_dir.glob("*.json"))
    assert len(list((config.drop_dir / ".processed").glob("*.json"))) == 1


def test_same_candidate_version_is_idempotent_across_time_and_changed_content_conflicts(tmp_path):
    config = settings(tmp_path)
    path = config.source_dir / "aapl.csv"
    path.write_bytes(prices())
    queue = queue_for(config)
    first = producer(config, queue).produce("aapl.csv", spec())
    second = producer(config, queue, now=NOW + timedelta(minutes=10)).produce("aapl.csv", spec())
    assert second["state"] == "reused"
    assert first["request_id"] == second["request_id"]
    assert queue.db.fetch_one("SELECT count(*) count FROM backtest_candidate_production_receipts")["count"] == 1

    path.write_bytes(prices(rows=261))
    with pytest.raises(CandidateProducerError, match="different frozen content"):
        producer(config, queue).produce("aapl.csv", spec())


@pytest.mark.parametrize(
    "wrong_spec",
    [
        spec(candidate_id="MSFT.trend"),
        spec(candidate_id="AAPL.breakout"),
        spec(template_key="equity.breakout.long_flat.v1"),
    ],
)
def test_producer_rejects_root_candidate_lineage_that_does_not_match_source_and_template(tmp_path, wrong_spec):
    config = settings(tmp_path)
    (config.source_dir / "aapl.csv").write_bytes(prices())
    queue = queue_for(config)

    with pytest.raises(CandidateProducerError, match="lineage"):
        producer(config, queue).produce("aapl.csv", wrong_spec)

    assert not list(config.drop_dir.iterdir())
    assert queue.db.fetch_one("SELECT id FROM backtest_jobs") is None


def test_producer_rejects_derived_candidate_with_cross_family_id_before_delivery(tmp_path):
    config = settings(tmp_path)
    (config.source_dir / "aapl.csv").write_bytes(prices())
    queue = queue_for(config)
    child = spec(
        provenance_source="derived_candidate",
        candidate_id="AAPL.breakout",
        parent_version="v0",
        parent_job_id="parent-job",
        parent_manifest_sha256="a" * 64,
        parent_result_sha256="b" * 64,
    )

    with pytest.raises(CandidateProducerError, match="lineage"):
        producer(config, queue).produce("aapl.csv", child)

    assert not list(config.drop_dir.iterdir())


def test_automatic_cycle_uses_real_allowlisted_source_and_emits_only_one_root_candidate(tmp_path):
    config = settings(tmp_path)
    (config.source_dir / "aapl.csv").write_bytes(prices())
    queue = queue_for(config)

    result = producer(config, queue).produce_next()
    request = json.loads((config.drop_dir / f"{result['request_id']}.json").read_text(encoding="utf-8"))

    assert result["state"] == "produced"
    assert request["source_sha256"] == __import__("hashlib").sha256(prices()).hexdigest()
    assert request["candidate_spec"]["provenance_source"] == "approved_seed"
    assert request["candidate_spec"]["search_space"] == {
        "lookback": [request["candidate_spec"]["parameters"]["lookback"]]
    }
    assert len(list(config.drop_dir.glob("*.json"))) == 1


def test_automatic_cycle_without_real_source_is_idle_and_does_not_reserve(tmp_path):
    config = settings(tmp_path)
    queue = queue_for(config)

    assert producer(config, queue).produce_next() == {"state": "idle", "publication": "disabled"}
    assert queue.db.fetch_one("SELECT count(*) count FROM backtest_candidate_production_receipts")["count"] == 0


def test_automatic_cycle_rejects_short_or_non_allowlisted_source(tmp_path):
    short_root = tmp_path / "short"
    short_root.mkdir()
    short = settings(short_root)
    (short.source_dir / "aapl.csv").write_bytes(prices(rows=251))
    with pytest.raises(CandidateProducerError, match="at least 252"):
        producer(short, queue_for(short)).produce_next()

    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign = settings(foreign_root)
    (foreign.source_dir / "nvda.csv").write_bytes(prices("NVDA"))
    with pytest.raises(CandidateProducerError, match="outside the deployed allow-list"):
        producer(foreign, queue_for(foreign)).produce_next()


def test_completed_parent_produces_one_lineage_bound_child_and_never_skips_to_official(tmp_path):
    config = settings(tmp_path)
    (config.source_dir / "aapl.csv").write_bytes(prices())
    queue = queue_for(config)
    parent = producer(config, queue)._automatic_spec("AAPL", "equity.trend.long_flat.v1", None, NOW)
    manifest = {
        "schema_version": 1,
        "template_key": parent["template_key"],
        "evaluation_date": "2026-08-12",
        "dataset_end": "2026-08-11",
        "code_bundle_sha256": "a" * 64,
        "inputs": [{"artifact_key": "prices.csv", "sha256": "b" * 64, "dataset_end": "2026-08-11"}],
        "candidate_id": parent["candidate_id"],
        "candidate_version": parent["candidate_version"],
        "provenance": {"source": "approved_seed", "generated_by": "seed-admin"},
        "hypothesis": parent["hypothesis"],
        "parent_version": None,
        "parent_job_id": None,
        "parent_manifest_sha256": None,
        "parent_result_sha256": None,
        "asset_universe": {"market": "US", "instrument_family": "equity", "symbols": ["AAPL"], "direction": "long_flat", "research_proxy": False, "data_mode": "point_in_time_prices"},
        "search_space": parent["search_space"],
        "experiment_budget": parent["experiment_budget"],
        "parameters": parent["parameters"],
        "evidence_hashes": {"prices.csv": "b" * 64},
        "authority": {"origin_site": "hk-strategy-worker", "deployment_role": "strategy_worker", "publication_ceiling": "shadow", "outbound_publish_enabled": False, "user_visible": False, "execution_eligible": False, "recommendations_published": False},
        "risk_contract": {"defined_risk": True, "max_loss_amount": 500.0, "currency": "USD", "max_loss_pct_model_equity": 0.005, "risk_basis_equity": 100_000.0, "risk_basis_captured_at": "2026-08-12T00:00:00Z", "portfolio_open_risk_cap_pct": 0.03, "daily_new_risk_pause_pct": 0.015, "quarantine_drawdown_pct": 0.08, "invalidation_condition": "bounded research invalidation"},
        "validation_plan": {"oos_method": "point_in_time", "walk_forward": True, "cost_multipliers": [1.0, 2.0], "stress_tests": ["gap", "liquidity", "volatility"], "minimum_trades": 30, "minimum_coverage_days": 252, "market_regimes": ["bull", "bear", "sideways"]},
    }
    job, _ = queue.enqueue(None, {"type": "candidate.evaluate.v1", "manifest": manifest}, idempotency_scope="system:test-parent", idempotency_key="candidate-parent", internal=True)
    result_hash = "c" * 64
    queue.db.execute("UPDATE backtest_jobs SET status='completed',result_json=?,result_sha256=?,completed_at=?,updated_at=? WHERE id=?", (_json({"evidence": "shadow"}), result_hash, "2026-08-12T17:00:00Z", "2026-08-12T17:00:00Z", job["id"]))

    outcome = producer(config, queue).produce_next()
    child = json.loads((config.drop_dir / f"{outcome['request_id']}.json").read_text(encoding="utf-8"))["candidate_spec"]

    assert child["provenance_source"] == "derived_candidate"
    assert child["parent_job_id"] == job["id"]
    assert child["parent_manifest_sha256"] == sha256_json(manifest)
    assert child["parent_result_sha256"] == result_hash
    assert child["search_space"] == {"lookback": [10]}
    assert "official" not in json.dumps(child).lower() and "live" not in json.dumps(child).lower()


@pytest.mark.parametrize(
    ("now", "probe", "message"),
    [
        (datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc), Probe(), "off-peak"),
        (NOW, Probe(61.0, 20.0), "resource gated"),
        (NOW, Probe(20.0, 76.0), "resource gated"),
    ],
)
def test_schedule_and_resources_fail_before_a_durable_reservation(tmp_path, now, probe, message):
    config = settings(tmp_path)
    (config.source_dir / "aapl.csv").write_bytes(prices())
    queue = queue_for(config)

    with pytest.raises(CandidateProducerError, match=message):
        producer(config, queue, probe=probe, now=now).produce("aapl.csv", spec())

    assert queue.db.fetch_one("SELECT count(*) count FROM backtest_candidate_production_receipts")["count"] == 0


def test_daily_and_pending_budgets_fail_closed(tmp_path):
    daily_root = tmp_path / "daily"
    daily_root.mkdir()
    daily = settings(daily_root, max_daily_candidates=1)
    (daily.source_dir / "aapl.csv").write_bytes(prices())
    daily_queue = queue_for(daily)
    producer(daily, daily_queue).produce("aapl.csv", spec("v1"))
    with pytest.raises(CandidateProducerError, match="daily budget"):
        producer(daily, daily_queue).produce("aapl.csv", spec("v2"))

    pending_root = tmp_path / "pending"
    pending_root.mkdir()
    pending = settings(pending_root, max_pending_jobs=1)
    (pending.source_dir / "aapl.csv").write_bytes(prices())
    pending_queue = queue_for(pending)
    manifest = {
        "schema_version": 1,
        "evaluation_date": "2026-01-01",
        "dataset_end": "2026-01-01",
        "code_bundle_sha256": "a" * 64,
        "inputs": [{"artifact_key": "prices.csv", "sha256": "b" * 64, "dataset_end": "2026-01-01"}],
        "experiment_budget": {"runs": 1},
    }
    pending_queue.enqueue(None, {"type": "catalog.evaluate.v1", "manifest": manifest}, idempotency_scope="system:test", idempotency_key="pending-job", internal=True)
    with pytest.raises(CandidateProducerError, match="pending-budget"):
        producer(pending, pending_queue).produce("aapl.csv", spec())


def test_pending_request_blocks_overwriting_shared_csv_until_compute_gate_archives_json(tmp_path):
    config = settings(tmp_path)
    source = config.source_dir / "aapl.csv"
    original = prices()
    source.write_bytes(original)
    queue = queue_for(config)
    first = producer(config, queue).produce("aapl.csv", spec("v1"))
    assert (config.drop_dir / f"{first['request_id']}.json").exists()

    source.write_bytes(prices(rows=261))
    with pytest.raises(CandidateProducerError, match="still references"):
        producer(config, queue).produce("aapl.csv", spec("v2"))

    assert (config.drop_dir / "aapl.csv").read_bytes() == original
    assert queue.db.fetch_one("SELECT count(*) count FROM backtest_candidate_production_receipts")["count"] == 2
    assert queue.db.fetch_one("SELECT state FROM backtest_candidate_production_receipts WHERE candidate_version='v2'")["state"] == "reserved"

    (config.drop_dir / f"{first['request_id']}.json").unlink()
    recovered = producer(config, queue).produce("aapl.csv", spec("v2"))
    assert recovered["state"] == "reused"
    assert queue.db.fetch_one("SELECT state FROM backtest_candidate_production_receipts WHERE candidate_version='v2'")["state"] == "delivered"


def test_candidate_receipt_migration_matches_reserved_to_delivered_runtime_contract(tmp_path):
    config = settings(tmp_path)
    queue = queue_for(config)
    columns = {row["name"] for row in queue.db.fetch_all("PRAGMA table_info(backtest_candidate_production_receipts)")}
    assert {"state", "delivered_at", "request_json", "universe_sha256"} <= columns

    (config.source_dir / "aapl.csv").write_bytes(prices())
    outcome = producer(config, queue).produce("aapl.csv", spec())
    receipt = queue.db.fetch_one(
        "SELECT state,delivered_at,request_sha256 FROM backtest_candidate_production_receipts WHERE request_id=?",
        (outcome["request_id"],),
    )
    assert receipt["state"] == "delivered" and receipt["delivered_at"]
    assert receipt["request_sha256"] == outcome["request_sha256"]
    with pytest.raises(Exception):
        queue.db.execute(
            "UPDATE backtest_candidate_production_receipts SET candidate_id='MUTATED' WHERE request_id=?",
            (outcome["request_id"],),
        )


def test_candidate_contract_rejects_unbounded_search_live_fields_and_fake_parent():
    with pytest.raises(CandidateInputError):
        validate_candidate_spec(spec(search_space={"lookback": [5, 10]}, parameters={"lookback": 5}, experiment_budget={"runs": 2, "folds": 3}))
    with pytest.raises(CandidateInputError):
        validate_candidate_spec(spec(search_space={"script": ["buy"]}, parameters={"lookback": 5}))
    with pytest.raises(CandidateInputError):
        validate_candidate_spec(spec(provenance_source="approved_seed", parent_version="v0"))
    with pytest.raises(CandidateInputError):
        validate_candidate_spec({**spec(), "publication": "official"})


def test_derived_candidate_requires_complete_parent_hashes(tmp_path):
    config = settings(tmp_path)
    (config.source_dir / "aapl.csv").write_bytes(prices())
    queue = queue_for(config)
    with pytest.raises(CandidateInputError):
        producer(config, queue).produce("aapl.csv", spec(provenance_source="derived_candidate"))


def test_disabled_environment_has_no_path_or_database_side_effect(tmp_path):
    settings = CandidateProducerSettings.from_environment({})
    assert settings.enabled is False
    assert settings.queue_db is None and settings.source_dir is None
    assert not list(tmp_path.iterdir())


class Marker:
    def __init__(self, mode: int, owner: int = 0):
        self.mode = mode
        self.owner = owner

    def lstat(self):
        return SimpleNamespace(st_mode=self.mode, st_uid=self.owner)


def test_enabled_environment_requires_a_root_controlled_integration_marker(monkeypatch, tmp_path):
    environment = {
        "TRADEAI_CANDIDATE_PRODUCER_ENABLED": "true",
        "TRADEAI_COMPUTE_ALLOWED_SYMBOLS": "AAPL",
        "TRADEAI_CANDIDATE_SOURCE_DIR": str((tmp_path / "sources").resolve()),
        "TRADEAI_COMPUTE_DROP_DIR": str((tmp_path / "inbox").resolve()),
        "TRADEAI_STRATEGY_WORKER_QUEUE_DB": str((tmp_path / "queue.db").resolve()),
        "TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR": str((tmp_path / "artifacts").resolve()),
    }

    monkeypatch.setattr("src.apps.worker.candidate_producer_config._integration_marker_ready", lambda _marker: False)
    missing = CandidateProducerSettings.from_environment(environment)
    assert missing.enabled is False
    assert missing.queue_db is None and missing.source_dir is None
    assert not list(tmp_path.iterdir())

    monkeypatch.setattr("src.apps.worker.candidate_producer_config._integration_marker_ready", lambda _marker: True)
    ready = CandidateProducerSettings.from_environment(environment)
    assert ready.enabled is True
    assert ready.queue_db == (tmp_path / "queue.db").resolve()


@pytest.mark.parametrize(
    ("marker", "message"),
    [
        (Marker(stat.S_IFLNK | 0o777), "regular file"),
        (Marker(stat.S_IFREG | 0o660), "root-controlled"),
        (Marker(stat.S_IFREG | 0o640, owner=1000), "root-controlled"),
    ],
)
def test_integration_marker_rejects_symlinks_writable_files_and_non_root_owners(marker, message):
    with pytest.raises(CandidateProducerError, match=message):
        _integration_marker_ready(marker, platform_name="posix")


def test_integration_marker_missing_is_disabled(tmp_path):
    assert _integration_marker_ready(tmp_path / "missing", platform_name="posix") is False


def test_enabled_env_without_marker_exits_before_runtime_construction(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRADEAI_CANDIDATE_PRODUCER_ENABLED", "true")
    monkeypatch.setenv("TRADEAI_CANDIDATE_SOURCE_DIR", str(tmp_path / "must-not-be-created"))
    monkeypatch.setattr("src.apps.worker.candidate_producer_config._integration_marker_ready", lambda _marker: False)
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.ComputeGateSettings.from_environment", lambda: pytest.fail("Compute Gate settings opened"))
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.BacktestQueueDatabase", lambda *_: pytest.fail("database opened"))
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.ArtifactStore", lambda *_: pytest.fail("artifact directory opened"))
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.AutonomousCandidateProducer", lambda *_: pytest.fail("producer constructed"))

    assert main(["--once", "--execute-one"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"
    assert not list(tmp_path.iterdir())


def test_cli_parser_accepts_the_exact_systemd_orchestrator_command():
    completed = subprocess.run(
        [sys.executable, "-m", "src.apps.worker.autonomous_candidate_producer", "--help"],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0
    assert "--once" in completed.stdout and "--execute-one" in completed.stdout


def test_orchestrator_processes_existing_inbox_even_when_producer_is_idle(monkeypatch, tmp_path, capsys):
    config = settings(tmp_path)
    queue = queue_for(config)
    compute = ComputeGateSettings(
        drop_dir=config.drop_dir,
        queue_db=config.queue_db,
        artifact_dir=config.artifact_dir,
        allowed_symbols=config.allowed_symbols,
        minimum_free_bytes=1,
    )
    monkeypatch.setattr(CandidateProducerSettings, "from_environment", classmethod(lambda cls: config))
    monkeypatch.setattr(ComputeGateSettings, "from_environment", classmethod(lambda cls: compute))
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.BacktestQueueDatabase", lambda _path: queue.db)
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.ArtifactStore", lambda _path: queue.artifacts)
    monkeypatch.setattr(AutonomousCandidateProducer, "produce_next", lambda self: {"state": "idle", "publication": "disabled"})

    calls = {"import": 0, "execute": 0}

    class Gate:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_once(self):
            calls["import"] += 1
            return {"state": "produced", "job_ids": ["existing-job"]}

        def execution_gate_state(self):
            return "ready"

    class Runtime:
        def __init__(self, *_args, **_kwargs):
            pass

        def run_once(self):
            calls["execute"] += 1
            return type("Outcome", (), {"state": "completed", "job_id": "existing-job"})()

    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.ComputeGate", Gate)
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.BacktestRuntime", Runtime)

    assert main(["--once", "--execute-one"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == {"import": 1, "execute": 1}
    assert payload["state"] == "idle"
    assert payload["execution"] == {"job_id": "existing-job", "state": "completed"}


def test_disabled_orchestrator_never_opens_queue_or_compute_gate(monkeypatch, capsys):
    monkeypatch.setattr(CandidateProducerSettings, "from_environment", classmethod(lambda cls: CandidateProducerSettings(False, None, None, None, None, frozenset())))
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.BacktestQueueDatabase", lambda *_: pytest.fail("queue opened"))
    monkeypatch.setattr("src.apps.worker.autonomous_candidate_producer.ComputeGate", lambda *_: pytest.fail("compute gate opened"))

    assert main(["--once", "--execute-one"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"


def test_systemd_unit_is_local_only_disabled_by_configuration_and_single_shot():
    root = Path(__file__).resolve().parents[4]
    service = (root / "ops" / "ciclotrade-candidate-producer.service").read_text(encoding="utf-8")
    timer = (root / "ops" / "ciclotrade-candidate-producer.timer").read_text(encoding="utf-8")
    environment = (root / "config" / "strategy-worker.env.example").read_text(encoding="utf-8")

    assert "PrivateNetwork=true" in service
    assert "ConditionPathExists=/etc/ciclotrade-worker/enable-candidate-producer.after-integration" in service
    assert "--once --execute-one" in service and "candidate-sources" in service
    assert "OnSuccess=" not in service
    assert "ReadOnlyPaths=/var/lib/ciclotrade-worker/candidate-sources" in service
    assert "OnUnitActiveSec=" not in timer and "Persistent=false" in timer
    assert timer.count("OnCalendar=*-*-*") == 12
    assert "OnCalendar=*-*-* 00:50:00 Asia/Hong_Kong" in timer
    assert "OnCalendar=*-*-* 06:20:00 Asia/Hong_Kong" in timer
    assert "TRADEAI_CANDIDATE_PRODUCER_ENABLED=false" in environment
    assert "enable-candidate-producer.after-integration" in environment
    assert "There is no demo/synthetic/network fallback" in environment
