# -*- coding: utf-8 -*-
"""Append-only strategy signal and model-position journal."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from core.compat import UTC
import hashlib
import json
import math
import re
import sqlite3
from typing import Any, Mapping, Sequence

from core.database import DatabaseManager, get_database


_EVENT_TYPES = {"signal", "correction", "reversal"}
_SOURCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_US_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_HK_SYMBOL_RE = re.compile(r"\d{5}")
_CN_SYMBOL_RE = re.compile(r"\d{6}")
_MAX_NUMBER = 1e12
_LEGACY_MARKET_CURRENCIES = {"US": "USD", "CN": "CNY"}
_OFFICIAL_PAPER_V2_MARKET_CURRENCIES = {"US": "USD", "HK": "HKD", "CN": "CNY"}
OFFICIAL_PAPER_V2_INITIAL_CASH = {"USD": 10_000.0, "HKD": 10_000.0, "CNY": 10_000.0}


def _text(value: Any, name: str, max_length: int, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    result = value.strip()
    if not result or len(result) > max_length or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError(f"{name} is invalid")
    if pattern and not pattern.fullmatch(result):
        raise ValueError(f"{name} is invalid")
    return result


def _number(
    value: Any,
    name: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or abs(result) > _MAX_NUMBER:
        raise ValueError(f"{name} must be a finite number")
    if positive and result <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return 0.0 if result == 0 else result


def _utc_iso(value: datetime | str | None) -> str:
    if value is None:
        parsed = datetime.now(UTC)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO datetime") from exc
    else:
        raise ValueError("occurred_at must be an ISO datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("occurred_at must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def _json_object(value: Any, name: str = "metadata") -> tuple[dict[str, Any], str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > 65_536:
        raise ValueError(f"{name} is too large")
    return json.loads(encoded), encoded


def _strike_token(value: float) -> str:
    return format(value, ".12g")


def _normalize_leg(
    raw: Any,
    market_currencies: Mapping[str, str] = _LEGACY_MARKET_CURRENCIES,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each leg must be an object")
    allowed = {
        "market", "instrument_type", "symbol", "currency", "option_expiry", "option_right",
        "option_strike", "target_quantity", "quantity_delta", "delta", "price", "multiplier", "commission",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown leg fields: {', '.join(sorted(unknown))}")

    market = str(raw.get("market", "")).strip().upper()
    instrument_type = str(raw.get("instrument_type", "")).strip().lower()
    if market not in market_currencies:
        raise ValueError(f"market must be one of {', '.join(market_currencies)}")
    if instrument_type not in {"stock", "option"}:
        raise ValueError("instrument_type must be stock or option")
    symbol = str(raw.get("symbol", "")).strip().upper()
    symbol_pattern = {
        "US": _US_SYMBOL_RE,
        "HK": _HK_SYMBOL_RE,
        "CN": _CN_SYMBOL_RE,
    }[market]
    if not symbol_pattern.fullmatch(symbol):
        raise ValueError("symbol does not match market")

    expected_currency = market_currencies[market]
    currency = str(raw.get("currency", expected_currency)).strip().upper()
    if currency != expected_currency:
        raise ValueError("currency does not match market")
    target = _number(raw.get("target_quantity"), "target_quantity")
    if "quantity_delta" in raw and "delta" in raw:
        first = _number(raw["quantity_delta"], "quantity_delta")
        second = _number(raw["delta"], "delta")
        if first != second:
            raise ValueError("delta and quantity_delta must match")
    delta = _number(raw.get("quantity_delta", raw.get("delta")), "quantity_delta")
    if abs(delta) < 1e-12:
        raise ValueError("quantity_delta must be non-zero")
    price = _number(raw.get("price"), "price", positive=True)
    commission = _number(raw.get("commission", 0), "commission", nonnegative=True)

    expiry: str | None = None
    right: str | None = None
    strike: float | None = None
    if instrument_type == "stock":
        if any(raw.get(key) is not None for key in ("option_expiry", "option_right", "option_strike")):
            raise ValueError("stock legs cannot contain option fields")
        multiplier = _number(raw.get("multiplier", 1), "multiplier", positive=True)
        if multiplier != 1:
            raise ValueError("stock multiplier must be 1")
        instrument_key = f"{market}:STOCK:{symbol}"
    else:
        if market != "US":
            raise ValueError("only US option contracts are supported")
        try:
            expiry = date.fromisoformat(str(raw.get("option_expiry", ""))).isoformat()
        except ValueError as exc:
            raise ValueError("option_expiry must be an ISO date") from exc
        right_value = str(raw.get("option_right", "")).strip().upper()
        right = {"C": "CALL", "CALL": "CALL", "P": "PUT", "PUT": "PUT"}.get(right_value)
        if right is None:
            raise ValueError("option_right must be CALL or PUT")
        strike = _number(raw.get("option_strike"), "option_strike", positive=True)
        multiplier = _number(raw.get("multiplier", 100), "multiplier", positive=True)
        instrument_key = f"US:OPTION:{symbol}:{expiry}:{right}:{_strike_token(strike)}"

    return {
        "market": market,
        "instrument_type": instrument_type,
        "instrument_key": instrument_key,
        "symbol": symbol,
        "currency": currency,
        "option_expiry": expiry,
        "option_right": right,
        "option_strike": strike,
        "target_quantity": target,
        "quantity_delta": delta,
        "delta": delta,
        "price": price,
        "multiplier": multiplier,
        "commission": commission,
    }


def _rows_to_events(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        event_id = int(row["event_id"])
        event = by_id.get(event_id)
        if event is None:
            event = {
                "id": event_id,
                "ledger_key": row["ledger_key"],
                "source": row["source"],
                "external_event_id": row["external_event_id"],
                "event_type": row["event_type"],
                "strategy_name": row["strategy_name"],
                "strategy_version": row["strategy_version"],
                "corrects_event_id": row["corrects_event_id"],
                "occurred_at": row["occurred_at"],
                "recorded_at": row["recorded_at"],
                "leg_count": int(row["leg_count"]),
                "metadata": json.loads(row["metadata_json"]),
                "payload_hash": row["payload_hash"],
                "legs": [],
            }
            by_id[event_id] = event
            events.append(event)
        if row.get("leg_id") is not None:
            event["legs"].append(
                {
                    "id": int(row["leg_id"]),
                    "leg_no": int(row["leg_no"]),
                    "market": row["market"],
                    "instrument_type": row["instrument_type"],
                    "instrument_key": row["instrument_key"],
                    "symbol": row["symbol"],
                    "currency": row["currency"],
                    "option_expiry": row["option_expiry"],
                    "option_right": row["option_right"],
                    "option_strike": row["option_strike"],
                    "target_quantity": float(row["target_quantity"]),
                    "quantity_delta": float(row["quantity_delta"]),
                    "delta": float(row["quantity_delta"]),
                    "price": float(row["price"]),
                    "multiplier": float(row["multiplier"]),
                    "commission": float(row["commission"]),
                }
            )
    for event in events:
        if len(event["legs"]) != event["leg_count"]:
            raise RuntimeError(f"quant event {event['id']} has an incomplete leg set")
    return events


def _event_select(event_table: str, leg_table: str) -> str:
    return f"""
SELECT e.id event_id,e.ledger_key,e.source,e.external_event_id,e.event_type,
       e.strategy_name,e.strategy_version,e.corrects_event_id,e.occurred_at,e.recorded_at,
       e.leg_count,e.metadata_json,e.payload_hash,
       l.id leg_id,l.leg_no,l.market,l.instrument_type,l.instrument_key,l.symbol,l.currency,
       l.option_expiry,l.option_right,l.option_strike,l.target_quantity,l.quantity_delta,
       l.price,l.multiplier,l.commission
FROM {event_table} e LEFT JOIN {leg_table} l ON l.event_id=e.id
"""


def _active_events(events: Sequence[dict[str, Any]], extra_superseded: set[int] | None = None) -> list[dict[str, Any]]:
    superseded = {int(event["corrects_event_id"]) for event in events if event["corrects_event_id"] is not None}
    superseded.update(extra_superseded or ())
    return [event for event in events if event["id"] not in superseded and event["event_type"] != "reversal"]


def _fold(events: Sequence[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, float], dict[str, float]]:
    positions: dict[str, dict[str, Any]] = {}
    cash_flow: dict[str, float] = {}
    realized: dict[str, float] = {}
    for event in events:
        for leg in event["legs"]:
            key = leg["instrument_key"]
            currency = leg["currency"]
            quantity_delta = float(leg["quantity_delta"])
            price = float(leg["price"])
            multiplier = float(leg["multiplier"])
            commission = float(leg["commission"])
            cash_flow[currency] = cash_flow.get(currency, 0.0) - quantity_delta * price * multiplier - commission
            realized[currency] = realized.get(currency, 0.0) - commission
            position = positions.get(key)
            if position is None:
                position = {
                    **{name: leg[name] for name in (
                        "market", "instrument_type", "instrument_key", "symbol", "currency",
                        "option_expiry", "option_right", "option_strike", "multiplier",
                    )},
                    "quantity": 0.0,
                    "average_cost": 0.0,
                    "last_price": price,
                }
                positions[key] = position
            elif position["currency"] != currency or position["multiplier"] != multiplier:
                raise RuntimeError(f"inconsistent contract identity for {key}")

            quantity = float(position["quantity"])
            average = float(position["average_cost"])
            new_quantity = quantity + quantity_delta
            if quantity == 0 or quantity * quantity_delta > 0:
                position["average_cost"] = (
                    (abs(quantity) * average + abs(quantity_delta) * price) / abs(new_quantity)
                    if new_quantity else 0.0
                )
            else:
                closed = min(abs(quantity), abs(quantity_delta))
                realized[currency] += closed * (price - average) * (1 if quantity > 0 else -1) * multiplier
                if abs(new_quantity) < 1e-12:
                    new_quantity = 0.0
                    position["average_cost"] = 0.0
                elif quantity * new_quantity < 0:
                    position["average_cost"] = price
            position["quantity"] = new_quantity
            position["last_price"] = price
    return positions, cash_flow, realized


class QuantJournal:
    """Atomic append and deterministic replay over the immutable SQLite journal."""

    _EVENT_TABLE = "quant_events"
    _LEG_TABLE = "quant_event_legs"
    _SNAPSHOT_TABLE = "quant_equity_snapshots"
    _MARKET_CURRENCIES = _LEGACY_MARKET_CURRENCIES
    _SNAPSHOT_HAS_MARKET = False
    _DEFAULT_INITIAL_CASH = 100_000.0
    _REQUIRED_INITIAL_CASH: float | None = None

    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def _load(self, conn: sqlite3.Connection, ledger_key: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            _event_select(self._EVENT_TABLE, self._LEG_TABLE)
            + " WHERE e.ledger_key=? ORDER BY e.id,l.leg_no",
            (ledger_key,),
        ).fetchall()
        return _rows_to_events([dict(row) for row in rows])

    def _one(self, conn: sqlite3.Connection, event_id: int) -> dict[str, Any]:
        rows = conn.execute(
            _event_select(self._EVENT_TABLE, self._LEG_TABLE) + " WHERE e.id=? ORDER BY l.leg_no",
            (event_id,),
        ).fetchall()
        events = _rows_to_events([dict(row) for row in rows])
        if not events:
            raise ValueError("quant event does not exist")
        return events[0]

    def append_event(
        self,
        *,
        ledger_key: str,
        source: str,
        external_event_id: str,
        strategy_name: str,
        strategy_version: str,
        legs: Sequence[dict[str, Any]] = (),
        event_type: str = "signal",
        corrects_event_id: int | None = None,
        occurred_at: datetime | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ledger_key = _text(ledger_key, "ledger_key", 128, _KEY_RE)
        source = _text(source, "source", 64, _SOURCE_RE)
        external_event_id = _text(external_event_id, "external_event_id", 128)
        strategy_name = _text(strategy_name, "strategy_name", 100)
        strategy_version = _text(strategy_version, "strategy_version", 64)
        event_type = str(event_type).strip().lower()
        if event_type not in _EVENT_TYPES:
            raise ValueError("event_type must be signal, correction, or reversal")
        if isinstance(legs, (str, bytes)) or not isinstance(legs, Sequence):
            raise ValueError("legs must be a sequence")
        normalized_legs = [_normalize_leg(leg, self._MARKET_CURRENCIES) for leg in legs]
        instrument_keys = [leg["instrument_key"] for leg in normalized_legs]
        if len(instrument_keys) != len(set(instrument_keys)):
            raise ValueError("an event cannot repeat an instrument")
        if event_type == "reversal" and normalized_legs:
            raise ValueError("reversal events cannot contain legs")
        if event_type == "correction" and not normalized_legs:
            raise ValueError("correction events require replacement legs")
        if event_type == "signal" and corrects_event_id is not None:
            raise ValueError("signal events cannot correct another event")
        if event_type != "signal":
            if isinstance(corrects_event_id, bool) or not isinstance(corrects_event_id, int) or corrects_event_id <= 0:
                raise ValueError("correction and reversal events require corrects_event_id")
        metadata_value, metadata_json = _json_object(metadata)
        explicit_occurred = _utc_iso(occurred_at) if occurred_at is not None else None

        try:
            with self.db.transaction() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    f"SELECT id,occurred_at,payload_hash FROM {self._EVENT_TABLE} "
                    "WHERE source=? AND external_event_id=?",
                    (source, external_event_id),
                ).fetchone()
                normalized_occurred = explicit_occurred or (str(existing["occurred_at"]) if existing else _utc_iso(None))
                payload = {
                    "ledger_key": ledger_key,
                    "source": source,
                    "external_event_id": external_event_id,
                    "event_type": event_type,
                    "strategy_name": strategy_name,
                    "strategy_version": strategy_version,
                    "corrects_event_id": corrects_event_id,
                    "occurred_at": normalized_occurred,
                    "metadata": metadata_value,
                    "legs": normalized_legs,
                }
                payload_hash = hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                ).hexdigest()
                if existing:
                    if existing["payload_hash"] != payload_hash:
                        raise ValueError("idempotency key was reused with different content")
                    result = self._one(conn, int(existing["id"]))
                    result["created"] = False
                    return result

                events = self._load(conn, ledger_key)
                if events and normalized_occurred < events[-1]["occurred_at"]:
                    raise ValueError("occurred_at cannot precede the latest ledger event")
                extra_superseded: set[int] = set()
                if corrects_event_id is not None:
                    target = conn.execute(
                        f"SELECT id,ledger_key FROM {self._EVENT_TABLE} WHERE id=?", (corrects_event_id,)
                    ).fetchone()
                    if not target or target["ledger_key"] != ledger_key:
                        raise ValueError("corrected event must exist in the same ledger")
                    if conn.execute(
                        f"SELECT 1 FROM {self._EVENT_TABLE} WHERE corrects_event_id=?", (corrects_event_id,)
                    ).fetchone():
                        raise ValueError("quant event has already been superseded")
                    extra_superseded.add(corrects_event_id)

                current_positions, _, _ = _fold(_active_events(events, extra_superseded))
                for leg in normalized_legs:
                    current = float(current_positions.get(leg["instrument_key"], {}).get("quantity", 0.0))
                    expected = current + float(leg["quantity_delta"])
                    if not math.isclose(expected, float(leg["target_quantity"]), rel_tol=1e-9, abs_tol=1e-9):
                        raise ValueError(
                            f"target_quantity for {leg['instrument_key']} must equal current quantity plus quantity_delta"
                        )
                    prior = current_positions.get(leg["instrument_key"])
                    if prior and (
                        prior["currency"] != leg["currency"] or prior["multiplier"] != leg["multiplier"]
                    ):
                        raise ValueError("instrument identity cannot change across events")

                cursor = conn.execute(
                    f"""INSERT INTO {self._EVENT_TABLE}
                        (ledger_key,source,external_event_id,event_type,strategy_name,strategy_version,
                         corrects_event_id,occurred_at,recorded_at,leg_count,metadata_json,payload_hash)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ledger_key, source, external_event_id, event_type, strategy_name, strategy_version,
                        corrects_event_id, normalized_occurred, _utc_iso(None), len(normalized_legs),
                        metadata_json, payload_hash,
                    ),
                )
                event_id = int(cursor.lastrowid)
                conn.executemany(
                    f"""INSERT INTO {self._LEG_TABLE}
                        (event_id,leg_no,market,instrument_type,instrument_key,symbol,currency,
                         option_expiry,option_right,option_strike,target_quantity,quantity_delta,
                         price,multiplier,commission)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (
                            event_id, leg_no, leg["market"], leg["instrument_type"], leg["instrument_key"],
                            leg["symbol"], leg["currency"], leg["option_expiry"], leg["option_right"],
                            leg["option_strike"], leg["target_quantity"], leg["quantity_delta"],
                            leg["price"], leg["multiplier"], leg["commission"],
                        )
                        for leg_no, leg in enumerate(normalized_legs)
                    ],
                )
                result = self._one(conn, event_id)
                result["created"] = True
                return result
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"quant journal constraint failed: {exc}") from exc

    def append_reversal(
        self,
        *,
        source: str,
        external_event_id: str,
        corrects_event_id: int,
        occurred_at: datetime | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if isinstance(corrects_event_id, bool) or not isinstance(corrects_event_id, int) or corrects_event_id <= 0:
            raise ValueError("corrects_event_id must be a positive integer")
        row = self.db.fetch_one(
            f"SELECT ledger_key,strategy_name,strategy_version FROM {self._EVENT_TABLE} WHERE id=?",
            (corrects_event_id,),
        )
        if not row:
            raise ValueError("quant event does not exist")
        return self.append_event(
            ledger_key=row["ledger_key"],
            source=source,
            external_event_id=external_event_id,
            event_type="reversal",
            strategy_name=row["strategy_name"],
            strategy_version=row["strategy_version"],
            corrects_event_id=corrects_event_id,
            occurred_at=occurred_at,
            metadata=metadata,
        )

    def list_events(self, ledger_key: str) -> list[dict[str, Any]]:
        ledger_key = _text(ledger_key, "ledger_key", 128, _KEY_RE)
        rows = self.db.fetch_all(
            _event_select(self._EVENT_TABLE, self._LEG_TABLE)
            + " WHERE e.ledger_key=? ORDER BY e.id,l.leg_no",
            (ledger_key,),
        )
        events = _rows_to_events(rows)
        superseded = {event["corrects_event_id"] for event in events if event["corrects_event_id"] is not None}
        for event in events:
            event["active"] = event["id"] not in superseded and event["event_type"] != "reversal"
        return events

    def execution_legs(self, event_id: int) -> list[dict[str, Any]]:
        """Derive the position adjustment caused by an immutable event."""
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
            raise ValueError("event_id must be a positive integer")
        row = self.db.fetch_one(
            f"SELECT ledger_key FROM {self._EVENT_TABLE} WHERE id=?", (event_id,)
        )
        if not row:
            raise ValueError("quant event does not exist")
        events = self.list_events(row["ledger_key"])
        index = next(index for index, event in enumerate(events) if event["id"] == event_id)
        event = events[index]
        before, _, _ = _fold(_active_events(events[:index]))
        after, _, _ = _fold(_active_events(events[: index + 1]))
        corrected = next(
            (item for item in events[:index] if item["id"] == event["corrects_event_id"]),
            None,
        )
        old_legs = {leg["instrument_key"]: leg for leg in corrected["legs"]} if corrected else {}
        new_legs = {leg["instrument_key"]: leg for leg in event["legs"]}
        keys = dict.fromkeys((*old_legs, *new_legs))
        result = []
        for key in keys:
            source = new_legs.get(key) or old_legs[key]
            before_quantity = float(before.get(key, {}).get("quantity", 0.0))
            after_quantity = float(after.get(key, {}).get("quantity", 0.0))
            leg = {name: value for name, value in source.items() if name not in {"id", "leg_no"}}
            leg.update(
                target_quantity=after_quantity,
                quantity_delta=after_quantity - before_quantity,
                delta=after_quantity - before_quantity,
                price=new_legs.get(key, {}).get("price"),
            )
            result.append(leg)
        return result

    def replay(
        self,
        ledger_key: str,
        *,
        marks: Mapping[str, int | float] | None = None,
        initial_cash: Mapping[str, int | float] | None = None,
    ) -> dict[str, Any]:
        events = self.list_events(ledger_key)
        active = [event for event in events if event["active"]]
        positions, cash_flow, realized = _fold(active)

        mark_values: dict[str, float] = {}
        if marks is not None:
            if not isinstance(marks, Mapping):
                raise ValueError("marks must be an object")
            for key, value in marks.items():
                if not isinstance(key, str) or key not in positions:
                    raise ValueError("marks contain an unknown instrument")
                mark_values[key] = _number(value, f"mark for {key}", positive=True)

        initial: dict[str, float] = {}
        if initial_cash is not None:
            if not isinstance(initial_cash, Mapping):
                raise ValueError("initial_cash must be an object keyed by currency")
            for currency, value in initial_cash.items():
                if currency not in set(self._MARKET_CURRENCIES.values()):
                    raise ValueError(
                        "initial_cash currency must be one of "
                        + ", ".join(self._MARKET_CURRENCIES.values())
                    )
                initial[currency] = _number(value, f"initial_cash {currency}", nonnegative=True)

        output_positions: dict[str, dict[str, Any]] = {}
        unrealized: dict[str, float] = {}
        market_value: dict[str, float] = {}
        for key, position in positions.items():
            quantity = float(position["quantity"])
            if abs(quantity) < 1e-12:
                continue
            mark = mark_values.get(key, float(position["last_price"]))
            multiplier = float(position["multiplier"])
            value = quantity * mark * multiplier
            floating = quantity * (mark - float(position["average_cost"])) * multiplier
            currency = position["currency"]
            market_value[currency] = market_value.get(currency, 0.0) + value
            unrealized[currency] = unrealized.get(currency, 0.0) + floating
            output_positions[key] = {
                **position,
                "mark_price": mark,
                "market_value": value,
                "unrealized_pnl": floating,
            }

        currencies = set(initial) | set(cash_flow) | set(realized) | set(unrealized) | set(market_value)
        totals = {}
        for currency in sorted(currencies):
            realized_value = realized.get(currency, 0.0)
            unrealized_value = unrealized.get(currency, 0.0)
            totals[currency] = {
                "initial_cash": initial.get(currency, 0.0),
                "cash_flow": cash_flow.get(currency, 0.0),
                "cash": initial.get(currency, 0.0) + cash_flow.get(currency, 0.0),
                "market_value": market_value.get(currency, 0.0),
                "realized_pnl": realized_value,
                "unrealized_pnl": unrealized_value,
                "total_pnl": realized_value + unrealized_value,
            }
        return {
            "ledger_key": ledger_key,
            "event_count": len(events),
            "active_event_count": len(active),
            "positions": output_positions,
            "currencies": totals,
            "timeline": [
                {
                    "id": event["id"],
                    "event_type": event["event_type"],
                    "strategy_name": event["strategy_name"],
                    "strategy_version": event["strategy_version"],
                    "occurred_at": event["occurred_at"],
                    "active": event["active"],
                }
                for event in events
            ],
        }

    def append_equity_snapshot(
        self,
        *,
        ledger_key: str,
        source: str,
        external_snapshot_id: str,
        currency: str,
        market: str | None = None,
        cash: int | float,
        market_value: int | float,
        realized_pnl: int | float,
        unrealized_pnl: int | float,
        initial_cash: int | float | None = None,
        captured_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        ledger_key = _text(ledger_key, "ledger_key", 128, _KEY_RE)
        source = _text(source, "source", 64, _SOURCE_RE)
        external_snapshot_id = _text(external_snapshot_id, "external_snapshot_id", 128)
        currency = str(currency).strip().upper()
        allowed_currencies = set(self._MARKET_CURRENCIES.values())
        if currency not in allowed_currencies:
            raise ValueError("currency must be one of " + ", ".join(self._MARKET_CURRENCIES.values()))
        normalized_market: str | None = None
        if self._SNAPSHOT_HAS_MARKET:
            normalized_market = str(market or "").strip().upper()
            if self._MARKET_CURRENCIES.get(normalized_market) != currency:
                raise ValueError("market and currency do not match")
        elif market is not None:
            raise ValueError("market is not supported by this journal contract")
        if initial_cash is None:
            initial_cash = self._DEFAULT_INITIAL_CASH
        initial = _number(initial_cash, "initial_cash", nonnegative=True)
        if self._REQUIRED_INITIAL_CASH is not None and not math.isclose(
            initial, self._REQUIRED_INITIAL_CASH, rel_tol=0, abs_tol=0.001
        ):
            raise ValueError(f"initial_cash must equal {self._REQUIRED_INITIAL_CASH:g}")
        cash_value = _number(cash, "cash")
        market_value_value = _number(market_value, "market_value")
        realized = _number(realized_pnl, "realized_pnl")
        unrealized = _number(unrealized_pnl, "unrealized_pnl")
        equity = cash_value + market_value_value
        total_pnl = realized + unrealized
        if not math.isclose(initial + total_pnl, equity, rel_tol=1e-9, abs_tol=0.01):
            raise ValueError("equity must equal initial_cash plus realized and unrealized pnl")
        captured = _utc_iso(captured_at)
        payload = {
            "ledger_key": ledger_key,
            "source": source,
            "external_snapshot_id": external_snapshot_id,
            "captured_at": captured,
            "currency": currency,
            "initial_cash": initial,
            "cash": cash_value,
            "market_value": market_value_value,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_equity": equity,
            "total_pnl": total_pnl,
        }
        if normalized_market is not None:
            payload["market"] = normalized_market
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        try:
            with self.db.transaction() as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    f"""SELECT * FROM {self._SNAPSHOT_TABLE}
                        WHERE source=? AND external_snapshot_id=? AND currency=?""",
                    (source, external_snapshot_id, currency),
                ).fetchone()
                if existing:
                    if existing["payload_hash"] != payload_hash:
                        raise ValueError("snapshot idempotency key was reused with different content")
                    return {**dict(existing), "created": False}
                latest = conn.execute(
                    f"""SELECT captured_at FROM {self._SNAPSHOT_TABLE}
                        WHERE ledger_key=? AND currency=? ORDER BY captured_at DESC,id DESC LIMIT 1""",
                    (ledger_key, currency),
                ).fetchone()
                if latest and captured < latest["captured_at"]:
                    raise ValueError("captured_at cannot precede the latest equity snapshot")
                columns = (
                    "ledger_key,source,external_snapshot_id,captured_at,"
                    + ("market," if self._SNAPSHOT_HAS_MARKET else "")
                    + "currency,initial_cash,cash,market_value,realized_pnl,unrealized_pnl,"
                    "total_equity,total_pnl,recorded_at,payload_hash"
                )
                values = (
                    ledger_key, source, external_snapshot_id, captured,
                    *((normalized_market,) if self._SNAPSHOT_HAS_MARKET else ()),
                    currency, initial, cash_value, market_value_value, realized, unrealized,
                    equity, total_pnl, _utc_iso(None), payload_hash,
                )
                placeholders = ",".join("?" for _ in values)
                cursor = conn.execute(
                    f"INSERT INTO {self._SNAPSHOT_TABLE} ({columns}) VALUES ({placeholders})",
                    values,
                )
                row = conn.execute(
                    f"SELECT * FROM {self._SNAPSHOT_TABLE} WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
                return {**dict(row), "created": True}
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"quant equity snapshot constraint failed: {exc}") from exc

    def list_equity_snapshots(self, ledger_key: str, currency: str, limit: int = 20_000) -> list[dict[str, Any]]:
        ledger_key = _text(ledger_key, "ledger_key", 128, _KEY_RE)
        currency = str(currency).strip().upper()
        if currency not in set(self._MARKET_CURRENCIES.values()):
            raise ValueError("currency must be one of " + ", ".join(self._MARKET_CURRENCIES.values()))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20_000:
            raise ValueError("limit must be between 1 and 20000")
        rows = self.db.fetch_all(
            f"""SELECT * FROM {self._SNAPSHOT_TABLE} WHERE ledger_key=? AND currency=?
                ORDER BY captured_at DESC,id DESC LIMIT ?""",
            (ledger_key, currency, limit),
        )
        return list(reversed(rows))

    def performance_windows(self, ledger_key: str, currency: str) -> dict[str, Any] | None:
        snapshots = self.list_equity_snapshots(ledger_key, currency)
        if not snapshots:
            return None
        latest = snapshots[-1]
        latest_time = datetime.fromisoformat(latest["captured_at"])
        windows = {}
        for label, days in (("1周", 7), ("1个月", 30), ("3个月", 90), ("6个月", 182), ("1年", 365)):
            cutoff = latest_time - timedelta(days=days)
            baseline = next(
                (row for row in reversed(snapshots[:-1]) if datetime.fromisoformat(row["captured_at"]) <= cutoff),
                None,
            )
            if baseline is None or cutoff - datetime.fromisoformat(baseline["captured_at"]) > timedelta(days=7):
                windows[label] = {"available": False, "days": days}
                continue
            change = float(latest["total_equity"]) - float(baseline["total_equity"])
            base_equity = float(baseline["total_equity"])
            windows[label] = {
                "available": True,
                "days": days,
                "baseline_at": baseline["captured_at"],
                "baseline_equity": base_equity,
                "pnl": change,
                "return": change / base_equity if base_equity else None,
            }
        return {"current": latest, "windows": windows, "snapshot_count": len(snapshots)}


class OfficialPaperJournalV2(QuantJournal):
    """Independent three-market paper ledger with a fixed 10,000-unit genesis."""

    _EVENT_TABLE = "official_paper_events_v2"
    _LEG_TABLE = "official_paper_event_legs_v2"
    _SNAPSHOT_TABLE = "official_paper_equity_snapshots_v2"
    _MARKET_CURRENCIES = _OFFICIAL_PAPER_V2_MARKET_CURRENCIES
    _SNAPSHOT_HAS_MARKET = True
    _DEFAULT_INITIAL_CASH = 10_000.0
    _REQUIRED_INITIAL_CASH = 10_000.0
    _DEFAULT_LEDGER_KEY = "tradeai-official-paper-v2"
    _GENESIS_SOURCE = "ciclotrade-official-paper-v2"
    _GENESIS_AT = "1970-01-01T00:00:00.000000+00:00"

    def ensure_genesis(self, ledger_key: str = _DEFAULT_LEDGER_KEY) -> list[dict[str, Any]]:
        ledger_key = _text(ledger_key, "ledger_key", 128, _KEY_RE)
        external_snapshot_id = (
            "genesis"
            if ledger_key == self._DEFAULT_LEDGER_KEY
            else "genesis-" + hashlib.sha256(ledger_key.encode("utf-8")).hexdigest()[:24]
        )
        rows = self.db.fetch_all(
            f"""SELECT * FROM {self._SNAPSHOT_TABLE}
                WHERE ledger_key=? AND source=? AND external_snapshot_id=?""",
            (ledger_key, self._GENESIS_SOURCE, external_snapshot_id),
        )
        present = {str(row["market"]): row for row in rows}
        for market, currency in self._MARKET_CURRENCIES.items():
            if market in present:
                continue
            present[market] = self.append_equity_snapshot(
                ledger_key=ledger_key,
                source=self._GENESIS_SOURCE,
                external_snapshot_id=external_snapshot_id,
                market=market,
                currency=currency,
                initial_cash=OFFICIAL_PAPER_V2_INITIAL_CASH[currency],
                cash=OFFICIAL_PAPER_V2_INITIAL_CASH[currency],
                market_value=0,
                realized_pnl=0,
                unrealized_pnl=0,
                captured_at=self._GENESIS_AT,
            )
        return [dict(present[market]) for market in self._MARKET_CURRENCIES]
