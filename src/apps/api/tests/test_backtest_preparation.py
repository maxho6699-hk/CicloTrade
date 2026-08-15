from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading

import pandas as pd
import pytest

from core.backtest_artifacts import ArtifactStore
from core.backtest_queue import BacktestQueue, BacktestQueueError
from core.backtest_queue_database import BacktestQueueDatabase
from data.datasource import DataSourceError
from src.apps.api.backtest_preparation import BacktestPreparationService


NOW = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
REQUEST = {
    "schema_version": 1,
    "type": "backtest.run.v1",
    "template_key": "equity.trend.long_flat.v1",
    "symbol": "AAPL",
    "timeframe": "1d",
    "sample_years": 1,
    "lookback": 20,
}


def _frame(*, rows: int = 300, end: str = "2026-08-14") -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=rows, tz="America/New_York")
    base = pd.Series(range(rows), index=index, dtype=float) / 10 + 100
    return pd.DataFrame(
        {
            "Open": base,
            "High": base + 2,
            "Low": base - 2,
            "Close": base + 1,
            "Volume": pd.Series([1_000_000] * rows, index=index, dtype=float),
        },
        index=index,
    )


class Source:
    name = "test-us-equity-source"

    def __init__(
        self,
        frame: pd.DataFrame | None = None,
        error: Exception | None = None,
        *,
        instrument_type: str = "股票",
        exchange: str = "NASDAQ",
    ) -> None:
        self.frame = _frame() if frame is None else frame
        self.error = error
        self.instrument_type = instrument_type
        self.exchange = exchange
        self.calls: list[tuple[str, str, str]] = []
        self.search_calls: list[tuple[str, str, int]] = []

    def search(self, query: str, market: str = "美股", max_results: int = 8):
        self.search_calls.append((query, market, max_results))
        return [{
            "symbol": query,
            "name": query,
            "exchange": self.exchange,
            "type": self.instrument_type,
        }]

    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        self.calls.append((symbol, period, interval))
        if self.error is not None:
            raise self.error
        return self.frame.copy()


def _queue(tmp_path) -> BacktestQueue:
    return BacktestQueue(
        BacktestQueueDatabase(tmp_path / "queue.db"),
        ArtifactStore(tmp_path / "artifacts", max_bytes=16 * 1024 * 1024),
    )


def _service(tmp_path, source: Source | None = None, queue: BacktestQueue | None = None):
    queue = queue or _queue(tmp_path)
    source = source or Source()
    service = BacktestPreparationService(queue, data_source=source, now=lambda: NOW)
    return service, queue, source


@pytest.mark.parametrize(
    "payload",
    [
        {**REQUEST, "manifest": {}},
        {**REQUEST, "code_bundle_sha256": "0" * 64},
        {**REQUEST, "inputs": []},
        {**REQUEST, "python_code": "import os"},
        {**REQUEST, "unknown": True},
        {**REQUEST, "schema_version": True},
        {**REQUEST, "type": "backtest.optimize.v1"},
        {**REQUEST, "template_key": "option.long_call.v1"},
        {**REQUEST, "symbol": "../AAPL"},
        {**REQUEST, "timeframe": "1h"},
        {**REQUEST, "sample_years": 2},
        {**REQUEST, "lookback": 1},
        {**REQUEST, "lookback": 251},
    ],
)
def test_browser_request_is_exact_and_cannot_supply_execution_material(tmp_path, payload):
    service, queue, source = _service(tmp_path)

    with pytest.raises(BacktestQueueError):
        service.prepare(1, "高级版", payload, "strictkey")

    assert source.calls == []
    assert queue.list(1) == []


@pytest.mark.parametrize(
    ("plan", "sample_years", "allowed"),
    [
        ("免费版", 1, True),
        ("免费版", 3, False),
        ("标准版", 3, True),
        ("标准版", 10, False),
        ("高级版", 10, True),
        ("专业版", 10, True),
        ("定制版", 10, True),
        ("unknown-plan", 10, False),
    ],
)
def test_sample_window_is_revalidated_from_server_plan(tmp_path, plan, sample_years, allowed):
    service, queue, source = _service(tmp_path)
    payload = {**REQUEST, "sample_years": sample_years}

    if allowed:
        key = f"window-{sample_years}-{abs(hash(plan))}"
        job, created = service.prepare(1, plan, payload, key)
        assert created is True
        assert job["manifest"]["provenance"]["sample_years"] == sample_years
    else:
        with pytest.raises(BacktestQueueError) as denied:
            service.prepare(1, plan, payload, f"window-{sample_years}-{abs(hash(plan))}")
        assert denied.value.status == 403
        assert source.calls == []
        assert queue.list(1) == []


def test_success_freezes_real_pit_input_before_job_becomes_claimable(tmp_path):
    service, queue, source = _service(tmp_path)

    job, created = service.prepare(1, "免费版", REQUEST, "prepare1")

    assert created is True
    assert source.search_calls == [("AAPL", "美股", 8)]
    assert source.calls == [("AAPL", "1y", "1d")]
    provenance = job["manifest"]["provenance"]
    assert provenance["adapter"] == "test-us-equity-source"
    assert provenance["asset_type"] == "股票"
    assert provenance["exchange"] == "NASDAQ"
    assert provenance["availability_policy"] == (
        "synthetic_23_59_59_america_new_york_completed_session_v1"
    )
    descriptor = job["manifest"]["inputs"][0]
    stored = queue.db.fetch_one(
        "SELECT * FROM backtest_job_artifacts WHERE job_id=? AND direction='input'",
        (job["id"],),
    )
    assert stored is not None
    assert descriptor == {
        "artifact_key": "prices.csv",
        "sha256": stored["sha256"],
        "bytes": stored["bytes"],
        "rows": stored["row_count"],
        "dataset_end": job["manifest"]["dataset_end"],
    }
    body = queue.artifacts.read(stored["storage_key"], stored["sha256"])
    assert len(body) == descriptor["bytes"]
    assert queue.claim("worker", 60)["id"] == job["id"]


@pytest.mark.parametrize(
    "template",
    [
        "equity.trend.long_flat.v1",
        "equity.mean_reversion.long_flat.v1",
        "equity.breakout.long_flat.v1",
    ],
)
def test_all_three_server_templates_are_available_without_user_code(tmp_path, template):
    service, _, _ = _service(tmp_path)

    job, created = service.prepare(
        1,
        "免费版",
        {**REQUEST, "template_key": template},
        f"template-{template.split('.')[1]}",
    )

    assert created is True
    assert job["manifest"]["template_key"] == template


@pytest.mark.parametrize(
    "source",
    [
        Source(error=DataSourceError("unavailable")),
        Source(frame=pd.DataFrame()),
        Source(frame=_frame(rows=1, end="2026-08-16")),
        Source(frame=_frame().assign(High=1.0)),
    ],
)
def test_source_or_freeze_failure_creates_no_job(tmp_path, source):
    service, queue, _ = _service(tmp_path, source=source)

    with pytest.raises(BacktestQueueError):
        service.prepare(1, "免费版", REQUEST, "sourcefail")

    assert queue.list(1) == []


def test_input_registration_failure_cancels_half_prepared_job(tmp_path):
    class FailingQueue(BacktestQueue):
        def register_input(self, *args, **kwargs):
            raise BacktestQueueError("freeze write failed", 409)

    queue = FailingQueue(
        BacktestQueueDatabase(tmp_path / "queue.db"),
        ArtifactStore(tmp_path / "artifacts", max_bytes=16 * 1024 * 1024),
    )
    service, _, _ = _service(tmp_path, queue=queue)

    with pytest.raises(BacktestQueueError):
        service.prepare(1, "免费版", REQUEST, "registerfail")

    jobs = queue.list(1)
    assert len(jobs) == 1 and jobs[0]["status"] == "cancelled"
    assert queue.claim("worker", 60) is None


def test_verified_input_then_exception_and_cancel_failure_still_cannot_claim(tmp_path):
    class FailingFinalizeQueue(BacktestQueue):
        def register_input(self, *args, **kwargs):
            super().register_input(*args, **kwargs)
            raise RuntimeError("fault after durable input commit")

        def cancel(self, *args, **kwargs):
            raise RuntimeError("fault during cancellation")

    queue = FailingFinalizeQueue(
        BacktestQueueDatabase(tmp_path / "queue.db"),
        ArtifactStore(tmp_path / "artifacts", max_bytes=16 * 1024 * 1024),
    )
    service, _, _ = _service(tmp_path, queue=queue)

    with pytest.raises(RuntimeError, match="durable input"):
        service.prepare(1, "免费版", REQUEST, "durable-failure")

    jobs = queue.list(1)
    assert len(jobs) == 1 and jobs[0]["status"] == "preparing"
    assert queue.inputs_ready(jobs[0]["id"], 1) is True
    assert queue.claim("worker", 60) is None


@pytest.mark.parametrize("symbol", ["BTC-USD", "ETH-USD"])
def test_non_equity_symbol_is_rejected_before_historical_bars_are_fetched(tmp_path, symbol):
    source = Source(instrument_type="加密货币", exchange="CCC")
    service, queue, _ = _service(tmp_path, source=source)

    with pytest.raises(BacktestQueueError) as denied:
        service.prepare(1, "免费版", {**REQUEST, "symbol": symbol}, f"crypto-{symbol}")

    assert denied.value.status == 403
    assert source.search_calls == [(symbol, "美股", 8)]
    assert source.calls == []
    assert queue.list(1) == []


def test_idempotency_reuses_same_browser_request_and_conflicts_before_refetch(tmp_path):
    service, queue, source = _service(tmp_path)
    first, created = service.prepare(1, "免费版", REQUEST, "same-request")
    source.error = DataSourceError("must not refetch")

    repeated, repeated_created = service.prepare(1, "免费版", REQUEST, "same-request")

    assert repeated_created is False and repeated["id"] == first["id"]
    assert len(source.calls) == 1
    with pytest.raises(BacktestQueueError) as conflict:
        service.prepare(1, "免费版", {**REQUEST, "lookback": 50}, "same-request")
    assert conflict.value.status == 409
    assert len(source.calls) == 1
    with pytest.raises(BacktestQueueError):
        queue.get(first["id"], 2)


def test_concurrent_same_idempotency_request_converges_on_one_prepared_job(tmp_path):
    barrier = threading.Barrier(2)

    class ConcurrentSource(Source):
        def search(self, query: str, market: str = "美股", max_results: int = 8):
            result = super().search(query, market, max_results)
            barrier.wait(timeout=5)
            return result

    moments = iter((NOW, NOW + timedelta(seconds=5)))
    moment_lock = threading.Lock()

    def current_time():
        with moment_lock:
            return next(moments)

    queue = _queue(tmp_path)
    service = BacktestPreparationService(
        queue, data_source=ConcurrentSource(), now=current_time
    )
    outcomes: list[tuple[dict, bool]] = []
    errors: list[Exception] = []

    def prepare():
        try:
            outcomes.append(
                service.prepare(1, "免费版", REQUEST, "concurrent-request")
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=prepare) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(outcomes) == 2
    assert len({item[0]["id"] for item in outcomes}) == 1
    assert sorted(item[1] for item in outcomes) == [False, True]
    assert queue.list(1)[0]["status"] == "queued"
