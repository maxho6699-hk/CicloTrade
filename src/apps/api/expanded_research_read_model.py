"""Authenticated, sanitized projection for 97-symbol shadow research."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Callable

from core.compat import UTC
from core.expanded_research_contracts import TIER_A, TIER_C, UNIVERSE_SHA256, UNIVERSE_VERSION

from core.expanded_research_store import ExpandedResearchStore


class ExpandedResearchReadModel:
    def __init__(self, store: ExpandedResearchStore, *, authorize: Callable[[Any], bool]) -> None:
        if not isinstance(store, ExpandedResearchStore):
            raise TypeError("store must be ExpandedResearchStore")
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        self.store = store
        self.authorize = authorize

    def status(self, identity: Any) -> dict[str, Any]:
        self._require(identity)
        rows = self.store.latest_by_symbol()
        latest = _latest_cycle(rows)
        last_received = _latest_received(rows)
        stale = any(_stale(str(row["received_at"])) for row in latest.values())
        covered = len(latest)
        return {
            "available": bool(latest),
            "state": "stale" if latest and stale else "healthy" if latest and covered == 97 else "degraded" if latest else "waiting",
            "authority": _authority(),
            "universe": _universe(),
            "last_heartbeat_at": None,
            "last_result_at": last_received,
            "expires_at": _expires(last_received),
            "coverage_count": covered,
            "no_data_count": 97 - covered,
            "spool": None,
        }

    @staticmethod
    def unavailable_status() -> dict[str, Any]:
        return {
            "available": False,
            "state": "waiting",
            "authority": _authority(),
            "universe": _universe(),
            "last_heartbeat_at": None,
            "last_result_at": None,
            "expires_at": None,
            "coverage_count": 0,
            "no_data_count": 97,
            "spool": None,
        }

    def latest(self, identity: Any) -> dict[str, Any]:
        self._require(identity)
        rows = self.store.latest_by_symbol()
        value = _latest_cycle(rows)
        return {
            "available": bool(value), "authority": _authority(),
            "validation_label": "97标的扩容研究，仅供影子研究参考，不构成交易信号。",
            "cycle": _cycle_payload(value),
        }

    @staticmethod
    def unavailable_latest() -> dict[str, Any]:
        return {
            "available": False,
            "authority": _authority(),
            "validation_label": "97标的扩容研究尚未启用，当前没有可展示的影子研究结果。",
            "cycle": None,
        }

    def history(self, identity: Any, limit: int = 20) -> dict[str, Any]:
        self._require(identity)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("expanded research history limit must be between 1 and 20")
        rows = self.store.history(100)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["dataset_end"], []).append(row)
        items = [_history_item(date_key, values) for date_key, values in list(grouped.items())[:limit]]
        return {
            "available": bool(items), "authority": _authority(), "limit": 20, "items": items,
        }

    @staticmethod
    def unavailable_history(limit: int = 20) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("expanded research history limit must be between 1 and 20")
        return {"available": False, "authority": _authority(), "limit": 20, "items": []}

    def _require(self, identity: Any) -> None:
        if identity is None or not self.authorize(identity):
            raise PermissionError("expanded research read requires authenticated access")


def _authority() -> dict[str, Any]:
    return {
        "publication_ceiling": "shadow",
        "projection_scope": "authenticated_research",
        "source_user_visible": False,
        "research_only": True,
        "actionable": False,
        "outbound": False,
        "execution": False,
        "official": False,
        "live": False,
    }


def _universe() -> dict[str, Any]:
    return {"key": "expanded_equity_research", "version": UNIVERSE_VERSION, "count": 97, "sha256": UNIVERSE_SHA256}


def _latest_cycle(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not rows:
        return {}
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["symbol"] not in selected:
            selected[row["symbol"]] = row
    return selected


def _latest_received(rows: list[dict[str, Any]]) -> str | None:
    return max((str(row["received_at"]) for row in rows), default=None)


def _stale(value: str | None) -> bool:
    if value is None:
        return False
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (datetime.now(UTC) - at.astimezone(UTC)).total_seconds() > 12 * 60 * 60
    except ValueError:
        return True


def _expires(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (at.astimezone(UTC) + timedelta(hours=12)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _cycle_payload(rows: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    dataset_end = max(row["dataset_end"] for row in rows.values())
    received = max(row["received_at"] for row in rows.values())
    symbols = _symbols(rows)
    return {
        "cycle_id": f"expanded-research-{dataset_end}", "evaluation_date": dataset_end,
        "evaluated_at": received, "strategy_key": "expanded-equity-research",
        "strategy_name": "97标的扩容研究", "strategy_version": UNIVERSE_VERSION,
        "summary": {"long_count": 0, "flat_count": 0, "wait_count": len(rows), "no_data_count": 97 - len(rows)},
        "symbols": symbols,
        "evidence": {"universe_sha256": UNIVERSE_SHA256, "source_snapshot_sha256": _aggregate_hash(row["source_sha256"] for row in rows.values()), "code_bundle_sha256": _aggregate_hash(_code_bundle(row) for row in rows.values()), "result_sha256": _aggregate_hash(rows[symbol]["evidence_sha256"] for symbol in (*TIER_A, *TIER_C) if symbol in rows)},
    }


def _symbols(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    updated = {symbol: row["received_at"] for symbol, row in rows.items()}
    for symbol in (*TIER_A, *TIER_C):
        if symbol in rows:
            result.append({"market": "US", "symbol": symbol, "tier": "A" if symbol in TIER_A else "C", "data_state": "stale" if _stale(updated[symbol]) else "fresh", "signal": "wait", "rationale": None, "updated_at": updated[symbol]})
        else:
            result.append({"market": "US", "symbol": symbol, "tier": "A" if symbol in TIER_A else "C", "data_state": "missing", "signal": "wait", "rationale": None, "updated_at": None})
    return result


def _history_item(dataset_end: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    received = max(row["received_at"] for row in rows)
    coverage = len({row["symbol"] for row in rows})
    states = {state: sum(row["projection_state"] == state for row in rows) for state in ("active", "invalidated", "expired", "superseded")}
    return {
        "cycle_id": f"expanded-research-{dataset_end}", "evaluation_date": dataset_end,
        "evaluated_at": received, "received_at": received, "coverage_count": coverage,
        "no_data_count": 97 - coverage, "long_count": 0, "flat_count": 0,
        "wait_count": coverage, "receipt_count": len(rows), "active_count": states["active"],
        "invalidated_count": states["invalidated"], "expired_count": states["expired"],
        "superseded_count": states["superseded"],
    }


def _code_bundle(row: dict[str, Any]) -> str:
    evidence = row["equity"][sorted(row["equity"])[0]]
    return str(evidence.get("code_bundle_sha256", row["evidence_sha256"]))


def _aggregate_hash(values: Any) -> str:
    ordered = [str(value) for value in values]
    return hashlib.sha256(json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
