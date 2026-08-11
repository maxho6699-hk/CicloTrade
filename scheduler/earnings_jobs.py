"""Deterministic research-only task contracts for earnings evidence workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.earnings_forecast_contracts import parse_timestamp, sha256_json


class SchedulerContractError(ValueError):
    """Raised when a due task would violate immutable research timing."""


@dataclass(frozen=True)
class ForecastTaskInput:
    event_revision_id: int
    scheduled_at: str
    exchange_timezone: str
    decision_at: str
    model_request_version: str


@dataclass(frozen=True)
class OutcomeTaskInput:
    event_revision_id: int
    scheduled_at: str
    checkpoint: str
    due_at: str


@dataclass(frozen=True)
class PostmortemTaskInput:
    event_revision_id: int
    scheduled_at: str
    due_at: str


@dataclass(frozen=True)
class SealedResearchTask:
    task_id: str
    task_type: str
    payload: Mapping[str, Any]
    payload_sha256: str
    authority: Mapping[str, Any]


class DueSource(Protocol):
    def due(self, as_of: datetime) -> list[Any]: ...


class TaskSink(Protocol):
    def seal(self, task: SealedResearchTask) -> tuple[SealedResearchTask, bool]: ...


_AUTHORITY = {
    "publication_state": "research",
    "research_only": True,
    "execution_eligible": False,
    "automatic_ordering": False,
    "telegram_enabled": False,
    "quant_event_write_enabled": False,
    "broker_execution_enabled": False,
}
_CHECKPOINTS = {"AFTER_HOURS", "NEXT_CLOSE", "D3_CLOSE", "D5_CLOSE"}


def _event_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchedulerContractError("event_revision_id must be a positive integer")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise SchedulerContractError(f"{label} is invalid")
    return value


def _time(value: Any, label: str) -> datetime:
    try:
        return parse_timestamp(value, label)
    except ValueError as exc:
        raise SchedulerContractError(str(exc)) from exc


def _seal(task_type: str, payload: dict[str, Any]) -> SealedResearchTask:
    normalized = {"schema_version": 1, **payload}
    digest = sha256_json(normalized)
    identity = sha256_json({"task_type": task_type, "payload_sha256": digest})
    return SealedResearchTask(
        task_id=f"{task_type}:{identity}",
        task_type=task_type,
        payload=normalized,
        payload_sha256=digest,
        authority=dict(_AUTHORITY),
    )


def _forecast(value: ForecastTaskInput) -> SealedResearchTask:
    event_id = _event_id(value.event_revision_id)
    scheduled = _time(value.scheduled_at, "scheduled_at")
    decision = _time(value.decision_at, "decision_at")
    if decision >= scheduled:
        raise SchedulerContractError("forecast decision_at must be before scheduled_at")
    try:
        ZoneInfo(_text(value.exchange_timezone, "exchange_timezone"))
    except ZoneInfoNotFoundError as exc:
        raise SchedulerContractError("exchange_timezone is invalid") from exc
    return _seal("earnings.forecast_snapshot.v1", {
        "event_revision_id": event_id,
        "scheduled_at": scheduled.isoformat(),
        "exchange_timezone": value.exchange_timezone,
        "decision_at": decision.isoformat(),
        "model_request_version": _text(
            value.model_request_version, "model_request_version"
        ),
        "publication_state": "research",
    })


def _outcome(value: OutcomeTaskInput) -> SealedResearchTask:
    event_id = _event_id(value.event_revision_id)
    scheduled = _time(value.scheduled_at, "scheduled_at")
    due = _time(value.due_at, "due_at")
    if due <= scheduled:
        raise SchedulerContractError("outcome due_at must be after scheduled_at")
    if value.checkpoint not in _CHECKPOINTS:
        raise SchedulerContractError("outcome checkpoint is invalid")
    return _seal("earnings.outcome_observation.v1", {
        "event_revision_id": event_id,
        "scheduled_at": scheduled.isoformat(),
        "checkpoint": value.checkpoint,
        "due_at": due.isoformat(),
        "trusted_session_validation_required": value.checkpoint in {"D3_CLOSE", "D5_CLOSE"},
        "publication_state": "research",
    })


def _postmortem(value: PostmortemTaskInput) -> SealedResearchTask:
    event_id = _event_id(value.event_revision_id)
    scheduled = _time(value.scheduled_at, "scheduled_at")
    due = _time(value.due_at, "due_at")
    if due <= scheduled:
        raise SchedulerContractError("postmortem due_at must be after scheduled_at")
    return _seal("earnings.postmortem.v1", {
        "event_revision_id": event_id,
        "scheduled_at": scheduled.isoformat(),
        "due_at": due.isoformat(),
        "completion_state": "pending_trusted_session_validation",
        "trusted_d5_session_required": True,
        "publication_state": "research",
    })


class EarningsResearchScheduler:
    def __init__(
        self, source: DueSource, sink: TaskSink, *, clock: Callable[[], datetime]
    ):
        self.source = source
        self.sink = sink
        self.clock = clock

    @staticmethod
    def _task(value: Any) -> SealedResearchTask:
        if isinstance(value, ForecastTaskInput):
            return _forecast(value)
        if isinstance(value, OutcomeTaskInput):
            return _outcome(value)
        if isinstance(value, PostmortemTaskInput):
            return _postmortem(value)
        raise SchedulerContractError("unsupported earnings research task input")

    def run(self) -> dict[str, Any]:
        as_of = _time(self.clock(), "scheduler clock")
        try:
            due = list(self.source.due(as_of))
        except Exception:
            return {
                "status": "blocked", "due": 0, "created": 0, "reused": 0,
                "reason": "research inputs unavailable",
            }
        if not due:
            return {"status": "no_data", "due": 0, "created": 0, "reused": 0}
        created = reused = 0
        for value in due:
            task = self._task(value)
            sealed, was_created = self.sink.seal(task)
            if sealed.task_id != task.task_id or sealed.payload_sha256 != task.payload_sha256:
                raise SchedulerContractError("task sink returned a mismatched sealed task")
            created += int(bool(was_created))
            reused += int(not was_created)
        return {
            "status": "sealed", "due": len(due),
            "created": created, "reused": reused,
        }


__all__ = [
    "EarningsResearchScheduler", "ForecastTaskInput", "OutcomeTaskInput",
    "PostmortemTaskInput", "SchedulerContractError", "SealedResearchTask",
]
