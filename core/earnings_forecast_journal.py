"""Persistence service for immutable earnings research evidence."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Callable, Mapping

from core.compat import UTC
from core.database import DatabaseManager
from core.earnings_forecast_contracts import (
    EarningsContractError,
    IdempotencyConflict,
    canonical_json,
    parse_timestamp,
    sha256_json,
    timestamp,
    validate_event_revision,
    validate_forecast_snapshot,
    validate_outcome,
    validate_postmortem,
)
from core.earnings_forecast_integrity import (
    active_event, postmortem_binding, trusted_session_receipt, validate_idempotency_key,
)
from core.earnings_option_research import OptionLegQuote, verify_defined_risk_result


TABLES = dict(
    event="earnings_event_revisions", forecast="earnings_forecast_snapshots",
    option="earnings_option_research_snapshots", outcome="earnings_outcomes",
    postmortem="earnings_postmortems",
)


class EarningsForecastJournal:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        session_validator: Callable[..., Mapping[str, Any]] | None = None,
    ):
        if not isinstance(database, DatabaseManager):
            raise TypeError("database must be a DatabaseManager")
        self.database = database
        self._session_validator = session_validator

    @staticmethod
    def _idempotent_row(connection, table: str, key: str, payload_hash: str):
        row = connection.execute(
            f"SELECT * FROM {table} WHERE idempotency_key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_sha256"]) != payload_hash:
            raise IdempotencyConflict("idempotency key was reused with different content")
        return dict(row)

    @staticmethod
    def _event(connection, event_revision_id: int) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM earnings_event_revisions WHERE id=?", (event_revision_id,)
        ).fetchone()
        if row is None:
            raise EarningsContractError("earnings event revision does not exist")
        return dict(row)

    def record_event_revision(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        value = validate_event_revision(payload)
        validate_idempotency_key(idempotency_key)
        with self.database.transaction() as connection:
            existing = self._idempotent_row(
                connection, TABLES["event"], idempotency_key, value["payload_sha256"]
            )
            if existing:
                return existing
            if value["revision_no"] == 1:
                if value["supersedes_revision_id"] is not None or value["status"] != "CONFIRMED":
                    raise EarningsContractError("the first event revision must be confirmed and root")
            else:
                previous_id = value["supersedes_revision_id"]
                if previous_id is None:
                    raise EarningsContractError("later event revisions must supersede the active revision")
                previous = self._event(connection, previous_id)
                immutable_identity = (
                    "event_key", "market", "symbol", "fiscal_period",
                    "exchange_timezone", "source", "source_event_id",
                )
                if any(previous[name] != value[name] for name in immutable_identity):
                    raise EarningsContractError("event revision identity cannot change")
                if int(previous["revision_no"]) + 1 != value["revision_no"]:
                    raise EarningsContractError("event revision numbers must be contiguous")
                if parse_timestamp(value["available_at"], "available_at") < parse_timestamp(
                    previous["available_at"], "previous available_at"
                ) or parse_timestamp(value["recorded_at"], "recorded_at") < parse_timestamp(
                    previous["recorded_at"], "previous recorded_at"
                ):
                    raise EarningsContractError("event revision chronology cannot regress")
                successor = connection.execute(
                    "SELECT id FROM earnings_event_revisions WHERE supersedes_revision_id=?",
                    (previous_id,),
                ).fetchone()
                if successor:
                    raise IdempotencyConflict("the previous event revision is already superseded")
            duplicate = connection.execute(
                "SELECT payload_sha256 FROM earnings_event_revisions "
                "WHERE event_key=? AND revision_no=?",
                (value["event_key"], value["revision_no"]),
            ).fetchone()
            if duplicate:
                raise IdempotencyConflict("event revision identity already has different content")
            journal_ingested_at = timestamp(datetime.now(UTC), "journal_ingested_at")
            if value["supersedes_revision_id"] is not None:
                previous_ingested = parse_timestamp(
                    previous["journal_ingested_at"], "previous journal_ingested_at"
                )
                if parse_timestamp(journal_ingested_at, "journal_ingested_at") < previous_ingested:
                    raise EarningsContractError("event journal ingestion time cannot regress")
            journal_receipt_sha256 = sha256_json({
                "payload_sha256": value["payload_sha256"],
                "journal_ingested_at": journal_ingested_at,
            })
            cursor = connection.execute(
                """INSERT INTO earnings_event_revisions
                   (idempotency_key,event_key,revision_no,market,symbol,fiscal_period,
                    scheduled_at,exchange_timezone,timing,status,source,source_event_id,
                    observed_at,available_at,recorded_at,journal_ingested_at,
                    journal_receipt_sha256,supersedes_revision_id,payload_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key, value["event_key"], value["revision_no"], value["market"],
                    value["symbol"], value["fiscal_period"], value["scheduled_at"],
                    value["exchange_timezone"], value["timing"], value["status"],
                    value["source"], value["source_event_id"], value["observed_at"],
                    value["available_at"], value["recorded_at"], journal_ingested_at,
                    journal_receipt_sha256,
                    value["supersedes_revision_id"], value["payload_sha256"],
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM earnings_event_revisions WHERE id=?", (cursor.lastrowid,)
            ).fetchone())

    def current_event(self, event_key: str) -> dict[str, Any] | None:
        row = self.database.fetch_one(
            """SELECT e.* FROM earnings_event_revisions e
               WHERE e.event_key=? AND NOT EXISTS (
                   SELECT 1 FROM earnings_event_revisions newer
                   WHERE newer.supersedes_revision_id=e.id
               ) ORDER BY e.revision_no DESC,e.id DESC LIMIT 1""",
            (event_key,),
        )
        return row

    def record_forecast(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        validate_idempotency_key(idempotency_key)
        event_id = payload.get("event_revision_id") if isinstance(payload, Mapping) else None
        if isinstance(event_id, bool) or not isinstance(event_id, int):
            raise EarningsContractError("event_revision_id must be an integer")
        event = self.database.fetch_one(
            "SELECT * FROM earnings_event_revisions WHERE id=?", (event_id,)
        )
        if event is None:
            raise EarningsContractError("earnings event revision does not exist")
        value = validate_forecast_snapshot(
            payload,
            scheduled_at=event["scheduled_at"],
            exchange_timezone=event["exchange_timezone"],
        )
        logical_run_key = sha256_json({
            "event_revision_id": value["event_revision_id"],
            "countdown_day": value["countdown_day"],
            "model_version": value["model_version"],
            "input_manifest_sha256": value["input_manifest_sha256"],
        })
        with self.database.transaction() as connection:
            existing = self._idempotent_row(
                connection, TABLES["forecast"], idempotency_key, value["payload_sha256"]
            )
            if existing:
                return existing
            event = self._event(connection, value["event_revision_id"])
            if event["status"] == "CANCELLED":
                raise EarningsContractError("cancelled events cannot receive forecasts")
            if connection.execute(
                "SELECT 1 FROM earnings_event_revisions WHERE supersedes_revision_id=?",
                (event["id"],),
            ).fetchone():
                raise EarningsContractError("superseded events cannot receive new forecasts")
            duplicate = connection.execute(
                """SELECT payload_sha256 FROM earnings_forecast_snapshots
                   WHERE event_revision_id=? AND countdown_day=? AND model_version=?""",
                (value["event_revision_id"], value["countdown_day"], value["model_version"]),
            ).fetchone()
            if duplicate:
                raise IdempotencyConflict("forecast day already has a sealed model snapshot")
            cursor = connection.execute(
                """INSERT INTO earnings_forecast_snapshots
                   (idempotency_key,logical_run_key,event_revision_id,countdown_day,decision_at,
                    available_cutoff_at,recorded_at,model_id,model_version,model_artifact_sha256,
                    input_manifest_json,input_manifest_sha256,p_up,p_down,p_flat,flat_band_pct,
                    confidence,calibration_sample_size,reference_price,currency,price_p10,
                    price_p50,price_p90,estimated_mfe_pct,estimated_mae_pct,simulated_action,
                    narrative_json,causal_graph_json,risk_json,publication_state,research_only,
                    execution_eligible,automatic_ordering,payload_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key, logical_run_key, value["event_revision_id"],
                    value["countdown_day"], value["decision_at"], value["available_cutoff_at"],
                    value["recorded_at"], value["model_id"], value["model_version"],
                    value["model_artifact_sha256"], canonical_json(value["input_manifest"]),
                    value["input_manifest_sha256"], value["p_up"], value["p_down"],
                    value["p_flat"], value["flat_band_pct"], value["confidence"],
                    value["calibration_sample_size"], value["reference_price"], value["currency"],
                    value["price_p10"], value["price_p50"], value["price_p90"],
                    value["estimated_mfe_pct"], value["estimated_mae_pct"],
                    value["simulated_action"], canonical_json(value["narrative"]),
                    canonical_json(value["causal_graph"]), canonical_json(value["risk"]),
                    "research", 1, 0, 0, value["payload_sha256"],
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM earnings_forecast_snapshots WHERE id=?", (cursor.lastrowid,)
            ).fetchone())

    def record_option_research(
        self, forecast_snapshot_id: int, result: Any, *, idempotency_key: str
    ) -> dict[str, Any]:
        validate_idempotency_key(idempotency_key)
        value = verify_defined_risk_result(result)
        required = {
            "structure_type", "evidence_mode", "historical_oos_validated", "research_only",
            "execution_eligible", "automatic_ordering", "contracts", "total_premium",
            "commission_cost", "spread_cost", "slippage_cost", "max_loss", "lower_breakeven",
            "upper_breakeven", "required_move_pct", "model_expected_move_pct",
            "iv_implied_move_pct", "probability_outside_breakeven",
            "expected_value_net_costs", "call_zero_coverage", "put_zero_coverage",
            "iv_crush_scenarios", "terminal_sample_size", "decision_at", "payload_sha256",
        }
        if set(value) != required:
            raise EarningsContractError("option research fields are invalid")
        if (
            value["evidence_mode"] != "current_snapshot_research_estimate"
            or value["historical_oos_validated"] is not False
            or value["research_only"] is not True
            or value["execution_eligible"] is not False
            or value["automatic_ordering"] is not False
        ):
            raise EarningsContractError("option research cannot claim OOS, official, or live authority")
        if not result.contracts or not all(
            type(contract) is OptionLegQuote for contract in result.contracts
        ):
            raise EarningsContractError("option research contracts are invalid")
        rights = [contract.right for contract in result.contracts]
        if any(
            contract.quantity <= 0
            or contract.multiplier <= 0
            or contract.ask <= 0
            or contract.bid < 0
            or contract.ask < contract.bid
            for contract in result.contracts
        ):
            raise EarningsContractError("option research requires positive long contracts")
        if result.structure_type == "LONG_CALL" and rights != ["CALL"]:
            raise EarningsContractError("LONG_CALL requires exactly one purchased call")
        if result.structure_type == "LONG_PUT" and rights != ["PUT"]:
            raise EarningsContractError("LONG_PUT requires exactly one purchased put")
        if result.structure_type in {"LONG_STRADDLE", "LONG_STRANGLE"} and sorted(rights) != [
            "CALL", "PUT"
        ]:
            raise EarningsContractError("two-leg research requires one call and one put")
        if result.terminal_sample_size < 100:
            raise EarningsContractError("option terminal distribution is too small")
        expected_max_loss = (
            result.total_premium + result.commission_cost + result.slippage_cost
        )
        if not math.isclose(result.max_loss, expected_max_loss, abs_tol=1e-9):
            raise EarningsContractError("option max_loss is inconsistent with sealed costs")
        if not 0 <= result.probability_outside_breakeven <= 1:
            raise EarningsContractError("option probability is invalid")
        with self.database.transaction() as connection:
            forecast = connection.execute(
                "SELECT * FROM earnings_forecast_snapshots WHERE id=?", (forecast_snapshot_id,)
            ).fetchone()
            if forecast is None:
                raise EarningsContractError("forecast snapshot does not exist")
            if parse_timestamp(value["decision_at"], "decision_at") != parse_timestamp(
                forecast["decision_at"], "forecast decision_at"
            ):
                raise EarningsContractError("option research must bind the forecast decision_at")
            payload_hash = str(value["payload_sha256"])
            existing = self._idempotent_row(
                connection, TABLES["option"], idempotency_key, payload_hash
            )
            if existing:
                return existing
            if connection.execute(
                "SELECT 1 FROM earnings_option_research_snapshots "
                "WHERE forecast_snapshot_id=? AND structure_type=?",
                (forecast_snapshot_id, value["structure_type"]),
            ).fetchone():
                raise IdempotencyConflict("option structure is already sealed for the forecast")
            coverage = {
                "call_zero_coverage": value["call_zero_coverage"],
                "put_zero_coverage": value["put_zero_coverage"],
                "terminal_sample_size": value["terminal_sample_size"],
            }
            cursor = connection.execute(
                """INSERT INTO earnings_option_research_snapshots
                   (idempotency_key,forecast_snapshot_id,structure_type,evidence_mode,
                    historical_oos_validated,research_only,execution_eligible,automatic_ordering,
                    contracts_json,total_premium,commission_cost,spread_cost,max_loss,
                    slippage_cost,
                    lower_breakeven,upper_breakeven,required_move_pct,model_expected_move_pct,
                    iv_implied_move_pct,probability_outside_breakeven,expected_value_net_costs,
                    one_leg_coverage_json,iv_crush_json,decision_at,payload_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key, forecast_snapshot_id, value["structure_type"],
                    value["evidence_mode"], 0, 1, 0, 0, canonical_json(value["contracts"]),
                    value["total_premium"], value["commission_cost"], value["spread_cost"],
                    value["max_loss"], value["slippage_cost"], value["lower_breakeven"], value["upper_breakeven"],
                    value["required_move_pct"], value["model_expected_move_pct"],
                    value["iv_implied_move_pct"], value["probability_outside_breakeven"],
                    value["expected_value_net_costs"], canonical_json(coverage),
                    canonical_json(value["iv_crush_scenarios"]), value["decision_at"], payload_hash,
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM earnings_option_research_snapshots WHERE id=?", (cursor.lastrowid,)
            ).fetchone())

    def record_outcome(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        validate_idempotency_key(idempotency_key)
        event_id = payload.get("event_revision_id") if isinstance(payload, Mapping) else None
        event = self.database.fetch_one(
            "SELECT * FROM earnings_event_revisions WHERE id=?", (event_id,)
        )
        if event is None:
            raise EarningsContractError("earnings event revision does not exist")
        value = validate_outcome(payload, scheduled_at=event["scheduled_at"])
        with self.database.transaction() as connection:
            existing = self._idempotent_row(
                connection, TABLES["outcome"], idempotency_key, value["payload_sha256"]
            )
            if existing:
                return existing
            event = active_event(connection, value["event_revision_id"])
            session = trusted_session_receipt(self._session_validator, event, value)
            active = connection.execute(
                """SELECT current.* FROM earnings_outcomes current
                   WHERE current.event_revision_id=? AND current.checkpoint=?
                     AND NOT EXISTS (SELECT 1 FROM earnings_outcomes newer
                                     WHERE newer.supersedes_outcome_id=current.id)
                   ORDER BY current.id DESC LIMIT 1""",
                (value["event_revision_id"], value["checkpoint"]),
            ).fetchone()
            supersedes = value["supersedes_outcome_id"]
            if active and supersedes is None:
                raise IdempotencyConflict("an active outcome already exists; correction must supersede it")
            if supersedes is not None:
                if active is None or int(active["id"]) != supersedes:
                    raise IdempotencyConflict("outcome correction must supersede the active outcome")
            cursor = connection.execute(
                """INSERT INTO earnings_outcomes
                   (idempotency_key,event_revision_id,checkpoint,baseline_price,observed_price,
                    return_pct,mfe_pct,mae_pct,observed_at,available_at,recorded_at,
                    source_snapshot_id,session_close_at,calendar_artifact_sha256,
                    session_validation_receipt_sha256,supersedes_outcome_id,payload_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key, value["event_revision_id"], value["checkpoint"],
                    value["baseline_price"], value["observed_price"], value["return_pct"],
                    value["mfe_pct"], value["mae_pct"], value["observed_at"],
                    value["available_at"], value["recorded_at"], value["source_snapshot_id"],
                    session["session_close_at"], session["calendar_artifact_sha256"],
                    session["session_validation_receipt_sha256"], supersedes,
                    value["payload_sha256"],
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM earnings_outcomes WHERE id=?", (cursor.lastrowid,)
            ).fetchone())

    def current_outcomes(self, event_revision_id: int) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            """SELECT current.* FROM earnings_outcomes current
               WHERE current.event_revision_id=?
                 AND NOT EXISTS (SELECT 1 FROM earnings_outcomes newer
                                 WHERE newer.supersedes_outcome_id=current.id)
               ORDER BY CASE current.checkpoint
                   WHEN 'AFTER_HOURS' THEN 1 WHEN 'NEXT_CLOSE' THEN 2
                   WHEN 'D3_CLOSE' THEN 3 ELSE 4 END""",
            (event_revision_id,),
        )

    def record_postmortem(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        validate_idempotency_key(idempotency_key)
        value = validate_postmortem(payload)
        with self.database.transaction() as connection:
            existing = self._idempotent_row(
                connection, TABLES["postmortem"], idempotency_key, value["payload_sha256"]
            )
            if existing:
                return existing
            active_event(connection, value["event_revision_id"])
            binding = postmortem_binding(connection, value)
            outcomes = binding["outcomes"]
            for name in (
                "forecast_snapshot_set_sha256", "outcome_set_sha256",
                "direction_correct", "interval_covered",
            ):
                if value[name] != binding[name]:
                    raise EarningsContractError(f"postmortem {name} does not match sealed evidence")
            for name in ("paper_pnl_net", "paper_max_drawdown"):
                if not math.isclose(float(value[name]), float(binding[name]), abs_tol=1e-9):
                    raise EarningsContractError(f"postmortem {name} does not match sealed evidence")
            checkpoints = {str(row["checkpoint"]) for row in outcomes}
            if value["stage"] == "FINAL" and "D5_CLOSE" not in checkpoints:
                raise IdempotencyConflict("FINAL postmortem requires D5_CLOSE outcome")
            if value["stage"] == "FINAL" and not any(
                row["checkpoint"] == "D5_CLOSE"
                and row["session_validation_receipt_sha256"]
                for row in outcomes
            ):
                raise EarningsContractError(
                    "FINAL postmortem requires trusted D5_CLOSE session evidence"
                )
            latest_available = max(parse_timestamp(row["available_at"], "outcome available_at") for row in outcomes)
            if parse_timestamp(value["completed_at"], "completed_at") < latest_available:
                raise EarningsContractError("postmortem cannot predate its outcome evidence")
            supersedes = value["supersedes_postmortem_id"]
            if value["stage"] == "CORRECTION" and supersedes is None:
                raise EarningsContractError("CORRECTION postmortem must supersede a prior record")
            active = connection.execute(
                """SELECT current.* FROM earnings_postmortems current
                   WHERE current.event_revision_id=? AND current.model_id=?
                     AND current.model_version=?
                     AND NOT EXISTS (SELECT 1 FROM earnings_postmortems newer
                                     WHERE newer.supersedes_postmortem_id=current.id)
                   ORDER BY current.id DESC LIMIT 1""",
                (value["event_revision_id"], value["model_id"], value["model_version"]),
            ).fetchone()
            if active is not None and supersedes is None:
                raise IdempotencyConflict(
                    "an active postmortem already exists; the next record must supersede it"
                )
            if supersedes is not None:
                previous = connection.execute(
                    "SELECT * FROM earnings_postmortems WHERE id=?", (supersedes,)
                ).fetchone()
                if (
                    previous is None
                    or active is None
                    or int(active["id"]) != supersedes
                    or int(previous["event_revision_id"]) != value["event_revision_id"]
                    or previous["model_id"] != value["model_id"]
                    or previous["model_version"] != value["model_version"]
                ):
                    raise EarningsContractError("postmortem supersedes reference is invalid")
                if connection.execute(
                    "SELECT 1 FROM earnings_postmortems WHERE supersedes_postmortem_id=?",
                    (supersedes,),
                ).fetchone():
                    raise IdempotencyConflict("postmortem is already superseded")
            cursor = connection.execute(
                """INSERT INTO earnings_postmortems
                   (idempotency_key,event_revision_id,model_id,model_version,stage,completed_at,
                    forecast_snapshot_set_sha256,outcome_set_sha256,direction_correct,
                    interval_covered,paper_pnl_net,paper_max_drawdown,analysis_json,
                    candidate_ref,supersedes_postmortem_id,publication_state,payload_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idempotency_key, value["event_revision_id"], value["model_id"],
                    value["model_version"], value["stage"], value["completed_at"],
                    value["forecast_snapshot_set_sha256"], value["outcome_set_sha256"],
                    int(value["direction_correct"]), int(value["interval_covered"]),
                    value["paper_pnl_net"], value["paper_max_drawdown"],
                    canonical_json(value["analysis"]), value["candidate_ref"], supersedes,
                    "research", value["payload_sha256"],
                ),
            )
            return dict(connection.execute(
                "SELECT * FROM earnings_postmortems WHERE id=?", (cursor.lastrowid,)
            ).fetchone())
