from __future__ import annotations

import csv
from datetime import datetime, timezone
import io

import pytest

from src.apps.worker.point_in_time_freezer import PointInTimeError, freeze_daily_ohlcv


AS_OF = datetime(2025, 2, 1, tzinfo=timezone.utc)


def row(session: str, *, opened: str | None = None, closed: str | None = None, available: str | None = None, close: str = "101") -> tuple[str, ...]:
    return ("AAPL", session, opened or f"{session}T14:30:00Z", closed or f"{session}T21:00:00Z", available or f"{session}T21:05:00Z", "100", "102", "99", close, "1000")


def daily_csv(rows: list[tuple[str, ...]]) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(("symbol", "session_date", "session_open_at", "session_close_at", "available_at", "open", "high", "low", "close", "volume"))
    writer.writerows(rows)
    return target.getvalue().encode()


def test_freeze_normalizes_unordered_rows_and_exposes_manifest_descriptor():
    ordered = freeze_daily_ohlcv(daily_csv([row("2025-01-02"), row("2025-01-03")]), as_of=AS_OF, allowed_symbols={"AAPL"})
    unordered = freeze_daily_ohlcv(daily_csv([row("2025-01-03"), row("2025-01-02")]), as_of=AS_OF, allowed_symbols={"AAPL"})

    assert ordered.canonical_csv == unordered.canonical_csv
    assert ordered.manifest_input()["rows"] == 2
    with pytest.raises(PointInTimeError):
        freeze_daily_ohlcv(daily_csv([row("2025-01-02"), row("2025-01-02")]), as_of=AS_OF)


@pytest.mark.parametrize(
    "rows",
    [
        [row("2025-01-02", opened="2025-01-02T00:00:00Z", closed="2025-01-02T00:00:00Z")],
        [row("2025-01-02", available="2025-02-02T21:05:00Z")],
        [row("2025-01-02"), row("2025-01-03", opened="2025-01-03T14:30:00Z", available="2025-01-03T21:05:00Z")],
        [("AAPL", "2025-01-02", "2025-01-02T14:30:00Z", "2025-01-02T21:00:00Z", "2025-01-02T21:05:00Z", "nan", "102", "99", "101", "1000")],
    ],
)
def test_freeze_rejects_bad_session_time_or_nonfinite_rows(rows):
    if len(rows) == 2:
        rows[0] = row("2025-01-02", available="2025-01-03T15:00:00Z")
    with pytest.raises(PointInTimeError):
        freeze_daily_ohlcv(daily_csv(rows), as_of=AS_OF)
