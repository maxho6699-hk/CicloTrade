"""Controlled, local-only CSV import for point-in-time research snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Collection

from core.backtest_source_snapshot import source_snapshot_id, validate_source_snapshot
from src.apps.worker.point_in_time_freezer import FrozenDailyOhlcv, PointInTimeError, freeze_daily_ohlcv


SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.csv$")


class LocalSnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class LocalCsvSnapshot:
    source_name: str
    raw_csv: bytes
    frozen: FrozenDailyOhlcv
    receipt: dict[str, object]
    receipt_json: bytes

    @property
    def snapshot_id(self) -> str:
        return str(self.receipt["snapshot_id"])

    def artifact_bodies(self) -> dict[str, bytes]:
        return {
            "source.csv": self.raw_csv,
            "source-snapshot.json": self.receipt_json,
            "prices.csv": self.frozen.canonical_csv,
        }

    def manifest_inputs(self) -> list[dict[str, object]]:
        dataset_end = self.frozen.dataset_end.isoformat()
        return [
            _descriptor("source.csv", self.raw_csv, dataset_end, self.frozen.row_count),
            _descriptor("source-snapshot.json", self.receipt_json, dataset_end, 1),
            self.frozen.manifest_input(),
        ]

    def evidence_hashes(self) -> dict[str, str]:
        return {key: hashlib.sha256(body).hexdigest() for key, body in self.artifact_bodies().items()}

    def with_receipt(self, receipt: dict[str, object]) -> "LocalCsvSnapshot":
        value = dict(receipt)
        if value.get("source_name") != self.source_name:
            raise LocalSnapshotError("canonical source snapshot name does not match the imported file")
        inputs = {
            item["artifact_key"]: item
            for item in (
                _descriptor("source.csv", self.raw_csv, self.frozen.dataset_end.isoformat(), self.frozen.row_count),
                self.frozen.manifest_input(),
            )
        }
        try:
            validate_source_snapshot(
                value,
                evaluation_date=self.frozen.as_of.date(),
                manifest_dataset_end=self.frozen.dataset_end,
                inputs=inputs,
            )
        except (PointInTimeError, ValueError) as exc:
            raise LocalSnapshotError("canonical source snapshot is incompatible with the imported file") from exc
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return LocalCsvSnapshot(self.source_name, self.raw_csv, self.frozen, value, encoded)


def import_local_csv_snapshot(
    import_root: str | Path,
    source_name: str,
    *,
    as_of: datetime,
    allowed_symbols: Collection[str],
    imported_at: datetime | None = None,
    maximum_bytes: int = 8 * 1024 * 1024,
) -> LocalCsvSnapshot:
    if not isinstance(source_name, str) or not SAFE_SOURCE.fullmatch(source_name):
        raise LocalSnapshotError("source_name 必须是安全的单层 CSV 文件名。")
    root = Path(import_root)
    if not root.is_absolute():
        raise LocalSnapshotError("import_root 必须是绝对本地路径。")
    root = root.resolve()
    source = root / source_name
    if source.is_symlink() or source.parent.resolve() != root:
        raise LocalSnapshotError("受控 CSV 来源必须是 import_root 内的普通文件。")
    try:
        path_before = source.stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalSnapshotError("受控 CSV 来源不存在。") from exc
    if not stat.S_ISREG(path_before.st_mode):
        raise LocalSnapshotError("受控 CSV 来源必须是普通文件。")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise LocalSnapshotError("受控 CSV 来源不存在或无法安全打开。") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size <= 0 or before.st_size > maximum_bytes:
                raise LocalSnapshotError("受控 CSV 来源大小或文件类型超出限制。")
            raw = handle.read(maximum_bytes + 1)
            after = os.fstat(handle.fileno())
            path_after = source.stat(follow_symlinks=False)
    except OSError as exc:
        raise LocalSnapshotError("受控 CSV 来源读取失败。") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    path_identity_before = (
        path_before.st_dev,
        path_before.st_ino,
        path_before.st_size,
        path_before.st_mtime_ns,
    )
    path_identity_after = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
    )
    if (
        identity_before != identity_after
        or identity_before != path_identity_before
        or identity_before != path_identity_after
        or len(raw) != before.st_size
        or len(raw) > maximum_bytes
    ):
        raise LocalSnapshotError("受控 CSV 在导入期间发生变化。")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise LocalSnapshotError("as_of 必须包含时区。")
    captured = (imported_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if captured < as_of.astimezone(timezone.utc):
        raise LocalSnapshotError("imported_at 不得早于 as_of。")
    symbols = sorted(set(allowed_symbols))
    if len(symbols) != 1:
        raise LocalSnapshotError("P0 每个 source snapshot 只允许一个美股 symbol。")
    try:
        frozen = freeze_daily_ohlcv(raw, as_of=as_of, allowed_symbols=symbols)
    except PointInTimeError as exc:
        raise LocalSnapshotError(str(exc)) from exc
    receipt: dict[str, object] = {
        "schema_version": 1,
        "source_kind": "controlled_local_csv",
        "source_name": source_name,
        "imported_at": _stamp(captured),
        "as_of": _stamp(as_of.astimezone(timezone.utc)),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "prices_sha256": frozen.sha256,
        "canonical_bytes": len(frozen.canonical_csv),
        "canonical_rows": frozen.row_count,
        "dataset_end": frozen.dataset_end.isoformat(),
        "symbol": symbols[0],
    }
    receipt["snapshot_id"] = source_snapshot_id(receipt)
    inputs = {item["artifact_key"]: item for item in [
        _descriptor("source.csv", raw, frozen.dataset_end.isoformat(), frozen.row_count),
        frozen.manifest_input(),
    ]}
    validate_source_snapshot(
        receipt,
        evaluation_date=as_of.date(),
        manifest_dataset_end=frozen.dataset_end,
        inputs=inputs,
    )
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return LocalCsvSnapshot(source_name, raw, frozen, receipt, encoded)


def _descriptor(key: str, body: bytes, dataset_end: str, rows: int) -> dict[str, object]:
    return {
        "artifact_key": key,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "rows": rows,
        "dataset_end": dataset_end,
    }


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
