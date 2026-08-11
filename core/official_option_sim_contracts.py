"""Fail-closed contracts for the official real-quote option simulation."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Mapping

from core.compat import UTC


SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
EVENTS = {"PROPOSED", "ACCEPTED", "OPENED", "MARKED", "CLOSING", "CLOSED", "REJECTED", "CANCELLED"}
STRUCTURES = {"LONG_CALL", "LONG_PUT", "LONG_STRADDLE", "LONG_STRANGLE", "CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD", "PROTECTIVE_HEDGE"}
QUOTE_EVENTS = {"PROPOSED", "OPENED", "MARKED", "CLOSED"}


class OfficialOptionSimulationError(ValueError):
    """A receipt cannot safely affect the official simulation."""


class OfficialOptionSimulationIdempotencyConflict(OfficialOptionSimulationError):
    """The same idempotency key was sent with different evidence."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise OfficialOptionSimulationError("payload must be finite canonical JSON") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise OfficialOptionSimulationError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfficialOptionSimulationError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def stamp(value: Any, label: str) -> str:
    return parse_timestamp(value, label).isoformat(timespec="seconds").replace("+00:00", "Z")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OfficialOptionSimulationError(f"{label} must be an object")
    return dict(value)


def _fields(value: Mapping[str, Any], required: set[str], optional: set[str], label: str) -> None:
    missing = required - set(value)
    if missing:
        raise OfficialOptionSimulationError(f"{label} missing fields")
    if set(value) - required - optional:
        raise OfficialOptionSimulationError(f"{label} has unknown fields")


def _text(value: Any, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum or "\x00" in value:
        raise OfficialOptionSimulationError(f"{label} is invalid")
    return value.strip()


def _id(value: Any, label: str) -> str:
    text = _text(value, label, 128)
    if not SAFE.fullmatch(text):
        raise OfficialOptionSimulationError(f"{label} is invalid")
    return text


def _number(value: Any, label: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise OfficialOptionSimulationError(f"{label} must be finite")
    result = float(value)
    if (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
        raise OfficialOptionSimulationError(f"{label} is out of range")
    return result


def _integer(value: Any, label: str, minimum: int = 1, maximum: int = 100_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise OfficialOptionSimulationError(f"{label} is invalid")
    return value


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise OfficialOptionSimulationError(f"{label} must be a SHA-256")
    return value


def _leg(raw: Any, action_at: datetime) -> dict[str, Any]:
    value = _object(raw, "option leg")
    required = {"contract_key", "side", "quantity", "expiry", "right", "strike", "multiplier", "bid", "ask", "quote_at", "is_realtime", "actionable_quote", "fallback_from", "quote_source", "commission"}
    _fields(value, required, {"execution_price"}, "option leg")
    value["contract_key"] = _id(value["contract_key"], "contract_key")
    if value["side"] not in {"BUY", "SELL"}:
        raise OfficialOptionSimulationError("option side is invalid")
    value["quantity"] = _integer(value["quantity"], "quantity")
    if not isinstance(value["expiry"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value["expiry"]):
        raise OfficialOptionSimulationError("expiry is invalid")
    if value["right"] not in {"CALL", "PUT"}:
        raise OfficialOptionSimulationError("option right is invalid")
    value["strike"] = _number(value["strike"], "strike", 0.000001)
    value["multiplier"] = _integer(value["multiplier"], "multiplier", 1, 10_000)
    value["bid"] = _number(value["bid"], "bid", 0)
    value["ask"] = _number(value["ask"], "ask", 0.000001)
    if value["ask"] < value["bid"]:
        raise OfficialOptionSimulationError("ask is below bid")
    quote_at = parse_timestamp(value["quote_at"], "quote_at")
    if quote_at > action_at.replace(microsecond=0) or (action_at - quote_at).total_seconds() > 300:
        raise OfficialOptionSimulationError("quote is stale or future-dated")
    if value["is_realtime"] is not True or value["actionable_quote"] is not True or value["fallback_from"] is not None:
        raise OfficialOptionSimulationError("official simulation requires actionable realtime quotes")
    value["quote_source"] = _id(value["quote_source"], "quote_source")
    value["commission"] = _number(value["commission"], "commission", 0)
    value["quote_at"] = stamp(quote_at, "quote_at")
    if "execution_price" in value:
        value["execution_price"] = _number(value["execution_price"], "execution_price", 0.000001)
    return value


def _position(raw: Any, action_at: datetime) -> dict[str, Any]:
    value = _object(raw, "position")
    required = {"structure_type", "underlying", "currency", "account_equity", "portfolio_risk_before_pct", "portfolio_risk_limit_pct", "risk", "legs"}
    _fields(value, required, set(), "position")
    if value["structure_type"] not in STRUCTURES:
        raise OfficialOptionSimulationError("structure_type is not permitted")
    if not isinstance(value["underlying"], str) or not SYMBOL.fullmatch(value["underlying"]):
        raise OfficialOptionSimulationError("underlying is invalid")
    if value["currency"] != "USD":
        raise OfficialOptionSimulationError("official option simulation currently requires USD")
    value["account_equity"] = _number(value["account_equity"], "account_equity", 1)
    value["portfolio_risk_before_pct"] = _number(value["portfolio_risk_before_pct"], "portfolio_risk_before_pct", 0, 8)
    value["portfolio_risk_limit_pct"] = _number(value["portfolio_risk_limit_pct"], "portfolio_risk_limit_pct", 0.000001, 8)
    risk = _object(value["risk"], "risk")
    _fields(risk, {"defined_risk", "max_loss", "max_account_pct", "invalidation_condition"}, set(), "risk")
    if risk["defined_risk"] is not True:
        raise OfficialOptionSimulationError("defined risk is required")
    risk["max_loss"] = _number(risk["max_loss"], "max_loss", 0.000001)
    risk["max_account_pct"] = _number(risk["max_account_pct"], "max_account_pct", 0.000001, 3)
    risk["invalidation_condition"] = _text(risk["invalidation_condition"], "invalidation_condition", 2_000)
    if risk["max_loss"] > value["account_equity"] * risk["max_account_pct"] / 100 + 1e-9:
        raise OfficialOptionSimulationError("max_loss exceeds the account risk contract")
    if value["portfolio_risk_before_pct"] + risk["max_account_pct"] > value["portfolio_risk_limit_pct"] + 1e-9:
        raise OfficialOptionSimulationError("portfolio risk limit would be exceeded")
    if not isinstance(value["legs"], list) or not 1 <= len(value["legs"]) <= 4:
        raise OfficialOptionSimulationError("position requires one to four legs")
    legs = [_leg(item, action_at) for item in value["legs"]]
    if len({item["contract_key"] for item in legs}) != len(legs):
        raise OfficialOptionSimulationError("contract keys must be unique")
    _structure(value["structure_type"], legs)
    value["risk"], value["legs"] = risk, legs
    return value


def _structure(kind: str, legs: list[dict[str, Any]]) -> None:
    sides = [(leg["side"], leg["right"]) for leg in legs]
    if kind == "LONG_CALL" and sides != [("BUY", "CALL")]:
        raise OfficialOptionSimulationError("LONG_CALL requires one purchased call")
    if kind == "LONG_PUT" and sides != [("BUY", "PUT")]:
        raise OfficialOptionSimulationError("LONG_PUT requires one purchased put")
    if kind in {"LONG_STRADDLE", "LONG_STRANGLE"}:
        if len(legs) != 2 or sorted(sides) != [("BUY", "CALL"), ("BUY", "PUT")]:
            raise OfficialOptionSimulationError("long volatility structures require purchased call and put")
        if legs[0]["expiry"] != legs[1]["expiry"] or (kind == "LONG_STRADDLE" and legs[0]["strike"] != legs[1]["strike"]):
            raise OfficialOptionSimulationError("long volatility structure expiry or strike is invalid")
    if kind in {"CALL_DEBIT_SPREAD", "PUT_DEBIT_SPREAD"}:
        right = "CALL" if kind.startswith("CALL") else "PUT"
        if len(legs) != 2 or sorted(sides) != [("BUY", right), ("SELL", right)] or legs[0]["expiry"] != legs[1]["expiry"]:
            raise OfficialOptionSimulationError("debit spread legs are invalid")
        buy = next(leg for leg in legs if leg["side"] == "BUY")
        sell = next(leg for leg in legs if leg["side"] == "SELL")
        if buy["quantity"] != sell["quantity"] or (right == "CALL" and buy["strike"] >= sell["strike"]) or (right == "PUT" and buy["strike"] <= sell["strike"]):
            raise OfficialOptionSimulationError("debit spread cannot be a naked or credit structure")
    if kind == "PROTECTIVE_HEDGE" and sides != [("BUY", "PUT")]:
        raise OfficialOptionSimulationError("protective hedge requires one purchased put")


def _validate_execution(position: dict[str, Any], raw: Any, action_at: datetime, *, close: bool) -> dict[str, Any]:
    value = _object(raw, "execution")
    _fields(value, {"slippage_bps", "legs"}, set(), "execution")
    value["slippage_bps"] = _number(value["slippage_bps"], "slippage_bps", 0, 100)
    if not isinstance(value["legs"], list) or len(value["legs"]) != len(position["legs"]):
        raise OfficialOptionSimulationError("FOK/ALL_OR_NONE requires every position leg")
    legs = [_leg(item, action_at) for item in value["legs"]]
    by_key = {leg["contract_key"]: leg for leg in legs}
    if set(by_key) != {leg["contract_key"] for leg in position["legs"]}:
        raise OfficialOptionSimulationError("execution legs do not match the proposed position")
    for proposed in position["legs"]:
        actual = by_key[proposed["contract_key"]]
        if any(actual[key] != proposed[key] for key in ("expiry", "right", "strike", "multiplier", "quantity")):
            raise OfficialOptionSimulationError("execution contract identity changed")
        expected_side = ("BUY" if proposed["side"] == "SELL" else "SELL") if close else proposed["side"]
        if actual["side"] != expected_side or "execution_price" not in actual:
            raise OfficialOptionSimulationError("execution side or price is invalid")
        expected = actual["ask"] * (1 + value["slippage_bps"] / 10_000) if actual["side"] == "BUY" else actual["bid"] * (1 - value["slippage_bps"] / 10_000)
        if not math.isclose(actual["execution_price"], expected, abs_tol=1e-8):
            raise OfficialOptionSimulationError("execution price must use adverse bid/ask slippage")
    value["legs"] = legs
    return value


def validate_receipt(payload: Any, *, now: Any) -> dict[str, Any]:
    value = _object(payload, "simulation receipt")
    required = {"schema_version", "event_id", "position_key", "event_type", "action_at", "worker_id", "fencing_epoch", "strategy_id", "strategy_version", "model_version", "manifest_sha256", "evidence_hashes"}
    optional = {"position", "execution"}
    _fields(value, required, optional, "simulation receipt")
    if value["schema_version"] != 1 or value["event_type"] not in EVENTS:
        raise OfficialOptionSimulationError("simulation receipt schema or event type is invalid")
    for name in ("event_id", "position_key", "worker_id", "strategy_id", "strategy_version", "model_version"):
        value[name] = _id(value[name], name)
    value["fencing_epoch"] = _integer(value["fencing_epoch"], "fencing_epoch", 1, 2_147_483_647)
    value["manifest_sha256"] = _hash(value["manifest_sha256"], "manifest_sha256")
    if not isinstance(value["evidence_hashes"], list) or not 1 <= len(value["evidence_hashes"]) <= 32:
        raise OfficialOptionSimulationError("evidence_hashes is invalid")
    value["evidence_hashes"] = [_hash(item, "evidence_hash") for item in value["evidence_hashes"]]
    if len(set(value["evidence_hashes"])) != len(value["evidence_hashes"]):
        raise OfficialOptionSimulationError("evidence_hashes must be unique")
    action_at, current = parse_timestamp(value["action_at"], "action_at"), parse_timestamp(now, "now")
    if action_at > current.replace(microsecond=0) or (current - action_at).total_seconds() > 300:
        raise OfficialOptionSimulationError("receipt action time is stale or future-dated")
    value["action_at"] = stamp(action_at, "action_at")
    event = value["event_type"]
    if event == "PROPOSED":
        if set(value) - required - {"position"} or "position" not in value:
            raise OfficialOptionSimulationError("proposal must contain position only")
        value["position"] = _position(value["position"], action_at)
    elif event in {"OPENED", "CLOSED"}:
        if set(value) - required - {"execution"} or "execution" not in value:
            raise OfficialOptionSimulationError("execution event is incomplete")
        value["execution"] = _object(value["execution"], "execution")
    elif event == "MARKED":
        if set(value) - required - {"execution"} or "execution" not in value:
            raise OfficialOptionSimulationError("mark event is incomplete")
        execution = _object(value["execution"], "execution")
        _fields(execution, {"legs"}, set(), "mark receipt")
        value["execution"] = execution
    elif set(value) != required:
        raise OfficialOptionSimulationError("state event contains unsafe fields")
    value["payload_sha256"] = sha256_json(value)
    return value


__all__ = ["OfficialOptionSimulationError", "OfficialOptionSimulationIdempotencyConflict", "canonical_json", "parse_timestamp", "sha256_json", "stamp", "validate_receipt", "_validate_execution"]
