"""Compute Gate batch contract built on the single canonical PIT freezer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from typing import Collection, Iterable

from src.apps.worker.point_in_time_freezer import FrozenDailyOhlcv, PointInTimeError, freeze_daily_ohlcv


ADJUSTMENT_BASES = frozenset({"raw", "split_adjusted", "total_return"})


class ComputeBatchError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenComputeBatch:
    raw_sha256: str
    frozen: FrozenDailyOhlcv
    calendar_sha256: str
    adjustment_basis: str
    calendar_sessions: tuple[date, ...]

    def manifest(self) -> dict[str, object]:
        observed_by_symbol: dict[str, set[date]] = {}
        for bar in self.frozen.bars:
            observed_by_symbol.setdefault(bar.symbol, set()).add(bar.session_date)
        relevant_sessions = tuple(day for day in self.calendar_sessions if day <= self.frozen.dataset_end)
        missing_by_symbol = {
            symbol: [day.isoformat() for day in relevant_sessions if day not in observed]
            for symbol, observed in sorted(observed_by_symbol.items())
        }
        missing_union = sorted({day for days in missing_by_symbol.values() for day in days})
        return {
            "schema_version": 1,
            "raw_sha256": self.raw_sha256,
            "prices_sha256": self.frozen.sha256,
            "available_at": max(bar.available_at for bar in self.frozen.bars).isoformat().replace("+00:00", "Z"),
            "trading_calendar_sha256": self.calendar_sha256,
            "adjustment_basis": self.adjustment_basis,
            "missing_calendar_sessions": missing_union,
            "missing_calendar_sessions_by_symbol": missing_by_symbol,
        }


def freeze_compute_batch(
    raw_csv: bytes,
    *,
    as_of: datetime,
    allowed_symbols: Collection[str],
    trading_calendar: Iterable[date],
    adjustment_basis: str,
    expected_raw_sha256: str | None = None,
) -> FrozenComputeBatch:
    if adjustment_basis not in ADJUSTMENT_BASES:
        raise ComputeBatchError("adjustment_basis is invalid")
    digest = hashlib.sha256(raw_csv).hexdigest() if isinstance(raw_csv, bytes) else ""
    if not digest or expected_raw_sha256 is not None and digest != expected_raw_sha256:
        raise ComputeBatchError("raw CSV hash does not match the declared batch")
    declared_sessions = tuple(trading_calendar)
    if not declared_sessions or not all(isinstance(day, date) and not isinstance(day, datetime) for day in declared_sessions):
        raise ComputeBatchError("trading calendar is invalid")
    sessions = tuple(sorted(set(declared_sessions)))
    if declared_sessions != sessions:
        raise ComputeBatchError("trading calendar must be unique and strictly ordered")
    try:
        frozen = freeze_daily_ohlcv(raw_csv, as_of=as_of, allowed_symbols=allowed_symbols)
    except PointInTimeError as exc:
        raise ComputeBatchError(str(exc)) from exc
    if any(bar.session_date not in sessions for bar in frozen.bars):
        raise ComputeBatchError("OHLCV session is not in the declared trading calendar")
    calendar_body = json.dumps([day.isoformat() for day in sessions], separators=(",", ":")).encode()
    return FrozenComputeBatch(digest, frozen, hashlib.sha256(calendar_body).hexdigest(), adjustment_basis, sessions)
