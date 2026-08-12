"""Strict contracts for generic equity Compute Gate shadow evidence delivery."""

from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import re
from typing import Any, Mapping

from core.backtest_artifacts import ArtifactStore
from core.backtest_contracts import (
    BacktestQueueError,
    sha256_json,
    validate_candidate_manifest,
    validate_manifest,
    validate_result_shape,
)
from core.compat import UTC


PACKAGE_KIND = "compute.equity-shadow.package.v1"
RECEIVER_ENDPOINT = "compute-equity-shadow-package"
RECEIVER_HTTP_PATH = "/api/rewrite/internal/v1/compute-evidence/equity-shadow"
PUBLICATION_STATES = frozenset({"quarantine", "shadow"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
SHORT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
NONCE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
MEDIA_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-/]{0,126}$")
AUTHORITY = {
    "research_only": True,
    "publication_ceiling": "shadow",
    "actionable": False,
    "user_visible": False,
    "official": False,
    "live": False,
}


class ComputeEvidenceError(ValueError):
    pass


class ComputeEvidenceConflict(ComputeEvidenceError):
    pass


class ComputeEvidenceStaleFence(ComputeEvidenceError):
    pass


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise ComputeEvidenceError("compute evidence must be canonical finite JSON") from exc


def sha256_bytes(body: bytes) -> str:
    if not isinstance(body, bytes):
        raise ComputeEvidenceError("compute evidence body must be bytes")
    return hashlib.sha256(body).hexdigest()


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ComputeEvidenceError("timestamp must include a timezone")
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ComputeEvidenceError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ComputeEvidenceError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ComputeEvidenceError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def package_id(job_id: str, manifest_sha256: str, result_sha256: str) -> str:
    return f"compute-{_safe_id(job_id, 'job_id')}-{_hash(manifest_sha256, 'manifest_sha256')[:12]}-{_hash(result_sha256, 'result_sha256')[:12]}"


def validate_package(value: Any) -> dict[str, Any]:
    item = _object(value, "compute evidence package")
    expected = {
        "schema_version",
        "kind",
        "package_id",
        "site_id",
        "worker_id",
        "job_id",
        "job_type",
        "attempt_no",
        "fencing_epoch",
        "completed_at",
        "manifest_sha256",
        "result_sha256",
        "manifest",
        "result",
        "artifacts",
        "authority",
    }
    _fields(item, expected, "compute evidence package")
    if item["schema_version"] != 1 or item["kind"] != PACKAGE_KIND:
        raise ComputeEvidenceError("compute evidence package version or kind is invalid")
    site_id = _short_id(item["site_id"], "site_id")
    worker_id = _short_id(item["worker_id"], "worker_id")
    job_id = _safe_id(item["job_id"], "job_id")
    if item["job_type"] != "candidate.evaluate.v1":
        raise ComputeEvidenceError("compute evidence must come from candidate evaluation")
    attempt_no = _positive_integer(item["attempt_no"], "attempt_no")
    fencing_epoch = _positive_integer(item["fencing_epoch"], "fencing_epoch")
    completed_at = stamp(parse_timestamp(item["completed_at"], "completed_at"))
    manifest_sha = _hash(item["manifest_sha256"], "manifest_sha256")
    result_sha = _hash(item["result_sha256"], "result_sha256")
    try:
        manifest = validate_manifest(dict(_object(item["manifest"], "manifest")))
        validate_candidate_manifest(manifest)
    except BacktestQueueError as exc:
        raise ComputeEvidenceError(str(exc)) from exc
    universe = manifest.get("asset_universe")
    authority = manifest.get("authority")
    if (
        not isinstance(universe, Mapping)
        or universe.get("market") != "US"
        or universe.get("instrument_family") != "equity"
        or universe.get("research_proxy") is not False
        or authority is None
        or authority.get("origin_site") != site_id
        or authority.get("publication_ceiling") != "shadow"
        or any(
            authority.get(key) is not False
            for key in ("outbound_publish_enabled", "user_visible", "execution_eligible", "recommendations_published")
        )
    ):
        raise ComputeEvidenceError("compute evidence must remain non-proxy US equity shadow research")
    if sha256_json(manifest) != manifest_sha:
        raise ComputeEvidenceError("compute evidence manifest hash does not match")
    result = dict(_object(item["result"], "result"))
    row = {
        "id": job_id,
        "job_type": "candidate.evaluate.v1",
        "manifest_json": canonical_json(manifest).decode("utf-8"),
        "manifest_sha256": manifest_sha,
        "fencing_epoch": fencing_epoch,
    }
    try:
        result = validate_result_shape(result, row)
    except BacktestQueueError as exc:
        raise ComputeEvidenceError(str(exc)) from exc
    if sha256_json(result) != result_sha:
        raise ComputeEvidenceError("compute evidence result hash does not match")
    validation = result.get("evidence", {}).get("validation", {})
    if result.get("evidence", {}).get("kind") != "research" or validation.get("candidate_status") != "shadow":
        raise ComputeEvidenceError("only completed shadow candidate evidence may be accepted")
    artifacts = _artifacts(item["artifacts"], manifest, result, attempt_no)
    expected_package_id = package_id(job_id, manifest_sha, result_sha)
    if item["package_id"] != expected_package_id:
        raise ComputeEvidenceError("compute evidence package_id is invalid")
    if item["authority"] != AUTHORITY:
        raise ComputeEvidenceError("compute evidence package authority is invalid")
    normalized = {
        "schema_version": 1,
        "kind": PACKAGE_KIND,
        "package_id": expected_package_id,
        "site_id": site_id,
        "worker_id": worker_id,
        "job_id": job_id,
        "job_type": "candidate.evaluate.v1",
        "attempt_no": attempt_no,
        "fencing_epoch": fencing_epoch,
        "completed_at": completed_at,
        "manifest_sha256": manifest_sha,
        "result_sha256": result_sha,
        "manifest": manifest,
        "result": result,
        "artifacts": artifacts,
        "authority": dict(AUTHORITY),
    }
    canonical_json(normalized)
    return normalized


def delivery_signature(
    secret: str | bytes,
    *,
    site_id: str,
    publisher_id: str,
    source_worker_id: str,
    fencing_epoch: int,
    idempotency_key: str,
    nonce: str,
    expires_at: str,
    package_sha256: str,
) -> str:
    raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not isinstance(raw_secret, bytes) or len(raw_secret) < 32:
        raise ComputeEvidenceError("compute evidence secret must contain at least 32 bytes")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ComputeEvidenceError("compute evidence nonce is invalid")
    message = "\n".join(
        (
            "compute-evidence-signature-v1",
            RECEIVER_ENDPOINT,
            _short_id(site_id, "site_id"),
            _short_id(publisher_id, "publisher_id"),
            _short_id(source_worker_id, "source_worker_id"),
            str(_positive_integer(fencing_epoch, "fencing_epoch")),
            _safe_id(idempotency_key, "idempotency_key"),
            nonce,
            stamp(parse_timestamp(expires_at, "expires_at")),
            _hash(package_sha256, "package_sha256"),
        )
    ).encode("utf-8")
    return "sha256=" + hmac.new(raw_secret, message, hashlib.sha256).hexdigest()


def _artifacts(
    value: Any, manifest: Mapping[str, Any], result: Mapping[str, Any], attempt_no: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 2 <= len(value) <= 128:
        raise ComputeEvidenceError("compute evidence artifacts are invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        item = _object(raw, "artifact")
        _fields(
            item, {"direction", "artifact_key", "attempt_no", "sha256", "bytes", "row_count", "media_type"}, "artifact"
        )
        direction = item["direction"]
        key = item["artifact_key"]
        if (
            direction not in {"input", "output"}
            or not isinstance(key, str)
            or not ArtifactStore.valid_key(key)
            or (direction, key) in seen
        ):
            raise ComputeEvidenceError("artifact identity is invalid or duplicated")
        seen.add((direction, key))
        declared_attempt = item["attempt_no"]
        expected_attempt = 0 if direction == "input" else attempt_no
        if declared_attempt != expected_attempt:
            raise ComputeEvidenceError("artifact attempt is not bound to the completed attempt")
        size = item["bytes"]
        rows = item["row_count"]
        media_type = item["media_type"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ComputeEvidenceError("artifact bytes is invalid")
        if rows is not None and (not isinstance(rows, int) or isinstance(rows, bool) or rows < 0):
            raise ComputeEvidenceError("artifact row_count is invalid")
        if not isinstance(media_type, str) or not MEDIA_TYPE.fullmatch(media_type):
            raise ComputeEvidenceError("artifact media_type is invalid")
        normalized.append(
            {
                "direction": direction,
                "artifact_key": key,
                "attempt_no": expected_attempt,
                "sha256": _hash(item["sha256"], "artifact.sha256"),
                "bytes": size,
                "row_count": rows,
                "media_type": media_type,
            }
        )
    expected_inputs = {item["artifact_key"]: item["sha256"] for item in manifest["inputs"]}
    input_descriptors = {item["artifact_key"]: item for item in manifest["inputs"]}
    expected_outputs = result["output_hashes"]
    actual_inputs = {item["artifact_key"]: item["sha256"] for item in normalized if item["direction"] == "input"}
    actual_outputs = {item["artifact_key"]: item["sha256"] for item in normalized if item["direction"] == "output"}
    if actual_inputs != expected_inputs or actual_outputs != expected_outputs:
        raise ComputeEvidenceError("artifact descriptors do not match manifest and result hashes")
    for item in normalized:
        if item["direction"] != "input":
            continue
        declared = input_descriptors[item["artifact_key"]]
        if "bytes" in declared and item["bytes"] != declared["bytes"]:
            raise ComputeEvidenceError("input artifact metadata does not match the frozen manifest")
        if "rows" in declared and item["row_count"] != declared["rows"]:
            raise ComputeEvidenceError("input artifact metadata does not match the frozen manifest")
    return sorted(normalized, key=lambda item: (item["direction"], item["artifact_key"]))


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ComputeEvidenceError(f"{label} must be an object")
    return dict(value)


def _fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ComputeEvidenceError(f"{label} fields are invalid")


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ComputeEvidenceError(f"{label} must be a lowercase SHA-256")
    return value


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ComputeEvidenceError(f"{label} is invalid")
    return value


def _short_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHORT_ID.fullmatch(value):
        raise ComputeEvidenceError(f"{label} is invalid")
    return value


def _positive_integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 2_147_483_647:
        raise ComputeEvidenceError(f"{label} is invalid")
    return value
