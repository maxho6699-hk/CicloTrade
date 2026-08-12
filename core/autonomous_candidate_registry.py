"""Immutable shadow-only autonomous candidate registration contract."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping


SHA = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
STATUSES = frozenset({"rejected", "quarantine", "shadow"})


class CandidateRegistryError(ValueError):
    pass


def candidate_record(candidate_id: str, candidate_version: str, hypothesis: str, parent_version: str | None, universe_sha256: str, data_sha256: str, code_sha256: str, evidence_sha256: str, status: str, ranking_inputs: Mapping[str, float], rejection_reason: str | None, rollback_target: str | None) -> dict[str, object]:
    values = {
        "schema_version": 1, "candidate_id": candidate_id, "candidate_version": candidate_version,
        "hypothesis": hypothesis, "parent_version": parent_version, "universe_sha256": universe_sha256,
        "data_sha256": data_sha256, "code_sha256": code_sha256, "evidence_sha256": evidence_sha256,
        "status": status, "ranking_inputs": dict(ranking_inputs), "rejection_reason": rejection_reason,
        "rollback_target": rollback_target,
    }
    return validate_candidate_record(values)


def validate_candidate_record(value: Any) -> dict[str, object]:
    required = {"schema_version", "candidate_id", "candidate_version", "hypothesis", "parent_version", "universe_sha256", "data_sha256", "code_sha256", "evidence_sha256", "status", "ranking_inputs", "rejection_reason", "rollback_target"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema_version") != 1:
        raise CandidateRegistryError("candidate registry fields are invalid")
    result = dict(value)
    for key in ("candidate_id", "candidate_version"):
        if not isinstance(result[key], str) or not SAFE_ID.fullmatch(result[key]):
            raise CandidateRegistryError(f"{key} is invalid")
    if not isinstance(result["hypothesis"], str) or not result["hypothesis"].strip() or len(result["hypothesis"]) > 2_000:
        raise CandidateRegistryError("hypothesis is invalid")
    for key in ("parent_version", "rollback_target"):
        if result[key] is not None and (not isinstance(result[key], str) or not SAFE_ID.fullmatch(result[key])):
            raise CandidateRegistryError(f"{key} is invalid")
    for key in ("universe_sha256", "data_sha256", "code_sha256", "evidence_sha256"):
        if not isinstance(result[key], str) or not SHA.fullmatch(result[key]):
            raise CandidateRegistryError(f"{key} is invalid")
    if result["status"] not in STATUSES:
        raise CandidateRegistryError("candidate status is shadow-only and fail-closed")
    ranking = result["ranking_inputs"]
    if not isinstance(ranking, Mapping) or not ranking or len(ranking) > 32:
        raise CandidateRegistryError("ranking_inputs is invalid")
    for name, score in ranking.items():
        if not isinstance(name, str) or not SAFE_ID.fullmatch(name) or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise CandidateRegistryError("ranking_inputs contains an invalid value")
    if result["status"] == "shadow":
        if result["rejection_reason"] is not None:
            raise CandidateRegistryError("shadow candidate cannot carry a rejection reason")
        if result["rollback_target"] is not None:
            raise CandidateRegistryError("shadow candidate cannot carry a rollback target")
    elif not isinstance(result["rejection_reason"], str) or not result["rejection_reason"].strip() or len(result["rejection_reason"]) > 512:
        raise CandidateRegistryError("failed candidate requires a rejection reason")
    return result


def record_sha256(value: Mapping[str, object]) -> str:
    validated = validate_candidate_record(value)
    return hashlib.sha256(json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Registration:
    record: dict[str, object]
    champion: dict[str, object] | None
    created: bool


class CandidateRegistry:
    """In-memory reference registry; SQL persistence is append-only by migration."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, object]] = {}
        self._champions: dict[str, dict[str, object]] = {}

    def register(self, value: Mapping[str, object]) -> Registration:
        record = validate_candidate_record(value)
        key = (str(record["candidate_id"]), str(record["candidate_version"]))
        current = self._records.get(key)
        if current is not None:
            if record_sha256(current) != record_sha256(record):
                raise CandidateRegistryError("candidate/version is immutable")
            return Registration(dict(current), self.champion(key[0]), False)
        stored = dict(record)
        rollback_target = stored.get("rollback_target")
        champion = self._champions.get(key[0])
        if rollback_target is not None and (
            champion is None or champion.get("candidate_version") != rollback_target
        ):
            raise CandidateRegistryError("rollback target must name the current immutable champion")
        self._records[key] = stored
        if stored["status"] == "shadow":
            existing = self._champions.get(key[0])
            if existing is None or _score(stored) > _score(existing):
                self._champions[key[0]] = stored
        return Registration(dict(stored), self.champion(key[0]), True)

    def champion(self, candidate_id: str) -> dict[str, object] | None:
        item = self._champions.get(candidate_id)
        return dict(item) if item else None


def _score(value: Mapping[str, object]) -> float:
    ranking = value["ranking_inputs"]
    assert isinstance(ranking, Mapping)
    score = ranking.get("score", float("-inf"))
    return float(score)
