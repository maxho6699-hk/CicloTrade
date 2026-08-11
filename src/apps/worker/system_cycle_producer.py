"""Produce at most one canonical system-cycle shadow result per invocation."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from core.backtest_queue_database import BacktestQueueDatabase
from core.compat import UTC
from core.strategy_evaluation import SYSTEM_UNIVERSE
from core.system_cycle_research_contracts import SystemCycleResearchConflict
from data.yfinance_adapter import YFinanceAdapter
from src.apps.worker.system_cycle_evaluator import DEFAULT_CATALOG, evaluate_system_cycle
from src.apps.worker.system_cycle_research import build_system_cycle_research_result
from src.apps.worker.system_cycle_spool import PersistentSystemCycleSpool


NEW_YORK = ZoneInfo("America/New_York")
WORKER_ID = "system-cycle-producer"
DEFAULT_SPOOL_PATH = "/var/lib/ciclotrade-worker/system-cycle-spool.db"


def cycle_slot_at(moment: datetime) -> str:
    """Map any aware moment to one stable New York market checkpoint."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("cycle clock must include a timezone")
    local = moment.astimezone(NEW_YORK)
    minute = local.hour * 60 + local.minute
    if 4 * 60 <= minute < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= minute < 16 * 60:
        return "intraday"
    if 16 * 60 <= minute < 20 * 60:
        return "after_close"
    return "overnight"


def cycle_date_at(moment: datetime) -> date:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("cycle clock must include a timezone")
    return moment.astimezone(NEW_YORK).date()


def cycle_idempotency_key(evaluation_date: date, slot: str) -> str:
    return f"system-cycle-{evaluation_date.isoformat()}-{slot}"


def produce_once(
    *,
    spool: PersistentSystemCycleSpool,
    data_source: Any | None = None,
    now: datetime | None = None,
    catalog_path: str | Path = DEFAULT_CATALOG,
    worker_id: str = WORKER_ID,
) -> dict[str, Any]:
    """Read delayed daily bars, calculate shadow evidence, and enqueue once.

    The existing idempotency row is checked before accessing the data source, so
    reruns in the same New York slot neither re-evaluate nor conflict because of
    a new evaluated_at timestamp.
    """
    moment = now or datetime.now(UTC)
    evaluation_date = cycle_date_at(moment)
    slot = cycle_slot_at(moment)
    key = cycle_idempotency_key(evaluation_date, slot)
    existing = spool.database.fetch_one(
        "SELECT id,result_sha256,state FROM system_cycle_research_spool WHERE idempotency_key=?", (key,)
    )
    if existing is not None:
        return {"created": False, "skipped": True, "idempotency_key": key, "spool_id": int(existing["id"]), "state": existing["state"], "result_sha256": existing["result_sha256"]}

    source = data_source or YFinanceAdapter()
    symbols = tuple(symbol for market in ("US", "CN") for symbol in SYSTEM_UNIVERSE[market])
    try:
        closes, _volumes = source.history(symbols, period="3y", interval="1d")
    except Exception:
        # The evaluator truthfully converts a completely unavailable source to
        # thirteen no_data records.  In production YFinanceAdapter itself is
        # fail-closed through MARKET_DATA_ENABLED.
        closes = pd.DataFrame()
    if not isinstance(closes, pd.DataFrame):
        closes = pd.DataFrame()
    evaluated = evaluate_system_cycle(closes, evaluation_date=evaluation_date, catalog_path=catalog_path)
    epoch = spool.allocate_fencing_epoch(worker_id)
    result = build_system_cycle_research_result(
        worker_id=worker_id,
        fencing_epoch=epoch,
        evaluation_date=evaluation_date,
        cycle_slot=slot,
        strategy_key=evaluated["strategy"]["key"],
        strategy_name=evaluated["strategy"]["name"],
        strategy_version=evaluated["strategy"]["version"],
        source_snapshot_sha256=evaluated["source_snapshot_sha256"],
        catalog_snapshot_sha256=evaluated["catalog_snapshot_sha256"],
        stock_results=evaluated["stock_results"],
        evaluated_at=moment,
    )
    try:
        row, created = spool.enqueue(result, idempotency_key=key)
    except SystemCycleResearchConflict:
        # Another timer invocation won the race after our initial read.  It is
        # already the authoritative shadow record for this date/slot.
        existing = spool.database.fetch_one(
            "SELECT id,result_sha256,state FROM system_cycle_research_spool WHERE idempotency_key=?", (key,)
        )
        if existing is None:
            raise
        return {"created": False, "skipped": True, "idempotency_key": key, "spool_id": int(existing["id"]), "state": existing["state"], "result_sha256": existing["result_sha256"]}
    return {"created": created, "skipped": not created, "idempotency_key": key, "spool_id": int(row["id"]), "state": row["state"], "result_sha256": row["result_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical system-cycle shadow research producer")
    parser.add_argument("--once", action="store_true", help="generate and enqueue at most one New York cycle slot")
    parser.add_argument("--spool-db", default=os.getenv("TRADEAI_SYSTEM_CYCLE_SPOOL_DB", DEFAULT_SPOOL_PATH))
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("--once is required")
    try:
        spool = PersistentSystemCycleSpool(BacktestQueueDatabase(args.spool_db))
        print(json.dumps(produce_once(spool=spool), sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"system cycle producer refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
