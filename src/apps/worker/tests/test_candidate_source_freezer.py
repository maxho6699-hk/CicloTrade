from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from src.apps.worker.candidate_source_freezer import (
    CandidateSourceFreezerError,
    CandidateSourceFreezerSettings,
    run_candidate_source_freezer,
)
from src.apps.worker.point_in_time_freezer import CANONICAL_COLUMNS, freeze_daily_ohlcv


ROOT = Path(__file__).resolve().parents[4]
COLLECTED_AT = datetime(2025, 11, 4, 18, tzinfo=timezone.utc)  # 02:00 Hong Kong / after DST fall-back.


class Source:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[str] = []

    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        assert period == "5y" and interval == "1d"
        self.calls.append(symbol)
        return self.frames[symbol]


def _frame(*sessions: str) -> pd.DataFrame:
    index = pd.DatetimeIndex([f"{session} 00:00:00" for session in sessions], tz="America/New_York")
    return pd.DataFrame(
        {"Open": [100.0] * len(index), "High": [102.0] * len(index), "Low": [99.0] * len(index), "Close": [101.0] * len(index), "Volume": [1000] * len(index)},
        index=index,
    )


def _enabled_env(directory: Path, **override: str) -> dict[str, str]:
    values = {
        "TRADEAI_CANDIDATE_SOURCE_FREEZER_ENABLED": "true",
        "MARKET_DATA_ENABLED": "true",
        "TRADEAI_CANDIDATE_SOURCE_DIR": str(directory.resolve()),
        "TRADEAI_COMPUTE_ALLOWED_SYMBOLS": "AAPL,MSFT",
        "TRADEAI_CANDIDATE_SOURCE_MAX_SYMBOLS_PER_RUN": "1",
        "TRADEAI_COMPUTE_TIMEZONE": "Asia/Hong_Kong",
        "TRADEAI_COMPUTE_OFFPEAK_START": "00:30",
        "TRADEAI_COMPUTE_OFFPEAK_END": "06:30",
    }
    values.update(override)
    return values


def test_disabled_by_default_and_requires_explicit_market_access(tmp_path):
    assert run_candidate_source_freezer(env={}) == {"state": "disabled", "written": 0}
    with pytest.raises(CandidateSourceFreezerError, match="MARKET_DATA_ENABLED"):
        CandidateSourceFreezerSettings.from_env(_enabled_env(tmp_path, MARKET_DATA_ENABLED="false"))


def test_freezes_only_bounded_allowlist_with_dst_safe_new_york_sessions(tmp_path):
    source = Source({"AAPL": _frame("2025-03-07", "2025-11-03"), "MSFT": _frame("2025-11-03")})
    result = run_candidate_source_freezer(env=_enabled_env(tmp_path), data_source=source, now=COLLECTED_AT)
    assert result == {"state": "frozen", "written": 1, "symbols": ["AAPL"]}
    assert source.calls == ["AAPL"]
    source_file = tmp_path / "aapl.csv"
    assert source_file.exists() and not (tmp_path / "msft.csv").exists()
    frozen = freeze_daily_ohlcv(source_file.read_bytes(), as_of=COLLECTED_AT, allowed_symbols={"AAPL"})
    assert frozen.bars[0].session_open_at.isoformat() == "2025-03-07T14:30:00+00:00"
    assert frozen.bars[1].session_open_at.isoformat() == "2025-11-03T14:30:00+00:00"
    assert frozen.bars[1].available_at.isoformat() == "2025-11-04T04:59:59+00:00"
    assert source_file.read_text(encoding="utf-8").splitlines()[0].split(",") == list(CANONICAL_COLUMNS)


def test_rejects_unclosed_or_not_yet_available_daily_bars(tmp_path):
    source = Source({"AAPL": _frame("2025-11-03")})
    early = datetime(2025, 11, 4, 4, 59, 58, tzinfo=timezone.utc)
    with pytest.raises(CandidateSourceFreezerError, match="AAPL"):
        run_candidate_source_freezer(
            env=_enabled_env(
                tmp_path,
                TRADEAI_COMPUTE_TIMEZONE="UTC",
                TRADEAI_COMPUTE_OFFPEAK_START="00:00",
                TRADEAI_COMPUTE_OFFPEAK_END="06:00",
            ),
            data_source=source,
            now=early,
        )
    assert not (tmp_path / "aapl.csv").exists()


def test_atomic_write_preserves_old_file_when_new_source_is_invalid(tmp_path):
    destination = tmp_path / "aapl.csv"
    destination.write_bytes(b"old-safe-content\n")
    before = hashlib.sha256(destination.read_bytes()).hexdigest()
    invalid = pd.DataFrame({"Open": [100], "High": [102], "Low": [99], "Close": [101], "Volume": [0]}, index=pd.DatetimeIndex(["2025-11-03"], tz="America/New_York"))
    source = Source({"AAPL": invalid, "MSFT": invalid})
    with pytest.raises(CandidateSourceFreezerError):
        run_candidate_source_freezer(env=_enabled_env(tmp_path), data_source=source, now=COLLECTED_AT)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob(".aapl.csv.*.tmp"))


def test_round_robin_prefers_missing_then_oldest_file(tmp_path):
    (tmp_path / "aapl.csv").write_text("old", encoding="utf-8")
    source = Source({"AAPL": _frame("2025-11-03"), "MSFT": _frame("2025-11-03")})
    result = run_candidate_source_freezer(env=_enabled_env(tmp_path), data_source=source, now=COLLECTED_AT)
    assert result["symbols"] == ["MSFT"] and source.calls == ["MSFT"]
    (tmp_path / "msft.csv").unlink()
    (tmp_path / "aapl.csv").touch()
    result = run_candidate_source_freezer(env=_enabled_env(tmp_path), data_source=source, now=COLLECTED_AT)
    assert result["symbols"] == ["MSFT"]


def test_rejects_any_multi_symbol_transaction_setting(tmp_path):
    with pytest.raises(CandidateSourceFreezerError, match="maximum symbols"):
        CandidateSourceFreezerSettings.from_env(_enabled_env(tmp_path, TRADEAI_CANDIDATE_SOURCE_MAX_SYMBOLS_PER_RUN="2"))


def test_source_service_isolated_from_queue_artifacts_inbox_and_spool():
    service = (ROOT / "ops/ciclotrade-candidate-source-freezer.service").read_text(encoding="utf-8")
    timer = (ROOT / "ops/ciclotrade-candidate-source-freezer.timer").read_text(encoding="utf-8")
    config = (ROOT / "config/candidate-source-freezer.env.example").read_text(encoding="utf-8")
    assert "PrivateNetwork=false" in service
    assert "ReadWritePaths=/var/lib/ciclotrade-worker/candidate-sources" in service
    assert "InaccessiblePaths=" in service
    for forbidden in ("-/var/lib/ciclotrade-worker/backtest-queue.db", "-/var/lib/ciclotrade-worker/artifacts", "-/var/lib/ciclotrade-worker/inbox", "-/var/lib/ciclotrade-worker/compute-evidence", "-/var/lib/ciclotrade-worker/system-cycle-spool.db"):
        assert forbidden in service
    assert "TRADEAI_CANDIDATE_SOURCE_FREEZER_ENABLED=false" in config
    assert "MARKET_DATA_ENABLED=false" in config
    assert "TRADEAI_COMPUTE_ALLOWED_SYMBOLS" in config
    assert "ConditionPathExists=/etc/ciclotrade-worker/enable-candidate-source-freezer.after-integration" in service
    assert "OnCalendar=*-*-* 00:35:00 Asia/Hong_Kong" in timer
    assert "Persistent=false" in timer and "WantedBy=timers.target" in timer
