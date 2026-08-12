from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import io

import pytest

from src.apps.worker.frozen_compute_batch import ComputeBatchError, freeze_compute_batch
from src.apps.worker.point_in_time_freezer import PointInTimeError
from src.apps.worker.us_equity_universe import (
    LAYERS,
    UniverseError,
    UniverseLayerReceipt,
    UniverseMember,
    build_universe_snapshot,
)


AS_OF = datetime(2025, 2, 1, tzinfo=timezone.utc)


def _member(symbol: str, layer: str, priority: int = 10) -> UniverseMember:
    return UniverseMember(
        symbol=symbol,
        source_layer=layer,
        inclusion_reason="historical constituent",
        priority=priority,
        valid_from=date(2024, 1, 1),
        valid_to=None,
        source_as_of=date(2025, 1, 31),
        listing_status="active",
    )


def _receipts(members: list[UniverseMember], *, source_as_of: date = date(2025, 1, 31)) -> list[UniverseLayerReceipt]:
    counts = {layer: 0 for layer in LAYERS}
    for member in members:
        counts[member.source_layer] += 1
    return [
        UniverseLayerReceipt(layer, hashlib.sha256(layer.encode()).hexdigest(), source_as_of, counts[layer])
        for layer in LAYERS
    ]


def _csv(rows: list[tuple[str, ...]]) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(("symbol", "session_date", "session_open_at", "session_close_at", "available_at", "open", "high", "low", "close", "volume"))
    writer.writerows(rows)
    return target.getvalue().encode()


def _row(session: str, close: str = "101") -> tuple[str, ...]:
    return ("AAPL", session, f"{session}T14:30:00Z", f"{session}T21:00:00Z", f"{session}T21:05:00Z", "100", "102", "99", close, "1000")


def test_universe_is_normalized_historical_deduplicated_and_hash_stable():
    first_members = [_member("aapl", "nasdaq100", 20), _member("AAPL", "popular", 5)]
    second_members = list(reversed(first_members))
    first = build_universe_snapshot(AS_OF, first_members, _receipts(first_members))
    second = build_universe_snapshot(AS_OF, second_members, reversed(_receipts(second_members)))

    assert first.sha256 == second.sha256
    assert first.symbols == ("AAPL",)
    assert [entry.source_layer for entry in first.members] == ["popular", "nasdaq100"]
    assert first.members[0].priority == 5


def test_universe_refuses_current_static_list_for_historical_as_of_and_bad_delisting():
    member = _member("AAPL", "sp500")
    with pytest.raises(UniverseError):
        build_universe_snapshot(
            AS_OF,
            [UniverseMember("AAPL", "sp500", "current list", 1, date(2024, 1, 1), None, date(2025, 2, 2), "active")],
            _receipts([member]),
        )
    with pytest.raises(UniverseError):
        UniverseMember("AAPL", "sp500", "bad delist", 1, date(2024, 1, 1), None, date(2025, 1, 1), "delisted")
    with pytest.raises(UniverseError):
        build_universe_snapshot(AS_OF, [member], _receipts([member])[:-1])


def test_compute_batch_binds_raw_hash_calendar_and_does_not_fill_gaps():
    raw = _csv([_row("2025-01-02"), _row("2025-01-06")])
    batch = freeze_compute_batch(
        raw,
        as_of=AS_OF,
        allowed_symbols={"AAPL"},
        trading_calendar=(date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)),
        adjustment_basis="split_adjusted",
    )

    assert batch.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert batch.frozen.row_count == 2
    assert batch.manifest()["missing_calendar_sessions"] == ["2025-01-03"]
    assert batch.manifest()["missing_calendar_sessions_by_symbol"] == {"AAPL": ["2025-01-03"]}
    with pytest.raises(ComputeBatchError):
        freeze_compute_batch(raw, as_of=AS_OF, allowed_symbols={"AAPL"}, trading_calendar=(date(2025, 1, 2),), adjustment_basis="split_adjusted")


def test_compute_batch_refuses_future_nonfinite_and_unordered_bars():
    future = _csv([("AAPL", "2025-01-02", "2025-01-02T14:30:00Z", "2025-01-02T21:00:00Z", "2025-02-02T21:05:00Z", "100", "102", "99", "101", "1000")])
    with pytest.raises((ComputeBatchError, PointInTimeError)):
        freeze_compute_batch(future, as_of=AS_OF, allowed_symbols={"AAPL"}, trading_calendar=(date(2025, 1, 2),), adjustment_basis="raw")
    unordered = _csv([_row("2025-01-03"), _row("2025-01-02", "nan")])
    with pytest.raises((ComputeBatchError, PointInTimeError)):
        freeze_compute_batch(unordered, as_of=AS_OF, allowed_symbols={"AAPL"}, trading_calendar=(date(2025, 1, 2), date(2025, 1, 3)), adjustment_basis="raw")


def test_compute_batch_reports_missing_sessions_per_symbol_without_cross_symbol_masking():
    rows = [
        _row("2025-01-02"),
        ("MSFT", "2025-01-02", "2025-01-02T14:30:00Z", "2025-01-02T21:00:00Z", "2025-01-02T21:05:00Z", "100", "102", "99", "101", "1000"),
        ("MSFT", "2025-01-03", "2025-01-03T14:30:00Z", "2025-01-03T21:00:00Z", "2025-01-03T21:05:00Z", "101", "103", "100", "102", "1100"),
    ]
    batch = freeze_compute_batch(
        _csv(rows),
        as_of=AS_OF,
        allowed_symbols={"AAPL", "MSFT"},
        trading_calendar=(date(2025, 1, 2), date(2025, 1, 3)),
        adjustment_basis="raw",
    )

    assert batch.manifest()["missing_calendar_sessions_by_symbol"] == {
        "AAPL": ["2025-01-03"],
        "MSFT": [],
    }
