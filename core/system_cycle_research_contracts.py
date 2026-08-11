"""Strict contracts for canonical system-cycle shadow research receipts."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import hmac
import json
import math
import re
from typing import Any, Mapping

from core.compat import UTC
from core.strategy_evaluation import SYSTEM_UNIVERSE


RESULT_KIND = "system.strategy-cycle.research.v1"
HEARTBEAT_KIND = "system.strategy-cycle.heartbeat.v1"
RECEIVER_ENDPOINT_RESULT = "result"
RECEIVER_ENDPOINT_HEARTBEAT = "heartbeat"
RECEIVER_ENDPOINTS = frozenset({RECEIVER_ENDPOINT_RESULT, RECEIVER_ENDPOINT_HEARTBEAT})
CYCLE_SLOTS = frozenset({"premarket", "intraday", "after_close", "overnight", "manual", "daily"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
WORKER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
AUTHORITY = {
    "research_only": True,
    "publication_ceiling": "shadow",
    "outbound": False,
    "user_visible": False,
    "official": False,
    "live": False,
}


class SystemCycleResearchError(ValueError):
    """Raised when a research result is outside the sealed shadow contract."""


class SystemCycleResearchConflict(SystemCycleResearchError):
    """Raised when an idempotency identity is reused with different content."""


class SystemCycleResearchStaleFence(SystemCycleResearchError):
    """Raised when a worker attempts a protected write with an old epoch."""


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SystemCycleResearchError("research payload must be canonical finite JSON") from exc


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_system_universe() -> dict[str, list[str]]:
    if not isinstance(SYSTEM_UNIVERSE, Mapping) or set(SYSTEM_UNIVERSE) != {"US", "CN"}:
        raise SystemCycleResearchError("canonical SYSTEM_UNIVERSE markets changed")
    value: dict[str, list[str]] = {}
    seen: set[str] = set()
    for market in ("US", "CN"):
        symbols = SYSTEM_UNIVERSE[market]
        if not isinstance(symbols, tuple) or not symbols or any(not isinstance(item, str) or not item for item in symbols):
            raise SystemCycleResearchError("canonical SYSTEM_UNIVERSE symbols are invalid")
        if seen.intersection(symbols):
            raise SystemCycleResearchError("canonical SYSTEM_UNIVERSE symbols must be unique")
        seen.update(symbols)
        value[market] = list(symbols)
    if len(seen) != 13:
        raise SystemCycleResearchError("canonical SYSTEM_UNIVERSE must contain exactly 13 stocks")
    return value


CANONICAL_SYSTEM_UNIVERSE = canonical_system_universe()
SYSTEM_UNIVERSE_SHA256 = sha256_bytes(canonical_json(CANONICAL_SYSTEM_UNIVERSE))
CANONICAL_STOCKS = tuple((market, symbol) for market in ("US", "CN") for symbol in CANONICAL_SYSTEM_UNIVERSE[market])


def validate_system_cycle_result(value: Any) -> dict[str, Any]:
    item = _object(value, "system cycle result")
    _fields(item, {
        "schema_version", "kind", "cycle_id", "worker_id", "fencing_epoch", "evaluated_at",
        "cycle", "universe", "inputs", "stocks", "authority",
    }, "system cycle result")
    if item["schema_version"] != 1 or item["kind"] != RESULT_KIND:
        raise SystemCycleResearchError("system cycle result version or kind is invalid")
    cycle = _cycle(item["cycle"])
    expected_cycle_id = f"system-cycle-{cycle['evaluation_date']}-{cycle['cycle_slot']}"
    if item["cycle_id"] != expected_cycle_id or not SAFE_ID.fullmatch(str(item["cycle_id"])):
        raise SystemCycleResearchError("system cycle result cycle_id is invalid")
    worker = _worker(item["worker_id"])
    epoch = _epoch(item["fencing_epoch"])
    evaluated = parse_timestamp(item["evaluated_at"], "evaluated_at")
    if evaluated.date() < date.fromisoformat(cycle["evaluation_date"]):
        raise SystemCycleResearchError("evaluated_at cannot precede evaluation_date")
    universe = _universe(item["universe"])
    inputs = _inputs(item["inputs"])
    stocks = _stocks(item["stocks"], cycle)
    if item["authority"] != AUTHORITY:
        raise SystemCycleResearchError("system cycle result must remain research-only shadow evidence")
    result = {
        "schema_version": 1,
        "kind": RESULT_KIND,
        "cycle_id": expected_cycle_id,
        "worker_id": worker,
        "fencing_epoch": epoch,
        "evaluated_at": stamp(evaluated),
        "cycle": cycle,
        "universe": universe,
        "inputs": inputs,
        "stocks": stocks,
        "authority": dict(AUTHORITY),
    }
    canonical_json(result)
    return result


def validate_system_cycle_heartbeat(value: Any) -> dict[str, Any]:
    item = _object(value, "system cycle heartbeat")
    _fields(item, {
        "schema_version", "kind", "worker_id", "fencing_epoch", "heartbeat_at",
        "spool", "last_result_sha256", "authority",
    }, "system cycle heartbeat")
    if item["schema_version"] != 1 or item["kind"] != HEARTBEAT_KIND:
        raise SystemCycleResearchError("system cycle heartbeat version or kind is invalid")
    spool = _object(item["spool"], "heartbeat spool")
    _fields(spool, {"pending", "claimed", "retryable", "delivered"}, "heartbeat spool")
    normalized_spool = {key: _nonnegative_integer(spool[key], f"spool.{key}") for key in sorted(spool)}
    last_hash = item["last_result_sha256"]
    if last_hash is not None:
        last_hash = _hash(last_hash, "last_result_sha256")
    if item["authority"] != AUTHORITY:
        raise SystemCycleResearchError("system cycle heartbeat must remain research-only shadow evidence")
    return {
        "schema_version": 1,
        "kind": HEARTBEAT_KIND,
        "worker_id": _worker(item["worker_id"]),
        "fencing_epoch": _epoch(item["fencing_epoch"]),
        "heartbeat_at": stamp(parse_timestamp(item["heartbeat_at"], "heartbeat_at")),
        "spool": normalized_spool,
        "last_result_sha256": last_hash,
        "authority": dict(AUTHORITY),
    }


def receiver_signature(
    secret: str | bytes,
    *,
    endpoint: str,
    worker_id: str,
    fencing_epoch: int,
    idempotency_key: str,
    sent_at: str,
    body_sha256: str,
) -> str:
    raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not isinstance(raw_secret, bytes) or len(raw_secret) < 32:
        raise SystemCycleResearchError("system cycle receiver secret must contain at least 32 bytes")
    if endpoint not in RECEIVER_ENDPOINTS:
        raise SystemCycleResearchError("system cycle receiver endpoint is invalid")
    message = "\n".join((
        "system-cycle-research-signature-v1", endpoint, _worker(worker_id), str(_epoch(fencing_epoch)),
        _safe_id(idempotency_key, "idempotency_key"), stamp(parse_timestamp(sent_at, "sent_at")),
        _hash(body_sha256, "body_sha256"),
    )).encode("utf-8")
    return "sha256=" + hmac.new(raw_secret, message, hashlib.sha256).hexdigest()


def parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise SystemCycleResearchError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemCycleResearchError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemCycleResearchError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SystemCycleResearchError("timestamp must include a timezone")
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cycle(value: Any) -> dict[str, Any]:
    item = _object(value, "cycle")
    _fields(item, {
        "evaluation_date", "cycle_slot", "strategy_key", "strategy_name", "strategy_version", "selected_symbols",
    }, "cycle")
    try:
        evaluation = date.fromisoformat(item["evaluation_date"])
    except (TypeError, ValueError) as exc:
        raise SystemCycleResearchError("cycle.evaluation_date must be an ISO date") from exc
    slot = item["cycle_slot"]
    if slot not in CYCLE_SLOTS:
        raise SystemCycleResearchError("cycle.cycle_slot is invalid")
    selected = item["selected_symbols"]
    if not isinstance(selected, list) or any(not isinstance(symbol, str) for symbol in selected) or len(set(selected)) != len(selected):
        raise SystemCycleResearchError("cycle.selected_symbols must be a unique list")
    canonical_symbols = {symbol for _, symbol in CANONICAL_STOCKS}
    if any(symbol not in canonical_symbols for symbol in selected):
        raise SystemCycleResearchError("cycle.selected_symbols is outside the canonical universe")
    return {
        "evaluation_date": evaluation.isoformat(),
        "cycle_slot": slot,
        "strategy_key": _text(item["strategy_key"], "cycle.strategy_key", 128),
        "strategy_name": _text(item["strategy_name"], "cycle.strategy_name", 256),
        "strategy_version": _text(item["strategy_version"], "cycle.strategy_version", 128),
        "selected_symbols": selected,
    }


def _universe(value: Any) -> dict[str, Any]:
    item = _object(value, "universe")
    _fields(item, {"markets", "sha256"}, "universe")
    if item["markets"] != CANONICAL_SYSTEM_UNIVERSE or item["sha256"] != SYSTEM_UNIVERSE_SHA256:
        raise SystemCycleResearchError("result universe does not match canonical SYSTEM_UNIVERSE")
    return {"markets": {key: list(values) for key, values in CANONICAL_SYSTEM_UNIVERSE.items()}, "sha256": SYSTEM_UNIVERSE_SHA256}


def _inputs(value: Any) -> dict[str, str]:
    item = _object(value, "inputs")
    fields = {"source_snapshot_sha256", "catalog_snapshot_sha256", "code_bundle_sha256"}
    _fields(item, fields, "inputs")
    return {key: _hash(item[key], f"inputs.{key}") for key in sorted(fields)}


def _stocks(value: Any, cycle: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(CANONICAL_STOCKS):
        raise SystemCycleResearchError("result must contain exactly one record for each canonical stock")
    rows: list[dict[str, Any]] = []
    expected_selected: list[str] = []
    evaluation = date.fromisoformat(str(cycle["evaluation_date"]))
    fields = {
        "market", "symbol", "status", "rows", "dataset_end", "selected",
        "signal_state", "latest_price", "target_quantity", "reason",
    }
    for raw, (market, symbol) in zip(value, CANONICAL_STOCKS, strict=True):
        item = _object(raw, f"stock {symbol}")
        _fields(item, fields, f"stock {symbol}")
        if item["market"] != market or item["symbol"] != symbol:
            raise SystemCycleResearchError("stock coverage must follow canonical SYSTEM_UNIVERSE order")
        rows_count = _nonnegative_integer(item["rows"], f"stock {symbol}.rows")
        selected = item["selected"]
        if not isinstance(selected, bool):
            raise SystemCycleResearchError(f"stock {symbol}.selected must be boolean")
        status = item["status"]
        if status == "coverage":
            if rows_count <= 0:
                raise SystemCycleResearchError(f"stock {symbol} coverage requires rows")
            try:
                dataset_end = date.fromisoformat(item["dataset_end"])
            except (TypeError, ValueError) as exc:
                raise SystemCycleResearchError(f"stock {symbol}.dataset_end must be an ISO date") from exc
            if dataset_end > evaluation:
                raise SystemCycleResearchError(f"stock {symbol} coverage exceeds evaluation_date")
            latest = _positive_number(item["latest_price"], f"stock {symbol}.latest_price")
            target = _nonnegative_number(item["target_quantity"], f"stock {symbol}.target_quantity")
            signal = item["signal_state"]
            if signal not in {"long", "flat"} or selected != (signal == "long" and target > 0):
                raise SystemCycleResearchError(f"stock {symbol} selection and signal are inconsistent")
            if signal == "flat" and target != 0:
                raise SystemCycleResearchError(f"stock {symbol} flat signal must have zero target")
            if item["reason"] is not None:
                raise SystemCycleResearchError(f"stock {symbol} coverage reason must be null")
            normalized = {
                "market": market, "symbol": symbol, "status": status, "rows": rows_count,
                "dataset_end": dataset_end.isoformat(), "selected": selected, "signal_state": signal,
                "latest_price": latest, "target_quantity": target, "reason": None,
            }
        elif status == "no_data":
            if any((item["dataset_end"] is not None, selected, item["signal_state"] != "no_data", item["latest_price"] is not None)):
                raise SystemCycleResearchError(f"stock {symbol} no_data fields are inconsistent")
            if _nonnegative_number(item["target_quantity"], f"stock {symbol}.target_quantity") != 0:
                raise SystemCycleResearchError(f"stock {symbol} no_data target must be zero")
            normalized = {
                "market": market, "symbol": symbol, "status": status, "rows": rows_count,
                "dataset_end": None, "selected": False, "signal_state": "no_data",
                "latest_price": None, "target_quantity": 0.0,
                "reason": _text(item["reason"], f"stock {symbol}.reason", 500),
            }
        else:
            raise SystemCycleResearchError(f"stock {symbol}.status must be coverage or no_data")
        if normalized["selected"]:
            expected_selected.append(symbol)
        rows.append(normalized)
    if list(cycle["selected_symbols"]) != expected_selected:
        raise SystemCycleResearchError("cycle.selected_symbols does not match stock coverage")
    return rows


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SystemCycleResearchError(f"{label} must be an object")
    return dict(value)


def _fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise SystemCycleResearchError(f"{label} fields do not match the contract")


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise SystemCycleResearchError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _worker(value: Any) -> str:
    if not isinstance(value, str) or not WORKER_ID.fullmatch(value):
        raise SystemCycleResearchError("worker_id is invalid")
    return value


def _epoch(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2_147_483_647:
        raise SystemCycleResearchError("fencing_epoch is invalid")
    return value


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise SystemCycleResearchError(f"{label} is invalid")
    return value


def _text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not (cleaned := value.strip()) or len(cleaned) > maximum or any(ord(char) < 32 for char in cleaned):
        raise SystemCycleResearchError(f"{label} is invalid")
    return cleaned


def _nonnegative_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
        raise SystemCycleResearchError(f"{label} must be a non-negative integer")
    return value


def _positive_number(value: Any, label: str) -> float:
    number = _nonnegative_number(value, label)
    if number <= 0:
        raise SystemCycleResearchError(f"{label} must be positive")
    return number


def _nonnegative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SystemCycleResearchError(f"{label} must be a non-negative finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise SystemCycleResearchError(f"{label} must be a non-negative finite number") from exc
    if not math.isfinite(number) or number < 0:
        raise SystemCycleResearchError(f"{label} must be a non-negative finite number")
    return number
