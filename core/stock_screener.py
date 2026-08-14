"""Fail-closed, action-oriented US stock screener DTOs and pure engine.

The screener consumes already-authorized official or research candidates.  It
does not fetch quotes, persist preferences, prove a quote, or submit orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from itertools import islice
from math import isfinite
import re
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo

from core.compat import UTC
from core.database import DatabaseManager, get_database


HONG_KONG = ZoneInfo("Asia/Hong_Kong")
SCHEMA_VERSION = 1
PAGE_SIZE_DEFAULT = 20
PAGE_SIZE_MAX = 100
PAGE_MAX = 1000
CANDIDATE_MAX = 500
SORT_FIELDS = frozenset({"score", "symbol", "price", "change_pct", "updated_at"})
SORT_DIRECTIONS = frozenset({"asc", "desc"})
DATA_STATES = frozenset({"fresh", "delayed", "stale", "missing"})
HEALTH_STATES = frozenset({"healthy", "degraded", "unavailable"})
CANDIDATE_STATES = frozenset({"official", "research"})
CANDIDATE_ACTIONS = frozenset({"buy", "short", "wait", "hold", "reduce", "exit"})
FILTER_FIELDS = frozenset({
    "actions", "data_states", "max_price", "max_score", "min_price",
    "min_score", "states", "symbols",
})
REQUEST_FIELDS = frozenset({"filters", "page", "page_size", "preset", "sort"})
PRESET_FIELDS = frozenset({"filters", "name", "sort", "version"})
PRESET_NAMES = frozenset({"all", "momentum", "pullback", "risk_first"})
SAFE_RESEARCH_ROUTE = "/discover?tool=screener"
SCREENER_PRESET_KEY = "stock_screener_preset"
US_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,5})?$")
MAX_PRICE = 10_000_000.0
MAX_CHANGE_PCT = 1_000.0


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


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field)


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
    if any(field in result and not 0 <= result[field] <= 100 for field in ("min_score", "max_score")):
        raise StockScreenerError("score filter must be between 0 and 100")
    if any(field in result and not 0 < result[field] <= MAX_PRICE for field in ("min_price", "max_price")):
        raise StockScreenerError("price filter is out of range")
    if "min_score" in result and "max_score" in result and result["min_score"] > result["max_score"]:
        raise StockScreenerError("min_score cannot exceed max_score")
    if "min_price" in result and "max_price" in result and result["min_price"] > result["max_price"]:
        raise StockScreenerError("min_price cannot exceed max_price")
    for field in ("actions", "data_states", "states", "symbols"):
        if field not in raw:
            continue
        values = _list_of_text(raw[field], field)
        if field == "actions" and not set(values).issubset(CANDIDATE_ACTIONS):
            raise StockScreenerError("invalid action filter")
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


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise StockScreenerError(f"{field} must be an object")
    return dict(value)


def validate_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the public screening DTO and return a normalized copy."""
    raw = _mapping(payload, "request")
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
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= PAGE_MAX:
        raise StockScreenerError("page must be a positive integer")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= PAGE_SIZE_MAX:
        raise StockScreenerError("page_size is out of range")
    sort = raw.get("sort", {"field": "updated_at", "direction": "desc"})
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
    raw = _mapping(payload, "preset")
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


class ScreenerPresetStore:
    """CAS-protected screener preset storage inside the existing settings row."""

    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def _user_id(user_id: int) -> int:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise StockScreenerError("user_id must be a positive integer")
        return user_id

    @staticmethod
    def _settings(value: Any) -> dict[str, Any]:
        try:
            decoded = json.loads(value) if value is not None else {}
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StockScreenerError("stored user settings are invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise StockScreenerError("stored user settings must be an object")
        return decoded

    def load(self, user_id: int) -> dict[str, Any] | None:
        user_id = self._user_id(user_id)
        row = self.db.fetch_one("SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,))
        settings = self._settings(row["settings_json"] if row else None)
        raw = settings.get(SCREENER_PRESET_KEY)
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise StockScreenerError("stored screener preset is invalid")
        return validate_preset(raw)

    def replace(self, user_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        user_id = self._user_id(user_id)
        incoming = validate_preset(payload)
        with self.db.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)
            ).fetchone()
            settings = self._settings(row["settings_json"] if row else None)
            raw = settings.get(SCREENER_PRESET_KEY)
            current = None
            if raw is not None:
                if not isinstance(raw, Mapping):
                    raise StockScreenerError("stored screener preset is invalid")
                current = validate_preset(raw)
            updated = update_preset(current, incoming)
            settings[SCREENER_PRESET_KEY] = updated
            connection.execute(
                """INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES (?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
                (user_id, json.dumps(settings, ensure_ascii=False), datetime.now(UTC).isoformat(timespec="seconds")),
            )
        return updated

    save = replace


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
    if not US_SYMBOL_RE.fullmatch(symbol):
        raise StockScreenerError("symbol must be a controlled US symbol")
    state = _text(raw.get("state"), "state", max_length=16).lower()
    if state not in CANDIDATE_STATES:
        raise StockScreenerError("candidate state must be official or research")
    action = _text(raw.get("action"), "action", max_length=20).lower()
    if action not in CANDIDATE_ACTIONS:
        raise StockScreenerError("candidate action is not allowed")
    score = _optional_number(raw.get("score"), "score")
    if score is not None and not 0 <= score <= 100:
        raise StockScreenerError("score must be between 0 and 100")
    price = _finite_number(raw.get("price"), "price")
    change_pct = _finite_number(raw.get("change_pct", 0), "change_pct")
    if not 0 < price <= MAX_PRICE:
        raise StockScreenerError("price must be positive")
    if abs(change_pct) > MAX_CHANGE_PCT:
        raise StockScreenerError("change_pct is out of range")
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
    paper_prefill = None
    blocked_reason = None
    actionable = False
    if action == "buy" and data_state == "fresh" and health == "healthy":
        paper_prefill = {"market": "US", "symbol": symbol, "side": "BUY"}
        actionable = True
    elif data_state != "fresh":
        blocked_reason = "market_data_not_fresh"
    elif health != "healthy":
        blocked_reason = "candidate_health_not_healthy"
    else:
        blocked_reason = "candidate_action_not_tradeable"
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
        "updated_at": _timestamp_hk(raw.get("updated_at"), now),
        "hong_kong_time": _timestamp_hk(raw.get("updated_at"), now),
        "research_url": _action_url(symbol),
        "alert_prefill": {"market": "US", "symbol": symbol},
        "paper_prefill": paper_prefill,
        "blocked_reason": blocked_reason,
        "actionable": actionable,
    }


def _matches(item: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    if item["score"] is None and ("min_score" in filters or "max_score" in filters):
        return False
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
    try:
        raw_candidates = list(islice(iter(candidates), CANDIDATE_MAX + 1))
    except (TypeError, ValueError) as exc:
        raise StockScreenerError("candidates must be iterable") from exc
    if len(raw_candidates) > CANDIDATE_MAX:
        raise StockScreenerError("candidate count exceeds limit")
    try:
        normalized = [_normalize_candidate(dict(item), moment) for item in raw_candidates]
    except (TypeError, ValueError) as exc:
        raise StockScreenerError("candidate must be an object") from exc
    symbols = [item["symbol"] for item in normalized]
    if len(set(symbols)) != len(symbols):
        raise StockScreenerError("candidate symbols must be unique")
    filtered = [item for item in normalized if _matches(item, normalized_request["filters"])]
    sort_field = normalized_request["sort"]["field"]
    reverse = normalized_request["sort"]["direction"] == "desc"
    filtered.sort(key=lambda item: item["symbol"])
    if sort_field == "score":
        filtered.sort(key=lambda item: item["score"] is None)
        filtered.sort(key=lambda item: item["score"] if item["score"] is not None else 0, reverse=reverse)
    else:
        filtered.sort(key=lambda item: item[sort_field], reverse=reverse)
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


def recommendation_to_candidate(recommendation: Mapping[str, Any]) -> dict[str, Any]:
    """Map the existing recommendation DTO without inventing a score."""
    if not isinstance(recommendation, Mapping):
        raise StockScreenerError("recommendation must be an object")
    instrument = recommendation.get("instrument")
    if instrument is None:
        if recommendation.get("market") != "US" or recommendation.get("instrument_type") != "stock":
            raise StockScreenerError("recommendation must describe a US stock")
        action = recommendation.get("action")
        state = recommendation.get("state")
        if state not in {"official", "research"}:
            raise StockScreenerError("recommendation status is not screener eligible")
        return {
            "symbol": recommendation.get("symbol"),
            "name": recommendation.get("symbol"),
            "state": state,
            "action": str(action or "").lower(),
            "score": recommendation.get("score"),
            "price": recommendation.get("reference_price"),
            "change_pct": recommendation.get("change_pct", 0),
            "reasons": [recommendation.get("rationale", "未提供理由。")],
            "counter_evidence": recommendation.get("counter_evidence", []),
            "risk": recommendation.get("risk", "未提供风险说明。"),
            "invalidation": recommendation.get("invalidation"),
            "data_state": recommendation.get("data_state", "missing"),
            "health": recommendation.get("health", "unavailable"),
            "updated_at": recommendation.get("updated_at", recommendation.get("occurred_at")),
        }
    if not isinstance(instrument, Mapping) or instrument.get("market") != "US" or instrument.get("instrument_type") != "stock":
        raise StockScreenerError("recommendation must describe a US stock")
    status = recommendation.get("status")
    if status not in {"official", "research"}:
        raise StockScreenerError("recommendation status is not screener eligible")
    action = recommendation.get("action")
    if action not in {"BUY", "HOLD", "REDUCE", "EXIT", "WAIT"}:
        raise StockScreenerError("recommendation action is invalid")
    evidence = recommendation.get("evidence") or {}
    risk = recommendation.get("risk") or {}
    provenance = recommendation.get("provenance") or {}
    if not isinstance(evidence, Mapping) or not isinstance(risk, Mapping) or not isinstance(provenance, Mapping):
        raise StockScreenerError("recommendation evidence, risk, and provenance must be objects")
    freshness = {"live": "fresh", "delayed": "delayed", "stale": "stale", "incomplete": "missing"}.get(
        risk.get("data_freshness", "missing"), "missing"
    )
    return {
        "symbol": instrument.get("symbol"),
        "name": instrument.get("symbol"),
        "state": status,
        "action": action.lower(),
        "score": recommendation.get("score"),
        "price": recommendation.get("reference_price", recommendation.get("price")),
        "change_pct": recommendation.get("change_pct", 0),
        "reasons": evidence.get("supporting", []),
        "counter_evidence": evidence.get("counter", []),
        "risk": risk.get("risk") or "未提供风险说明。",
        "invalidation": risk.get("invalidation"),
        "data_state": freshness,
        "health": recommendation.get("health", "healthy"),
        "updated_at": provenance.get("generated_at", recommendation.get("updated_at")),
    }


@dataclass(frozen=True)
class StockScreenerAdapter:
    """API-facing adapter; persistence remains owned by the caller."""

    has_capability: Callable[..., bool] | None = None

    def _check_access(self) -> None:
        allowed = False
        if self.has_capability is not None:
            try:
                allowed = bool(self.has_capability("strategy_all"))
            except Exception:
                allowed = False
        if not allowed:
            raise StockScreenerAccessError("当前会员未开放行动型美股选股器。")

    def screen(self, candidates: Iterable[Mapping[str, Any]], request: Mapping[str, Any] | None = None, *, now: datetime | None = None) -> dict[str, Any]:
        self._check_access()
        return screen_candidates(candidates, request, now=now)

    def save_preset(self, current: Mapping[str, Any] | None, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._check_access()
        return update_preset(current, payload)
