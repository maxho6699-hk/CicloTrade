"""Capability-gated, source-anonymous earnings research projections."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import base64
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from core.earnings_forecast_contracts import parse_timestamp, timestamp
from core.earnings_forecast_metrics import (
    ForecastMetricObservation,
    compute_forecast_metrics,
)


class EarningsResearchNotFound(LookupError):
    """Generic failure shared by malformed and nonexistent opaque identifiers."""


_NOT_FOUND = "earnings research unavailable"


class OpaqueIdCodec:
    """Bind integer identifiers to a kind without exposing the database value."""

    def __init__(self, secret: str | bytes):
        raw = secret.encode("utf-8") if isinstance(secret, str) else secret
        if not isinstance(raw, bytes) or len(raw) < 32:
            raise ValueError("opaque id secret must contain at least 32 bytes")
        self._secret = raw

    def encode(self, kind: str, value: int) -> str:
        if not isinstance(kind, str) or not kind or isinstance(value, bool) or value <= 0:
            raise ValueError("opaque id input is invalid")
        integer = int(value).to_bytes(8, "big")
        mask = hmac.digest(self._secret, b"mask:" + kind.encode(), "sha256")[:8]
        body = bytes(left ^ right for left, right in zip(integer, mask))
        tag = hmac.digest(
            self._secret, b"id:" + kind.encode() + body, "sha256"
        )[:16]
        return base64.urlsafe_b64encode(b"\x01" + body + tag).decode().rstrip("=")

    def decode(self, kind: str, token: str) -> int:
        try:
            if not isinstance(token, str) or not 20 <= len(token) <= 64:
                raise ValueError
            padded = token + "=" * (-len(token) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            if len(raw) != 25 or raw[0] != 1:
                raise ValueError
            body, supplied = raw[1:9], raw[9:]
            expected = hmac.digest(
                self._secret, b"id:" + kind.encode() + body, "sha256"
            )[:16]
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            mask = hmac.digest(self._secret, b"mask:" + kind.encode(), "sha256")[:8]
            value = int.from_bytes(
                bytes(left ^ right for left, right in zip(body, mask)), "big"
            )
            if value <= 0:
                raise ValueError
            return value
        except Exception as exc:
            raise EarningsResearchNotFound(_NOT_FOUND) from exc


class EarningsForecastReadModel:
    def __init__(self, db_path: str | Path, codec: OpaqueIdCodec):
        self.db_path = Path(db_path).resolve()
        self.codec = codec

    def _connection(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise EarningsResearchNotFound(_NOT_FOUND)
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _moment(value: datetime | str) -> datetime:
        return parse_timestamp(value, "as_of")

    @staticmethod
    def _limit(value: int, maximum: int = 200) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError("limit is invalid")
        return value

    def _confirmed_count(self, as_of: datetime, window_days: int) -> int:
        start = timestamp(as_of, "as_of")
        end = timestamp(as_of + timedelta(days=window_days), "window_end")
        with self._connection() as connection:
            row = connection.execute(
                """SELECT COUNT(*) count FROM earnings_event_revisions event
                   WHERE event.status='CONFIRMED' AND event.scheduled_at>=?
                     AND event.scheduled_at<=?
                     AND NOT EXISTS (SELECT 1 FROM earnings_event_revisions newer
                                     WHERE newer.supersedes_revision_id=event.id)""",
                (start, end),
            ).fetchone()
        return int(row["count"])

    def _locked(self, as_of: datetime, window_days: int = 7) -> dict[str, Any]:
        return {
            "state": "locked",
            "feature": "earnings_forecast",
            "required_capability": "earnings_forecast",
            "window_days": window_days,
            "confirmed_event_count": self._confirmed_count(as_of, window_days),
            "reason_code": "legacy_entitlement_required",
            "description": "未来 7 天业绩预测、历史轨迹与复盘仅对历史有效专业权益开放；当前不公开新购或升级。",
            "upgrade_path": None,
        }

    @staticmethod
    def _json(row: sqlite3.Row, name: str) -> Any:
        return json.loads(str(row[name]))

    @staticmethod
    def _redact(value: Any, secrets: Iterable[str]) -> Any:
        tokens = tuple(token for token in secrets if token)
        if isinstance(value, str):
            output = value
            for token in tokens:
                output = output.replace(token, "[redacted]")
            return output
        if isinstance(value, list):
            return [EarningsForecastReadModel._redact(item, tokens) for item in value]
        if isinstance(value, dict):
            return {
                key: EarningsForecastReadModel._redact(item, tokens)
                for key, item in value.items()
            }
        return value

    def _forecast_secrets(self, row: sqlite3.Row, event: sqlite3.Row) -> list[str]:
        manifest = self._json(row, "input_manifest_json")
        evidence = manifest.get("evidence", []) if isinstance(manifest, dict) else []
        secrets = [event["source"], event["source_event_id"], row["model_id"]]
        for item in evidence:
            if isinstance(item, dict):
                secrets.extend((item.get("source"), item.get("source_snapshot_id")))
        return [str(value) for value in secrets if value]

    def _forecast_projection(
        self,
        row: sqlite3.Row,
        event: sqlite3.Row,
        option_research: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = self._json(row, "input_manifest_json")
        evidence = manifest.get("evidence", []) if isinstance(manifest, dict) else []
        secrets = self._forecast_secrets(row, event)
        narrative = self._redact(self._json(row, "narrative_json"), secrets)
        causal = self._json(row, "causal_graph_json")
        claims = []
        for claim in causal.get("claims", []) if isinstance(causal, dict) else []:
            if not isinstance(claim, dict):
                continue
            snapshot_ids = claim.get("evidence_snapshot_ids", [])
            claims.append(self._redact({
                "kind": claim.get("kind"),
                "claim": claim.get("claim"),
                "confidence": claim.get("confidence"),
                "evidence_count": len(snapshot_ids) if isinstance(snapshot_ids, list) else 0,
                "confounders": claim.get("confounders", []),
            }, secrets))
        risk = self._redact(self._json(row, "risk_json"), secrets)
        manifest_hash = str(row["input_manifest_sha256"])
        action_contract = {
            "structure": str(row["simulated_action"]),
            "entry": {"limit_price": None, "quantity": None},
            "stop": None,
            "targets": [],
            "max_loss": risk.get("max_loss_amount"),
            "max_account_pct": None,
            "breakeven": None,
            "invalidation": risk.get("invalidation_condition"),
            "exit": "每个财报日重新评估；不会自动平仓。",
            "roll": "不自动展期。",
            "quote_at": row["decision_at"],
            "model_artifact_sha256": row["model_artifact_sha256"],
            "evidence_manifest_sha256": manifest_hash,
            "execution_eligible": False,
            "automatic_ordering": False,
        }
        return {
            "countdown_day": int(row["countdown_day"]),
            "decision_at": row["decision_at"],
            "available_cutoff_at": row["available_cutoff_at"],
            "p_up": float(row["p_up"]),
            "p_down": float(row["p_down"]),
            "p_flat": float(row["p_flat"]),
            "flat_band_pct": float(row["flat_band_pct"]),
            "confidence": float(row["confidence"]),
            "calibration_sample_size": int(row["calibration_sample_size"]),
            "reference_price": float(row["reference_price"]),
            "currency": row["currency"],
            "price_p10": float(row["price_p10"]),
            "price_p50": float(row["price_p50"]),
            "price_p90": float(row["price_p90"]),
            "estimated_mfe_pct": float(row["estimated_mfe_pct"]),
            "estimated_mae_pct": float(row["estimated_mae_pct"]),
            "simulated_action": row["simulated_action"],
            "narrative": narrative,
            "causal_graph": {"claims": claims},
            "risk": risk,
            "evidence_count": len(evidence),
            "evidence_sha256": [
                item["sha256"] for item in evidence
                if isinstance(item, dict) and isinstance(item.get("sha256"), str)
            ],
            "model_artifact_sha256": row["model_artifact_sha256"],
            "evidence_manifest_sha256": manifest_hash,
            "research_only": True,
            "execution_eligible": False,
            "automatic_ordering": False,
            "action_contract": action_contract,
            "option_research": option_research,
        }

    def _option_research_projection(
        self,
        connection: sqlite3.Connection,
        forecast_id: int,
        *,
        has_option_capability: bool,
        cutoff: str | None,
    ) -> dict[str, Any]:
        if not has_option_capability:
            return {
                "state": "locked",
                "feature": "earnings_option_research",
                "required_capability": "earnings_option_defined_risk",
                "reason_code": "legacy_entitlement_required",
                "upgrade_path": None,
            }
        sql = """SELECT id,structure_type FROM earnings_option_research_snapshots
                 WHERE forecast_snapshot_id=?"""
        params: list[Any] = [forecast_id]
        if cutoff:
            sql += " AND decision_at<=?"
            params.append(cutoff)
        sql += " ORDER BY decision_at DESC,id DESC"
        rows = connection.execute(sql, params).fetchall()
        items = [{
            "option_id": self.codec.encode("option", int(row["id"])),
            "structure_type": row["structure_type"],
        } for row in rows]
        return {"state": "available" if items else "no_data", "items": items}

    @staticmethod
    def _canonical_forecast_rows(
        connection: sqlite3.Connection,
        event_revision_id: int,
        cutoff: str | None,
    ) -> list[sqlite3.Row]:
        current_availability = ""
        candidate_availability = ""
        params: list[Any] = [event_revision_id]
        if cutoff:
            current_availability = " AND current.decision_at<=? AND current.recorded_at<=?"
            params.extend((cutoff, cutoff))
            candidate_availability = " AND candidate.decision_at<=? AND candidate.recorded_at<=?"
            params.extend((cutoff, cutoff))
        return connection.execute(
            f"""SELECT current.* FROM earnings_forecast_snapshots current
                WHERE current.event_revision_id=?{current_availability}
                  AND current.id=(
                      SELECT candidate.id FROM earnings_forecast_snapshots candidate
                      WHERE candidate.event_revision_id=current.event_revision_id
                        AND candidate.countdown_day=current.countdown_day
                        {candidate_availability}
                      ORDER BY candidate.decision_at DESC,candidate.recorded_at DESC,candidate.id DESC
                      LIMIT 1)
                ORDER BY current.countdown_day DESC""",
            params,
        ).fetchall()

    def _event_projection(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": self.codec.encode("event", int(row["id"])),
            "market": row["market"], "symbol": row["symbol"],
            "fiscal_period": row["fiscal_period"], "scheduled_at": row["scheduled_at"],
            "exchange_timezone": row["exchange_timezone"], "timing": row["timing"],
            "status": row["status"],
        }

    def _upcoming_research_items(
        self, as_of: datetime, window_days: int, limit: int,
        *, has_option_capability: bool,
    ) -> list[dict[str, Any]]:
        start, end = timestamp(as_of, "as_of"), timestamp(
            as_of + timedelta(days=window_days), "window_end"
        )
        with self._connection() as connection:
            events = connection.execute(
                """SELECT * FROM earnings_event_revisions event
                   WHERE event.status='CONFIRMED' AND event.scheduled_at>=?
                     AND event.scheduled_at<=?
                     AND NOT EXISTS (SELECT 1 FROM earnings_event_revisions newer
                                     WHERE newer.supersedes_revision_id=event.id)
                   ORDER BY event.scheduled_at,event.market,event.symbol LIMIT ?""",
                (start, end, limit),
            ).fetchall()
            items = []
            for event in events:
                forecast = connection.execute(
                    """SELECT * FROM earnings_forecast_snapshots
                       WHERE event_revision_id=? AND decision_at<=? AND recorded_at<=?
                       ORDER BY countdown_day ASC,decision_at DESC,recorded_at DESC,id DESC LIMIT 1""",
                    (event["id"], start, start),
                ).fetchone()
                item = self._event_projection(event)
                item["latest_forecast"] = (
                    self._forecast_projection(
                        forecast,
                        event,
                        self._option_research_projection(
                            connection,
                            int(forecast["id"]),
                            has_option_capability=has_option_capability,
                            cutoff=start,
                        ),
                    ) if forecast else None
                )
                item["forecast_state"] = "sealed" if forecast else "pending"
                items.append(item)
        return items

    def overview(
        self, *, has_capability: bool, as_of: datetime | str,
        window_days: int = 7, limit: int = 100,
        has_option_capability: bool = False,
    ) -> dict[str, Any]:
        moment = self._moment(as_of)
        if not has_capability:
            return self._locked(moment, window_days)
        items = self._upcoming_research_items(
            moment, window_days, self._limit(limit),
            has_option_capability=has_option_capability,
        )
        return {
            "state": "research", "data_state": "ready" if items else "no_data",
            "window_days": window_days, "research_only": True,
            "execution_eligible": False, "automatic_ordering": False, "items": items,
        }

    def _event_row(self, connection: sqlite3.Connection, opaque_id: str) -> sqlite3.Row:
        event_id = self.codec.decode("event", opaque_id)
        row = connection.execute(
            "SELECT * FROM earnings_event_revisions WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise EarningsResearchNotFound(_NOT_FOUND)
        return row

    def detail(
        self, *, has_capability: bool, opaque_event_id: str,
        as_of: datetime | str | None = None,
        has_option_capability: bool = False,
    ) -> dict[str, Any]:
        if not has_capability:
            return self._locked(self._moment(as_of or datetime.now().astimezone()))
        cutoff = timestamp(self._moment(as_of), "as_of") if as_of is not None else None
        with self._connection() as connection:
            event = self._event_row(connection, opaque_event_id)
            forecasts = self._canonical_forecast_rows(
                connection, int(event["id"]), cutoff
            )
            outcome_sql = """SELECT current.* FROM earnings_outcomes current
                WHERE current.event_revision_id=? AND NOT EXISTS (
                    SELECT 1 FROM earnings_outcomes newer
                    WHERE newer.supersedes_outcome_id=current.id)"""
            outcome_params: list[Any] = [event["id"]]
            if cutoff:
                outcome_sql += " AND current.available_at<=? AND current.recorded_at<=?"
                outcome_params.extend((cutoff, cutoff))
            outcome_sql += " ORDER BY current.observed_at,current.id"
            outcomes = connection.execute(outcome_sql, outcome_params).fetchall()
            postmortem_sql = (
                "SELECT * FROM earnings_postmortems WHERE event_revision_id=?"
            )
            postmortem_params: list[Any] = [event["id"]]
            if cutoff:
                postmortem_sql += " AND completed_at<=?"
                postmortem_params.append(cutoff)
            postmortem_sql += " ORDER BY completed_at,id"
            postmortems = connection.execute(
                postmortem_sql, postmortem_params
            ).fetchall()
            secret_sql = """SELECT model_id,input_manifest_json
                            FROM earnings_forecast_snapshots
                            WHERE event_revision_id=?"""
            secret_params: list[Any] = [event["id"]]
            if cutoff:
                secret_sql += " AND decision_at<=? AND recorded_at<=?"
                secret_params.extend((cutoff, cutoff))
            secret_rows = connection.execute(secret_sql, secret_params).fetchall()
            postmortem_secrets: list[str] = [
                str(event["source"]), str(event["source_event_id"])
            ]
            for row in secret_rows:
                postmortem_secrets.extend(self._forecast_secrets(row, event))
            timeline = [self._forecast_projection(
                row,
                event,
                self._option_research_projection(
                    connection,
                    int(row["id"]),
                    has_option_capability=has_option_capability,
                    cutoff=cutoff,
                ),
            ) for row in forecasts]
        return {
            "state": "research", **self._event_projection(event),
            "research_only": True, "execution_eligible": False,
            "automatic_ordering": False,
            "timeline": timeline,
            "outcomes": [{
                "checkpoint": row["checkpoint"], "baseline_price": row["baseline_price"],
                "observed_price": row["observed_price"], "return_pct": row["return_pct"],
                "mfe_pct": row["mfe_pct"], "mae_pct": row["mae_pct"],
                "observed_at": row["observed_at"], "available_at": row["available_at"],
            } for row in outcomes],
            "postmortems": [{
                "stage": row["stage"], "completed_at": row["completed_at"],
                "direction_correct": bool(row["direction_correct"]),
                "interval_covered": bool(row["interval_covered"]),
                "paper_performance": {
                    "state": row["paper_performance_state"],
                    "pnl_net": row["paper_pnl_net_v2"],
                    "max_drawdown": row["paper_max_drawdown_v2"],
                    "ledger_snapshot_sha256": row["paper_ledger_snapshot_sha256"],
                },
                "analysis": self._redact(
                    json.loads(row["analysis_json"]),
                    (*postmortem_secrets, row["model_id"]),
                ),
            } for row in postmortems],
        }

    def history(
        self, *, has_capability: bool, as_of: datetime | str,
        limit: int = 50, cursor: str | None = None,
        has_option_capability: bool = False,
    ) -> dict[str, Any]:
        moment = self._moment(as_of)
        if not has_capability:
            return self._locked(moment)
        cutoff, maximum = timestamp(moment, "as_of"), self._limit(limit)
        cursor_id = self.codec.decode("history", cursor) if cursor else None
        with self._connection() as connection:
            sql = """SELECT event.* FROM earnings_event_revisions event
                WHERE event.scheduled_at<=? AND NOT EXISTS (
                    SELECT 1 FROM earnings_event_revisions newer
                    WHERE newer.supersedes_revision_id=event.id)
                  AND EXISTS (SELECT 1 FROM earnings_outcomes outcome
                              WHERE outcome.event_revision_id=event.id
                                AND outcome.available_at<=?
                                AND outcome.recorded_at<=?)"""
            params: list[Any] = [cutoff, cutoff, cutoff]
            if cursor_id is not None:
                sql += " AND event.id<?"
                params.append(cursor_id)
            sql += " ORDER BY event.id DESC LIMIT ?"
            params.append(maximum + 1)
            rows = connection.execute(sql, params).fetchall()
        page = rows[:maximum]
        items = [self.detail(
            has_capability=True,
            opaque_event_id=self.codec.encode("event", int(row["id"])), as_of=moment,
            has_option_capability=has_option_capability,
        ) for row in page]
        next_cursor = (
            self.codec.encode("history", int(page[-1]["id"]))
            if len(rows) > maximum and page else None
        )
        return {"state": "research", "items": items, "next_cursor": next_cursor}

    def statistics(
        self, *, has_capability: bool, as_of: datetime | str
    ) -> dict[str, Any]:
        moment = self._moment(as_of)
        if not has_capability:
            return self._locked(moment)
        cutoff = timestamp(moment, "as_of")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT forecast.*,outcome.return_pct actual_return_pct,
                          outcome.observed_price actual_price
                   FROM earnings_event_revisions event
                   JOIN earnings_forecast_snapshots forecast ON forecast.id=(
                       SELECT candidate.id FROM earnings_forecast_snapshots candidate
                       WHERE candidate.event_revision_id=event.id
                         AND candidate.countdown_day=1 AND candidate.decision_at<=?
                         AND candidate.recorded_at<=?
                        ORDER BY candidate.decision_at DESC,candidate.recorded_at DESC,candidate.id DESC LIMIT 1)
                   JOIN earnings_outcomes outcome ON outcome.id=(
                       SELECT result.id FROM earnings_outcomes result
                       WHERE result.event_revision_id=event.id
                         AND result.checkpoint='NEXT_CLOSE' AND result.available_at<=?
                         AND result.recorded_at<=?
                         AND NOT EXISTS (SELECT 1 FROM earnings_outcomes newer
                                         WHERE newer.supersedes_outcome_id=result.id)
                       ORDER BY result.id DESC LIMIT 1)
                   WHERE NOT EXISTS (SELECT 1 FROM earnings_event_revisions newer_event
                                     WHERE newer_event.supersedes_revision_id=event.id)""",
                (cutoff, cutoff, cutoff, cutoff),
            ).fetchall()
        observations = [ForecastMetricObservation(
            p_up=row["p_up"], p_down=row["p_down"], p_flat=row["p_flat"],
            actual_return_pct=row["actual_return_pct"], flat_band_pct=row["flat_band_pct"],
            price_p10=row["price_p10"], price_p90=row["price_p90"],
            actual_price=row["actual_price"], paper_pnl_net=None,
        ) for row in rows]
        metrics = compute_forecast_metrics(observations, starting_equity=100_000.0)
        return {"state": "research", "metrics": asdict(metrics)}

    def option_detail(
        self, *, has_forecast_capability: bool, has_option_capability: bool,
        opaque_event_id: str, opaque_option_id: str,
        as_of: datetime | str | None = None,
    ) -> dict[str, Any]:
        if not has_forecast_capability:
            return self._locked(self._moment(as_of or datetime.now().astimezone()))
        if not has_option_capability:
            return {
                "state": "locked", "feature": "earnings_option_research",
                "required_capability": "earnings_option_defined_risk",
                "reason_code": "legacy_entitlement_required", "upgrade_path": None,
            }
        event_id = self.codec.decode("event", opaque_event_id)
        option_id = self.codec.decode("option", opaque_option_id)
        cutoff = timestamp(self._moment(as_of), "as_of") if as_of is not None else None
        with self._connection() as connection:
            sql = """SELECT option.*,forecast.model_artifact_sha256,
                            forecast.input_manifest_sha256,forecast.risk_json
                     FROM earnings_option_research_snapshots option
                     JOIN earnings_forecast_snapshots forecast
                       ON forecast.id=option.forecast_snapshot_id
                     WHERE option.id=? AND forecast.event_revision_id=?"""
            params: list[Any] = [option_id, event_id]
            if cutoff:
                sql += """ AND option.decision_at<=?
                            AND option.recorded_at<=?
                            AND forecast.decision_at<=?
                            AND forecast.recorded_at<=?"""
                params.extend((cutoff, cutoff, cutoff, cutoff))
            row = connection.execute(sql, params).fetchone()
        if row is None:
            raise EarningsResearchNotFound(_NOT_FOUND)
        legs = self._json(row, "contracts_json")
        coverage = self._json(row, "one_leg_coverage_json")
        risk = self._json(row, "risk_json")
        quote_at = max((leg.get("quote_at", "") for leg in legs), default=row["decision_at"])
        action_contract = {
            "structure": row["structure_type"],
            "entry": {"order_type": "LIMIT_RESEARCH_ONLY", "legs": [{
                "contract_id": leg.get("contract_id"), "right": leg.get("right"),
                "strike": leg.get("strike"), "expiry": leg.get("expiry"),
                "quantity": leg.get("quantity"), "multiplier": leg.get("multiplier"),
                "limit_price": leg.get("ask"),
            } for leg in legs]},
            "stop": None, "targets": [], "max_loss": row["max_loss"],
            "max_account_pct": None,
            "breakeven": {"lower": row["lower_breakeven"], "upper": row["upper_breakeven"]},
            "invalidation": risk.get("invalidation_condition"),
            "exit": "仅供研究，退出需人工复核。", "roll": "不自动展期。",
            "quote_at": quote_at,
            "model_artifact_sha256": row["model_artifact_sha256"],
            "evidence_manifest_sha256": row["input_manifest_sha256"],
            "execution_eligible": False, "automatic_ordering": False,
        }
        return {
            "state": "research", "structure_type": row["structure_type"],
            "evidence_mode": row["evidence_mode"],
            "historical_oos_validated": False, "research_only": True,
            "execution_eligible": False, "automatic_ordering": False,
            "legs": legs, "total_premium": row["total_premium"],
            "commission_cost": row["commission_cost"], "spread_cost": row["spread_cost"],
            "slippage_cost": row["slippage_cost"], "max_loss": row["max_loss"],
            "lower_breakeven": row["lower_breakeven"],
            "upper_breakeven": row["upper_breakeven"],
            "required_move_pct": row["required_move_pct"],
            "model_expected_move_pct": row["model_expected_move_pct"],
            "iv_implied_move_pct": row["iv_implied_move_pct"],
            "probability_outside_breakeven": row["probability_outside_breakeven"],
            "expected_value_net_costs": row["expected_value_net_costs"],
            "call_zero_coverage": coverage.get("call_zero_coverage"),
            "put_zero_coverage": coverage.get("put_zero_coverage"),
            "terminal_sample_size": coverage.get("terminal_sample_size"),
            "iv_crush_scenarios": self._json(row, "iv_crush_json"),
            "decision_at": row["decision_at"], "action_contract": action_contract,
        }
