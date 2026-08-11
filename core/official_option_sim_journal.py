"""Append-only ledger for the official, real-quote paper option account."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Callable, Mapping

from core.compat import UTC
from core.database import DatabaseManager
from core.official_option_sim_contracts import (
    OfficialOptionSimulationError,
    OfficialOptionSimulationIdempotencyConflict,
    _validate_execution,
    canonical_json,
    parse_timestamp,
    stamp,
    validate_receipt,
)


_NEXT = {
    "PROPOSED": {"ACCEPTED", "REJECTED", "CANCELLED"},
    "ACCEPTED": {"OPENED", "REJECTED", "CANCELLED"},
    "OPENED": {"MARKED", "CLOSING"},
    "CLOSING": {"CLOSED", "MARKED"},
    "MARKED": {"MARKED", "CLOSING"},
}
_STATE = {
    "PROPOSED": "proposed", "ACCEPTED": "accepted", "OPENED": "open",
    "MARKED": "open", "CLOSING": "closing", "CLOSED": "closed",
    "REJECTED": "rejected", "CANCELLED": "cancelled",
}


class OfficialOptionSimulationJournal:
    """Records every proposal, rejection, fill, mark and exit without broker I/O."""

    def __init__(self, database: DatabaseManager, *, clock: Callable[[], datetime] | None = None):
        if not isinstance(database, DatabaseManager):
            raise TypeError("database must be a DatabaseManager")
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _row(connection: Any, table: str, idempotency_key: str, digest: str) -> dict[str, Any] | None:
        row = connection.execute(f"SELECT * FROM {table} WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row is None:
            return None
        if row["payload_sha256"] != digest:
            raise OfficialOptionSimulationIdempotencyConflict("idempotency key was reused with different content")
        return dict(row)

    @staticmethod
    def _position(connection: Any, position_key: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT * FROM official_option_sim_positions WHERE position_key=?", (position_key,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _latest(connection: Any, position_id: int) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM official_option_sim_events WHERE position_id=? ORDER BY id DESC LIMIT 1", (position_id,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _proposal_payload(connection: Any, position_id: int) -> dict[str, Any]:
        row = connection.execute(
            "SELECT payload_json FROM official_option_sim_events WHERE position_id=? AND event_type='PROPOSED'", (position_id,)
        ).fetchone()
        if row is None:
            raise OfficialOptionSimulationError("position proposal is missing")
        import json
        return json.loads(row["payload_json"])

    @staticmethod
    def _advance_fence(connection: Any, worker_id: str, epoch: int, now: str) -> None:
        row = connection.execute(
            "SELECT highest_epoch FROM official_option_sim_worker_fences WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if row and epoch < int(row["highest_epoch"]):
            raise OfficialOptionSimulationError("stale fencing epoch")
        if row is None:
            connection.execute(
                "INSERT INTO official_option_sim_worker_fences(worker_id,highest_epoch,updated_at) VALUES (?,?,?)",
                (worker_id, epoch, now),
            )
        elif epoch > int(row["highest_epoch"]):
            connection.execute(
                "UPDATE official_option_sim_worker_fences SET highest_epoch=?,updated_at=? WHERE worker_id=?",
                (epoch, now, worker_id),
            )

    @staticmethod
    def _position_contract(row: Mapping[str, Any], proposal: Mapping[str, Any]) -> dict[str, Any]:
        position = dict(proposal["position"])
        if any(str(row[name]) != str(expected) for name, expected in (
            ("structure_type", position["structure_type"]), ("underlying", position["underlying"]),
            ("currency", position["currency"]), ("strategy_id", proposal["strategy_id"]),
            ("strategy_version", proposal["strategy_version"]), ("model_version", proposal["model_version"]),
            ("manifest_sha256", proposal["manifest_sha256"]),
        )):
            raise OfficialOptionSimulationError("sealed position contract is inconsistent")
        return position

    @staticmethod
    def _assert_event_identity(value: Mapping[str, Any], position: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
        for name in ("strategy_id", "strategy_version", "model_version", "manifest_sha256"):
            if value[name] != proposal[name]:
                raise OfficialOptionSimulationError("position strategy or evidence identity changed")
        if value["evidence_hashes"] != proposal["evidence_hashes"]:
            raise OfficialOptionSimulationError("position evidence hashes changed")
        if position["currency"] != "USD":
            raise OfficialOptionSimulationError("position currency is unsafe")

    @staticmethod
    def _event_legs(value: Mapping[str, Any], position: Mapping[str, Any], *, close: bool) -> tuple[list[dict[str, Any]], float]:
        execution = _validate_execution(position, value["execution"], parse_timestamp(value["action_at"], "action_at"), close=close)
        legs = execution["legs"]
        cash_flow = 0.0
        for leg in legs:
            signed = -1 if leg["side"] == "BUY" else 1
            cash_flow += signed * leg["execution_price"] * leg["quantity"] * leg["multiplier"]
            cash_flow -= leg["commission"]
        if not close and cash_flow >= -1e-9:
            raise OfficialOptionSimulationError("opening execution must be a defined-risk debit")
        return legs, cash_flow

    @staticmethod
    def _insert_legs(connection: Any, event_id: int, legs: list[Mapping[str, Any]]) -> None:
        for index, leg in enumerate(legs):
            connection.execute(
                """INSERT INTO official_option_sim_event_legs
                   (event_id,leg_no,contract_key,side,quantity,expiry,right,strike,multiplier,bid,ask,quote_at,execution_price,commission)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, index, leg["contract_key"], leg["side"], leg["quantity"], leg["expiry"], leg["right"],
                 leg["strike"], leg["multiplier"], leg["bid"], leg["ask"], leg["quote_at"], leg.get("execution_price"), leg["commission"]),
            )

    @staticmethod
    def _position_pnl(connection: Any, position_id: int, *, mark: bool = False) -> tuple[float, float]:
        rows = connection.execute(
            "SELECT event_type,cash_flow,unrealized_pnl FROM official_option_sim_events WHERE position_id=? ORDER BY id", (position_id,)
        ).fetchall()
        realized = sum(float(row["cash_flow"]) for row in rows if row["event_type"] in {"OPENED", "CLOSED"})
        latest_mark = next((float(row["unrealized_pnl"]) for row in reversed(rows) if row["unrealized_pnl"] is not None), 0.0)
        return realized, latest_mark if mark else 0.0

    @staticmethod
    def _mark_value(position: Mapping[str, Any], raw: Mapping[str, Any], action_at: datetime) -> float:
        # Marking long legs at bid and spread short legs at ask is intentionally adverse.
        from core.official_option_sim_contracts import _leg
        proposal_legs = position["legs"]
        mark_legs = raw.get("execution", {}).get("legs") if isinstance(raw.get("execution"), Mapping) else None
        if not isinstance(mark_legs, list):
            raise OfficialOptionSimulationError("MARKED requires a full real-time quote receipt")
        if len(mark_legs) != len(proposal_legs):
            raise OfficialOptionSimulationError("mark receipt must cover every open leg")
        legs = [_leg(leg, action_at) for leg in mark_legs]
        by_key = {leg["contract_key"]: leg for leg in legs}
        if set(by_key) != {leg["contract_key"] for leg in proposal_legs}:
            raise OfficialOptionSimulationError("mark contracts do not match position")
        entry = 0.0
        marked = 0.0
        for original in proposal_legs:
            quote = by_key[original["contract_key"]]
            if any(quote[key] != original[key] for key in ("side", "quantity", "expiry", "right", "strike", "multiplier")):
                raise OfficialOptionSimulationError("mark contract identity changed")
            entry_price = original["ask"] if original["side"] == "BUY" else original["bid"]
            entry += (-1 if original["side"] == "BUY" else 1) * entry_price * original["quantity"] * original["multiplier"] - original["commission"]
            marked += (quote["bid"] if original["side"] == "BUY" else -quote["ask"]) * quote["quantity"] * quote["multiplier"]
        return entry + marked

    @staticmethod
    def _snapshot(connection: Any, event_id: int, position: Mapping[str, Any], event_type: str, realized: float, unrealized: float, action_at: str) -> None:
        equity = float(position["account_equity"]) + realized + unrealized
        high = connection.execute(
            "SELECT MAX(equity) value FROM official_option_sim_equity_snapshots WHERE position_id=?", (position["id"],)
        ).fetchone()["value"]
        high_water = max(float(position["account_equity"]), float(high) if high is not None else float(position["account_equity"]))
        drawdown = max(0.0, high_water - equity)
        connection.execute(
            """INSERT INTO official_option_sim_equity_snapshots
               (event_id,position_id,captured_at,equity,realized_pnl,unrealized_pnl,drawdown)
               VALUES (?,?,?,?,?,?,?)""",
            (event_id, position["id"], action_at, equity, realized, unrealized, drawdown),
        )

    def record(self, payload: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
            raise OfficialOptionSimulationError("idempotency key is invalid")
        value = validate_receipt(payload, now=self.clock())
        recorded_at = stamp(self.clock(), "recorded_at")
        with self.database.transaction() as connection:
            self._advance_fence(connection, value["worker_id"], value["fencing_epoch"], recorded_at)
            existing = self._row(connection, "official_option_sim_events", idempotency_key, value["payload_sha256"])
            if existing:
                return existing
            external = connection.execute(
                "SELECT * FROM official_option_sim_events WHERE external_event_id=?",
                (value["event_id"],),
            ).fetchone()
            if external is not None:
                raise OfficialOptionSimulationIdempotencyConflict(
                    "external event id was reused with a different idempotency key"
                )
            event = value["event_type"]
            position = self._position(connection, value["position_key"])
            if event == "PROPOSED":
                if position is not None:
                    raise OfficialOptionSimulationIdempotencyConflict("position key already exists")
                contract = value["position"]
                risk = contract["risk"]
                cursor = connection.execute(
                    """INSERT INTO official_option_sim_positions
                    (position_key,structure_type,underlying,currency,strategy_id,strategy_version,model_version,manifest_sha256,evidence_hashes_json,account_equity,max_loss,max_account_pct,portfolio_risk_before_pct,portfolio_risk_limit_pct,invalidation_condition,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (value["position_key"], contract["structure_type"], contract["underlying"], contract["currency"],
                     value["strategy_id"], value["strategy_version"], value["model_version"], value["manifest_sha256"],
                     canonical_json(value["evidence_hashes"]), contract["account_equity"], risk["max_loss"], risk["max_account_pct"],
                     contract["portfolio_risk_before_pct"], contract["portfolio_risk_limit_pct"], risk["invalidation_condition"], recorded_at),
                )
                position = self._position(connection, value["position_key"])
                assert position is not None and cursor.lastrowid == position["id"]
            if position is None:
                raise OfficialOptionSimulationError("position proposal does not exist")
            proposal = self._proposal_payload(connection, int(position["id"])) if event != "PROPOSED" else value
            contract = self._position_contract(position, proposal)
            self._assert_event_identity(value, position, proposal)
            latest = self._latest(connection, int(position["id"]))
            if event != "PROPOSED":
                if latest is None or event not in _NEXT.get(latest["event_type"], set()):
                    raise OfficialOptionSimulationError("simulation lifecycle transition is invalid")
            legs: list[dict[str, Any]] = []
            cash_flow = 0.0
            if event == "PROPOSED":
                legs = contract["legs"]
            elif event == "OPENED":
                legs, cash_flow = self._event_legs(value, contract, close=False)
                required_loss = -cash_flow
                if not math.isclose(required_loss, float(position["max_loss"]), abs_tol=0.01):
                    raise OfficialOptionSimulationError("sealed max_loss does not equal simulated defined risk")
            elif event == "CLOSED":
                legs, cash_flow = self._event_legs(value, contract, close=True)
            elif event == "MARKED":
                unrealized = self._mark_value(contract, value, parse_timestamp(value["action_at"], "action_at"))
            prior_realized, _ = self._position_pnl(connection, int(position["id"]), mark=False)
            event_realized = prior_realized + cash_flow if event in {"OPENED", "CLOSED"} else prior_realized
            lifecycle_state = "closing" if (
                event == "MARKED" and latest is not None and latest["lifecycle_state"] == "closing"
            ) else _STATE[event]
            cursor = connection.execute(
                """INSERT INTO official_option_sim_events
                   (idempotency_key,external_event_id,position_id,event_type,lifecycle_state,action_at,recorded_at,worker_id,fencing_epoch,cash_flow,realized_pnl,unrealized_pnl,payload_json,payload_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (idempotency_key, value["event_id"], position["id"], event, lifecycle_state, value["action_at"], recorded_at,
                 value["worker_id"], value["fencing_epoch"], cash_flow, event_realized, unrealized if event == "MARKED" else None,
                 canonical_json(value), value["payload_sha256"]),
            )
            event_id = int(cursor.lastrowid)
            if legs:
                self._insert_legs(connection, event_id, legs)
            realized, prior_unrealized = self._position_pnl(connection, int(position["id"]), mark=event != "CLOSED")
            unrealized_pnl = unrealized if event == "MARKED" else (0.0 if event == "CLOSED" else prior_unrealized)
            self._snapshot(connection, event_id, position, event, realized, unrealized_pnl, value["action_at"])
            row = connection.execute("SELECT * FROM official_option_sim_events WHERE id=?", (event_id,)).fetchone()
            return dict(row)

    def position_events(self, position_key: str) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            """SELECT event.* FROM official_option_sim_events event
               JOIN official_option_sim_positions position ON position.id=event.position_id
               WHERE position.position_key=? ORDER BY event.id""", (position_key,)
        )


__all__ = ["OfficialOptionSimulationJournal"]
