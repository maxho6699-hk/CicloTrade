"""Strict contracts for the append-only earnings research journal."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.compat import UTC


SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SYMBOL = re.compile(r"^(?:[A-Z][A-Z0-9.-]{0,15}|\d{6})$")
EVENT_STATUSES = {"CONFIRMED", "RESCHEDULED", "CANCELLED"}
TIMINGS = {"BMO", "AMC", "DURING", "UNKNOWN"}
CHECKPOINTS = {"AFTER_HOURS", "NEXT_CLOSE", "D3_CLOSE", "D5_CLOSE"}
POSTMORTEM_STAGES = {"PRELIMINARY", "FINAL", "CORRECTION"}
SIMULATED_ACTIONS = {
    "OBSERVE", "PAPER_OPEN", "PAPER_ADD", "PAPER_REDUCE", "PAPER_CLOSE",
    "RESEARCH_LONG_CALL", "RESEARCH_LONG_PUT", "RESEARCH_LONG_STRADDLE",
    "RESEARCH_LONG_STRANGLE",
}


class EarningsContractError(ValueError):
    """Raised when research evidence is unsafe, ambiguous, or non-PIT."""


class IdempotencyConflict(EarningsContractError):
    """Raised when an idempotency key is reused for different content."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise EarningsContractError("payload must be finite canonical JSON") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EarningsContractError(f"{label} must be an object")
    return dict(value)


def _strict_fields(
    value: Mapping[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    fields = set(value)
    if required - fields:
        raise EarningsContractError(f"{label} missing fields: {', '.join(sorted(required - fields))}")
    if fields - required - optional:
        raise EarningsContractError(f"{label} has unknown fields")


def _safe_text(value: Any, label: str, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum or "\x00" in value:
        raise EarningsContractError(f"{label} is invalid")
    return value.strip()


def _safe_id(value: Any, label: str) -> str:
    text = _safe_text(value, label, 128)
    if not SAFE_ID.fullmatch(text):
        raise EarningsContractError(f"{label} is invalid")
    return text


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise EarningsContractError(f"{label} must be a lowercase SHA-256")
    return value


def _number(
    value: Any, label: str, *, minimum: float | None = None, maximum: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EarningsContractError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise EarningsContractError(f"{label} must be a finite number")
    if minimum is not None and result < minimum:
        raise EarningsContractError(f"{label} is below its minimum")
    if maximum is not None and result > maximum:
        raise EarningsContractError(f"{label} is above its maximum")
    return result


def _integer(value: Any, label: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EarningsContractError(f"{label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise EarningsContractError(f"{label} is outside its allowed range")
    return value


def parse_timestamp(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise EarningsContractError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EarningsContractError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def timestamp(value: Any, label: str) -> str:
    return parse_timestamp(value, label).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_event_revision(payload: Any) -> dict[str, Any]:
    value = _object(payload, "event revision")
    required = {
        "event_key", "revision_no", "market", "symbol", "fiscal_period", "scheduled_at",
        "exchange_timezone", "timing", "status", "source", "source_event_id",
        "observed_at", "available_at", "recorded_at", "supersedes_revision_id",
    }
    _strict_fields(value, required, set(), "event revision")
    value["event_key"] = _safe_id(value["event_key"], "event_key")
    value["revision_no"] = _integer(value["revision_no"], "revision_no", minimum=1)
    if value["market"] not in {"US", "CN"}:
        raise EarningsContractError("market is unsupported")
    if not isinstance(value["symbol"], str) or not SYMBOL.fullmatch(value["symbol"]):
        raise EarningsContractError("symbol is invalid")
    value["fiscal_period"] = _safe_text(value["fiscal_period"], "fiscal_period", 40)
    expected_event_key = f'{value["market"]}:{value["symbol"]}:{value["fiscal_period"]}'
    if value["event_key"] != expected_event_key:
        raise EarningsContractError("event_key must match market, symbol, and fiscal_period")
    value["source"] = _safe_id(value["source"], "source")
    value["source_event_id"] = _safe_id(value["source_event_id"], "source_event_id")
    if value["timing"] not in TIMINGS:
        raise EarningsContractError("timing is invalid")
    if value["status"] not in EVENT_STATUSES:
        raise EarningsContractError("status is invalid")
    try:
        ZoneInfo(str(value["exchange_timezone"]))
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise EarningsContractError("exchange_timezone is invalid") from exc
    observed = parse_timestamp(value["observed_at"], "observed_at")
    available = parse_timestamp(value["available_at"], "available_at")
    recorded = parse_timestamp(value["recorded_at"], "recorded_at")
    scheduled = parse_timestamp(value["scheduled_at"], "scheduled_at")
    if not observed <= available <= recorded:
        raise EarningsContractError("event time ordering is invalid")
    if value["status"] != "CANCELLED" and recorded >= scheduled:
        raise EarningsContractError("confirmed event must be recorded before scheduled_at")
    supersedes = value["supersedes_revision_id"]
    if supersedes is not None:
        value["supersedes_revision_id"] = _integer(
            supersedes, "supersedes_revision_id", minimum=1
        )
    value.update(
        scheduled_at=timestamp(scheduled, "scheduled_at"),
        observed_at=timestamp(observed, "observed_at"),
        available_at=timestamp(available, "available_at"),
        recorded_at=timestamp(recorded, "recorded_at"),
    )
    value["payload_sha256"] = sha256_json(value)
    return value


def _validate_manifest(
    raw: Any, decision: datetime, available_cutoff: datetime
) -> tuple[dict[str, Any], str]:
    manifest = _object(raw, "input_manifest")
    _strict_fields(
        manifest,
        {"schema_version", "historical_backfill", "evidence"},
        set(),
        "input_manifest",
    )
    if manifest["schema_version"] != 1 or not isinstance(manifest["historical_backfill"], bool):
        raise EarningsContractError("input_manifest schema is invalid")
    evidence = manifest["evidence"]
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or not evidence:
        raise EarningsContractError("input_manifest evidence must be non-empty")
    normalized = []
    snapshot_ids: set[str] = set()
    for item in evidence:
        record = _object(item, "evidence item")
        _strict_fields(
            record,
            {"source", "source_snapshot_id", "observed_at", "available_at", "sha256"},
            set(),
            "evidence item",
        )
        record["source"] = _safe_id(record["source"], "evidence.source")
        snapshot_id = _safe_id(record["source_snapshot_id"], "evidence.source_snapshot_id")
        if snapshot_id in snapshot_ids:
            raise EarningsContractError("evidence source_snapshot_id must be unique")
        snapshot_ids.add(snapshot_id)
        observed = parse_timestamp(record["observed_at"], "evidence.observed_at")
        available = parse_timestamp(record["available_at"], "evidence.available_at")
        if observed > available:
            raise EarningsContractError("evidence observed_at is after available_at")
        if available > decision:
            raise EarningsContractError("evidence available_at is after decision_at")
        if available > available_cutoff:
            raise EarningsContractError("evidence available_at is after available_cutoff_at")
        record.update(
            source_snapshot_id=snapshot_id,
            observed_at=timestamp(observed, "evidence.observed_at"),
            available_at=timestamp(available, "evidence.available_at"),
            sha256=_hash(record["sha256"], "evidence.sha256"),
        )
        normalized.append(record)
    manifest["evidence"] = normalized
    return manifest, sha256_json(manifest)


def _validate_narrative(raw: Any) -> dict[str, Any]:
    value = _object(raw, "narrative")
    required = {"summary", "changed_since_previous", "supporting_evidence", "counter_evidence"}
    _strict_fields(value, required, set(), "narrative")
    value["summary"] = _safe_text(value["summary"], "narrative.summary", 4_000)
    for key in required - {"summary"}:
        if not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key]):
            raise EarningsContractError(f"narrative.{key} must be a string list")
    return value


def _validate_causal_graph(
    raw: Any,
    evidence_snapshot_ids: set[str],
) -> dict[str, Any]:
    value = _object(raw, "causal_graph")
    _strict_fields(value, {"claims"}, set(), "causal_graph")
    if not isinstance(value["claims"], list):
        raise EarningsContractError("causal_graph.claims must be a list")
    normalized: list[dict[str, Any]] = []
    for claim in value["claims"]:
        item = _object(claim, "causal claim")
        _strict_fields(
            item,
            {"kind", "claim", "confidence", "evidence_snapshot_ids", "confounders"},
            set(),
            "causal claim",
        )
        if item.get("kind") != "mechanism_hypothesis":
            raise EarningsContractError("causal claims must remain mechanism hypotheses")
        item["claim"] = _safe_text(item["claim"], "causal claim", 4_000)
        item["confidence"] = _number(
            item["confidence"], "causal claim confidence", minimum=0, maximum=1
        )
        references = item["evidence_snapshot_ids"]
        if not isinstance(references, list) or not references:
            raise EarningsContractError("causal claim evidence must be explicit")
        normalized_references = [
            _safe_id(reference, "causal claim evidence_snapshot_id")
            for reference in references
        ]
        if len(set(normalized_references)) != len(normalized_references):
            raise EarningsContractError("causal claim evidence must be unique")
        if not set(normalized_references) <= evidence_snapshot_ids:
            raise EarningsContractError("causal claim evidence is not in the sealed manifest")
        item["evidence_snapshot_ids"] = normalized_references
        if not isinstance(item.get("confounders"), list) or not all(
            isinstance(confounder, str) for confounder in item["confounders"]
        ):
            raise EarningsContractError("causal claim confounders must be explicit")
        item["confounders"] = [
            _safe_text(confounder, "causal claim confounder", 1_000)
            for confounder in item["confounders"]
        ]
        normalized.append(item)
    value["claims"] = normalized
    return value


def _validate_risk(raw: Any, currency: str) -> dict[str, Any]:
    value = _object(raw, "risk")
    required = {"defined_risk", "max_loss_amount", "currency", "invalidation_condition"}
    _strict_fields(value, required, set(), "risk")
    if value["defined_risk"] is not True:
        raise EarningsContractError("research path must have defined risk")
    value["max_loss_amount"] = _number(value["max_loss_amount"], "max_loss_amount", minimum=0)
    if value["currency"] != currency:
        raise EarningsContractError("risk currency must match forecast currency")
    value["invalidation_condition"] = _safe_text(
        value["invalidation_condition"], "invalidation_condition", 2_000
    )
    return value


def validate_forecast_snapshot(
    payload: Any, *, scheduled_at: Any, exchange_timezone: str
) -> dict[str, Any]:
    value = _object(payload, "forecast snapshot")
    required = {
        "event_revision_id", "countdown_day", "decision_at", "available_cutoff_at",
        "model_id", "model_version", "model_artifact_sha256", "input_manifest",
        "p_up", "p_down", "p_flat", "flat_band_pct", "confidence",
        "calibration_sample_size", "reference_price", "currency", "price_p10",
        "price_p50", "price_p90", "estimated_mfe_pct", "estimated_mae_pct",
        "simulated_action", "narrative", "causal_graph", "risk",
    }
    _strict_fields(value, required, {"recorded_at"}, "forecast snapshot")
    value["event_revision_id"] = _integer(value["event_revision_id"], "event_revision_id", minimum=1)
    value["countdown_day"] = _integer(value["countdown_day"], "countdown_day", minimum=1, maximum=7)
    decision = parse_timestamp(value["decision_at"], "decision_at")
    cutoff = parse_timestamp(value["available_cutoff_at"], "available_cutoff_at")
    scheduled = parse_timestamp(scheduled_at, "scheduled_at")
    recorded = parse_timestamp(value.get("recorded_at", decision), "recorded_at")
    if cutoff > decision or decision >= scheduled:
        raise EarningsContractError("forecast PIT ordering is invalid")
    if decision > recorded:
        raise EarningsContractError("recorded_at cannot predate decision_at")
    try:
        zone = ZoneInfo(exchange_timezone)
    except ZoneInfoNotFoundError as exc:
        raise EarningsContractError("exchange_timezone is invalid") from exc
    expected_day = (scheduled.astimezone(zone).date() - decision.astimezone(zone).date()).days
    if value["countdown_day"] != expected_day or expected_day not in range(1, 8):
        raise EarningsContractError("countdown_day does not match the immutable event revision")
    value["model_id"] = _safe_id(value["model_id"], "model_id")
    value["model_version"] = _safe_id(value["model_version"], "model_version")
    value["model_artifact_sha256"] = _hash(
        value["model_artifact_sha256"], "model_artifact_sha256"
    )
    manifest, manifest_hash = _validate_manifest(
        value["input_manifest"], decision, cutoff
    )
    probabilities = [
        _number(value[name], name, minimum=0, maximum=1) for name in ("p_up", "p_down", "p_flat")
    ]
    if not math.isclose(sum(probabilities), 1.0, abs_tol=1e-9):
        raise EarningsContractError("direction probabilities must sum to one")
    value.update(zip(("p_up", "p_down", "p_flat"), probabilities))
    value["flat_band_pct"] = _number(value["flat_band_pct"], "flat_band_pct", minimum=0)
    value["confidence"] = _number(value["confidence"], "confidence", minimum=0, maximum=1)
    value["calibration_sample_size"] = _integer(
        value["calibration_sample_size"], "calibration_sample_size"
    )
    value["reference_price"] = _number(value["reference_price"], "reference_price", minimum=0.000001)
    if value["currency"] not in {"USD", "CNY"}:
        raise EarningsContractError("currency is invalid")
    quantiles = [
        _number(value[name], name, minimum=0.000001) for name in ("price_p10", "price_p50", "price_p90")
    ]
    if quantiles != sorted(quantiles):
        raise EarningsContractError("price P10/P50/P90 order is invalid")
    value.update(zip(("price_p10", "price_p50", "price_p90"), quantiles))
    value["estimated_mfe_pct"] = _number(value["estimated_mfe_pct"], "estimated favorable move", minimum=0)
    value["estimated_mae_pct"] = _number(value["estimated_mae_pct"], "estimated adverse move", maximum=0)
    if value["simulated_action"] not in SIMULATED_ACTIONS:
        raise EarningsContractError("simulated_action is invalid or execution-capable")
    value["input_manifest"] = manifest
    value["input_manifest_sha256"] = manifest_hash
    value["narrative"] = _validate_narrative(value["narrative"])
    value["causal_graph"] = _validate_causal_graph(
        value["causal_graph"],
        {item["source_snapshot_id"] for item in manifest["evidence"]},
    )
    value["risk"] = _validate_risk(value["risk"], value["currency"])
    value["decision_at"] = timestamp(decision, "decision_at")
    value["available_cutoff_at"] = timestamp(cutoff, "available_cutoff_at")
    value["recorded_at"] = timestamp(recorded, "recorded_at")
    value.update(
        publication_state="research", research_only=True,
        execution_eligible=False, automatic_ordering=False,
    )
    value["payload_sha256"] = sha256_json(value)
    return value


def validate_outcome(payload: Any, *, scheduled_at: Any) -> dict[str, Any]:
    value = _object(payload, "outcome")
    required = {
        "event_revision_id", "checkpoint", "baseline_price", "observed_price", "return_pct",
        "mfe_pct", "mae_pct", "observed_at", "available_at", "recorded_at",
        "source_snapshot_id", "supersedes_outcome_id",
    }
    _strict_fields(value, required, set(), "outcome")
    value["event_revision_id"] = _integer(value["event_revision_id"], "event_revision_id", minimum=1)
    if value["checkpoint"] not in CHECKPOINTS:
        raise EarningsContractError("checkpoint is invalid")
    baseline = _number(value["baseline_price"], "baseline_price", minimum=0.000001)
    observed_price = _number(value["observed_price"], "observed_price", minimum=0.000001)
    actual_return = (observed_price / baseline - 1) * 100
    supplied_return = _number(value["return_pct"], "return_pct")
    if not math.isclose(actual_return, supplied_return, abs_tol=1e-8):
        raise EarningsContractError("return_pct does not match observed prices")
    observed = parse_timestamp(value["observed_at"], "observed_at")
    available = parse_timestamp(value["available_at"], "available_at")
    recorded = parse_timestamp(value["recorded_at"], "recorded_at")
    scheduled = parse_timestamp(scheduled_at, "scheduled_at")
    if not scheduled <= observed <= available <= recorded:
        raise EarningsContractError("outcome time ordering is invalid")
    mfe = _number(value["mfe_pct"], "mfe_pct", minimum=0)
    mae = _number(value["mae_pct"], "mae_pct", maximum=0)
    if not mae <= supplied_return <= mfe:
        raise EarningsContractError("outcome path extrema do not cover the endpoint return")
    value.update(
        baseline_price=baseline,
        observed_price=observed_price,
        return_pct=supplied_return,
        mfe_pct=mfe,
        mae_pct=mae,
        observed_at=timestamp(observed, "observed_at"),
        available_at=timestamp(available, "available_at"),
        recorded_at=timestamp(recorded, "recorded_at"),
        source_snapshot_id=_safe_id(value["source_snapshot_id"], "source_snapshot_id"),
    )
    if value["supersedes_outcome_id"] is not None:
        value["supersedes_outcome_id"] = _integer(
            value["supersedes_outcome_id"], "supersedes_outcome_id", minimum=1
        )
    value["payload_sha256"] = sha256_json(value)
    return value


def validate_postmortem(payload: Any) -> dict[str, Any]:
    value = _object(payload, "postmortem")
    required = {
        "event_revision_id", "model_id", "model_version", "stage", "completed_at",
        "forecast_snapshot_set_sha256", "outcome_set_sha256", "direction_correct",
        "interval_covered", "paper_performance", "analysis",
        "candidate_ref", "supersedes_postmortem_id",
    }
    _strict_fields(value, required, set(), "postmortem")
    value["event_revision_id"] = _integer(value["event_revision_id"], "event_revision_id", minimum=1)
    value["model_id"] = _safe_id(value["model_id"], "model_id")
    value["model_version"] = _safe_id(value["model_version"], "model_version")
    if value["stage"] not in POSTMORTEM_STAGES:
        raise EarningsContractError("postmortem stage is invalid")
    value["completed_at"] = timestamp(value["completed_at"], "completed_at")
    value["forecast_snapshot_set_sha256"] = _hash(
        value["forecast_snapshot_set_sha256"], "forecast_snapshot_set_sha256"
    )
    value["outcome_set_sha256"] = _hash(value["outcome_set_sha256"], "outcome_set_sha256")
    for name in ("direction_correct", "interval_covered"):
        if not isinstance(value[name], bool):
            raise EarningsContractError(f"{name} must be boolean")
    paper = _object(value["paper_performance"], "paper_performance")
    _strict_fields(
        paper,
        {"state", "pnl_net", "max_drawdown", "ledger_snapshot_sha256"},
        set(),
        "paper_performance",
    )
    if paper["state"] != "unavailable" or any(
        paper[name] is not None
        for name in ("pnl_net", "max_drawdown", "ledger_snapshot_sha256")
    ):
        raise EarningsContractError(
            "paper performance is unavailable without a sealed paper ledger"
        )
    value["paper_performance"] = paper
    analysis = _object(value["analysis"], "analysis")
    sections = {"correct", "incorrect", "error_categories", "lessons", "candidate_hypotheses"}
    _strict_fields(analysis, sections, set(), "analysis")
    for name in sections:
        if not isinstance(analysis[name], list) or not all(isinstance(item, str) for item in analysis[name]):
            raise EarningsContractError(f"analysis.{name} must be a string list")
    value["analysis"] = analysis
    if value["candidate_ref"] is not None:
        value["candidate_ref"] = _safe_id(value["candidate_ref"], "candidate_ref")
    if value["supersedes_postmortem_id"] is not None:
        value["supersedes_postmortem_id"] = _integer(
            value["supersedes_postmortem_id"], "supersedes_postmortem_id", minimum=1
        )
    value["publication_state"] = "research"
    value["payload_sha256"] = sha256_json(value)
    return value
