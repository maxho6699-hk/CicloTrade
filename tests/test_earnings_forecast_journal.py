from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace
from datetime import datetime

import pytest

from core.compat import UTC
from core.database import DatabaseManager
from core.earnings_forecast_contracts import (
    EarningsContractError,
    IdempotencyConflict,
    sha256_json,
)
from core.earnings_forecast_journal import EarningsForecastJournal
from core.earnings_option_research import OptionLegQuote, evaluate_defined_risk_structure
from src.apps.api.earnings_read_model import (
    EarningsForecastReadModel,
    EarningsResearchNotFound,
    OpaqueIdCodec,
)


def _event_payload() -> dict:
    return {
        "event_key": "US:AAPL:2026Q3",
        "revision_no": 1,
        "market": "US",
        "symbol": "AAPL",
        "fiscal_period": "2026Q3",
        "scheduled_at": "2026-08-18T16:15:00-04:00",
        "exchange_timezone": "America/New_York",
        "timing": "AMC",
        "status": "CONFIRMED",
        "source": "company-ir",
        "source_event_id": "aapl-2026q3",
        "observed_at": "2026-08-01T12:00:00Z",
        "available_at": "2026-08-01T12:01:00Z",
        "recorded_at": "2026-08-01T12:02:00Z",
        "supersedes_revision_id": None,
    }


def _forecast_payload(event_id: int) -> dict:
    return {
        "event_revision_id": event_id,
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
                    "source_snapshot_id": "aapl-event",
                    "observed_at": "2026-08-11T18:00:00Z",
                    "available_at": "2026-08-11T18:01:00Z",
                    "sha256": "b" * 64,
                }
            ],
        },
        "p_up": 0.5,
        "p_down": 0.3,
        "p_flat": 0.2,
        "flat_band_pct": 1.0,
        "confidence": 0.58,
        "calibration_sample_size": 200,
        "reference_price": 200.0,
        "currency": "USD",
        "price_p10": 180.0,
        "price_p50": 202.0,
        "price_p90": 228.0,
        "estimated_mfe_pct": 14.0,
        "estimated_mae_pct": -10.0,
        "simulated_action": "OBSERVE",
        "narrative": {
            "summary": "Research estimate only.",
            "changed_since_previous": [],
            "supporting_evidence": [],
            "counter_evidence": [],
        },
        "causal_graph": {"claims": []},
        "risk": {
            "defined_risk": True,
            "max_loss_amount": 0.0,
            "currency": "USD",
            "invalidation_condition": "Schedule changes.",
        },
    }


def _d1_forecast_payload(event_id: int) -> dict:
    payload = _forecast_payload(event_id)
    payload.update(
        countdown_day=1,
        decision_at="2026-08-17T20:00:00-04:00",
        available_cutoff_at="2026-08-17T19:59:00-04:00",
    )
    return payload


def _trusted_session_validator(**kwargs) -> dict:
    close_by_checkpoint = {
        "D3_CLOSE": "2026-08-21T20:00:00Z",
        "D5_CLOSE": "2026-08-25T20:00:00Z",
    }
    return {
        "checkpoint": kwargs["checkpoint"],
        "session_close_at": close_by_checkpoint[kwargs["checkpoint"]],
        "calendar_artifact_sha256": "e" * 64,
    }


def _postmortem_payload(journal, event: dict, forecast: dict, *, completed_at: str) -> dict:
    forecasts = journal.database.fetch_all(
        """SELECT payload_sha256 FROM earnings_forecast_snapshots
           WHERE event_revision_id=? AND model_id=? AND model_version=?
           ORDER BY countdown_day DESC,id""",
        (event["id"], forecast["model_id"], forecast["model_version"]),
    )
    outcomes = journal.database.fetch_all(
        """SELECT current.payload_sha256 FROM earnings_outcomes current
           WHERE current.event_revision_id=?
             AND NOT EXISTS (SELECT 1 FROM earnings_outcomes newer
                             WHERE newer.supersedes_outcome_id=current.id)
           ORDER BY CASE current.checkpoint
               WHEN 'AFTER_HOURS' THEN 1 WHEN 'NEXT_CLOSE' THEN 2
               WHEN 'D3_CLOSE' THEN 3 ELSE 4 END""",
        (event["id"],),
    )
    return {
        "event_revision_id": event["id"],
        "model_id": forecast["model_id"],
        "model_version": forecast["model_version"],
        "stage": "FINAL",
        "completed_at": completed_at,
        "forecast_snapshot_set_sha256": sha256_json(
            [row["payload_sha256"] for row in forecasts]
        ),
        "outcome_set_sha256": sha256_json(
            [row["payload_sha256"] for row in outcomes]
        ),
        "direction_correct": True,
        "interval_covered": True,
        "paper_performance": {
            "state": "unavailable",
            "pnl_net": None,
            "max_drawdown": None,
            "ledger_snapshot_sha256": None,
        },
        "analysis": {
            "correct": ["direction"],
            "incorrect": ["magnitude"],
            "error_categories": ["guidance"],
            "lessons": ["widen the tail interval"],
            "candidate_hypotheses": ["test guidance sensitivity"],
        },
        "candidate_ref": None,
        "supersedes_postmortem_id": None,
    }
@pytest.fixture
def journal(tmp_path) -> EarningsForecastJournal:
    return EarningsForecastJournal(
        DatabaseManager(str(tmp_path / "earnings.db")),
        session_validator=_trusted_session_validator,
    )


def test_event_and_forecast_are_idempotent_and_conflicts_fail_closed(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="event-aapl-q3-r1")
    assert journal.record_event_revision(
        _event_payload(), idempotency_key="event-aapl-q3-r1"
    )["id"] == event["id"]

    changed = _event_payload()
    changed["scheduled_at"] = "2026-08-18T16:30:00-04:00"
    with pytest.raises(IdempotencyConflict):
        journal.record_event_revision(changed, idempotency_key="event-aapl-q3-r1")

    forecast = journal.record_forecast(
        _forecast_payload(event["id"]), idempotency_key="forecast-aapl-d7-v1"
    )
    assert journal.record_forecast(
        _forecast_payload(event["id"]), idempotency_key="forecast-aapl-d7-v1"
    )["id"] == forecast["id"]

    changed_forecast = _forecast_payload(event["id"])
    changed_forecast["p_up"] = 0.6
    changed_forecast["p_down"] = 0.2
    with pytest.raises(IdempotencyConflict):
        journal.record_forecast(
            changed_forecast, idempotency_key="forecast-aapl-d7-v1"
        )
    with pytest.raises(EarningsContractError, match="idempotency_key"):
        journal.record_forecast(_forecast_payload(event["id"]), idempotency_key="")


def test_forecast_identity_allows_distinct_models_but_not_duplicate_model_revisions(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="model-identity-event")
    first = journal.record_forecast(
        _forecast_payload(event["id"]), idempotency_key="model-identity-first"
    )
    other_model = {
        **_forecast_payload(event["id"]),
        "model_id": "earnings-direction-alternate",
        "model_artifact_sha256": "d" * 64,
    }
    second = journal.record_forecast(other_model, idempotency_key="model-identity-second")

    assert second["id"] != first["id"]
    assert journal.database.fetch_one(
        "SELECT COUNT(*) count FROM earnings_forecast_snapshots WHERE event_revision_id=?",
        (event["id"],),
    )["count"] == 2

    duplicate_model = {
        **_forecast_payload(event["id"]),
        "p_up": 0.6,
        "p_down": 0.2,
    }
    with pytest.raises(IdempotencyConflict, match="sealed model snapshot"):
        journal.record_forecast(duplicate_model, idempotency_key="model-identity-duplicate")


def test_option_detail_hides_forecast_and_option_records_after_the_requested_point_in_time(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="option-pit-event")
    forecast = journal.record_forecast(
        {**_forecast_payload(event["id"]), "recorded_at": "2026-08-12T00:05:00Z"},
        idempotency_key="option-pit-forecast",
    )
    with journal.database.transaction() as connection:
        option_id = connection.execute(
            """INSERT INTO earnings_option_research_snapshots
               (idempotency_key,forecast_snapshot_id,structure_type,evidence_mode,
                historical_oos_validated,research_only,execution_eligible,automatic_ordering,
                contracts_json,total_premium,commission_cost,spread_cost,slippage_cost,max_loss,
                lower_breakeven,upper_breakeven,required_move_pct,model_expected_move_pct,
                iv_implied_move_pct,probability_outside_breakeven,expected_value_net_costs,
                one_leg_coverage_json,iv_crush_json,decision_at,recorded_at,payload_sha256)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "option-pit", forecast["id"], "LONG_CALL",
                "current_snapshot_research_estimate", 0, 1, 0, 0,
                '[{"quote_at":"2026-08-12T00:00:00Z"}]', 5.0, 0.0, 0.0, 0.0, 5.0,
                205.0, None, 2.5, 3.0, 2.0, 0.5, 0.0,
                '{"call_zero_coverage":false,"put_zero_coverage":true,"terminal_sample_size":100}',
                '[]', forecast["decision_at"], "2026-08-12T00:10:00Z", "c" * 64,
            ),
        ).lastrowid
    codec = OpaqueIdCodec(b"p" * 32)
    read_model = EarningsForecastReadModel(journal.database._db_path, codec)
    arguments = {
        "has_forecast_capability": True,
        "has_option_capability": True,
        "opaque_event_id": codec.encode("event", event["id"]),
        "opaque_option_id": codec.encode("option", option_id),
    }

    with pytest.raises(EarningsResearchNotFound):
        read_model.option_detail(**arguments, as_of="2026-08-12T00:00:00Z")
    with pytest.raises(EarningsResearchNotFound):
        read_model.option_detail(**arguments, as_of="2026-08-12T00:05:00Z")
    assert read_model.option_detail(**arguments, as_of="2026-08-12T00:10:00Z")["state"] == "research"


def test_database_triggers_make_the_complete_journal_append_only(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="event-immutable")
    forecast = journal.record_forecast(
        _forecast_payload(event["id"]), idempotency_key="forecast-immutable"
    )

    with journal.database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE earnings_forecast_snapshots SET confidence=0.1 WHERE id=?",
                (forecast["id"],),
            )
    with journal.database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM earnings_event_revisions WHERE id=?", (event["id"],)
            )


def test_reschedule_supersedes_event_without_rewriting_old_forecasts(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="event-r1")
    journal.record_forecast(_forecast_payload(event["id"]), idempotency_key="forecast-r1-d7")
    revision = {
        **_event_payload(),
        "revision_no": 2,
        "scheduled_at": "2026-08-19T16:15:00-04:00",
        "status": "RESCHEDULED",
        "observed_at": "2026-08-02T12:00:00Z",
        "available_at": "2026-08-02T12:01:00Z",
        "recorded_at": "2026-08-02T12:02:00Z",
        "supersedes_revision_id": event["id"],
    }
    current = journal.record_event_revision(revision, idempotency_key="event-r2")

    assert journal.current_event("US:AAPL:2026Q3")["id"] == current["id"]
    with pytest.raises(EarningsContractError, match="superseded"):
        changed = _forecast_payload(event["id"])
        changed["model_version"] = "1.0.1"
        journal.record_forecast(changed, idempotency_key="forecast-old-revision")
    stale_outcome = {
        "event_revision_id": event["id"], "checkpoint": "NEXT_CLOSE",
        "baseline_price": 200.0, "observed_price": 210.0, "return_pct": 5.0,
        "mfe_pct": 7.0, "mae_pct": -2.0,
        "observed_at": "2026-08-20T20:00:00Z",
        "available_at": "2026-08-20T20:01:00Z",
        "recorded_at": "2026-08-20T20:02:00Z",
        "source_snapshot_id": "stale-next-close", "supersedes_outcome_id": None,
    }
    with pytest.raises(EarningsContractError, match="superseded"):
        journal.record_outcome(stale_outcome, idempotency_key="outcome-old-revision")

    cancelled = {
        **revision,
        "revision_no": 3,
        "status": "CANCELLED",
        "observed_at": "2026-08-03T12:00:00Z",
        "available_at": "2026-08-03T12:01:00Z",
        "recorded_at": "2026-08-03T12:02:00Z",
        "supersedes_revision_id": current["id"],
    }
    cancelled = journal.record_event_revision(cancelled, idempotency_key="event-r3-cancel")
    stale_outcome["event_revision_id"] = cancelled["id"]
    with pytest.raises(EarningsContractError, match="cancelled"):
        journal.record_outcome(stale_outcome, idempotency_key="outcome-cancelled")


def test_event_revision_identity_and_chronology_are_immutable(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="identity-r1")
    poisoned = {
        **_event_payload(), "event_key": "US:MSFT:2026Q3", "symbol": "MSFT",
        "source_event_id": "msft-2026q3", "revision_no": 2,
        "status": "RESCHEDULED", "supersedes_revision_id": event["id"],
    }
    with pytest.raises(EarningsContractError, match="identity"):
        journal.record_event_revision(poisoned, idempotency_key="identity-poison")

    backdated = {
        **_event_payload(), "revision_no": 2, "status": "RESCHEDULED",
        "observed_at": "2026-07-01T12:00:00Z",
        "available_at": "2026-07-01T12:01:00Z",
        "recorded_at": "2026-07-01T12:02:00Z",
        "supersedes_revision_id": event["id"],
    }
    with pytest.raises(EarningsContractError, match="chronology"):
        journal.record_event_revision(backdated, idempotency_key="identity-backdated")
    assert event["journal_ingested_at"]
    assert len(event["journal_receipt_sha256"]) == 64


def test_defined_risk_multileg_option_snapshot_is_sealed_with_real_quote_times(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="event-options")
    forecast = journal.record_forecast(
        _forecast_payload(event["id"]), idempotency_key="forecast-options"
    )
    legs = [
        OptionLegQuote(
            contract_id=f"AAPL-{right}-200", right=right, strike=200.0,
            expiry="2026-08-21", quantity=1, multiplier=100, bid=4.8, ask=5.2,
            implied_volatility=0.48, delta=0.5 if right == "CALL" else -0.5,
            gamma=0.04, theta=-0.18, vega=0.22, volume=500, open_interest=2_000,
            quote_at="2026-08-11T23:58:00Z", available_at="2026-08-11T23:58:02Z",
        )
        for right in ("CALL", "PUT")
    ]
    result = evaluate_defined_risk_structure(
        structure_type="LONG_STRADDLE",
        spot=200.0,
        decision_at=datetime(2026, 8, 12, 0, 0, tzinfo=UTC),
        legs=legs,
        terminal_price_samples=[160.0 + index * 0.1 for index in range(801)],
        commission_per_contract=0.65,
        slippage_per_contract=0.35,
        model_expected_move_pct=12.0,
    )

    stored = journal.record_option_research(
        forecast["id"], result, idempotency_key="options-straddle-v1"
    )
    assert stored["historical_oos_validated"] == 0
    assert stored["execution_eligible"] == 0
    assert stored["automatic_ordering"] == 0
    assert stored["recorded_at"] >= forecast["recorded_at"]
    assert journal.record_option_research(
        forecast["id"], result, idempotency_key="options-straddle-v1"
    )["id"] == stored["id"]
    with journal.database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE earnings_option_research_snapshots SET max_loss=1 WHERE id=?",
                (stored["id"],),
            )

    forged = asdict(result)
    forged["expected_value_net_costs"] = 999_999.0
    with pytest.raises(EarningsContractError, match="DefinedRiskOptionResult"):
        journal.record_option_research(
            forecast["id"], forged, idempotency_key="options-forged-mapping"
        )
    forged_result = replace(result, expected_value_net_costs=999_999.0)
    with pytest.raises(EarningsContractError, match="evaluator seal"):
        journal.record_option_research(
            forecast["id"], forged_result, idempotency_key="options-forged-result"
        )


def test_outcome_corrections_and_final_postmortem_are_append_only(journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="event-outcome")
    forecast = journal.record_forecast(
        _d1_forecast_payload(event["id"]), idempotency_key="forecast-outcome"
    )
    first = journal.record_outcome(
        {
            "event_revision_id": event["id"],
            "checkpoint": "NEXT_CLOSE",
            "baseline_price": 200.0,
            "observed_price": 210.0,
            "return_pct": 5.0,
            "mfe_pct": 7.0,
            "mae_pct": -2.0,
            "observed_at": "2026-08-19T20:00:00Z",
            "available_at": "2026-08-19T20:01:00Z",
            "recorded_at": "2026-08-19T20:02:00Z",
            "source_snapshot_id": "aapl-next-close-v1",
            "supersedes_outcome_id": None,
        },
        idempotency_key="outcome-next-v1",
    )
    with pytest.raises(IdempotencyConflict, match="active outcome"):
        journal.record_outcome(
            {
                **{key: value for key, value in first.items() if key in {
                    "event_revision_id", "checkpoint", "baseline_price", "observed_price",
                    "return_pct", "mfe_pct", "mae_pct", "observed_at", "available_at",
                    "recorded_at", "source_snapshot_id", "supersedes_outcome_id",
                }},
                "source_snapshot_id": "aapl-next-close-v2",
            },
            idempotency_key="outcome-next-unlinked",
        )

    corrected = journal.record_outcome(
        {
            "event_revision_id": event["id"],
            "checkpoint": "NEXT_CLOSE",
            "baseline_price": 200.0,
            "observed_price": 211.0,
            "return_pct": 5.5,
            "mfe_pct": 7.0,
            "mae_pct": -2.0,
            "observed_at": "2026-08-19T20:00:00Z",
            "available_at": "2026-08-19T20:05:00Z",
            "recorded_at": "2026-08-19T20:06:00Z",
            "source_snapshot_id": "aapl-next-close-v2",
            "supersedes_outcome_id": first["id"],
        },
        idempotency_key="outcome-next-v2",
    )
    assert journal.current_outcomes(event["id"])[0]["id"] == corrected["id"]

    with pytest.raises(IdempotencyConflict, match="D5_CLOSE"):
        journal.record_postmortem(
            _postmortem_payload(
                journal, event, forecast, completed_at="2026-08-25T20:00:00Z"
            ),
            idempotency_key="postmortem-final-too-early",
        )

    journal.record_outcome(
        {
            "event_revision_id": event["id"],
            "checkpoint": "D5_CLOSE",
            "baseline_price": 200.0,
            "observed_price": 220.0,
            "return_pct": 10.0,
            "mfe_pct": 12.0,
            "mae_pct": -3.0,
            "observed_at": "2026-08-25T20:00:00Z",
            "available_at": "2026-08-25T20:01:00Z",
            "recorded_at": "2026-08-25T20:02:00Z",
            "source_snapshot_id": "aapl-d5-close-v1",
            "supersedes_outcome_id": None,
        },
        idempotency_key="outcome-d5-v1",
    )
    postmortem_payload = _postmortem_payload(
        journal, event, forecast, completed_at="2026-08-25T20:03:00Z"
    )
    postmortem = journal.record_postmortem(
        postmortem_payload, idempotency_key="postmortem-final-v1"
    )
    assert journal.record_postmortem(
        postmortem_payload, idempotency_key="postmortem-final-v1"
    )["id"] == postmortem["id"]
    with journal.database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM earnings_outcomes WHERE id=?", (corrected["id"],)
            )
    with journal.database.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE earnings_postmortems SET paper_pnl_net=0 WHERE id=?",
                (postmortem["id"],),
            )

    forged = {**postmortem_payload, "paper_performance": {
        "state": "unavailable", "pnl_net": 999_999.0,
        "max_drawdown": None, "ledger_snapshot_sha256": None,
    }}
    with pytest.raises(EarningsContractError, match="paper performance"):
        journal.record_postmortem(forged, idempotency_key="postmortem-forged-pnl")
    forged = {**postmortem_payload, "forecast_snapshot_set_sha256": "f" * 64}
    with pytest.raises(EarningsContractError, match="forecast_snapshot_set_sha256"):
        journal.record_postmortem(forged, idempotency_key="postmortem-forged-hash")
    forged = {**postmortem_payload, "model_id": "invented-model"}
    with pytest.raises(EarningsContractError, match="no sealed forecasts"):
        journal.record_postmortem(forged, idempotency_key="postmortem-forged-model")


def test_d5_outcome_requires_a_trusted_matching_session_receipt(tmp_path, journal):
    event = journal.record_event_revision(_event_payload(), idempotency_key="session-event")
    too_early = {
        "event_revision_id": event["id"], "checkpoint": "D5_CLOSE",
        "baseline_price": 200.0, "observed_price": 201.0, "return_pct": 0.5,
        "mfe_pct": 1.0, "mae_pct": -0.5,
        "observed_at": "2026-08-18T20:15:00Z",
        "available_at": "2026-08-18T20:15:01Z",
        "recorded_at": "2026-08-18T20:15:02Z",
        "source_snapshot_id": "fake-d5", "supersedes_outcome_id": None,
    }
    with pytest.raises(EarningsContractError, match="session close"):
        journal.record_outcome(too_early, idempotency_key="fake-instant-d5")

    no_calendar = EarningsForecastJournal(
        DatabaseManager(str(tmp_path / "no-calendar.db"))
    )
    event = no_calendar.record_event_revision(
        _event_payload(), idempotency_key="no-calendar-event"
    )
    too_early.update(
        event_revision_id=event["id"],
        observed_at="2026-08-25T20:00:00Z",
        available_at="2026-08-25T20:01:00Z",
        recorded_at="2026-08-25T20:02:00Z",
    )
    with pytest.raises(EarningsContractError, match="validation is unavailable"):
        no_calendar.record_outcome(too_early, idempotency_key="d5-no-calendar")
