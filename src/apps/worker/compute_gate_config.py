"""Strict parsing helpers shared by the local Compute Gate configuration."""
from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Any


class ComputeGateError(ValueError):
    """Raised when a local Compute Gate request is unsafe or malformed."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ComputeGateError("request JSON contains duplicate keys")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ComputeGateError(f"non-finite JSON constant {value} is forbidden")


def absolute(value: str, label: str) -> Path:
    if "://" in value or not Path(value).is_absolute():
        raise ComputeGateError(f"{label} must be an absolute local path")
    return Path(value)


def clock_time(value: str, label: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ComputeGateError(f"{label} must be HH:MM") from exc
    if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
        raise ComputeGateError(f"{label} must be a minute-only local time")
    return parsed


def integer(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ComputeGateError(f"{label} must be an integer") from exc


def number(value: str, label: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ComputeGateError(f"{label} must be a number") from exc


def as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
