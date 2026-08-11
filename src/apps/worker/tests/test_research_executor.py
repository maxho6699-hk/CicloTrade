from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from src.apps.worker.point_in_time_freezer import DailyBar
from src.apps.worker.research_canary import _request
from src.apps.worker.research_executor import ResearchExecutionError, _metrics, execute_all_templates, execute_research, main


def test_all_templates_compute_but_short_declared_coverage_fails_all_gates():
    _, manifest, inputs = _request(96, minimum_coverage_days=252)
    receipts = execute_all_templates(manifest, inputs)

    assert len(receipts) == 3
    assert all(not any(item["validation"][key] for key in ("oos_passed", "walk_forward_passed", "stress_passed")) for item in receipts.values())
    assert all(set(item["metrics"]) == {"costs", "oos", "chronological_folds", "stress", "regime_counts"} for item in receipts.values())


def test_open_to_next_open_captures_large_overnight_gap_and_final_exit_cost():
    start = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    bars = tuple(DailyBar("AAPL", date(2025, 1, 1) + timedelta(days=index), start + timedelta(days=index), start + timedelta(days=index, hours=6), start + timedelta(days=index, hours=7), 100.0 if index != 12 else 150.0, 151.0, 99.0, 100.0, 1000) for index in range(14))

    metrics = _metrics(bars, lambda history: 1, 1.0, start=11, stop=13)

    assert metrics["return_pct"] > 0.49
    assert metrics["trades"] == 2


def test_executor_rechecks_decision_availability_and_rejects_forbidden_template():
    start = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    bars = [DailyBar("AAPL", date(2025, 1, 1) + timedelta(days=index), start + timedelta(days=index), start + timedelta(days=index, hours=6), start + timedelta(days=index, hours=7), 100.0, 101.0, 99.0, 100.0, 1000) for index in range(14)]
    bars[10] = DailyBar("AAPL", bars[10].session_date, bars[10].session_open_at, bars[10].session_close_at, bars[11].session_open_at + timedelta(seconds=1), 100.0, 101.0, 99.0, 100.0, 1000)
    with pytest.raises(ResearchExecutionError):
        _metrics(tuple(bars), lambda history: 1, 1.0, start=11)
    _, manifest, inputs = _request(96, minimum_coverage_days=252)
    with pytest.raises(ResearchExecutionError):
        execute_research({**manifest, "template_key": "option.long_call.v1"}, inputs)


def test_negative_oos_and_insufficient_regimes_fail_closed():
    _, manifest, inputs = _request(96, minimum_coverage_days=252)
    receipt = execute_research(manifest, inputs)

    assert receipt["validation"]["coverage_days"] == 96
    assert receipt["validation"]["market_regimes"]
    assert receipt["validation"]["oos_passed"] is False


def test_negative_open_to_next_open_path_has_negative_oos_style_metrics():
    start = datetime(2025, 1, 1, 14, 30, tzinfo=timezone.utc)
    bars = tuple(DailyBar("AAPL", date(2025, 1, 1) + timedelta(days=index), start + timedelta(days=index), start + timedelta(days=index, hours=6), start + timedelta(days=index, hours=7), 100.0 - index, 101.0, 1.0, 100.0 - index, 1000) for index in range(16))

    assert _metrics(bars, lambda history: 1, 1.0, start=11)["return_pct"] < 0


def test_compute_gate_requires_all_three_inputs_and_file_ipc_writes_atomically():
    _, manifest, inputs = _request(96, minimum_coverage_days=252)
    with pytest.raises(ResearchExecutionError):
        execute_research(manifest, {"prices.csv": inputs["prices.csv"]})
    with tempfile.TemporaryDirectory() as directory:
        request, output = Path(directory) / "request.json", Path(directory) / "receipt.json"
        request.write_text(json.dumps({"manifest": manifest, "inputs": {key: base64.b64encode(value).decode() for key, value in inputs.items()}}), encoding="utf-8")
        assert main(["--request-file", str(request), "--output-file", str(output)]) == 0
        receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["code_bundle_sha256"] == manifest["code_bundle_sha256"]


def test_compute_gate_rejects_a_self_consistent_but_wrong_source_snapshot():
    _, manifest, inputs = _request(96, minimum_coverage_days=252)
    snapshot = json.loads(inputs["source-snapshot.json"])
    snapshot["symbol"] = "MSFT"
    core = {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    snapshot["snapshot_id"] = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    body = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    changed = {**inputs, "source-snapshot.json": body}
    digest = hashlib.sha256(body).hexdigest()
    descriptors = [dict(item) for item in manifest["inputs"]]
    next(item for item in descriptors if item["artifact_key"] == "source-snapshot.json").update({"sha256": digest, "bytes": len(body)})
    altered = {**manifest, "inputs": descriptors, "evidence_hashes": {**manifest["evidence_hashes"], "source-snapshot.json": digest}}

    with pytest.raises(ResearchExecutionError):
        execute_research(altered, changed)
