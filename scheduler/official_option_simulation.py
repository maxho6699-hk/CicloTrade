"""Seals official-simulation work without starting a broker or a live order."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol

from core.official_option_sim_contracts import OfficialOptionSimulationError, sha256_json, stamp


@dataclass(frozen=True)
class OfficialOptionSimulationTask:
    task_id: str
    task_type: str
    payload: Mapping[str, Any]
    payload_sha256: str
    authority: Mapping[str, Any]


class SimulationDueSource(Protocol):
    def due(self, as_of: datetime) -> list[Mapping[str, Any]]: ...


class SimulationTaskSink(Protocol):
    def seal(self, task: OfficialOptionSimulationTask) -> tuple[OfficialOptionSimulationTask, bool]: ...


_AUTHORITY = {
    "account_mode": "official_simulation", "real_quote_paper_execution": True,
    "broker_execution": False, "personal_order_write": False, "telegram_enabled": False,
    "requires_actionable_realtime_quotes": True, "fok_all_or_none": True,
}
_TYPES = {"proposal", "open", "mark", "close", "risk_review"}


def seal_simulation_task(task_type: str, payload: Mapping[str, Any]) -> OfficialOptionSimulationTask:
    if task_type not in _TYPES or not isinstance(payload, Mapping):
        raise OfficialOptionSimulationError("official simulation task is invalid")
    raw = {"schema_version": 1, "task_type": task_type, **dict(payload)}
    digest = sha256_json(raw)
    return OfficialOptionSimulationTask(
        task_id=f"official-option-sim:{digest}", task_type=task_type, payload=raw,
        payload_sha256=digest, authority=dict(_AUTHORITY),
    )


class OfficialOptionSimulationScheduler:
    """Turns due research decisions into sealed paper-only tasks.

    A caller must later enable and configure a signed receiver; this scheduler
    never contacts a quote source, Telegram, broker, or remote worker itself.
    """
    def __init__(self, source: SimulationDueSource, sink: SimulationTaskSink, *, clock: Callable[[], datetime]):
        self.source, self.sink, self.clock = source, sink, clock

    def run(self) -> dict[str, Any]:
        try:
            moment = self.clock()
            stamp(moment, "scheduler clock")
            due = list(self.source.due(moment))
        except Exception:
            return {"status": "blocked", "due": 0, "created": 0, "reused": 0, "reason": "simulation inputs unavailable"}
        created = reused = 0
        for item in due:
            if not isinstance(item, Mapping) or set(item) != {"type", "payload"}:
                raise OfficialOptionSimulationError("simulation task source returned an invalid item")
            task = seal_simulation_task(str(item["type"]), item["payload"])
            sealed, was_created = self.sink.seal(task)
            if sealed.task_id != task.task_id or sealed.payload_sha256 != task.payload_sha256:
                raise OfficialOptionSimulationError("simulation task sink returned a mismatched task")
            created += int(bool(was_created))
            reused += int(not was_created)
        return {"status": "sealed" if due else "no_data", "due": len(due), "created": created, "reused": reused}


__all__ = ["OfficialOptionSimulationScheduler", "OfficialOptionSimulationTask", "seal_simulation_task"]
