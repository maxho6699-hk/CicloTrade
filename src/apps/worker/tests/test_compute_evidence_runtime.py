from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import stat

import pytest

from core.backtest_queue_database import (
    BacktestQueueDatabase,
    BacktestQueueDatabaseError,
    ReadOnlyBacktestQueueDatabase,
)
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


def test_enabled_exporter_reads_existing_queue_without_modifying_database_or_artifacts(tmp_path):
    source_root = tmp_path / "source"
    queue = _completed_queue(source_root)
    queue_path = Path(queue.db._db_path)
    artifact_root = queue.artifacts.root
    protected = [queue_path, *sorted(path for path in artifact_root.rglob("*") if path.is_file())]
    before = {path: _file_identity(path) for path in protected}
    before_names = {path.relative_to(source_root).as_posix() for path in source_root.rglob("*")}
    original_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in protected}
    for path in protected:
        path.chmod(stat.S_IREAD)
    try:
        result = run_compute_evidence_exporter(
            env=_enabled_exporter_env(queue_path, artifact_root, tmp_path / "delivery" / "spool.db")
        )
    finally:
        for path, mode in original_modes.items():
            path.chmod(mode)

    assert result["state"] == "exported" and result["exported"] == 1
    assert {path: _file_identity(path) for path in protected} == before
    assert {path.relative_to(source_root).as_posix() for path in source_root.rglob("*")} == before_names


def test_enabled_exporter_fails_closed_when_source_queue_or_artifact_root_is_missing(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    missing_queue = tmp_path / "missing" / "queue.db"
    spool = tmp_path / "delivery" / "spool.db"
    with pytest.raises(ComputeEvidenceExporterError, match="does not exist"):
        run_compute_evidence_exporter(env=_enabled_exporter_env(missing_queue, artifacts, spool))
    assert not missing_queue.exists() and not spool.exists()

    queue = BacktestQueueDatabase(tmp_path / "queue.db")
    with pytest.raises(ComputeEvidenceExporterError, match="artifact directory does not exist"):
        run_compute_evidence_exporter(
            env=_enabled_exporter_env(Path(queue._db_path), tmp_path / "missing-artifacts", spool)
        )
    assert not spool.exists()


def test_read_only_queue_adapter_rejects_writes_at_the_sqlite_boundary(tmp_path):
    writable = BacktestQueueDatabase(tmp_path / "queue.db")
    readonly = ReadOnlyBacktestQueueDatabase(writable._db_path)
    before = _file_identity(Path(writable._db_path))
    with pytest.raises(BacktestQueueDatabaseError, match="rejects writes"):
        readonly.execute("DELETE FROM schema_migrations")
    with pytest.raises(BacktestQueueDatabaseError, match="query failed"):
        with readonly.transaction() as connection:
            connection.execute("DELETE FROM schema_migrations")
    assert _file_identity(Path(writable._db_path)) == before


def test_enabled_exporter_fails_closed_when_queue_cannot_be_opened_read_only(tmp_path, monkeypatch):
    queue = _completed_queue(tmp_path / "source")
    spool = tmp_path / "delivery" / "spool.db"

    def deny_read_only_connection(*_args, **_kwargs):
        raise sqlite3.OperationalError("permission denied")

    monkeypatch.setattr("core.backtest_queue_database.sqlite3.connect", deny_read_only_connection)
    with pytest.raises(ComputeEvidenceExporterError, match="cannot be opened"):
        run_compute_evidence_exporter(
            env=_enabled_exporter_env(Path(queue.db._db_path), queue.artifacts.root, spool)
        )
    assert not spool.exists()


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


def _enabled_exporter_env(queue: Path, artifacts: Path, spool: Path) -> dict[str, str]:
    return {
        "TRADEAI_COMPUTE_EVIDENCE_EXPORTER_ENABLED": "true",
        "TRADEAI_STRATEGY_WORKER_QUEUE_DB": str(queue.resolve()),
        "TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR": str(artifacts.resolve()),
        "TRADEAI_COMPUTE_EVIDENCE_SPOOL_DATABASE": str(spool.resolve()),
        "TRADEAI_COMPUTE_EVIDENCE_SITE_ID": SITE_ID,
    }


def _file_identity(path: Path) -> tuple[int, int, str]:
    metadata = path.stat()
    return metadata.st_mtime_ns, metadata.st_size, hashlib.sha256(path.read_bytes()).hexdigest()
