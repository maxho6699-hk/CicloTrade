"""Dedicated SQLite database for the research queue, isolated from product data."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class BacktestQueueDatabaseError(RuntimeError):
    pass


def _statements(script: str) -> Iterator[str]:
    buffer: list[str] = []
    for line in script.splitlines():
        buffer.append(line)
        candidate = "\n".join(buffer).strip()
        if candidate and sqlite3.complete_statement(candidate):
            yield candidate
            buffer.clear()
    if "\n".join(buffer).strip():
        raise BacktestQueueDatabaseError("backtest migration contains incomplete SQL")


class BacktestQueueDatabase:
    """Small DB adapter implementing only the operations used by BacktestQueue."""

    def __init__(self, path: str | Path | None = None, migrations: str | Path | None = None):
        raw_path = path or os.getenv("TRADEAI_BACKTEST_DATABASE_URL", "data/backtest-queue.db")
        if str(raw_path).startswith("sqlite:///"):
            raw_path = str(raw_path)[10:]
        self._db_path = str(Path(raw_path).expanduser().resolve())
        self._migrations = Path(migrations or Path(__file__).resolve().parents[1] / "migrations" / "backtest").resolve()
        self._lock = threading.RLock()
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version TEXT PRIMARY KEY,
                       applied_at TEXT NOT NULL
                )"""
            )
            conn.commit()
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for path in sorted(self._migrations.glob("*.sql")):
                if path.name in applied:
                    continue
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    for statement in _statements(path.read_text(encoding="utf-8")):
                        conn.execute(statement)
                    conn.execute(
                        "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                        (path.name, datetime.now(timezone.utc).isoformat(timespec="seconds")),
                    )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    raise BacktestQueueDatabaseError(f"failed backtest migration {path.name}") from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, closing(self._connect()) as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.transaction() as conn:
            return conn.execute(sql, params).rowcount

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock, closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
