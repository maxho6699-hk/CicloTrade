"""Pure builder for a canonical 13-stock system-cycle shadow result."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
from pathlib import Path
from typing import Any, Mapping

from core.compat import UTC
from core.system_cycle_research_contracts import (
    AUTHORITY,
    CANONICAL_STOCKS,
    CANONICAL_SYSTEM_UNIVERSE,
    HEARTBEAT_KIND,
    RESULT_KIND,
    SYSTEM_UNIVERSE_SHA256,
    stamp,
    validate_system_cycle_heartbeat,
    validate_system_cycle_result,
)


def system_cycle_code_bundle_sha256() -> str:
    """Bind the result to the normal strategy and research-contract sources."""
    import core.strategy_evaluation as evaluation
    import core.strategy_registry as registry
    import core.strategy_scoring as scoring
    import core.system_cycle_research_contracts as contracts

    paths = (Path(evaluation.__file__), Path(registry.__file__), Path(scoring.__file__), Path(contracts.__file__), Path(__file__))
    digest = hashlib.sha256()
    for path in paths:
        body = path.resolve().read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def build_system_cycle_research_result(
    *,
    worker_id: str,
    fencing_epoch: int,
    evaluation_date: str | date,
    cycle_slot: str,
    strategy_key: str,
    strategy_name: str,
    strategy_version: str,
    source_snapshot_sha256: str,
    catalog_snapshot_sha256: str,
    stock_results: Mapping[str, Mapping[str, Any]],
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Seal precomputed normal-cycle observations without writing product state."""
    evaluation = evaluation_date.isoformat() if isinstance(evaluation_date, date) else str(evaluation_date)
    expected_symbols = {symbol for _, symbol in CANONICAL_STOCKS}
    if not isinstance(stock_results, Mapping) or set(stock_results) != expected_symbols:
        raise ValueError("stock_results must cover exactly the canonical 13-stock universe")
    stocks: list[dict[str, Any]] = []
    selected: list[str] = []
    for market, symbol in CANONICAL_STOCKS:
        value = dict(stock_results[symbol])
        status = value.get("status")
        if status == "coverage":
            is_selected = value.get("selected") is True
            if is_selected:
                selected.append(symbol)
            stocks.append({
                "market": market,
                "symbol": symbol,
                "status": "coverage",
                "rows": value.get("rows"),
                "dataset_end": value.get("dataset_end"),
                "selected": is_selected,
                "signal_state": value.get("signal_state"),
                "latest_price": value.get("latest_price"),
                "target_quantity": value.get("target_quantity"),
                "reason": None,
            })
        elif status == "no_data":
            stocks.append({
                "market": market,
                "symbol": symbol,
                "status": "no_data",
                "rows": value.get("rows", 0),
                "dataset_end": None,
                "selected": False,
                "signal_state": "no_data",
                "latest_price": None,
                "target_quantity": 0.0,
                "reason": value.get("reason"),
            })
        else:
            raise ValueError(f"{symbol} must declare coverage or no_data")
    moment = evaluated_at or datetime.now(UTC)
    result = {
        "schema_version": 1,
        "kind": RESULT_KIND,
        "cycle_id": f"system-cycle-{evaluation}-{cycle_slot}",
        "worker_id": worker_id,
        "fencing_epoch": fencing_epoch,
        "evaluated_at": stamp(moment),
        "cycle": {
            "evaluation_date": evaluation,
            "cycle_slot": cycle_slot,
            "strategy_key": strategy_key,
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
            "selected_symbols": selected,
        },
        "universe": {
            "markets": {key: list(values) for key, values in CANONICAL_SYSTEM_UNIVERSE.items()},
            "sha256": SYSTEM_UNIVERSE_SHA256,
        },
        "inputs": {
            "source_snapshot_sha256": source_snapshot_sha256,
            "catalog_snapshot_sha256": catalog_snapshot_sha256,
            "code_bundle_sha256": system_cycle_code_bundle_sha256(),
        },
        "stocks": stocks,
        "authority": dict(AUTHORITY),
    }
    return validate_system_cycle_result(result)


def build_system_cycle_heartbeat(
    *,
    worker_id: str,
    fencing_epoch: int,
    counts: Mapping[str, int],
    last_result_sha256: str | None,
    heartbeat_at: datetime | None = None,
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "kind": HEARTBEAT_KIND,
        "worker_id": worker_id,
        "fencing_epoch": fencing_epoch,
        "heartbeat_at": stamp(heartbeat_at or datetime.now(UTC)),
        "spool": {key: counts.get(key, 0) for key in ("pending", "claimed", "retryable", "delivered")},
        "last_result_sha256": last_result_sha256,
        "authority": dict(AUTHORITY),
    }
    return validate_system_cycle_heartbeat(value)
