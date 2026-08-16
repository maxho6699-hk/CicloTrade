"""Shared validation and hashing primitives for the auto-live bounded context."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any, Mapping

from core.compat import UTC

MANDATE_STATES = frozenset({"draft", "pending_confirmation", "active", "paused", "blocked", "expired", "revoked"})
TERMINAL_STATES = frozenset({"expired", "revoked"})
SUPPORTED_BROKERS = frozenset({"futu_moomoo", "tiger", "ibkr", "webull", "longbridge"})
RUNTIME_FRESHNESS_SECONDS = 120
HEARTBEAT_FRESHNESS_SECONDS = 90
MAX_CLOCK_SKEW_SECONDS = 15
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class AutoLiveControlError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class AutoLiveConflict(AutoLiveControlError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 409)


def canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AutoLiveControlError("请求必须是有限、可序列化的 JSON 对象。") from exc


def sha256_json(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now(value: datetime | None) -> datetime:
    moment = value or datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise AutoLiveControlError("时间必须包含 UTC 时区。")
    return moment.astimezone(UTC)


def _iso(value: datetime | None = None) -> str:
    return _now(value).isoformat(timespec="seconds")


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AutoLiveControlError(f"{label}必须是包含时区的 ISO 8601 时间。")
    try:
        return _iso(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError as exc:
        raise AutoLiveControlError(f"{label}必须是有效的 ISO 8601 时间。") from exc


def _text(value: Any, label: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise AutoLiveControlError(f"{label}无效。")
    return value.strip()


def _opaque(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _row_public(row: Mapping[str, Any]) -> dict[str, Any]:
    # Do not expose user_id, broker_account_id, external account ids, or metadata.
    return {
        "public_id": str(row["public_id"]),
        "strategy_version": str(row["strategy_version"]),
        "risk_version": str(row["risk_version"]),
        "capital_limit_minor": int(row["capital_limit_minor"]),
        "frequency_limit": int(row["frequency_limit"]),
        "valid_from": str(row["valid_from"]),
        "valid_until": str(row["valid_until"]),
        "state": str(row["state"]),
        "can_reduce_exposure": True,
        "snapshot_sha256": str(row["snapshot_sha256"]),
        "confirmed_at": row["confirmed_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _gate(name: str, ok: bool, reason: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "reason": reason}
