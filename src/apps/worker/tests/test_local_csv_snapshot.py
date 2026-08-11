from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import os

import pytest

from src.apps.worker import local_csv_snapshot as snapshot_module
from src.apps.worker.local_csv_snapshot import LocalSnapshotError, import_local_csv_snapshot


AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _csv(available_at: str = "2025-01-03T21:05:00Z") -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow((
        "symbol", "session_date", "session_open_at", "session_close_at", "available_at",
        "open", "high", "low", "close", "volume",
    ))
    writer.writerow((
        "AAPL", "2025-01-03", "2025-01-03T14:30:00Z", "2025-01-03T21:00:00Z", available_at,
        "100", "102", "99", "101", "1000",
    ))
    return target.getvalue().encode("utf-8")


def test_controlled_local_import_is_deterministic_and_emits_three_bound_inputs(tmp_path):
    source = tmp_path / "aapl.csv"
    source.write_bytes(_csv())

    first = import_local_csv_snapshot(
        tmp_path.resolve(), "aapl.csv", as_of=AS_OF, imported_at=AS_OF, allowed_symbols={"AAPL"},
    )
    second = import_local_csv_snapshot(
        tmp_path.resolve(), "aapl.csv", as_of=AS_OF, imported_at=AS_OF, allowed_symbols={"AAPL"},
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.receipt_json == second.receipt_json
    assert set(first.artifact_bodies()) == {"source.csv", "source-snapshot.json", "prices.csv"}
    assert {item["artifact_key"] for item in first.manifest_inputs()} == set(first.artifact_bodies())
    assert first.receipt["prices_sha256"] == first.frozen.sha256


@pytest.mark.parametrize("source_name", ["../aapl.csv", "nested/aapl.csv", "aapl.txt", "bad name.csv"])
def test_controlled_local_import_rejects_unsafe_source_names(tmp_path, source_name):
    with pytest.raises(LocalSnapshotError):
        import_local_csv_snapshot(tmp_path.resolve(), source_name, as_of=AS_OF, allowed_symbols={"AAPL"})


def test_controlled_local_import_rejects_relative_root_size_limit_and_pit_leakage(tmp_path):
    (tmp_path / "aapl.csv").write_bytes(_csv())
    with pytest.raises(LocalSnapshotError, match="绝对"):
        import_local_csv_snapshot("relative", "aapl.csv", as_of=AS_OF, allowed_symbols={"AAPL"})
    with pytest.raises(LocalSnapshotError, match="大小"):
        import_local_csv_snapshot(tmp_path.resolve(), "aapl.csv", as_of=AS_OF, allowed_symbols={"AAPL"}, maximum_bytes=10)

    (tmp_path / "aapl.csv").write_bytes(_csv("2027-01-01T00:00:00Z"))
    with pytest.raises(LocalSnapshotError):
        import_local_csv_snapshot(tmp_path.resolve(), "aapl.csv", as_of=AS_OF, imported_at=AS_OF, allowed_symbols={"AAPL"})


def test_controlled_local_import_rejects_path_swap_between_validation_and_open(tmp_path, monkeypatch):
    source = tmp_path / "aapl.csv"
    replacement = tmp_path / "replacement.csv"
    source.write_bytes(_csv())
    replacement.write_bytes(_csv().replace(b"101", b"100"))
    real_open = snapshot_module.os.open

    def swapped_open(path, flags):
        os.replace(replacement, source)
        return real_open(path, flags)

    monkeypatch.setattr(snapshot_module.os, "open", swapped_open)

    with pytest.raises(LocalSnapshotError):
        import_local_csv_snapshot(
            tmp_path.resolve(), "aapl.csv", as_of=AS_OF, imported_at=AS_OF, allowed_symbols={"AAPL"}
        )


@pytest.mark.skipif(os.name != "posix", reason="O_NOFOLLOW is a POSIX deployment control")
def test_controlled_local_import_rejects_symlink_swap_at_open(tmp_path, monkeypatch):
    source = tmp_path / "aapl.csv"
    outside = tmp_path / "outside.csv"
    source.write_bytes(_csv())
    outside.write_bytes(_csv())
    real_open = snapshot_module.os.open

    def symlink_swap(path, flags):
        source.unlink()
        source.symlink_to(outside)
        return real_open(path, flags)

    monkeypatch.setattr(snapshot_module.os, "open", symlink_swap)

    with pytest.raises(LocalSnapshotError):
        import_local_csv_snapshot(
            tmp_path.resolve(), "aapl.csv", as_of=AS_OF, imported_at=AS_OF, allowed_symbols={"AAPL"}
        )
