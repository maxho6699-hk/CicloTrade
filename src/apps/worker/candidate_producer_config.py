"""Fail-closed environment settings for the local candidate producer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
import math
import os
from pathlib import Path
import stat
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.apps.worker.candidate_input_contracts import approved_universe_sha256
from src.apps.worker.compute_gate_config import (
    absolute as _absolute,
    as_bool as _as_bool,
    clock_time as _clock_time,
    integer as _integer,
    number as _number,
)


MINIMUM_FREE_BYTES = 2 * 1024 * 1024 * 1024
CANDIDATE_PRODUCER_INTEGRATION_MARKER = Path(
    "/etc/ciclotrade-worker/enable-candidate-producer.after-integration"
)


class CandidateProducerError(RuntimeError):
    """Raised before any request is committed to the Compute Gate inbox."""


@dataclass(frozen=True)
class CandidateProducerSettings:
    enabled: bool
    source_dir: Path | None
    drop_dir: Path | None
    queue_db: Path | None
    artifact_dir: Path | None
    allowed_symbols: frozenset[str]
    timezone_name: str = "Asia/Hong_Kong"
    offpeak_start: time = time(0, 30)
    offpeak_end: time = time(6, 30)
    max_cpu_percent: float = 60.0
    max_memory_percent: float = 75.0
    minimum_free_bytes: int = MINIMUM_FREE_BYTES
    max_daily_candidates: int = 12
    max_pending_jobs: int = 4

    def __post_init__(self) -> None:
        if not self.enabled:
            if any(value is not None for value in (self.source_dir, self.drop_dir, self.queue_db, self.artifact_dir)):
                raise CandidateProducerError("disabled producer must not bind runtime paths")
            return
        for field in ("source_dir", "drop_dir", "queue_db", "artifact_dir"):
            raw = getattr(self, field)
            if raw is None or not Path(raw).is_absolute():
                raise CandidateProducerError(f"{field} must be an absolute local path")
            object.__setattr__(self, field, Path(raw).resolve())
        if self.source_dir == self.drop_dir:
            raise CandidateProducerError("source and Compute Gate drop directories must be isolated")
        approved_universe_sha256(self.allowed_symbols)
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise CandidateProducerError("candidate producer timezone is unavailable") from exc
        if self.offpeak_start == self.offpeak_end:
            raise CandidateProducerError("candidate producer off-peak window cannot cover the full day")
        for label, value, maximum in (
            ("max_cpu_percent", self.max_cpu_percent, 70.0),
            ("max_memory_percent", self.max_memory_percent, 80.0),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= maximum:
                raise CandidateProducerError(f"{label} is outside the candidate producer ceiling")
        for label, value, minimum, maximum in (
            ("minimum_free_bytes", self.minimum_free_bytes, 1, 1024**4),
            ("max_daily_candidates", self.max_daily_candidates, 1, 10_000),
            ("max_pending_jobs", self.max_pending_jobs, 1, 1_000),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise CandidateProducerError(f"{label} is invalid")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "CandidateProducerSettings":
        env = os.environ if environment is None else environment
        enabled = _as_bool(env.get("TRADEAI_CANDIDATE_PRODUCER_ENABLED", "false"))
        if not enabled:
            return cls(False, None, None, None, None, frozenset())
        if not _integration_marker_ready(CANDIDATE_PRODUCER_INTEGRATION_MARKER):
            return cls(False, None, None, None, None, frozenset())
        if _as_bool(env.get("TRADEAI_STRATEGY_WORKER_OUTBOUND_PUBLISH_ENABLED", "false")) or _as_bool(env.get("TRADEAI_STRATEGY_WORKER_PUBLISH", "false")):
            raise CandidateProducerError("candidate production is unavailable while publication is enabled")
        symbols = frozenset(item.strip().upper() for item in env.get("TRADEAI_COMPUTE_ALLOWED_SYMBOLS", "").split(",") if item.strip())
        return cls(
            True,
            _absolute(env.get("TRADEAI_CANDIDATE_SOURCE_DIR", "/var/lib/ciclotrade-worker/candidate-sources"), "candidate source dir"),
            _absolute(env.get("TRADEAI_COMPUTE_DROP_DIR", "/var/lib/ciclotrade-worker/inbox"), "Compute Gate drop dir"),
            _absolute(env.get("TRADEAI_STRATEGY_WORKER_QUEUE_DB", "/var/lib/ciclotrade-worker/backtest-queue.db"), "queue db"),
            _absolute(env.get("TRADEAI_STRATEGY_WORKER_ARTIFACT_DIR", "/var/lib/ciclotrade-worker/artifacts"), "artifact dir"),
            symbols,
            env.get("TRADEAI_COMPUTE_TIMEZONE", "Asia/Hong_Kong"),
            _clock_time(env.get("TRADEAI_COMPUTE_OFFPEAK_START", "00:30"), "offpeak start"),
            _clock_time(env.get("TRADEAI_COMPUTE_OFFPEAK_END", "06:30"), "offpeak end"),
            _number(env.get("TRADEAI_COMPUTE_MAX_CPU_PERCENT", "60"), "max CPU"),
            _number(env.get("TRADEAI_COMPUTE_MAX_MEMORY_PERCENT", "75"), "max memory"),
            _integer(env.get("TRADEAI_COMPUTE_MIN_FREE_BYTES", str(MINIMUM_FREE_BYTES)), "minimum free bytes"),
            _integer(env.get("TRADEAI_CANDIDATE_MAX_DAILY", env.get("TRADEAI_COMPUTE_MAX_DAILY_JOBS", "12")), "candidate daily budget"),
            _integer(env.get("TRADEAI_COMPUTE_MAX_PENDING_JOBS", "4"), "pending job budget"),
        )


def _integration_marker_ready(marker: Path, *, platform_name: str | None = None) -> bool:
    """Verify the fixed integration receipt before runtime paths are parsed."""
    try:
        metadata = marker.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CandidateProducerError("candidate producer integration marker cannot be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise CandidateProducerError("candidate producer integration marker must be a regular file")
    if (os.name if platform_name is None else platform_name) != "posix":
        raise CandidateProducerError("candidate producer integration marker ownership cannot be verified")
    if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CandidateProducerError("candidate producer integration marker is not root-controlled")
    return True
