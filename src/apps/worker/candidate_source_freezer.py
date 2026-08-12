"""Freeze delayed US daily bars into the isolated candidate-source directory.

This process is deliberately a network-only source stage.  It neither imports
nor references the compute queue, artifacts, inbox, or delivery spools.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import io
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from data.datasource import DataSourceError
from data.yfinance_adapter import YFinanceAdapter
from src.apps.worker.point_in_time_freezer import CANONICAL_COLUMNS, PointInTimeError, freeze_daily_ohlcv


NEW_YORK = ZoneInfo("America/New_York")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")


class CandidateSourceFreezerError(RuntimeError):
    """Raised when a source run cannot make a safe frozen observation."""


class DailyBarsSource(Protocol):
    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame: ...


@dataclass(frozen=True)
class CandidateSourceFreezerSettings:
    """Explicit, bounded configuration for the network source stage."""

    enabled: bool
    source_directory: Path | None = None
    allowed_symbols: tuple[str, ...] = ()
    max_symbols_per_run: int = 1
    timezone_name: str = "Asia/Hong_Kong"
    offpeak_start: time = time(0, 30)
    offpeak_end: time = time(6, 30)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "CandidateSourceFreezerSettings":
        values = os.environ if env is None else env
        if not _boolean(values.get("TRADEAI_CANDIDATE_SOURCE_FREEZER_ENABLED", "false")):
            return cls(enabled=False)
        if not _boolean(values.get("MARKET_DATA_ENABLED", "false")):
            raise CandidateSourceFreezerError("MARKET_DATA_ENABLED must be explicitly true")
        directory = _absolute_path(
            values.get("TRADEAI_CANDIDATE_SOURCE_DIR", ""),
            "TRADEAI_CANDIDATE_SOURCE_DIR",
        )
        symbols = tuple(dict.fromkeys(
            item.strip().upper()
            for item in values.get("TRADEAI_COMPUTE_ALLOWED_SYMBOLS", "").split(",")
            if item.strip()
        ))
        if not symbols or any(not SYMBOL.fullmatch(symbol) for symbol in symbols):
            raise CandidateSourceFreezerError("TRADEAI_COMPUTE_ALLOWED_SYMBOLS must be a non-empty US equity allow-list")
        maximum = _integer(values.get("TRADEAI_CANDIDATE_SOURCE_MAX_SYMBOLS_PER_RUN", "1"), 1, 1)
        timezone_name = str(values.get("TRADEAI_COMPUTE_TIMEZONE", "Asia/Hong_Kong")).strip()
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise CandidateSourceFreezerError("candidate source compute timezone is unavailable") from exc
        start = _clock_time(values.get("TRADEAI_COMPUTE_OFFPEAK_START", "00:30"))
        end = _clock_time(values.get("TRADEAI_COMPUTE_OFFPEAK_END", "06:30"))
        if start == end:
            raise CandidateSourceFreezerError("candidate source off-peak window must not cover the full day")
        return cls(True, directory, symbols, maximum, timezone_name, start, end)


def run_candidate_source_freezer(
    *,
    env: Mapping[str, str] | None = None,
    data_source: DailyBarsSource | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and atomically freeze at most the configured approved symbols."""
    settings = CandidateSourceFreezerSettings.from_env(env)
    if not settings.enabled:
        return {"state": "disabled", "written": 0}
    if settings.source_directory is None:  # pragma: no cover - frozen settings invariant
        raise CandidateSourceFreezerError("enabled source freezer directory is missing")
    collected_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not _inside_window(collected_at, ZoneInfo(settings.timezone_name), settings.offpeak_start, settings.offpeak_end):
        raise CandidateSourceFreezerError("candidate source collection is outside the configured Compute Gate off-peak window")
    root = settings.source_directory
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise CandidateSourceFreezerError("candidate source directory must be an existing non-symlink directory")
    source = data_source or YFinanceAdapter()
    written: list[str] = []
    for symbol in _select_symbols(settings):
        try:
            frame = source.bars(symbol, period="5y", interval="1d")
            canonical = _freeze_symbol_frame(symbol, frame, collected_at)
            _atomic_write(root / f"{symbol.lower()}.csv", canonical)
        except (CandidateSourceFreezerError, DataSourceError, OSError, PointInTimeError, ValueError, TypeError) as exc:
            # Do not replace missing/invalid data with generated values or an
            # older source. A partial run is not reported as a valid snapshot.
            raise CandidateSourceFreezerError(f"candidate source freeze failed for {symbol}") from exc
        written.append(symbol)
    if not written:
        raise CandidateSourceFreezerError("candidate source allow-list produced no symbols")
    return {"state": "frozen", "written": len(written), "symbols": written}


def _freeze_symbol_frame(symbol: str, frame: pd.DataFrame, collected_at: datetime) -> bytes:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise CandidateSourceFreezerError("daily source returned no rows")
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        raise CandidateSourceFreezerError("daily source columns are incomplete")
    rows: list[tuple[str, ...]] = []
    seen_sessions: set[date] = set()
    for timestamp, values in frame.sort_index().iterrows():
        session = _session_date(timestamp)
        if session in seen_sessions:
            raise CandidateSourceFreezerError("daily source contains duplicate sessions")
        seen_sessions.add(session)
        opened = datetime.combine(session, time(9, 30), tzinfo=NEW_YORK).astimezone(timezone.utc)
        closed = datetime.combine(session, time(16), tzinfo=NEW_YORK).astimezone(timezone.utc)
        # Without a trading-calendar dependency, this conservative availability
        # boundary also covers early-close sessions. It is safe, but delayed.
        available = datetime.combine(session, time(23, 59, 59), tzinfo=NEW_YORK).astimezone(timezone.utc)
        if closed > collected_at or available > collected_at:
            continue
        numbers = tuple(_positive_float(values[name]) for name in ("Open", "High", "Low", "Close"))
        volume = _positive_volume(values["Volume"])
        rows.append((
            symbol,
            session.isoformat(),
            _stamp(opened),
            _stamp(closed),
            _stamp(available),
            *(format(number, ".12g") for number in numbers),
            str(volume),
        ))
    if not rows:
        raise CandidateSourceFreezerError("daily source has no completed and available sessions")
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(CANONICAL_COLUMNS)
    writer.writerows(rows)
    # The canonical freezer independently verifies order, OHLC bounds, and all
    # point-in-time constraints before the bytes are published.
    return freeze_daily_ohlcv(target.getvalue().encode("utf-8"), as_of=collected_at, allowed_symbols={symbol}).canonical_csv


def _session_date(value: Any) -> date:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert(NEW_YORK).date()
    return timestamp.date()


def _positive_float(value: Any) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise CandidateSourceFreezerError("daily source contains an invalid price")
    return parsed


def _positive_volume(value: Any) -> int:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0 or not parsed.is_integer():
        raise CandidateSourceFreezerError("daily source contains an invalid volume")
    return int(parsed)


def _atomic_write(destination: Path, body: bytes) -> None:
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise CandidateSourceFreezerError("candidate source destination is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _select_symbols(settings: CandidateSourceFreezerSettings) -> tuple[str, ...]:
    if settings.source_directory is None:  # pragma: no cover - frozen settings invariant
        return ()
    order = {symbol: position for position, symbol in enumerate(settings.allowed_symbols)}
    def priority(symbol: str) -> tuple[int, int, str]:
        path = settings.source_directory / f"{symbol.lower()}.csv"
        try:
            return (1, path.stat().st_mtime_ns, symbol)
        except FileNotFoundError:
            return (0, order[symbol], symbol)
    return tuple(sorted(settings.allowed_symbols, key=priority)[: settings.max_symbols_per_run])


def _inside_window(moment: datetime, zone: ZoneInfo, start: time, end: time) -> bool:
    local = moment.astimezone(zone).timetz().replace(tzinfo=None)
    return start <= local < end if start < end else local >= start or local < end


def _absolute_path(value: Any, label: str) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute():
        raise CandidateSourceFreezerError(f"{label} must be absolute")
    return path.resolve()


def _boolean(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise CandidateSourceFreezerError("candidate source freezer enabled flag is invalid")


def _integer(value: Any, minimum: int, maximum: int) -> int:
    text = str(value).strip()
    if not text.isdigit() or not minimum <= int(text) <= maximum:
        raise CandidateSourceFreezerError("candidate source maximum symbols is outside its bounds")
    return int(text)


def _clock_time(value: Any) -> time:
    try:
        parsed = time.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise CandidateSourceFreezerError("candidate source off-peak time is invalid") from exc
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise CandidateSourceFreezerError("candidate source off-peak time must be HH:MM")
    return parsed


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="freeze bounded delayed US daily candidate sources")
    parser.add_argument("--once", action="store_true", help="run one bounded source collection")
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("--once is required")
    try:
        print(_json(run_candidate_source_freezer()))
        return 0
    except CandidateSourceFreezerError as exc:
        print(_json({"state": "error", "error": str(exc)}), file=sys.stderr)
        return 2


def _json(value: Mapping[str, Any]) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


if __name__ == "__main__":
    raise SystemExit(main())
