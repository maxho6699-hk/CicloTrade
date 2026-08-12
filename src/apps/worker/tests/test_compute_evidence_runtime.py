from __future__ import annotations

from pathlib import Path

import pytest

from core.backtest_queue_database import BacktestQueueDatabase
from src.apps.worker.compute_evidence_exporter import (
    ComputeEvidenceExporter,
    ComputeEvidenceExporterError,
    ComputeEvidenceExporterSettings,
    run_compute_evidence_exporter,
)
from src.apps.worker.compute_evidence_publisher import main as publisher_main
from src.apps.worker.compute_evidence_spool import PersistentComputeEvidenceSpool
from tests.test_compute_evidence_acceptance import SITE_ID, _completed_queue


ROOT = Path(__file__).resolve().parents[4]


def test_exporter_is_disabled_and_rejects_shared_queue_spool_path(tmp_path):
    assert run_compute_evidence_exporter(env={}) == {"state": "disabled", "exported": 0}
    shared = str((tmp_path / "shared.db").resolve())
    with pytest.raises(ComputeEvidenceExporterError, match="isolated"):
        ComputeEvidenceExporterSettings.from_env(
            {
                "TRADEAI_COMPUTE_EVIDENCE_EXPORTER_ENABLED": "true",
                "TRADEAI_STRATEGY_WORKER_QUEUE_DB": shared,
                "TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR": str((tmp_path / "artifacts").resolve()),
                "TRADEAI_COMPUTE_EVIDENCE_SPOOL_DATABASE": shared,
                "TRADEAI_COMPUTE_EVIDENCE_SITE_ID": SITE_ID,
            }
        )


def test_exporter_is_bounded_idempotent_and_scans_only_completed_system_candidates(tmp_path):
    queue = _completed_queue(tmp_path / "completed")
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "spool.db"))
    exporter = ComputeEvidenceExporter(queue, spool, site_id=SITE_ID, max_packages_per_run=1)
    first = exporter.run_once()
    second = exporter.run_once()
    assert first == {
        "state": "exported",
        "exported": 1,
        "already_present": 0,
        "inspected": 1,
        "remaining_completed": 0,
    }
    assert second["state"] == "idle" and second["exported"] == 0 and second["already_present"] == 1
    assert spool.database.fetch_one("SELECT count(*) AS total FROM compute_evidence_spool")["total"] == 1

    queue.db.execute("UPDATE backtest_jobs SET status='failed'")
    empty_spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "empty-spool.db"))
    assert ComputeEvidenceExporter(queue, empty_spool, site_id=SITE_ID).run_once()["state"] == "idle"


@pytest.mark.parametrize(
    ("state", "expected"),
    [("disabled", 0), ("idle", 0), ("delivered", 0), ("retryable", 0), ("dead", 1), ("uncertain", 1)],
)
def test_publisher_cli_exit_codes(monkeypatch, state, expected):
    monkeypatch.setattr(
        "src.apps.worker.compute_evidence_publisher.run_compute_evidence_publisher",
        lambda: {"state": state},
    )
    assert publisher_main() == expected


def test_systemd_units_keep_compute_and_network_capabilities_separate():
    exporter = (ROOT / "ops/ciclotrade-compute-evidence-exporter.service").read_text(encoding="utf-8")
    publisher = (ROOT / "ops/ciclotrade-compute-evidence-publisher.service").read_text(encoding="utf-8")
    timers = [
        (ROOT / "ops/ciclotrade-compute-evidence-exporter.timer").read_text(encoding="utf-8"),
        (ROOT / "ops/ciclotrade-compute-evidence-publisher.timer").read_text(encoding="utf-8"),
    ]
    marker = "ConditionPathExists=/etc/ciclotrade-worker/enable-compute-evidence.after-integration"
    assert marker in exporter and marker in publisher
    assert "PrivateNetwork=true" in exporter
    assert "RestrictAddressFamilies=AF_UNIX" in exporter
    assert "ReadOnlyPaths=/var/lib/ciclotrade-worker/backtest-queue.db" in exporter
    assert "PrivateNetwork=false" in publisher
    assert "backtest-queue.db" not in publisher and "/artifacts" not in publisher
    assert all("WantedBy=timers.target" in timer and "Persistent=false" in timer for timer in timers)


def test_environment_template_is_root_only_guidance_and_disabled_by_default():
    template = (ROOT / "config/compute-evidence.env.example").read_text(encoding="utf-8")
    assert "root:root mode 0600" in template
    assert "TRADEAI_COMPUTE_EVIDENCE_EXPORTER_ENABLED=false" in template
    assert "TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ENABLED=false" in template
    assert "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=" in template
    assert "https://" not in template
