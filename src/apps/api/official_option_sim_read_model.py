"""Private, source-anonymous projections for official option simulation evidence."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from src.apps.api.earnings_read_model import OpaqueIdCodec


class OfficialOptionSimulationNotFound(LookupError):
    pass


class OfficialOptionSimulationReadModel:
    def __init__(self, db_path: str | Path, codec: OpaqueIdCodec):
        self.db_path, self.codec = Path(db_path).resolve(), codec

    def _connection(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise OfficialOptionSimulationNotFound("official simulation unavailable")
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _locked() -> dict[str, Any]:
        return {
            "state": "locked", "feature": "option_auto_paper_official",
            "required_capability": "option_auto_paper_official", "reason_code": "capability_required",
            "upgrade_path": "/membership",
        }

    @staticmethod
    def _base() -> dict[str, Any]:
        return {
            "account_mode": "official_simulation", "execution_label": "真实行情模拟执行",
            "paper": True, "research_only": False, "broker_execution": False,
            "real_account_return": False, "disclaimer": "此为真实行情下的模拟执行记录，不代表真实账户收益。",
        }

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_type": row["event_type"], "state": row["lifecycle_state"],
            "action_at": row["action_at"], "recorded_at": row["recorded_at"],
            "cash_flow": row["cash_flow"], "realized_pnl": row["realized_pnl"],
            "unrealized_pnl": row["unrealized_pnl"],
        }

    def overview(self, *, has_capability: bool, limit: int = 50) -> dict[str, Any]:
        if not has_capability:
            return self._locked()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit is invalid")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT position.*,event.event_type,event.lifecycle_state,event.action_at,event.recorded_at,
                          snapshot.equity,snapshot.realized_pnl,snapshot.unrealized_pnl,snapshot.drawdown
                   FROM official_option_sim_positions position
                   JOIN official_option_sim_events event ON event.id=(
                       SELECT current.id FROM official_option_sim_events current
                       WHERE current.position_id=position.id ORDER BY current.id DESC LIMIT 1)
                   LEFT JOIN official_option_sim_equity_snapshots snapshot ON snapshot.event_id=event.id
                   ORDER BY event.id DESC LIMIT ?""", (limit,),
            ).fetchall()
        items = [{
            "id": self.codec.encode("official-option-position", int(row["id"])),
            "underlying": row["underlying"], "structure": row["structure_type"], "currency": row["currency"],
            "state": row["lifecycle_state"], "last_event": row["event_type"], "action_at": row["action_at"],
            "equity": row["equity"], "realized_pnl": row["realized_pnl"],
            "unrealized_pnl": row["unrealized_pnl"], "drawdown": row["drawdown"],
            "max_loss": row["max_loss"], "max_account_pct": row["max_account_pct"],
            "invalidation_condition": row["invalidation_condition"],
        } for row in rows]
        return {**self._base(), "state": "ready" if items else "no_data", "items": items}

    def detail(self, *, has_capability: bool, opaque_id: str) -> dict[str, Any]:
        if not has_capability:
            return self._locked()
        try:
            position_id = self.codec.decode("official-option-position", opaque_id)
        except Exception as exc:
            raise OfficialOptionSimulationNotFound("official simulation unavailable") from exc
        with self._connection() as connection:
            position = connection.execute("SELECT * FROM official_option_sim_positions WHERE id=?", (position_id,)).fetchone()
            events = connection.execute("SELECT * FROM official_option_sim_events WHERE position_id=? ORDER BY id", (position_id,)).fetchall()
            snapshots = connection.execute("SELECT * FROM official_option_sim_equity_snapshots WHERE position_id=? ORDER BY id", (position_id,)).fetchall()
            legs = connection.execute(
                """SELECT leg.*,event.event_type FROM official_option_sim_event_legs leg
                   JOIN official_option_sim_events event ON event.id=leg.event_id
                   WHERE event.position_id=? ORDER BY leg.event_id,leg.leg_no""", (position_id,)
            ).fetchall()
        if position is None:
            raise OfficialOptionSimulationNotFound("official simulation unavailable")
        return {
            **self._base(), "state": "ready", "id": opaque_id, "underlying": position["underlying"],
            "structure": position["structure_type"], "currency": position["currency"], "max_loss": position["max_loss"],
            "max_account_pct": position["max_account_pct"], "invalidation_condition": position["invalidation_condition"],
            "timeline": [self._event(row) for row in events],
            "legs": [{
                "event_type": row["event_type"], "side": row["side"], "quantity": row["quantity"],
                "expiry": row["expiry"], "right": row["right"], "strike": row["strike"], "multiplier": row["multiplier"],
                "bid": row["bid"], "ask": row["ask"], "quote_at": row["quote_at"], "execution_price": row["execution_price"],
            } for row in legs],
            "equity_history": [{
                "captured_at": row["captured_at"], "equity": row["equity"], "realized_pnl": row["realized_pnl"],
                "unrealized_pnl": row["unrealized_pnl"], "drawdown": row["drawdown"],
            } for row in snapshots],
        }


__all__ = ["OfficialOptionSimulationNotFound", "OfficialOptionSimulationReadModel"]
