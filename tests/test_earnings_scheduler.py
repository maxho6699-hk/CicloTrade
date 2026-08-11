from __future__ import annotations

from datetime import datetime
import inspect

import pytest

from core.compat import UTC
from scheduler.earnings_jobs import (
    EarningsResearchScheduler,
    ForecastTaskInput,
    OutcomeTaskInput,
    PostmortemTaskInput,
    SchedulerContractError,
)


AS_OF = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


class Source:
    def __init__(self, tasks=(), error: Exception | None = None):
        self.tasks = list(tasks)
        self.error = error

    def due(self, as_of):
        if self.error:
            raise self.error
        return list(self.tasks)


class Sink:
    def __init__(self):
        self.tasks = {}

    def seal(self, task):
        existing = self.tasks.get(task.task_id)
        if existing:
            if existing.payload_sha256 != task.payload_sha256:
                raise AssertionError("task id was reused with different content")
            return existing, False
        self.tasks[task.task_id] = task
        return task, True


def _inputs():
    return [
        ForecastTaskInput(
            event_revision_id=11,
            scheduled_at="2026-08-18T20:15:00Z",
            exchange_timezone="America/New_York",
            decision_at="2026-08-12T00:00:00Z",
            model_request_version="earnings-research-v1",
        ),
        OutcomeTaskInput(
            event_revision_id=11,
            scheduled_at="2026-08-18T20:15:00Z",
            checkpoint="NEXT_CLOSE",
            due_at="2026-08-19T20:00:00Z",
        ),
        PostmortemTaskInput(
            event_revision_id=11,
            scheduled_at="2026-08-18T20:15:00Z",
            due_at="2026-08-25T20:05:00Z",
        ),
    ]


def test_scheduler_seals_only_research_tasks_and_retries_are_idempotent():
    sink = Sink()
    scheduler = EarningsResearchScheduler(Source(_inputs()), sink, clock=lambda: AS_OF)

    first = scheduler.run()
    second = scheduler.run()

    assert first == {"status": "sealed", "due": 3, "created": 3, "reused": 0}
    assert second == {"status": "sealed", "due": 3, "created": 0, "reused": 3}
    assert len(sink.tasks) == 3
    assert {task.task_type for task in sink.tasks.values()} == {
        "earnings.forecast_snapshot.v1",
        "earnings.outcome_observation.v1",
        "earnings.postmortem.v1",
    }
    for task in sink.tasks.values():
        assert task.authority == {
            "publication_state": "research",
            "research_only": True,
            "execution_eligible": False,
            "automatic_ordering": False,
            "telegram_enabled": False,
            "quant_event_write_enabled": False,
            "broker_execution_enabled": False,
        }
        assert len(task.payload_sha256) == 64


def test_scheduler_no_data_and_source_failure_are_fail_closed_and_generic():
    sink = Sink()
    assert EarningsResearchScheduler(Source(), sink, clock=lambda: AS_OF).run() == {
        "status": "no_data", "due": 0, "created": 0, "reused": 0,
    }
    blocked = EarningsResearchScheduler(
        Source(error=RuntimeError("private provider failed at C:/secret")),
        sink,
        clock=lambda: AS_OF,
    ).run()
    assert blocked == {
        "status": "blocked", "due": 0, "created": 0, "reused": 0,
        "reason": "research inputs unavailable",
    }
    assert sink.tasks == {}


def test_scheduler_rejects_lookahead_and_invalid_outcome_or_postmortem_timing():
    unsafe = ForecastTaskInput(
        event_revision_id=11,
        scheduled_at="2026-08-18T20:15:00Z",
        exchange_timezone="America/New_York",
        decision_at="2026-08-18T21:00:00Z",
        model_request_version="earnings-research-v1",
    )
    with pytest.raises(SchedulerContractError, match="before scheduled_at"):
        EarningsResearchScheduler(Source([unsafe]), Sink(), clock=lambda: AS_OF).run()

    outcome = OutcomeTaskInput(
        event_revision_id=11,
        scheduled_at="2026-08-18T20:15:00Z",
        checkpoint="NEXT_CLOSE",
        due_at="2026-08-18T19:00:00Z",
    )
    with pytest.raises(SchedulerContractError, match="after scheduled_at"):
        EarningsResearchScheduler(Source([outcome]), Sink(), clock=lambda: AS_OF).run()

    postmortem = PostmortemTaskInput(
        event_revision_id=11,
        scheduled_at="2026-08-18T20:15:00Z",
        due_at="2026-08-18T20:15:00Z",
    )
    with pytest.raises(SchedulerContractError, match="after scheduled_at"):
        EarningsResearchScheduler(Source([postmortem]), Sink(), clock=lambda: AS_OF).run()


def test_scheduler_module_has_no_execution_publication_or_quant_journal_dependency():
    import scheduler.earnings_jobs as module

    source = inspect.getsource(module)
    forbidden = (
        "trading.order_manager", "notification.", "QuantJournal",
        "INSERT INTO quant_events", "send_telegram", "broker_api",
    )
    assert not any(value in source for value in forbidden)
