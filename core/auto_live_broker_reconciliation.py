"""Read-only broker reconciliation for auto-live submission receipts.

The source protocol intentionally exposes only lookup.  This module cannot send,
retry, cancel, or replace an order.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Protocol

from core.auto_live_control import AutoLiveControlError, AutoLiveControlPlane


class BrokerReconciliationSource(Protocol):
    supported_providers: frozenset[str]

    def lookup(
        self, provider: str, broker_account_sha256: str, client_order_id: str
    ) -> Mapping[str, Any] | None: ...


class AutoLiveBrokerReconciler:
    def __init__(
        self,
        control: AutoLiveControlPlane,
        *,
        source: BrokerReconciliationSource,
        clock: Callable[[], datetime],
    ) -> None:
        self.control = control
        self.source = source
        self.clock = clock
        configured = getattr(source, "supported_providers", frozenset({"tiger"}))
        self.supported_providers = frozenset(str(item).strip().casefold() for item in configured)
        if not self.supported_providers:
            raise AutoLiveControlError("broker reconciliation source 未声明 provider。")

    def reconcile_once(
        self,
        mandate_public_id: str,
        client_order_id: str,
        *,
        expected_fencing_epoch: int,
    ) -> dict[str, Any]:
        moment = self.clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise AutoLiveControlError("broker reconciliation clock 必须带时区。")
        binding = self.control.broker_binding_for_order_intent(mandate_public_id, client_order_id)
        provider = binding["provider"]
        if provider not in self.supported_providers:
            raise AutoLiveControlError("broker reconciliation source 不支持该 provider。")
        observation = self.source.lookup(provider, binding["broker_account_sha256"], client_order_id)
        if observation is None:
            return {
                "status": "not_found",
                "mandate_public_id": mandate_public_id,
                "client_order_id": client_order_id,
            }
        if not isinstance(observation, Mapping):
            raise AutoLiveControlError("broker reconciliation source 返回无效观察。")
        return self.control.record_broker_order_receipt(
            mandate_public_id,
            client_order_id,
            observation,
            expected_fencing_epoch=expected_fencing_epoch,
            now=moment,
        )

    def reconcile_pending(self, *, limit: int = 100) -> dict[str, int | str]:
        pending = self.control.pending_broker_reconciliations(
            limit=limit, providers=tuple(sorted(self.supported_providers))
        )
        resolved = unresolved = failed = 0
        for item in pending:
            try:
                result = self.reconcile_once(
                    item["mandate_public_id"],
                    item["client_order_id"],
                    expected_fencing_epoch=item["expected_fencing_epoch"],
                )
            except Exception:
                failed += 1
                continue
            if result.get("status") == "not_found" or result.get("submission_state") == "submission_unknown":
                unresolved += 1
            else:
                resolved += 1
        return {
            "status": "completed",
            "total": len(pending),
            "resolved": resolved,
            "unresolved": unresolved,
            "failed": failed,
        }


__all__ = ["AutoLiveBrokerReconciler", "BrokerReconciliationSource"]
