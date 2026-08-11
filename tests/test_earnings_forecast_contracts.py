from __future__ import annotations

from copy import deepcopy

import pytest

from core.earnings_forecast_contracts import (
    EarningsContractError,
    validate_forecast_snapshot,
)


def _forecast_payload() -> dict:
    return {
        "event_revision_id": 1,
        "countdown_day": 7,
        "decision_at": "2026-08-11T20:00:00-04:00",
        "available_cutoff_at": "2026-08-11T19:59:00-04:00",
        "model_id": "earnings-direction",
        "model_version": "1.0.0",
        "model_artifact_sha256": "a" * 64,
        "input_manifest": {
            "schema_version": 1,
            "historical_backfill": False,
            "evidence": [
                {
                    "source": "company-ir",
                    "source_snapshot_id": "ir-aapl-2026q3",
                    "observed_at": "2026-08-11T18:00:00Z",
                    "available_at": "2026-08-11T18:01:00Z",
                    "sha256": "b" * 64,
                },
                {
                    "source": "market-bars",
                    "source_snapshot_id": "aapl-bars-20260811",
                    "observed_at": "2026-08-11T19:55:00Z",
                    "available_at": "2026-08-11T19:56:00Z",
                    "sha256": "c" * 64,
                },
            ],
        },
        "p_up": 0.52,
        "p_down": 0.31,
        "p_flat": 0.17,
        "flat_band_pct": 1.0,
        "confidence": 0.61,
        "calibration_sample_size": 243,
        "reference_price": 200.0,
        "currency": "USD",
        "price_p10": 180.0,
        "price_p50": 204.0,
        "price_p90": 232.0,
        "estimated_mfe_pct": 16.0,
        "estimated_mae_pct": -10.0,
        "simulated_action": "OBSERVE",
        "narrative": {
            "summary": "Revenue revisions improved, but valuation and event risk remain high.",
            "changed_since_previous": [],
            "supporting_evidence": ["estimate revisions"],
            "counter_evidence": ["elevated valuation"],
        },
        "causal_graph": {
            "claims": [
                {
                    "kind": "mechanism_hypothesis",
                    "claim": "Estimate revisions may support the earnings reaction.",
                    "confidence": 0.55,
                    "evidence_snapshot_ids": ["ir-aapl-2026q3"],
                    "confounders": ["macro shock"],
                }
            ]
        },
        "risk": {
            "defined_risk": True,
            "max_loss_amount": 0.0,
            "currency": "USD",
            "invalidation_condition": "The confirmed earnings time changes.",
        },
    }


def test_forecast_contract_normalizes_times_and_hashes_payload():
    value = validate_forecast_snapshot(
        _forecast_payload(),
        scheduled_at="2026-08-18T16:15:00-04:00",
        exchange_timezone="America/New_York",
    )

    assert value["decision_at"] == "2026-08-12T00:00:00Z"
    assert value["publication_state"] == "research"
    assert value["research_only"] is True
    assert value["execution_eligible"] is False
    assert value["automatic_ordering"] is False
    assert len(value["payload_sha256"]) == 64
    assert len(value["input_manifest_sha256"]) == 64


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(p_up=0.9), "sum to one"),
        (lambda value: value.update(price_p10=240.0), "P10"),
        (lambda value: value.update(estimated_mae_pct=2.0), "adverse"),
        (lambda value: value.update(simulated_action="LIVE_BUY"), "simulated_action"),
        (
            lambda value: value["input_manifest"]["evidence"][0].update(
                available_at="2026-08-12T00:01:00Z"
            ),
            "after decision_at",
        ),
        (
            lambda value: value.update(publication_state="official"),
            "unknown fields",
        ),
    ],
)
def test_forecast_contract_rejects_unsafe_or_lookahead_payloads(mutate, message):
    value = deepcopy(_forecast_payload())
    mutate(value)

    with pytest.raises(EarningsContractError, match=message):
        validate_forecast_snapshot(
            value,
            scheduled_at="2026-08-18T16:15:00-04:00",
            exchange_timezone="America/New_York",
        )


def test_forecast_contract_rejects_relabelled_countdown_day():
    value = _forecast_payload()
    value["countdown_day"] = 6

    with pytest.raises(EarningsContractError, match="countdown_day"):
        validate_forecast_snapshot(
            value,
            scheduled_at="2026-08-18T16:15:00-04:00",
            exchange_timezone="America/New_York",
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["causal_graph"]["claims"][0].pop("claim"),
            "fields",
        ),
        (
            lambda value: value["causal_graph"]["claims"][0].update(
                evidence_snapshot_ids=[]
            ),
            "evidence must be explicit",
        ),
        (
            lambda value: value["causal_graph"]["claims"][0].update(
                evidence_snapshot_ids=["not-in-manifest"]
            ),
            "sealed manifest",
        ),
    ],
)
def test_causal_claims_bind_text_and_evidence_to_the_sealed_manifest(mutate, message):
    value = deepcopy(_forecast_payload())
    mutate(value)
    with pytest.raises(EarningsContractError, match=message):
        validate_forecast_snapshot(
            value,
            scheduled_at="2026-08-18T16:15:00-04:00",
            exchange_timezone="America/New_York",
        )


def test_forecast_contract_binds_evidence_cutoff_and_seal_time():
    after_cutoff = _forecast_payload()
    after_cutoff["input_manifest"]["evidence"][1]["available_at"] = (
        "2026-08-11T23:59:30Z"
    )
    with pytest.raises(EarningsContractError, match="available_cutoff_at"):
        validate_forecast_snapshot(
            after_cutoff,
            scheduled_at="2026-08-18T16:15:00-04:00",
            exchange_timezone="America/New_York",
        )

    backdated = _forecast_payload()
    backdated["recorded_at"] = "2026-08-11T23:59:59Z"
    with pytest.raises(EarningsContractError, match="predate decision_at"):
        validate_forecast_snapshot(
            backdated,
            scheduled_at="2026-08-18T16:15:00-04:00",
            exchange_timezone="America/New_York",
        )


def test_event_key_cannot_disagree_with_symbol_or_fiscal_period():
    from core.earnings_forecast_contracts import validate_event_revision

    payload = {
        "event_key": "US:AAPL:2026Q3", "revision_no": 1, "market": "US",
        "symbol": "MSFT", "fiscal_period": "2027Q1",
        "scheduled_at": "2026-08-18T20:15:00Z",
        "exchange_timezone": "America/New_York", "timing": "AMC",
        "status": "CONFIRMED", "source": "company-ir",
        "source_event_id": "aapl-2026q3", "observed_at": "2026-08-01T12:00:00Z",
        "available_at": "2026-08-01T12:01:00Z",
        "recorded_at": "2026-08-01T12:02:00Z", "supersedes_revision_id": None,
    }
    with pytest.raises(EarningsContractError, match="event_key"):
        validate_event_revision(payload)


def test_outcome_extrema_must_cover_the_endpoint_return():
    from core.earnings_forecast_contracts import validate_outcome

    payload = {
        "event_revision_id": 1, "checkpoint": "NEXT_CLOSE",
        "baseline_price": 100.0, "observed_price": 110.0, "return_pct": 10.0,
        "mfe_pct": 0.0, "mae_pct": 0.0,
        "observed_at": "2026-08-19T20:00:00Z",
        "available_at": "2026-08-19T20:01:00Z",
        "recorded_at": "2026-08-19T20:02:00Z",
        "source_snapshot_id": "bars-next-close", "supersedes_outcome_id": None,
    }
    with pytest.raises(EarningsContractError, match="extrema"):
        validate_outcome(payload, scheduled_at="2026-08-18T20:15:00Z")
