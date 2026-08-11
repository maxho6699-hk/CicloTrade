"""Signed M2M receiver for the official option simulation ledger.

This module has no broker client and deliberately is not registered by itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.official_option_sim_contracts import OfficialOptionSimulationError
from core.official_option_sim_journal import OfficialOptionSimulationJournal


_WORKER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_EPOCH = re.compile(r"^[1-9][0-9]{0,9}$")


class OfficialOptionSimulationReceiverError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class OfficialOptionSimulationReceiver:
    """Verifies a bounded worker receipt before it reaches the append-only journal."""

    def __init__(self, journal: OfficialOptionSimulationJournal, *, shared_secret: str | bytes, enabled: bool = False):
        raw = shared_secret.encode("utf-8") if isinstance(shared_secret, str) else shared_secret
        if not isinstance(journal, OfficialOptionSimulationJournal):
            raise TypeError("journal must be an OfficialOptionSimulationJournal")
        if not isinstance(raw, bytes) or len(raw) < 32:
            raise ValueError("official simulation shared secret must contain 32 bytes")
        self.journal = journal
        self._secret = raw
        self.enabled = bool(enabled)

    def _signature(self, raw: bytes) -> str:
        return "sha256=" + hmac.new(self._secret, raw, hashlib.sha256).hexdigest()

    def accept(self, raw: bytes, headers: MappingLike) -> dict[str, Any]:
        if not self.enabled:
            raise OfficialOptionSimulationReceiverError("官方模拟接收端尚未启用。", 404)
        supplied = str(headers.get("x-ciclotrade-simulation-signature", ""))
        if len(raw) > 128 * 1024 or not supplied or not hmac.compare_digest(self._signature(raw), supplied):
            raise OfficialOptionSimulationReceiverError("模拟 Worker 签名验证失败。", 401)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialOptionSimulationReceiverError("模拟收据 JSON 无效。", 400) from exc
        if not isinstance(payload, dict):
            raise OfficialOptionSimulationReceiverError("模拟收据必须为对象。", 400)
        worker = str(headers.get("x-ciclotrade-worker-id", "")).strip()
        epoch = str(headers.get("x-ciclotrade-fencing-epoch", "")).strip()
        key = str(headers.get("idempotency-key", "")).strip()
        if not _WORKER.fullmatch(worker) or not _EPOCH.fullmatch(epoch) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
            raise OfficialOptionSimulationReceiverError("模拟 Worker 认证头无效。", 401)
        if payload.get("worker_id") != worker or payload.get("fencing_epoch") != int(epoch):
            raise OfficialOptionSimulationReceiverError("模拟收据与 Worker 围栏不一致。", 409)
        try:
            return self.journal.record(payload, idempotency_key=key)
        except OfficialOptionSimulationError as exc:
            raise OfficialOptionSimulationReceiverError(str(exc), 409) from exc


class MappingLike:
    """Small structural protocol substitute that avoids a runtime typing dependency."""
    def get(self, key: str, default: Any = None) -> Any: ...


def _receiver(request: Request) -> OfficialOptionSimulationReceiver:
    value = getattr(request.app.state, "official_option_sim_receiver", None)
    if not isinstance(value, OfficialOptionSimulationReceiver):
        raise OfficialOptionSimulationReceiverError("官方模拟接收端尚未配置。", 404)
    return value


async def official_option_sim_receipt(request: Request) -> Response:
    raw = await request.body()
    receipt = await run_in_threadpool(_receiver(request).accept, raw, request.headers)
    return JSONResponse({
        "status": receipt["lifecycle_state"], "event_type": receipt["event_type"],
        "recorded": True, "account_mode": "official_simulation", "broker_execution": False,
    }, status_code=201, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def official_option_sim_receiver_error(_: Request, exc: OfficialOptionSimulationReceiverError) -> Response:
    return JSONResponse({"error": str(exc)}, status_code=exc.status, headers={"Cache-Control": "no-store"})


__all__ = ["OfficialOptionSimulationReceiver", "OfficialOptionSimulationReceiverError", "official_option_sim_receipt", "official_option_sim_receiver_error"]
