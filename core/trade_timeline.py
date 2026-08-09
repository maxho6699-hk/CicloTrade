# -*- coding: utf-8 -*-
"""Deterministic trade lifecycles derived from the append-only quant journal."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.compat import UTC


_EPSILON = 1e-12
_HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def _finite(value: object, *, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _new_cycle(leg: dict[str, Any], event: dict[str, Any], sequence: int) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "instrument_key": str(leg["instrument_key"]),
        "market": str(leg["market"]),
        "instrument_type": str(leg["instrument_type"]),
        "symbol": str(leg["symbol"]),
        "currency": str(leg["currency"]),
        "option_expiry": leg.get("option_expiry"),
        "option_right": leg.get("option_right"),
        "option_strike": leg.get("option_strike"),
        "multiplier": _finite(leg.get("multiplier"), default=1.0),
        "direction": "long" if _finite(leg.get("quantity_delta")) > 0 else "short",
        "opened_at": str(event["occurred_at"]),
        "closed_at": None,
        "updated_at": str(event["occurred_at"]),
        "recorded_at": str(event.get("recorded_at") or event["occurred_at"]),
        "strategy_name": str(event.get("strategy_name") or "--"),
        "current_quantity": 0.0,
        "average_cost": 0.0,
        "opened_quantity": 0.0,
        "closed_quantity": 0.0,
        "entry_notional": 0.0,
        "exit_notional": 0.0,
        "net_cash": 0.0,
        "commission": 0.0,
        "realized_pnl": None,
        "return": None,
        "mark_price": None,
        "unrealized_pnl": None,
        "event_count": 0,
        "executions": [],
    }


def _execution(
    event: dict[str, Any],
    role: str,
    quantity: float,
    price: float,
    commission: float,
    position_after: float,
) -> dict[str, Any]:
    event_id = event.get("id")
    return {
        "event_id": event_id if isinstance(event_id, int) and not isinstance(event_id, bool) else None,
        "occurred_at": str(event["occurred_at"]),
        "role": role,
        "quantity": abs(quantity),
        "price": price,
        "commission": commission,
        "position_after": position_after,
    }


def closed_trade_window(period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return a half-open UTC window for one Hong Kong calendar period."""
    if period not in {"today", "yesterday", "7d"}:
        raise ValueError("period must be today, yesterday, or 7d")
    current = now or datetime.now(UTC)
    if not isinstance(current, datetime):
        raise TypeError("now must be a datetime")
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_date = current.astimezone(_HONG_KONG).date()
    today_start = datetime.combine(local_date, time.min, tzinfo=_HONG_KONG)
    if period == "today":
        start, end = today_start, today_start + timedelta(days=1)
    elif period == "yesterday":
        start, end = today_start - timedelta(days=1), today_start
    else:
        start, end = today_start - timedelta(days=6), today_start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def filter_closed_trade_cycles(
    cycles: Sequence[dict[str, Any]],
    period: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Select closed lifecycles by their Hong Kong closing calendar date."""
    start, end = closed_trade_window(period, now)
    selected: list[dict[str, Any]] = []
    for cycle in cycles:
        closed_at = cycle.get("closed_at")
        if not closed_at:
            continue
        try:
            closed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if closed.tzinfo is None:
            closed = closed.replace(tzinfo=UTC)
        if start <= closed.astimezone(UTC) < end:
            selected.append(cycle)
    return sorted(
        selected,
        key=lambda row: (str(row["closed_at"]), int(row["sequence"])),
        reverse=True,
    )


def project_trade_cycles(
    events: Sequence[dict[str, Any]],
    instrument_type: str,
    *,
    marks: Mapping[str, int | float] | None = None,
) -> list[dict[str, Any]]:
    """Project zero-to-zero position lifecycles in one ordered pass."""
    kind = str(instrument_type).strip().lower()
    if kind not in {"stock", "option"}:
        raise ValueError("instrument_type must be stock or option")
    mark_values: dict[str, float] = {}
    for key, value in (marks or {}).items():
        number = _finite(value, default=float("nan"))
        if isinstance(key, str) and math.isfinite(number) and number > 0:
            mark_values[key] = number

    states: dict[str, dict[str, Any]] = {}
    cycles: list[dict[str, Any]] = []
    sequence = 0
    for event in events:
        if event.get("active") is not True:
            continue
        for leg in event.get("legs") or ():
            if leg.get("instrument_type") != kind:
                continue
            delta = _finite(leg.get("quantity_delta"))
            price = _finite(leg.get("price"))
            multiplier = _finite(leg.get("multiplier"), default=1.0)
            commission = max(0.0, _finite(leg.get("commission")))
            if abs(delta) < _EPSILON or price <= 0 or multiplier <= 0:
                continue
            key = str(leg["instrument_key"])
            state = states.setdefault(key, {"quantity": 0.0, "average_cost": 0.0, "cycle": None})
            remaining = delta
            remaining_commission = commission
            while abs(remaining) >= _EPSILON:
                quantity = _finite(state["quantity"])
                average = _finite(state["average_cost"])
                if abs(quantity) < _EPSILON or quantity * remaining > 0:
                    opening = abs(quantity) < _EPSILON
                    if state["cycle"] is None:
                        sequence += 1
                        state["cycle"] = _new_cycle(leg, event, sequence)
                        cycles.append(state["cycle"])
                    cycle = state["cycle"]
                    piece = remaining
                    piece_commission = remaining_commission
                    new_quantity = quantity + piece
                    state["average_cost"] = (
                        (abs(quantity) * average + abs(piece) * price) / abs(new_quantity)
                        if abs(new_quantity) >= _EPSILON else 0.0
                    )
                    state["quantity"] = new_quantity
                    cycle["current_quantity"] = new_quantity
                    cycle["average_cost"] = state["average_cost"]
                    cycle["opened_quantity"] += abs(piece)
                    cycle["entry_notional"] += abs(piece) * price * multiplier
                    cycle["net_cash"] -= piece * price * multiplier + piece_commission
                    cycle["commission"] += piece_commission
                    cycle["event_count"] += 1
                    cycle["updated_at"] = str(event["occurred_at"])
                    cycle["recorded_at"] = max(
                        str(cycle.get("recorded_at") or ""),
                        str(event.get("recorded_at") or event["occurred_at"]),
                    )
                    cycle["executions"].append(
                        _execution(
                            event,
                            "open" if opening else "add",
                            piece,
                            price,
                            piece_commission,
                            new_quantity,
                        )
                    )
                    remaining = 0.0
                    remaining_commission = 0.0
                    continue

                close_quantity = min(abs(quantity), abs(remaining))
                piece = -math.copysign(close_quantity, quantity)
                piece_commission = (
                    remaining_commission * close_quantity / abs(remaining)
                    if abs(remaining) >= _EPSILON else 0.0
                )
                cycle = state["cycle"]
                if cycle is None:
                    raise RuntimeError("trade cycle state is inconsistent")
                new_quantity = quantity + piece
                cycle["closed_quantity"] += close_quantity
                cycle["exit_notional"] += close_quantity * price * multiplier
                cycle["net_cash"] -= piece * price * multiplier + piece_commission
                cycle["commission"] += piece_commission
                cycle["event_count"] += 1
                cycle["updated_at"] = str(event["occurred_at"])
                cycle["recorded_at"] = max(
                    str(cycle.get("recorded_at") or ""),
                    str(event.get("recorded_at") or event["occurred_at"]),
                )
                cycle["current_quantity"] = 0.0 if abs(new_quantity) < _EPSILON else new_quantity
                cycle["executions"].append(
                    _execution(
                        event,
                        "close" if abs(new_quantity) < _EPSILON else "reduce",
                        piece,
                        price,
                        piece_commission,
                        cycle["current_quantity"],
                    )
                )
                state["quantity"] = cycle["current_quantity"]
                remaining -= piece
                remaining_commission = max(0.0, remaining_commission - piece_commission)
                if abs(new_quantity) < _EPSILON:
                    cycle["closed_at"] = str(event["occurred_at"])
                    cycle["realized_pnl"] = cycle["net_cash"]
                    cycle["return"] = (
                        cycle["realized_pnl"] / cycle["entry_notional"]
                        if cycle["entry_notional"] > 0 else None
                    )
                    state["average_cost"] = 0.0
                    state["cycle"] = None

    for state in states.values():
        cycle = state.get("cycle")
        if not isinstance(cycle, dict):
            continue
        mark = mark_values.get(str(cycle["instrument_key"]))
        if mark is not None:
            cycle["mark_price"] = mark
            cycle["unrealized_pnl"] = (
                _finite(cycle["current_quantity"])
                * (mark - _finite(cycle["average_cost"]))
                * _finite(cycle["multiplier"], default=1.0)
            )
    return sorted(cycles, key=lambda row: (str(row["updated_at"]), int(row["sequence"])), reverse=True)


def summarize_trade_cycles(cycles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Summarize exactly the selected lifecycles without mixing currencies."""
    result: dict[str, Any] = {
        "total": len(cycles),
        "profitable": 0,
        "losing": 0,
        "breakeven": 0,
        "open": 0,
        "currencies": {},
    }
    for cycle in cycles:
        currency = str(cycle.get("currency") or "USD")
        bucket = result["currencies"].setdefault(
            currency,
            {"realized_pnl": 0.0, "entry_notional": 0.0, "unrealized_pnl": 0.0, "open_missing_marks": 0},
        )
        if cycle.get("closed_at"):
            pnl = _finite(cycle.get("realized_pnl"))
            bucket["realized_pnl"] += pnl
            bucket["entry_notional"] += max(0.0, _finite(cycle.get("entry_notional")))
            if pnl > _EPSILON:
                result["profitable"] += 1
            elif pnl < -_EPSILON:
                result["losing"] += 1
            else:
                result["breakeven"] += 1
        else:
            result["open"] += 1
            if cycle.get("unrealized_pnl") is None:
                bucket["open_missing_marks"] += 1
            else:
                bucket["unrealized_pnl"] += _finite(cycle.get("unrealized_pnl"))
    for bucket in result["currencies"].values():
        bucket["return"] = (
            bucket["realized_pnl"] / bucket["entry_notional"]
            if bucket["entry_notional"] > 0 else None
        )
    return result
