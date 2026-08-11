# -*- coding: utf-8 -*-
"""Fail-closed execution authorization checks for broker account rows."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from typing import Any

from core.compat import UTC


DEFAULT_AUTHORIZATION_TTL_SECONDS = 15 * 60
MIN_AUTHORIZATION_TTL_SECONDS = 60
MAX_AUTHORIZATION_TTL_SECONDS = 60 * 60
AUTHORIZATION_TTL_ENV = "TRADEAI_BROKER_AUTHORIZATION_TTL_SECONDS"


def _value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def _authorization_ttl_seconds() -> int | None:
    raw_value = os.getenv(AUTHORIZATION_TTL_ENV)
    if raw_value is None:
        return DEFAULT_AUTHORIZATION_TTL_SECONDS
    try:
        configured = int(raw_value)
    except (TypeError, ValueError):
        return None
    if not MIN_AUTHORIZATION_TTL_SECONDS <= configured <= MAX_AUTHORIZATION_TTL_SECONDS:
        return None
    return configured


def broker_execution_authorized(broker_row: Any, expected_account_id: str | None = None) -> bool:
    """Return whether one row proves current execution authority for shared Tiger live trading."""
    if broker_row is None:
        return False
    try:
        active = int(_value(broker_row, "is_active")) == 1
    except (TypeError, ValueError):
        return False
    if not active:
        return False
    if str(_value(broker_row, "provider") or "").strip().casefold() != "tiger":
        return False
    if str(_value(broker_row, "mode") or "").strip().casefold() != "live":
        return False
    if str(_value(broker_row, "status") or "").strip().casefold() != "authorized":
        return False

    configured_account_id = os.getenv("TIGER_ACCOUNT", "")
    resolved_account_id = configured_account_id if expected_account_id is None else str(expected_account_id)
    if (
        not configured_account_id
        or not resolved_account_id
        or resolved_account_id != configured_account_id
        or str(_value(broker_row, "external_account_id") or "") != resolved_account_id
    ):
        return False

    metadata_raw = _value(broker_row, "metadata_json")
    try:
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict) or metadata.get("execution_authorized") is not True:
        return False

    verified_raw = metadata.get("authorization_verified_at")
    if not isinstance(verified_raw, str) or not verified_raw.strip():
        return False
    try:
        normalized = verified_raw.strip().replace("Z", "+00:00")
        verified_at = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if verified_at.tzinfo is None or verified_at.utcoffset() != timedelta(0):
        return False
    verified_at = verified_at.astimezone(UTC)
    now = datetime.now(UTC)
    if verified_at > now:
        return False
    ttl_seconds = _authorization_ttl_seconds()
    if ttl_seconds is None:
        return False
    return now - verified_at <= timedelta(seconds=ttl_seconds)
