"""Strict deterministic freezing for point-in-time daily equity observations."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import io
import math
import re
from typing import Collection, Iterable


CANONICAL_COLUMNS = (
    "symbol", "session_date", "session_open_at", "session_close_at", "available_at",
    "open", "high", "low", "close", "volume",
)
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")


class PointInTimeError(ValueError):
    """Raised when an input cannot support time-bounded research."""


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    session_date: date
    session_open_at: datetime
    session_close_at: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class FrozenDailyOhlcv:
    bars: tuple[DailyBar, ...]
    canonical_csv: bytes
    sha256: str
    dataset_end: date
    as_of: datetime

    @property
    def row_count(self) -> int:
        return len(self.bars)

    def manifest_input(self, artifact_key: str = "prices.csv") -> dict[str, str | int]:
        return {
            "artifact_key": artifact_key, "sha256": self.sha256, "bytes": len(self.canonical_csv),
            "rows": self.row_count, "dataset_end": self.dataset_end.isoformat(),
        }


def freeze_daily_ohlcv(body: bytes, *, as_of: datetime, allowed_symbols: Collection[str] | None = None) -> FrozenDailyOhlcv:
    """Normalize a UTF-8 daily CSV and reject unavailable or malformed bars."""
    if not isinstance(body, bytes) or not body:
        raise PointInTimeError("daily OHLCV input must be non-empty bytes")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise PointInTimeError("as_of must include a timezone")
    try:
        reader = csv.DictReader(io.StringIO(body.decode("utf-8"), newline=""))
    except UnicodeDecodeError as exc:
        raise PointInTimeError("daily OHLCV input must be UTF-8") from exc
    if tuple(reader.fieldnames or ()) != CANONICAL_COLUMNS:
        raise PointInTimeError("daily OHLCV columns must exactly match the canonical contract")
    bars = tuple(sorted(_parse_rows(reader, as_of.astimezone(timezone.utc), allowed_symbols), key=lambda item: (item.symbol, item.session_date)))
    if not bars:
        raise PointInTimeError("daily OHLCV input has no rows")
    _validate_sequence(bars)
    canonical = _canonical_csv(bars)
    return FrozenDailyOhlcv(bars, canonical, hashlib.sha256(canonical).hexdigest(), max(bar.session_date for bar in bars), as_of.astimezone(timezone.utc))


def canonicalize_daily_ohlcv(body: bytes, *, as_of: datetime, allowed_symbols: Collection[str] | None = None) -> FrozenDailyOhlcv:
    return freeze_daily_ohlcv(body, as_of=as_of, allowed_symbols=allowed_symbols)


def _parse_rows(reader: csv.DictReader, as_of: datetime, allowed_symbols: Collection[str] | None) -> Iterable[DailyBar]:
    for number, row in enumerate(reader, start=2):
        if None in row or set(row) != set(CANONICAL_COLUMNS):
            raise PointInTimeError(f"row {number} has an invalid number of fields")
        symbol = row["symbol"]
        if not SYMBOL.fullmatch(symbol) or allowed_symbols is not None and symbol not in allowed_symbols:
            raise PointInTimeError(f"row {number} symbol is outside the allowed universe")
        session = _date(row["session_date"], number)
        opened = _timestamp(row["session_open_at"], number)
        closed = _timestamp(row["session_close_at"], number)
        available = _timestamp(row["available_at"], number)
        if not opened < closed <= available <= as_of:
            raise PointInTimeError(f"row {number} has invalid session or availability ordering")
        prices = tuple(_number(row[name], name, number) for name in ("open", "high", "low", "close"))
        open_, high, low, close = prices
        if low > min(open_, close) or high < max(open_, close):
            raise PointInTimeError(f"row {number} has inconsistent OHLC bounds")
        yield DailyBar(symbol, session, opened, closed, available, open_, high, low, close, _volume(row["volume"], number))


def _validate_sequence(bars: tuple[DailyBar, ...]) -> None:
    for previous, current in zip(bars, bars[1:]):
        if current.symbol != previous.symbol:
            continue
        if current.session_date == previous.session_date:
            raise PointInTimeError("each symbol/session_date pair must be unique")
        if previous.available_at > current.session_open_at:
            raise PointInTimeError("a prior bar is unavailable at the next session open")


def _date(value: str, number: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PointInTimeError(f"row {number} has an invalid session_date") from exc


def _timestamp(value: str, number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PointInTimeError(f"row {number} has an invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PointInTimeError(f"row {number} timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _number(value: str, name: str, number: int) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise PointInTimeError(f"row {number} has an invalid {name}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise PointInTimeError(f"row {number} {name} must be a positive finite number")
    return parsed


def _volume(value: str, number: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PointInTimeError(f"row {number} has an invalid volume") from exc
    if str(parsed) != value or parsed <= 0:
        raise PointInTimeError(f"row {number} volume must be a positive integer")
    return parsed


def _canonical_csv(bars: tuple[DailyBar, ...]) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(CANONICAL_COLUMNS)
    for bar in bars:
        writer.writerow((bar.symbol, bar.session_date.isoformat(), _stamp(bar.session_open_at), _stamp(bar.session_close_at), _stamp(bar.available_at), _float(bar.open), _float(bar.high), _float(bar.low), _float(bar.close), str(bar.volume)))
    return target.getvalue().encode("utf-8")


def _stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _float(value: float) -> str:
    return format(value, ".12g")
