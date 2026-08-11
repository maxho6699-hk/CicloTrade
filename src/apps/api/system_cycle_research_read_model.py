"""Sanitized, read-only browser projection for shadow system-cycle research."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Callable, Mapping

from core.compat import UTC
from core.system_cycle_research_contracts import (
    SystemCycleResearchError,
    parse_timestamp,
    validate_system_cycle_heartbeat,
    validate_system_cycle_result,
)
from core.system_cycle_research_store import SystemCycleResearchStore


VALIDATION_LABEL = "历史规则回放与状态扫描，不是严格样本外验证"
DEFAULT_STALE_SECONDS = 600
MIN_STALE_SECONDS = 30
MAX_STALE_SECONDS = 3600
DEGRADED_NO_DATA_COUNT = 7
NO_HEARTBEAT_RESULT_STALE_SECONDS = 12 * 60 * 60


class SystemCycleResearchReadModel:
    """Maps receiver-ledger rows to the intentionally small public contract."""

    def __init__(
        self,
        store: SystemCycleResearchStore,
        *,
        stale_seconds: int = DEFAULT_STALE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, SystemCycleResearchStore):
            raise TypeError("store must be a SystemCycleResearchStore")
        if not isinstance(stale_seconds, int) or not MIN_STALE_SECONDS <= stale_seconds <= MAX_STALE_SECONDS:
            raise ValueError("stale_seconds must be between 30 and 3600")
        self.store = store
        self.stale_seconds = stale_seconds
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def unavailable_status() -> dict[str, Any]:
        return {
            "available": False,
            "state": "waiting",
            "research_only": True,
            "actionable": False,
            "last_heartbeat_at": None,
            "last_result_at": None,
            "last_cycle_id": None,
            "stock_count": 0,
            "coverage_count": 0,
            "no_data_count": 0,
            "spool": None,
        }

    @staticmethod
    def unavailable_latest() -> dict[str, Any]:
        return {
            "available": False,
            "research_only": True,
            "actionable": False,
            "validation_label": VALIDATION_LABEL,
            "cycle": None,
        }

    @staticmethod
    def unavailable_history(limit: int) -> dict[str, Any]:
        return {
            "available": False,
            "research_only": True,
            "actionable": False,
            "limit": limit,
            "items": [],
        }

    def status(self) -> dict[str, Any]:
        snapshot = self.store.research_read_snapshot(1)
        latest = self._result(snapshot["latest"])
        heartbeat = self._heartbeat(snapshot["heartbeat"])
        if latest is None:
            result = self.unavailable_status()
            if heartbeat is not None:
                result["last_heartbeat_at"] = heartbeat["heartbeat_at"]
                result["spool"] = dict(heartbeat["spool"])
                if self._is_stale(heartbeat["heartbeat_at"]):
                    result["state"] = "stale"
            return result

        coverage_count, no_data_count = self._counts(latest)
        return {
            "available": True,
            "state": self._state(latest, snapshot["latest"], heartbeat, no_data_count),
            "research_only": True,
            "actionable": False,
            "last_heartbeat_at": heartbeat["heartbeat_at"] if heartbeat else None,
            "last_result_at": latest["evaluated_at"],
            "last_cycle_id": latest["cycle_id"],
            "stock_count": len(latest["stocks"]),
            "coverage_count": coverage_count,
            "no_data_count": no_data_count,
            "spool": dict(heartbeat["spool"]) if heartbeat else None,
        }

    def latest(self) -> dict[str, Any]:
        row = self.store.research_read_snapshot(1)["latest"]
        latest = self._result(row)
        if latest is None:
            return self.unavailable_latest()
        return {
            "available": True,
            "research_only": True,
            "actionable": False,
            "validation_label": VALIDATION_LABEL,
            "cycle": self._cycle(latest, str(row["result_sha256"])),
        }

    def history(self, limit: int) -> dict[str, Any]:
        snapshot = self.store.research_read_snapshot(limit)
        items = [
            self._summary(value, row)
            for row in snapshot["history"]
            if (value := self._result(row)) is not None
        ]
        return {
            "available": bool(items),
            "research_only": True,
            "actionable": False,
            "limit": limit,
            "items": items,
        }

    def _state(
        self,
        latest: Mapping[str, Any],
        latest_row: Mapping[str, Any],
        heartbeat: Mapping[str, Any] | None,
        no_data_count: int,
    ) -> str:
        # A producer emits a result once per market cycle, so a fresh heartbeat is
        # the liveness source of truth. Without one, a result may remain useful for
        # up to 12 hours before it is called stale.
        if heartbeat is not None:
            if self._is_stale(str(heartbeat["heartbeat_at"])):
                return "stale"
        elif all(
            self._is_stale(value, NO_HEARTBEAT_RESULT_STALE_SECONDS)
            for value in (str(latest["evaluated_at"]), str(latest_row["received_at"]))
        ):
            return "stale"
        if no_data_count >= DEGRADED_NO_DATA_COUNT:
            return "degraded"
        return "healthy"

    def _is_stale(self, value: str, threshold_seconds: int | None = None) -> bool:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise SystemCycleResearchError("research read-model clock must include a timezone")
        age = (now.astimezone(UTC) - parse_timestamp(value, "stored_at")).total_seconds()
        return age > (self.stale_seconds if threshold_seconds is None else threshold_seconds)

    @staticmethod
    def _result(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            value = json.loads(str(row["payload_json"]))
            return validate_system_cycle_result(value)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, SystemCycleResearchError):
            return None

    @staticmethod
    def _heartbeat(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            value = json.loads(str(row["payload_json"]))
            return validate_system_cycle_heartbeat(value)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, SystemCycleResearchError):
            return None

    @staticmethod
    def _counts(result: Mapping[str, Any]) -> tuple[int, int]:
        stocks = result["stocks"]
        return (
            sum(stock["status"] == "coverage" for stock in stocks),
            sum(stock["status"] == "no_data" for stock in stocks),
        )

    def _cycle(self, result: Mapping[str, Any], result_sha256: str) -> dict[str, Any]:
        coverage_count, no_data_count = self._counts(result)
        cycle = result["cycle"]
        stocks = [
            {
                key: stock[key]
                for key in (
                    "market", "symbol", "status", "rows", "dataset_end", "selected",
                    "signal_state", "latest_price", "target_quantity",
                )
            }
            for stock in result["stocks"]
        ]
        return {
            "cycle_id": result["cycle_id"],
            "evaluation_date": cycle["evaluation_date"],
            "cycle_slot": cycle["cycle_slot"],
            "strategy_key": cycle["strategy_key"],
            "strategy_name": cycle["strategy_name"],
            "strategy_version": cycle["strategy_version"],
            "evaluated_at": result["evaluated_at"],
            "coverage_count": coverage_count,
            "no_data_count": no_data_count,
            "selected_symbols": list(cycle["selected_symbols"]),
            "stocks": stocks,
            "evidence": {
                "universe_sha256": result["universe"]["sha256"],
                "source_snapshot_sha256": result["inputs"]["source_snapshot_sha256"],
                "catalog_snapshot_sha256": result["inputs"]["catalog_snapshot_sha256"],
                "code_bundle_sha256": result["inputs"]["code_bundle_sha256"],
                "result_sha256": result_sha256,
            },
        }

    def _summary(self, result: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
        coverage_count, no_data_count = self._counts(result)
        cycle = result["cycle"]
        return {
            "cycle_id": result["cycle_id"],
            "evaluation_date": cycle["evaluation_date"],
            "cycle_slot": cycle["cycle_slot"],
            "strategy_key": cycle["strategy_key"],
            "strategy_name": cycle["strategy_name"],
            "strategy_version": cycle["strategy_version"],
            "evaluated_at": result["evaluated_at"],
            "received_at": str(row["received_at"]),
            "coverage_count": coverage_count,
            "no_data_count": no_data_count,
            "selected_count": len(cycle["selected_symbols"]),
        }
