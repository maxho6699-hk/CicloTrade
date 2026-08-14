"""Contracts for the isolated 97-symbol US research delivery chain.

This module deliberately does not import the legacy 13-symbol strategy chain.
The result contract is evidence-only: it can be displayed to an authenticated
research surface, but it cannot become a recommendation, order, Telegram, or
live state.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import hmac
import json
import re
from typing import Any, Mapping

from core.compat import UTC
from core.expanded_research_universe_data import UNIVERSE_DATA


RESULT_KIND = "tradeai.expanded-local-research.v1"
INVALIDATION_KIND = "tradeai.expanded-local-research-invalidation.v1"
RECEIVER_ENDPOINT = "expanded-equity-research-result"
UNIVERSE_VERSION = "us-liquid-research-2026-08-13-v1"
MAX_INVALIDATION_FUTURE_SKEW = timedelta(minutes=5)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

AUTHORITY = {
    "research_only": True,
    "shadow": True,
    "outbound": False,
    "user_visible": False,
    "execution_eligible": False,
    "official": False,
    "live": False,
}

TIER_A = tuple(item["symbol"] for item in UNIVERSE_DATA["tier_a"])
TIER_C = tuple(item["symbol"] for item in UNIVERSE_DATA["tier_c"])


class ExpandedResearchError(ValueError):
    """Raised when a 97-symbol result violates its sealed contract."""


class ExpandedResearchConflict(ExpandedResearchError):
    pass


class ExpandedResearchStaleFence(ExpandedResearchError):
    pass


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExpandedResearchError("research payload must be canonical finite JSON") from exc


def sha256_bytes(body: bytes) -> str:
    if not isinstance(body, bytes):
        raise ExpandedResearchError("research body must be bytes")
    return hashlib.sha256(body).hexdigest()


def universe_payload() -> dict[str, Any]:
    return {
        "contract": "tradeai-equity-research-universe-v1",
        "version": UNIVERSE_DATA["version"],
        "as_of": UNIVERSE_DATA["as_of"],
        "tier_a": sorted(UNIVERSE_DATA["tier_a"], key=lambda item: (item["symbol"], item["industry"], item["asset_kind"])),
        "tier_c": sorted(UNIVERSE_DATA["tier_c"], key=lambda item: (item["symbol"], item["industry"], item["asset_kind"])),
        "authority": dict(AUTHORITY),
    }


UNIVERSE_SHA256 = sha256_bytes(canonical_json(universe_payload()))
CANONICAL_SYMBOLS = frozenset((*TIER_A, *TIER_C))


def validate_result(value: Any) -> dict[str, Any]:
    item = _object(value, "expanded research result")
    expected = {"schema_version", "kind", "result_id", "symbol", "tier", "source_sha256", "universe_sha256", "dataset_end", "equity", "option_proxy", "authority"}
    if set(item) != expected:
        raise ExpandedResearchError("expanded research result fields are invalid")
    if item["schema_version"] != 1 or item["kind"] != RESULT_KIND:
        raise ExpandedResearchError("expanded research result kind is invalid")
    result_id = _safe_id(item["result_id"], "result_id")
    symbol = _symbol(item["symbol"])
    tier = item["tier"]
    if tier not in {"A", "C"} or symbol not in CANONICAL_SYMBOLS:
        raise ExpandedResearchError("expanded research symbol or tier is invalid")
    if (tier == "A") != (symbol in TIER_A):
        raise ExpandedResearchError("expanded research tier does not match the sealed universe")
    if item["universe_sha256"] != UNIVERSE_SHA256:
        raise ExpandedResearchError("expanded research universe hash is invalid")
    source_sha = _hash(item["source_sha256"], "source_sha256")
    try:
        dataset_end = date.fromisoformat(str(item["dataset_end"]))
    except (TypeError, ValueError) as exc:
        raise ExpandedResearchError("dataset_end must be an ISO date") from exc
    if dataset_end > datetime.now(UTC).date():
        raise ExpandedResearchError("dataset_end cannot be in the future")
    if item["authority"] != AUTHORITY:
        raise ExpandedResearchError("expanded research authority is not shadow-only")
    equity = item["equity"]
    template_keys = {"equity.trend.long_flat.v1", "equity.mean_reversion.long_flat.v1", "equity.breakout.long_flat.v1"}
    if not isinstance(equity, Mapping) or set(equity) != template_keys:
        raise ExpandedResearchError("expanded research must contain the three fixed equity templates")
    normalized_equity: dict[str, dict[str, Any]] = {}
    for key in sorted(template_keys):
        evidence = _object(equity[key], f"equity.{key}")
        if evidence.get("runner") != "equity-research-v1":
            raise ExpandedResearchError("equity evidence runner is invalid")
        if len(canonical_json(evidence)) > 256 * 1024:
            raise ExpandedResearchError("equity evidence is too large")
        normalized_equity[key] = evidence
    option_proxy = item["option_proxy"]
    if tier == "C" and option_proxy is not None:
        raise ExpandedResearchError("Tier C cannot contain option proxy evidence")
    if tier == "A":
        if not isinstance(option_proxy, Mapping) or option_proxy.get("decision") != "WAIT" or option_proxy.get("actionable") is not False:
            raise ExpandedResearchError("Tier A option proxy must be WAIT-only and non-actionable")
        if any(key in option_proxy for key in ("contract", "strike", "expiry", "premium", "bid", "ask", "iv", "oi", "greeks")):
            raise ExpandedResearchError("option proxy must not claim contract-level data")
    normalized = {
        "schema_version": 1, "kind": RESULT_KIND, "result_id": result_id, "symbol": symbol, "tier": tier,
        "source_sha256": source_sha, "universe_sha256": UNIVERSE_SHA256, "dataset_end": dataset_end.isoformat(),
        "equity": normalized_equity, "option_proxy": dict(option_proxy) if isinstance(option_proxy, Mapping) else None,
        "authority": dict(AUTHORITY),
    }
    canonical_json(normalized)
    return normalized


def validate_invalidation(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    item = _object(value, "expanded research invalidation")
    expected = {
        "schema_version", "kind", "invalidation_id", "target_result_id", "symbol",
        "reason", "universe_sha256", "invalidated_at", "authority",
    }
    if set(item) != expected:
        raise ExpandedResearchError("expanded research invalidation fields are invalid")
    if item["schema_version"] != 1 or item["kind"] != INVALIDATION_KIND:
        raise ExpandedResearchError("expanded research invalidation kind is invalid")
    invalidation_id = _safe_id(item["invalidation_id"], "invalidation_id")
    target_result_id = _safe_id(item["target_result_id"], "target_result_id")
    symbol = _symbol(item["symbol"])
    if symbol not in CANONICAL_SYMBOLS:
        raise ExpandedResearchError("expanded research invalidation symbol is invalid")
    reason = item["reason"]
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 240:
        raise ExpandedResearchError("expanded research invalidation reason is invalid")
    if item["universe_sha256"] != UNIVERSE_SHA256:
        raise ExpandedResearchError("expanded research invalidation universe hash is invalid")
    invalidated_time = parse_timestamp(item["invalidated_at"], "invalidated_at")
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ExpandedResearchError("invalidation validation clock must include a timezone")
    if invalidated_time > reference.astimezone(UTC) + MAX_INVALIDATION_FUTURE_SKEW:
        raise ExpandedResearchError("expanded research invalidated_at is too far in the future")
    invalidated_at = stamp(invalidated_time)
    if item["authority"] != AUTHORITY:
        raise ExpandedResearchError("expanded research invalidation authority is not shadow-only")
    normalized = {
        "schema_version": 1, "kind": INVALIDATION_KIND, "invalidation_id": invalidation_id,
        "target_result_id": target_result_id, "symbol": symbol, "reason": reason.strip(),
        "universe_sha256": UNIVERSE_SHA256, "invalidated_at": invalidated_at,
        "authority": dict(AUTHORITY),
    }
    canonical_json(normalized)
    return normalized


def receiver_signature(secret: str | bytes, *, worker_id: str, fencing_epoch: int, idempotency_key: str, sent_at: str, body_sha256: str) -> str:
    raw_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not isinstance(raw_secret, bytes) or len(raw_secret) < 32:
        raise ExpandedResearchError("expanded research secret must contain at least 32 bytes")
    message = "\n".join(("expanded-research-signature-v1", RECEIVER_ENDPOINT, _worker(worker_id), str(_epoch(fencing_epoch)), _safe_id(idempotency_key, "idempotency_key"), stamp(parse_timestamp(sent_at, "sent_at")), _hash(body_sha256, "body_sha256"))).encode("utf-8")
    return "sha256=" + hmac.new(raw_secret, message, hashlib.sha256).hexdigest()


def parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ExpandedResearchError(f"{label} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExpandedResearchError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExpandedResearchError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExpandedResearchError("timestamp must include a timezone")
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExpandedResearchError(f"{label} must be an object")
    return dict(value)


def _symbol(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", value):
        raise ExpandedResearchError("symbol is invalid")
    return value


def _worker(value: Any) -> str:
    if not isinstance(value, str) or not WORKER_ID.fullmatch(value):
        raise ExpandedResearchError("worker_id is invalid")
    return value


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ExpandedResearchError(f"{label} is invalid")
    return value


def _epoch(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2_147_483_647:
        raise ExpandedResearchError("fencing_epoch is invalid")
    return value


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ExpandedResearchError(f"{label} must be a lowercase SHA-256")
    return value
