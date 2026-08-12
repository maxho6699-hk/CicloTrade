"""Temporary-directory canaries for the bounded local research path."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any

from core.backtest_source_snapshot import source_snapshot_id
from src.apps.worker.point_in_time_freezer import FrozenDailyOhlcv, freeze_daily_ohlcv
from src.apps.worker.research_executor import EQUITY_TEMPLATES, execute_all_templates, execute_research, research_code_bundle_sha256


def run_canary() -> dict[str, Any]:
    """Complete all templates on 96 bars; coverage keeps every result out of shadow."""
    frozen, manifest, inputs = _request(96, minimum_coverage_days=252)
    receipts = execute_all_templates(manifest, inputs)
    repeated = execute_all_templates(manifest, inputs)
    if receipts != repeated or set(receipts) != EQUITY_TEMPLATES:
        raise AssertionError("canary computation is not deterministic")
    if any(receipt["validation"]["candidate_status"] == "shadow" for receipt in receipts.values()):
        raise AssertionError("short canary must not enter shadow without declared coverage")
    return {"dataset_sha256": frozen.sha256, "templates": sorted(receipts), "rows": frozen.row_count}


def run_pass_canary() -> dict[str, Any]:
    """Run a longer bounded sample whose declared gates are eligible to pass."""
    frozen, manifest, inputs = _request(300, minimum_coverage_days=252, minimum_trades=1)
    receipt = execute_research(manifest, inputs)
    if not all(receipt["validation"][key] for key in (
        "oos_passed", "walk_forward_passed", "cost_1x_passed", "cost_2x_passed",
        "stress_passed", "multi_regime_passed", "minimum_trades_passed", "minimum_coverage_passed",
    )) or receipt["validation"]["candidate_status"] != "shadow":
        raise AssertionError("long canary did not meet its declared gates")
    return {"dataset_sha256": frozen.sha256, "rows": frozen.row_count, "validation": receipt["validation"]}


def run_queue_canary(template_key: str) -> dict[str, Any]:
    """Queue integration probe, parameterized for one fixed template.

    It is intentionally lazy: local source tests exercise computation only;
    lead-owned queue/runtime integration supplies the persistent boundary.
    """
    if template_key not in EQUITY_TEMPLATES:
        raise ValueError("template is outside the research allow-list")
    from core.backtest_artifacts import ArtifactStore
    from core.backtest_queue import BacktestQueue
    from core.backtest_queue_database import BacktestQueueDatabase
    from src.apps.worker.backtest_runtime import BacktestRuntime, ResourceSnapshot, WorkerSettings

    frozen, manifest, inputs = _request(96, template_key=template_key, minimum_coverage_days=252)
    with tempfile.TemporaryDirectory(prefix="tradeai-queue-canary-") as directory:
        root = Path(directory)
        queue = BacktestQueue(BacktestQueueDatabase(root / "queue.db"), ArtifactStore(root / "artifacts"))
        job, created = queue.enqueue(None, {"type": "candidate.evaluate.v1", "manifest": manifest}, idempotency_scope=f"system:canary:{template_key[-8:]}", idempotency_key=f"canary-{template_key[-12:]}", internal=True)
        if not created:
            raise AssertionError("queue canary unexpectedly reused work")
        descriptors = {item["artifact_key"]: item for item in manifest["inputs"]}
        for key, body in inputs.items():
            queue.register_input(
                job["id"],
                key,
                body,
                manifest["evidence_hashes"][key],
                row_count=descriptors[key].get("rows"),
                media_type="text/csv" if key != "source-snapshot.json" else "application/json",
            )

        class HealthyProbe:
            @staticmethod
            def snapshot() -> ResourceSnapshot:
                return ResourceSnapshot(cpu_percent=0.0, memory_percent=0.0)

        outcome = BacktestRuntime(queue, WorkerSettings(root / "queue.db", root / "artifacts", hard_timeout_seconds=10.0), resource_probe=HealthyProbe()).run_once()
        if outcome.state != "completed":
            raise AssertionError("queue canary did not complete")
        return {"state": queue.get(job["id"])["status"], "template_key": template_key, "rows": frozen.row_count}


def _freeze_temp(rows: int) -> FrozenDailyOhlcv:
    body, as_of = _synthetic_csv(rows)
    with tempfile.TemporaryDirectory(prefix="tradeai-research-canary-") as directory:
        source = Path(directory) / "daily.csv"
        source.write_bytes(body)
        return freeze_daily_ohlcv(source.read_bytes(), as_of=as_of, allowed_symbols={"AAPL"})


def _request(rows: int, **options: Any) -> tuple[FrozenDailyOhlcv, dict[str, Any], dict[str, bytes]]:
    source, as_of = _synthetic_csv(rows)
    frozen = freeze_daily_ohlcv(source, as_of=as_of, allowed_symbols={"AAPL"})
    snapshot = _snapshot(source, frozen)
    manifest = _manifest(frozen, source, snapshot, **options)
    return frozen, manifest, {"source.csv": source, "source-snapshot.json": snapshot, "prices.csv": frozen.canonical_csv}


def _manifest(dataset: FrozenDailyOhlcv, source: bytes, snapshot: bytes, *, template_key: str = "equity.trend.long_flat.v1", minimum_coverage_days: int, minimum_trades: int = 30) -> dict[str, Any]:
    return {
        "schema_version": 1, "template_key": template_key, "evaluation_date": "2027-01-01", "dataset_end": dataset.dataset_end.isoformat(), "code_bundle_sha256": research_code_bundle_sha256(), "inputs": [_descriptor("source.csv", source, dataset), _descriptor("source-snapshot.json", snapshot, dataset), dataset.manifest_input()],
        "candidate_id": "canary-equity", "candidate_version": "1", "provenance": {"source": "approved_seed", "generated_by": "ciclo-admin"}, "hypothesis": "bounded long flat daily equity templates", "parent_version": None, "parent_job_id": None, "parent_manifest_sha256": None, "parent_result_sha256": None,
        "asset_universe": {"market": "US", "instrument_family": "equity", "symbols": ["AAPL"], "direction": "long_flat", "research_proxy": False, "data_mode": "point_in_time_prices"},
        "search_space": {"lookback": [5, 10]}, "parameters": {"lookback": 5 if template_key == "equity.trend.long_flat.v1" else 10}, "experiment_budget": {"runs": 2, "folds": 3}, "evidence_hashes": {"source.csv": hashlib.sha256(source).hexdigest(), "source-snapshot.json": hashlib.sha256(snapshot).hexdigest(), "prices.csv": dataset.sha256},
        "authority": {"origin_site": "hk-strategy-worker", "deployment_role": "strategy_worker", "publication_ceiling": "shadow", "outbound_publish_enabled": False, "user_visible": False, "execution_eligible": False, "recommendations_published": False},
        "risk_contract": {"defined_risk": True, "max_loss_amount": 500.0, "currency": "USD", "max_loss_pct_model_equity": 0.005, "risk_basis_equity": 100_000.0, "risk_basis_captured_at": "2026-12-31T00:00:00Z", "portfolio_open_risk_cap_pct": 0.03, "daily_new_risk_pause_pct": 0.015, "quarantine_drawdown_pct": 0.08, "invalidation_condition": "frozen long-flat limit breached"},
        "validation_plan": {"oos_method": "point_in_time", "walk_forward": True, "cost_multipliers": [1.0, 2.0], "stress_tests": ["gap", "liquidity", "volatility"], "minimum_trades": minimum_trades, "minimum_coverage_days": minimum_coverage_days, "market_regimes": ["bull", "bear", "sideways"]},
    }


def _descriptor(key: str, body: bytes, dataset: FrozenDailyOhlcv) -> dict[str, str | int]:
    return {"artifact_key": key, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "rows": 1 if key == "source-snapshot.json" else dataset.row_count, "dataset_end": dataset.dataset_end.isoformat()}


def _snapshot(source: bytes, dataset: FrozenDailyOhlcv) -> bytes:
    base = {
        "schema_version": 1,
        "source_kind": "controlled_local_csv",
        "source_name": "canary.csv",
        "imported_at": "2027-01-01T00:00:00Z",
        "as_of": "2027-01-01T00:00:00Z",
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "prices_sha256": dataset.sha256,
        "canonical_bytes": len(dataset.canonical_csv),
        "canonical_rows": dataset.row_count,
        "dataset_end": dataset.dataset_end.isoformat(),
        "symbol": "AAPL",
    }
    return json.dumps({**base, "snapshot_id": source_snapshot_id(base)}, sort_keys=True, separators=(",", ":")).encode()


def _synthetic_csv(rows: int) -> tuple[bytes, datetime]:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(("symbol", "session_date", "session_open_at", "session_close_at", "available_at", "open", "high", "low", "close", "volume"))
    start, price = date(2025, 1, 2), 100.0
    for offset in range(rows):
        session = start + timedelta(days=offset)
        phase = offset % 30
        drift = 0.004 if phase < 10 else -0.003 if phase < 20 else 0.0002
        open_, close = price, price * (1 + drift)
        writer.writerow(("AAPL", session.isoformat(), f"{session.isoformat()}T14:30:00Z", f"{session.isoformat()}T21:00:00Z", f"{session.isoformat()}T21:05:00Z", f"{open_:.6f}", f"{max(open_, close) * 1.01:.6f}", f"{min(open_, close) * .99:.6f}", f"{close:.6f}", str(1_000_000 + offset)))
        price = close
    return target.getvalue().encode("utf-8"), datetime(2027, 1, 1, tzinfo=timezone.utc)
