"""Canonical source-snapshot receipt for controlled local CSV imports."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import hashlib
import json
import re
from typing import Any, Mapping


SHA = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.csv$")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
SNAPSHOT_FIELDS = {
    "schema_version", "snapshot_id", "source_kind", "source_name", "imported_at", "as_of",
    "source_sha256", "prices_sha256", "canonical_bytes", "canonical_rows", "dataset_end", "symbol",
}


class SourceSnapshotError(ValueError):
    pass


def source_snapshot_id(value: Mapping[str, Any]) -> str:
    try:
        payload = {key: value[key] for key in sorted(SNAPSHOT_FIELDS - {"snapshot_id"})}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceSnapshotError("source snapshot 无法生成确定性标识。") from exc
    return hashlib.sha256(encoded).hexdigest()


def validate_source_snapshot(
    value: Any,
    *,
    evaluation_date: date,
    manifest_dataset_end: date,
    inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != SNAPSHOT_FIELDS or value.get("schema_version") != 1:
        raise SourceSnapshotError("source snapshot 字段或版本无效。")
    if value["source_kind"] != "controlled_local_csv":
        raise SourceSnapshotError("source snapshot 来源类型无效。")
    if not isinstance(value["source_name"], str) or not SAFE_NAME.fullmatch(value["source_name"]):
        raise SourceSnapshotError("source snapshot source_name 无效。")
    if not isinstance(value["symbol"], str) or not SYMBOL.fullmatch(value["symbol"]):
        raise SourceSnapshotError("source snapshot symbol 无效。")
    for key in ("snapshot_id", "source_sha256", "prices_sha256"):
        if not isinstance(value[key], str) or not SHA.fullmatch(value[key]):
            raise SourceSnapshotError(f"source snapshot {key} 无效。")
    if value["snapshot_id"] != source_snapshot_id(value):
        raise SourceSnapshotError("source snapshot 标识与内容不匹配。")
    imported_at = _timestamp(value["imported_at"], "imported_at")
    as_of = _timestamp(value["as_of"], "as_of")
    if imported_at < as_of:
        raise SourceSnapshotError("source snapshot imported_at 不得早于 as_of。")
    if as_of > datetime.combine(evaluation_date, time.max, tzinfo=timezone.utc):
        raise SourceSnapshotError("source snapshot as_of 晚于 evaluation_date。")
    try:
        dataset_end = date.fromisoformat(str(value["dataset_end"]))
    except ValueError as exc:
        raise SourceSnapshotError("source snapshot dataset_end 无效。") from exc
    if dataset_end != manifest_dataset_end or dataset_end > evaluation_date:
        raise SourceSnapshotError("source snapshot dataset_end 未绑定 manifest。")
    for key in ("canonical_rows", "canonical_bytes"):
        if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] <= 0:
            raise SourceSnapshotError(f"source snapshot {key} 无效。")
    source = inputs.get("source.csv")
    prices = inputs.get("prices.csv")
    if not source or source.get("sha256") != value["source_sha256"]:
        raise SourceSnapshotError("source snapshot 未绑定 source.csv。")
    if not prices or prices.get("sha256") != value["prices_sha256"]:
        raise SourceSnapshotError("source snapshot 未绑定 prices.csv。")
    if prices.get("rows") != value["canonical_rows"] or prices.get("bytes") != value["canonical_bytes"]:
        raise SourceSnapshotError("source snapshot rows/bytes 未绑定 prices.csv。")
    return dict(value)


def _timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceSnapshotError(f"source snapshot {label} 无效。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceSnapshotError(f"source snapshot {label} 必须包含时区。")
    return parsed.astimezone(timezone.utc)
