"""Cross-row integrity checks for the immutable earnings journal."""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping

from core.earnings_forecast_contracts import (
    EarningsContractError,
    IdempotencyConflict,
    parse_timestamp,
    sha256_json,
    timestamp,
)


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise EarningsContractError(f"{label} must be a lowercase SHA-256")
    return value


def validate_idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or "\x00" in value:
        raise EarningsContractError("idempotency_key is invalid")
    return value


def active_event(connection, event_revision_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM earnings_event_revisions WHERE id=?", (event_revision_id,)
    ).fetchone()
    if row is None:
        raise EarningsContractError("earnings event revision does not exist")
    event = dict(row)
    if event["status"] == "CANCELLED":
        raise EarningsContractError("cancelled events cannot receive research results")
    if connection.execute(
        "SELECT 1 FROM earnings_event_revisions WHERE supersedes_revision_id=?",
        (event_revision_id,),
    ).fetchone():
        raise EarningsContractError("superseded events cannot receive research results")
    return event


def trusted_session_receipt(
    validator: Callable[..., Mapping[str, Any]] | None,
    event: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> dict[str, str | None]:
    if outcome["checkpoint"] not in {"D3_CLOSE", "D5_CLOSE"}:
        return {
            "session_close_at": None,
            "calendar_artifact_sha256": None,
            "session_validation_receipt_sha256": None,
        }
    if validator is None:
        raise EarningsContractError("trusted market session validation is unavailable")
    observed = parse_timestamp(outcome["observed_at"], "observed_at")
    available = parse_timestamp(outcome["available_at"], "available_at")
    try:
        supplied = validator(
            event=dict(event),
            checkpoint=str(outcome["checkpoint"]),
            observed_at=observed,
            available_at=available,
        )
    except EarningsContractError:
        raise
    except Exception as exc:
        raise EarningsContractError("trusted market session validation failed") from exc
    if not isinstance(supplied, Mapping) or set(supplied) != {
        "checkpoint", "session_close_at", "calendar_artifact_sha256"
    }:
        raise EarningsContractError("trusted market session receipt is invalid")
    if supplied["checkpoint"] != outcome["checkpoint"]:
        raise EarningsContractError("trusted market session checkpoint mismatch")
    close = parse_timestamp(supplied["session_close_at"], "session_close_at")
    if observed < close or available < close:
        raise EarningsContractError("outcome predates the trusted market session close")
    artifact_hash = _sha256(
        supplied["calendar_artifact_sha256"], "calendar_artifact_sha256"
    )
    normalized = {
        "event_revision_id": int(event["id"]),
        "market": str(event["market"]),
        "exchange_timezone": str(event["exchange_timezone"]),
        "checkpoint": str(outcome["checkpoint"]),
        "session_close_at": timestamp(close, "session_close_at"),
        "observed_at": timestamp(observed, "observed_at"),
        "available_at": timestamp(available, "available_at"),
        "calendar_artifact_sha256": artifact_hash,
    }
    return {
        "session_close_at": normalized["session_close_at"],
        "calendar_artifact_sha256": artifact_hash,
        "session_validation_receipt_sha256": sha256_json(normalized),
    }


def postmortem_binding(connection, value: Mapping[str, Any]) -> dict[str, Any]:
    forecasts = connection.execute(
        """SELECT * FROM earnings_forecast_snapshots
           WHERE event_revision_id=? AND model_id=? AND model_version=?
           ORDER BY countdown_day DESC,id""",
        (value["event_revision_id"], value["model_id"], value["model_version"]),
    ).fetchall()
    if not forecasts:
        raise EarningsContractError("postmortem model has no sealed forecasts")
    headline = next((row for row in forecasts if int(row["countdown_day"]) == 1), None)
    if headline is None:
        raise EarningsContractError("postmortem requires a sealed D-1 forecast")
    outcomes = connection.execute(
        """SELECT current.* FROM earnings_outcomes current
           WHERE current.event_revision_id=?
             AND NOT EXISTS (SELECT 1 FROM earnings_outcomes newer
                             WHERE newer.supersedes_outcome_id=current.id)
           ORDER BY CASE current.checkpoint
               WHEN 'AFTER_HOURS' THEN 1 WHEN 'NEXT_CLOSE' THEN 2
               WHEN 'D3_CLOSE' THEN 3 ELSE 4 END""",
        (value["event_revision_id"],),
    ).fetchall()
    if not outcomes:
        raise IdempotencyConflict("postmortem requires at least one recorded outcome")
    next_close = next((row for row in outcomes if row["checkpoint"] == "NEXT_CLOSE"), None)
    if next_close is None:
        raise EarningsContractError("postmortem requires a NEXT_CLOSE outcome")
    probabilities = (
        float(headline["p_up"]), float(headline["p_down"]), float(headline["p_flat"])
    )
    maximum = max(probabilities)
    tied = [
        index for index, probability in enumerate(probabilities)
        if math.isclose(probability, maximum)
    ]
    predicted = 2 if 2 in tied else tied[0]
    actual_return, flat_band = (
        float(next_close["return_pct"]), float(headline["flat_band_pct"])
    )
    actual = 0 if actual_return > flat_band else 1 if actual_return < -flat_band else 2
    return {
        "outcomes": outcomes,
        "forecast_snapshot_set_sha256": sha256_json(
            [str(row["payload_sha256"]) for row in forecasts]
        ),
        "outcome_set_sha256": sha256_json(
            [str(row["payload_sha256"]) for row in outcomes]
        ),
        "direction_correct": predicted == actual,
        "interval_covered": (
            float(headline["price_p10"])
            <= float(next_close["observed_price"])
            <= float(headline["price_p90"])
        ),
        # No sealed paper execution ledger is bound in the current release.
        "paper_performance": {
            "state": "unavailable",
            "pnl_net": None,
            "max_drawdown": None,
            "ledger_snapshot_sha256": None,
        },
    }
