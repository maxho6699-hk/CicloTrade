"""Strict request contract for the local autonomous candidate producer."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Collection, Mapping

from core.backtest_contracts import BUDGET_LIMITS, BacktestQueueError, validate_search_space


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROVENANCE_SOURCES = frozenset({"approved_seed", "derived_candidate"})
CANDIDATE_SPEC_FIELDS = frozenset({
    "candidate_id",
    "candidate_version",
    "template_key",
    "provenance_source",
    "hypothesis",
    "parent_version",
    "parent_job_id",
    "parent_manifest_sha256",
    "parent_result_sha256",
    "search_space",
    "experiment_budget",
    "parameters",
})


class CandidateInputError(ValueError):
    """Raised when a producer request could escape the bounded shadow scope."""


def approved_universe_sha256(symbols: Collection[str]) -> str:
    normalized = sorted(set(symbols))
    if not normalized or not all(isinstance(symbol, str) and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", symbol) for symbol in normalized):
        raise CandidateInputError("candidate universe must be an explicit normalized US equity allow-list")
    body = json.dumps(
        {"contract": "tradeai-approved-us-equity-universe-v1", "symbols": normalized},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def validate_candidate_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != CANDIDATE_SPEC_FIELDS:
        raise CandidateInputError("candidate_spec fields do not match the bounded contract")
    result = dict(value)
    for field in ("candidate_id", "candidate_version"):
        if not isinstance(result[field], str) or not SAFE_ID.fullmatch(result[field]):
            raise CandidateInputError(f"candidate_spec.{field} is invalid")
    if not isinstance(result["template_key"], str) or not SAFE_ID.fullmatch(result["template_key"]):
        raise CandidateInputError("candidate_spec.template_key is invalid")
    if result["provenance_source"] not in PROVENANCE_SOURCES:
        raise CandidateInputError("candidate_spec provenance is outside the approved seed/derived scope")
    hypothesis = result["hypothesis"]
    if not isinstance(hypothesis, str) or not 1 <= len(hypothesis.strip()) <= 2_000:
        raise CandidateInputError("candidate_spec hypothesis is invalid")
    parent_fields = ("parent_version", "parent_job_id", "parent_manifest_sha256", "parent_result_sha256")
    if result["provenance_source"] == "approved_seed":
        if any(result[field] is not None for field in parent_fields):
            raise CandidateInputError("approved seed candidate cannot claim a parent")
    else:
        for field in ("parent_version", "parent_job_id"):
            if not isinstance(result[field], str) or not SAFE_ID.fullmatch(result[field]):
                raise CandidateInputError(f"derived candidate {field} is invalid")
        for field in ("parent_manifest_sha256", "parent_result_sha256"):
            if not isinstance(result[field], str) or not SHA256.fullmatch(result[field]):
                raise CandidateInputError(f"derived candidate {field} is invalid")
    budget = result["experiment_budget"]
    if not isinstance(budget, dict) or not budget or set(budget) - set(BUDGET_LIMITS):
        raise CandidateInputError("candidate_spec experiment budget is invalid")
    for name, amount in budget.items():
        if not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= BUDGET_LIMITS[name]:
            raise CandidateInputError(f"candidate_spec experiment_budget.{name} is invalid")
    if budget.get("runs") != 1 or budget.get("folds") != 3 or set(budget) != {"runs", "folds"}:
        raise CandidateInputError("candidate producer executes exactly one candidate with three folds")
    try:
        result["search_space"] = validate_search_space(result["search_space"], budget)
    except BacktestQueueError as exc:
        raise CandidateInputError(str(exc)) from exc
    parameters = result["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {"lookback"}:
        raise CandidateInputError("candidate_spec parameters must select exactly one lookback")
    lookback = parameters["lookback"]
    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback not in result["search_space"].get("lookback", []):
        raise CandidateInputError("candidate_spec lookback must be frozen inside search_space")
    if result["search_space"] != {"lookback": [lookback]}:
        raise CandidateInputError("candidate producer search_space must freeze one bounded parameter point")
    return result
