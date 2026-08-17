"""Read-only Tiger order lookup for auto-live reconciliation.

This adapter intentionally exposes no send/cancel/replace method.  It converts a
broker order snapshot into the narrow observation contract consumed by
AutoLiveBrokerReconciler.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from core.auto_live_control_common import AutoLiveControlError, _iso, _now, sha256_json
from trading.tiger_api import TigerAPI


def _field(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _status_text(value: Any) -> str:
    nested = _field(value, "value")
    normalized = nested if nested is not None else value
    return str(normalized or "UNKNOWN").strip().upper() or "UNKNOWN"


def _submission_state(status: str, broker_order_id: str | None) -> str:
    if status in {"REJECTED", "EXPIRED", "FAILED"}:
        return "rejected"
    if status in {"CANCELLED", "CANCELED"}:
        return "cancelled" if broker_order_id else "submission_unknown"
    if status in {
        "NEW",
        "PENDING",
        "PENDING_NEW",
        "SUBMITTED",
        "HELD",
        "QUEUED",
        "PARTIALLY_FILLED",
        "PARTIAL_FILLED",
        "FILLED",
    }:
        return "accepted" if broker_order_id else "submission_unknown"
    return "submission_unknown"


class TigerOrderObservationSource:
    supported_providers = frozenset({"tiger"})

    def __init__(
        self,
        *,
        api_factory: Callable[[], TigerAPI] = TigerAPI,
        clock: Callable[[], datetime],
    ) -> None:
        self.api_factory = api_factory
        self.clock = clock

    def lookup(self, provider: str, broker_account_sha256: str, client_order_id: str) -> dict[str, Any] | None:
        if str(provider).strip().casefold() != "tiger":
            return None
        client_id = str(client_order_id).strip()
        if not client_id:
            raise AutoLiveControlError("Tiger reconciliation client_order_id 无效。")
        moment = self.clock()
        observed_at = _iso(_now(moment))
        orders = self.api_factory().orders(expected_account_sha256=broker_account_sha256) or []
        matches = []
        for order in orders:
            user_mark = _field(order, "user_mark", "client_order_id", "remark")
            if str(user_mark or "").strip() == client_id:
                matches.append(order)
        if len(matches) > 1:
            raise AutoLiveControlError("Tiger reconciliation 匹配到多个重复订单。")
        if not matches:
            return None
        order = matches[0]
        broker_order_id_value = _field(order, "id", "order_id", "broker_order_id")
        broker_order_id = str(broker_order_id_value).strip() if broker_order_id_value is not None else None
        if broker_order_id == "":
            broker_order_id = None
        broker_status = _status_text(_field(order, "status", "order_status"))
        state = _submission_state(broker_status, broker_order_id)
        evidence = {
            "provider": "tiger",
            "client_order_id": client_id,
            "broker_order_id": broker_order_id,
            "broker_status": broker_status,
            "submission_state": state,
            "broker_account_sha256": broker_account_sha256,
        }
        return {
            "provider": "tiger",
            "submission_state": state,
            "broker_order_id": broker_order_id,
            "broker_status": broker_status,
            "observed_at": observed_at,
            "evidence_sha256": sha256_json(evidence),
            "broker_account_sha256": broker_account_sha256,
        }


__all__ = ["TigerOrderObservationSource"]
