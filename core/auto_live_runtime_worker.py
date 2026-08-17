"""Shadow-only auto-live worker.

This worker proves the runtime, lease, heartbeat, strategy-intent and audit path
without holding a broker capability or sending an order.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, Protocol

from core.auto_live_control import AutoLiveControlError, AutoLiveControlPlane


class ShadowIntentSource(Protocol):
    def due(
        self,
        mandate_public_id: str,
        fencing_epoch: int,
        as_of: datetime,
    ) -> list[Mapping[str, Any]]: ...


class AutoLiveShadowWorker:
    def __init__(
        self,
        control: AutoLiveControlPlane,
        *,
        source: ShadowIntentSource,
        worker_id: str,
        clock: Callable[[], datetime],
        lease_seconds: int = 30,
    ) -> None:
        self.control = control
        self.source = source
        self.worker_id = str(worker_id).strip()
        self.clock = clock
        self.lease_seconds = lease_seconds
        if not self.worker_id:
            raise AutoLiveControlError("shadow worker id 不能为空。")

    def run_once(
        self,
        mandate_public_id: str,
        *,
        lease_token: str,
        fencing_epoch: int,
    ) -> dict[str, int | str]:
        moment = self.clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise AutoLiveControlError("shadow worker clock 必须带时区。")
        self.control.renew_runtime_heartbeat(
            mandate_public_id,
            worker_id=self.worker_id,
            lease_token=lease_token,
            expected_fencing_epoch=fencing_epoch,
            lease_seconds=self.lease_seconds,
            now=moment,
        )
        due = self.source.due(mandate_public_id, fencing_epoch, moment)
        if not isinstance(due, list):
            raise AutoLiveControlError("shadow intent source 必须返回列表。")
        if not due:
            return {"status": "no_data", "intents": 0, "reused": 0}
        if not all(isinstance(item, Mapping) for item in due):
            raise AutoLiveControlError("shadow intent source 返回无效项目。")
        return self.control.append_shadow_intents(
            mandate_public_id,
            list(due),
            worker_id=self.worker_id,
            lease_token=lease_token,
            expected_fencing_epoch=fencing_epoch,
            now=moment,
        )


__all__ = ["AutoLiveShadowWorker", "ShadowIntentSource"]
