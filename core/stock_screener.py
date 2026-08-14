"""Fail-closed, action-oriented US stock screener DTOs and pure engine.

The screener consumes already-authorized official or research candidates.  It
does not fetch quotes, persist preferences, prove a quote, or submit orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Iterable, Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo


HONG_KONG = ZoneInfo("Asia/Hong_Kong")
SCHEMA_VERSION = 1
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100
SORT_FIELDS = frozenset({"score", "symbol", "price", "change_pct"})
SORT_DIRECTIONS = frozenset({"asc", "desc"})
DATA_STATES = frozenset({"fresh", "delayed", "stale", "missing"})
HEALTH_STATES = frozenset({"healthy", "degraded", "unavailable"})
CANDIDATE_STATES = frozenset({"official", "research"})
FILTER_FIELDS = frozenset({
    "actions", "data_states", "max_price", "max_score", "min_price",
    "min_score", "states", "symbols",
})
REQUEST_FIELDS = frozenset({"filters", "page", "page_size", "preset", "sort"})
PRESET_FIELDS = frozenset({"filters", "name", "sort", "version"})
PRESET_NAMES = frozenset({"all", "momentum", "pullback", "risk_first"})
SAFE_RESEARCH_ROUTE = "/discover?tool=screener"


class StockScreenerError(ValueError):
    """Invalid or unsafe screener input."""


class StockScreenerConflict(StockScreenerError):
    """Optimistic preset version conflict."""


class StockScreenerAccessError(PermissionError):
    """The current membership cannot use the screener."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise StockScreenerError(f"{field} must be a finite number")
    return float(value)


def _text(value: Any, field: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise StockScreenerError(f"{field} must be a non-empty string")
    return value.strip()


def _list_of_text(value: Any, field: str, *, max_length: int = 40) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > max_length:
        raise StockScreenerError(f"{field} must be a bounded list")
    result = tuple(_text(item, field, max_length=40) for item in value)
    if len(set(result)) != len(result):
        raise StockScreenerError(f"{field} must not contain duplicates")
    return result


def _timestamp_hk(value: Any, now: datetime) -> str:
    if value is None:
        parsed = now
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StockScreenerError("updated_at must be an ISO timestamp") from exc
    else:
        raise StockScreenerError("updated_at must be an ISO timestamp")
    if parsed.tzinfo is None:
        raise StockScreenerError("updated_at must include a timezone")
    return parsed.astimezone(HONG_KONG).isoformat(timespec="seconds")


def _validate_filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = {} if filters is None else dict(filters)
    unknown = set(raw) - FILTER_FIELDS
    if unknown:
        raise StockScreenerError("unknown screener filter")
    result: dict[str, Any] = {}
    for field in ("min_score", "max_score", "min_price", "max_price"):
        if field in raw:
            result[field] = _finite_number(raw[field], field)
    if "min_score" in result and "max_score" in result and result["min_score"] > result["max_score"]:
        raise StockScreenerError("min_score cannot exceed max_score")
    if "min_price" in result and "max_price" in result and result["min_price"] > result["max_price"]:
        raise StockScreenerError("min_price cannot exceed max_price")
    for field in ("actions", "data_states", "states", "symbols"):
        if field not in raw:
            continue
        values = _list_of_text(raw[field], field)
        if field == "data_states" and not set(values).issubset(DATA_STATES):
            raise StockScreenerError("invalid data state filter")
        if field == "states" and not set(values).issubset(CANDIDATE_STATES):
            raise StockScreenerError("invalid candidate state filter")
        result[field] = values
    return result


def _preset_filters(name: str) -> dict[str, Any]:
    if name not in PRESET_NAMES:
        raise StockScreenerError("unknown screener preset")
    return {
        "all": {},
        "momentum": {"min_score": 30, "actions": ["buy", "hold"]},
        "pullback": {"actions": ["buy", "hold"], "max_score": 75},
        "risk_first": {"actions": ["wait", "reduce", "exit"]},
    }[name].copy()


def validate_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the public screening DTO and return a normalized copy."""
    raw = {} if payload is None else dict(payload)
    unknown = set(raw) - REQUEST_FIELDS
    if unknown:
        raise StockScreenerError("unknown screener request field")
    preset = raw.get("preset", "all")
    if not isinstance(preset, str) or preset not in PRESET_NAMES:
        raise StockScreenerError("invalid screener preset")
    filters = _preset_filters(preset)
    custom = _validate_filters(raw.get("filters"))
    filters.update(custom)
    page = raw.get("page", 1)
    page_size = raw.get("page_size", PAGE_SIZE_DEFAULT)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise StockScreenerError("page must be a positive integer")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= PAGE_SIZE_MAX:
        raise StockScreenerError("page_size is out of range")
    sort = raw.get("sort", {"field": "score", "direction": "desc"})
    if not isinstance(sort, Mapping) or set(sort) != {"field", "direction"}:
        raise StockScreenerError("sort must contain field and direction")
    field, direction = sort["field"], sort["direction"]
    if field not in SORT_FIELDS or direction not in SORT_DIRECTIONS:
        raise StockScreenerError("invalid screener sort")
    return {
        "schema_version": SCHEMA_VERSION,
        "preset": preset,
        "filters": filters,
        "page": page,
        "page_size": page_size,
        "sort": {"field": field, "direction": direction},
    }


def validate_preset(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a versioned, persistence-neutral preset DTO."""
    raw = dict(payload)
    if set(raw) - PRESET_FIELDS - {"schema_version"}:
        raise StockScreenerError("preset DTO fields are invalid")
    if "schema_version" in raw and raw["schema_version"] != SCHEMA_VERSION:
        raise StockScreenerError("preset schema version is invalid")
    if not PRESET_FIELDS.issubset(raw):
        raise StockScreenerError("preset DTO fields are incomplete")
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise StockScreenerError("preset version must be a non-negative integer")
    name = _text(raw["name"], "name", max_length=80)
    sort = raw["sort"]
    if not isinstance(sort, Mapping) or set(sort) != {"field", "direction"}:
        raise StockScreenerError("preset sort is invalid")
    request = validate_request({"filters": raw["filters"], "sort": sort})
    return {"schema_version": SCHEMA_VERSION, "version": version, "name": name, "filters": request["filters"], "sort": request["sort"]}


def update_preset(current: Mapping[str, Any] | None, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Apply an optimistic version update without writing shared settings."""
    incoming = validate_preset(payload)
    current_version = 0 if current is None else validate_preset(current)["version"]
    if incoming["version"] != current_version:
        raise StockScreenerConflict("screener preset version has changed")
    incoming["version"] += 1
    return incoming


def _action_url(symbol: str) -> str:
    return f"{SAFE_RESEARCH_ROUTE}&symbol={quote(symbol, safe='')}"


def _normalize_candidate(raw: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    allowed = {
        "symbol", "name", "state", "action", "score", "price", "change_pct",
        "reason", "reasons", "counter_evidence", "risk", "invalidation",
        "data_state", "health", "updated_at",
    }
    if set(raw) - allowed:
        raise StockScreenerError("unknown candidate field")
    symbol = _text(raw.get("symbol"), "symbol", max_length=16).upper()
    state = _text(raw.get("state"), "state", max_length=16).lower()
    if state not in CANDIDATE_STATES:
        raise StockScreenerError("candidate state must be official or research")
    action = _text(raw.get("action"), "action", max_length=20).lower()
    score = _finite_number(raw.get("score"), "score")
    price = _finite_number(raw.get("price"), "price")
    change_pct = _finite_number(raw.get("change_pct", 0), "change_pct")
    if price <= 0:
        raise StockScreenerError("price must be positive")
    data_state = _text(raw.get("data_state"), "data_state", max_length=16).lower()
    health = _text(raw.get("health", "healthy"), "health", max_length=16).lower()
    if data_state not in DATA_STATES or health not in HEALTH_STATES:
        raise StockScreenerError("invalid candidate data state")
    reason_values = raw.get("reasons", raw.get("reason", []))
    if isinstance(reason_values, str):
        reasons = [reason_values]
    else:
        reasons = list(_list_of_text(reason_values, "reasons", max_length=8))
    counter = list(_list_of_text(raw.get("counter_evidence", []), "counter_evidence", max_length=8))
    risk = _text(raw.get("risk"), "risk", max_length=240)
    invalidation = _text(raw.get("invalidation"), "invalidation", max_length=240)
    return {
        "symbol": symbol,
        "name": _text(raw.get("name", symbol), "name", max_length=120),
        "state": state,
        "action": action,
        "score": score,
        "price": price,
        "change_pct": change_pct,
        "reasons": reasons,
        "counter_evidence": counter,
        "risk": risk,
        "invalidation": invalidation,
        "data_state": data_state,
        "health": health,
        "hong_kong_time": _timestamp_hk(raw.get("updated_at"), now),
        "research_url": _action_url(symbol),
        "alert_prefill": {"market": "US", "symbol": symbol},
        "paper_prefill": {"market": "US", "symbol": symbol, "side": "BUY" if action in {"buy", "hold"} else "SELL"},
    }


def _matches(item: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    if "min_score" in filters and item["score"] < filters["min_score"]:
        return False
    if "max_score" in filters and item["score"] > filters["max_score"]:
        return False
    if "min_price" in filters and item["price"] < filters["min_price"]:
        return False
    if "max_price" in filters and item["price"] > filters["max_price"]:
        return False
    for field in ("actions", "data_states", "states", "symbols"):
        if field in filters and item["action" if field == "actions" else "data_state" if field == "data_states" else "state" if field == "states" else "symbol"] not in filters[field]:
            return False
    return True


def screen_candidates(
    candidates: Iterable[Mapping[str, Any]],
    request: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Filter and paginate authorized candidates without side effects."""
    moment = now or datetime.now(HONG_KONG)
    if moment.tzinfo is None:
        raise StockScreenerError("now must include a timezone")
    normalized_request = validate_request(request)
    normalized = [_normalize_candidate(dict(item), moment) for item in candidates]
    filtered = [item for item in normalized if _matches(item, normalized_request["filters"])]
    sort_field = normalized_request["sort"]["field"]
    reverse = normalized_request["sort"]["direction"] == "desc"
    filtered.sort(key=lambda item: (item[sort_field], item["symbol"]), reverse=reverse)
    total = len(filtered)
    start = (normalized_request["page"] - 1) * normalized_request["page_size"]
    end = start + normalized_request["page_size"]
    return {
        "schema_version": SCHEMA_VERSION,
        "preset": normalized_request["preset"],
        "filters": normalized_request["filters"],
        "sort": normalized_request["sort"],
        "page": normalized_request["page"],
        "page_size": normalized_request["page_size"],
        "total": total,
        "items": filtered[start:end],
    }


@dataclass(frozen=True)
class StockScreenerAdapter:
    """API-facing adapter; persistence remains owned by the caller."""

    plan: str

    def screen(self, candidates: Iterable[Mapping[str, Any]], request: Mapping[str, Any] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
        from core.plans import can

        if not can(self.plan, "strategy_all"):
            raise StockScreenerAccessError("当前会员未开放行动型美股选股器。")
        return screen_candidates(candidates, request, now=now)

    def save_preset(self, current: Mapping[str, Any] | None, payload: Mapping[str, Any]) -> dict[str, Any]:
        from core.plans import can

        if not can(self.plan, "strategy_all"):
            raise StockScreenerAccessError("当前会员未开放行动型美股选股器。")
        return update_preset(current, payload)
