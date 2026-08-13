"""Versioned membership lifecycle and commerce decisions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Mapping

from core.compat import UTC


LIFECYCLE_STATES = (
    "active_public",
    "hidden_beta",
    "sales_paused",
    "retired_legacy",
)
COMMERCE_ACTIONS = {
    "purchase": "purchasable",
    "renew": "renewable",
    "admin_grant": "admin_grantable",
    "upgrade": "upgrade_target",
}
COMMERCE_KEYS = frozenset({"public_visible", *COMMERCE_ACTIONS.values()})
READINESS_KEYS = frozenset({
    "capability_set_sha256",
    "evidence_ref",
})
PLAN_KEYS = frozenset({
    "key",
    "display_name",
    "summary",
    "prices",
    "features",
    "capabilities",
    "compatibility_capabilities",
    "lifecycle",
    "commerce",
    "readiness",
})


class EntitlementCommerceError(ValueError):
    """Raised when lifecycle or commerce evidence is invalid."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def capability_set_sha256(capabilities: list[str] | tuple[str, ...]) -> str:
    return hashlib.sha256(
        _canonical_json(sorted(set(capabilities))).encode("utf-8")
    ).hexdigest()


def readiness_proof(
    plan: str,
    lifecycle: str,
    capabilities: list[str] | tuple[str, ...],
    *,
    evidence_ref: str,
) -> dict[str, str]:
    del plan, lifecycle
    return {
        "capability_set_sha256": capability_set_sha256(capabilities),
        "evidence_ref": str(evidence_ref),
    }


def validate_plan_records(
    plans: Any,
    *,
    expected_keys: tuple[str, ...],
    public_plan_order: tuple[str, ...],
    parse_aware: Callable[[Any, str], datetime],
) -> None:
    if not isinstance(plans, list) or not all(isinstance(item, dict) for item in plans):
        raise EntitlementCommerceError("会员方案必须是对象数组。")
    if tuple(item.get("key") for item in plans) != expected_keys:
        raise EntitlementCommerceError("会员方案版本记录不完整或顺序错误。")
    for item in plans:
        _validate_plan(item, public_plan_order=public_plan_order, parse_aware=parse_aware)


def validate_policy_transition(
    previous: Mapping[str, Any] | None,
    proposed: Mapping[str, Any],
    *,
    fixed_plan_order: tuple[str, ...],
) -> None:
    """Allow ordered public expansion while preventing implicit retire/reorder."""
    public = tuple(str(value) for value in proposed.get("public_plan_order") or ())
    if not public or public != fixed_plan_order[: len(public)]:
        raise EntitlementCommerceError("公开会员顺序必须是固定五档顺序的前缀。")
    for item in proposed.get("plans", []):
        if item.get("lifecycle") == "active_public" and item["key"] not in public:
            raise EntitlementCommerceError("active_public 方案必须进入公开顺序。")
    if previous is None:
        return
    old_public = tuple(str(value) for value in previous.get("public_plan_order") or ())
    if public[: len(old_public)] != old_public:
        raise EntitlementCommerceError("新策略不得删除或重排既有公开会员。")


def _validate_plan(
    item: dict[str, Any],
    *,
    public_plan_order: tuple[str, ...],
    parse_aware: Callable[[Any, str], datetime],
) -> None:
    if set(item) != PLAN_KEYS:
        raise EntitlementCommerceError("会员方案字段不完整或包含未知字段。")
    plan = str(item.get("key") or "")
    lifecycle = item.get("lifecycle")
    if lifecycle not in LIFECYCLE_STATES:
        raise EntitlementCommerceError("会员方案 lifecycle 无效。")
    if not isinstance(item["display_name"], str) or not item["display_name"].strip():
        raise EntitlementCommerceError("会员方案显示名称无效。")
    if not isinstance(item["summary"], str) or not item["summary"].strip():
        raise EntitlementCommerceError("会员方案摘要无效。")
    if not isinstance(item["prices"], dict) or not all(
        isinstance(key, str)
        and isinstance(amount, (int, float))
        and not isinstance(amount, bool)
        and amount >= 0
        for key, amount in item["prices"].items()
    ):
        raise EntitlementCommerceError("会员方案价格无效。")
    for field in ("features", "capabilities", "compatibility_capabilities"):
        values = item[field]
        if not isinstance(values, list) or len(values) != len(set(values)) or not all(
            isinstance(value, str) and value.strip() for value in values
        ):
            raise EntitlementCommerceError(f"会员方案 {field} 无效。")
    commerce = item.get("commerce")
    if not isinstance(commerce, dict) or set(commerce) != COMMERCE_KEYS or not all(
        isinstance(value, bool) for value in commerce.values()
    ):
        raise EntitlementCommerceError("会员方案商业动作无效。")
    public = plan in public_plan_order
    if commerce["public_visible"] != (lifecycle == "active_public" and public):
        raise EntitlementCommerceError("会员方案公开状态与 lifecycle 不一致。")
    if lifecycle in {"sales_paused", "retired_legacy"} and any(commerce.values()):
        raise EntitlementCommerceError("暂停或退休方案不得开放商业动作。")
    if public and item["compatibility_capabilities"]:
        raise EntitlementCommerceError("公开方案不得使用历史兼容能力集合。")
    if not public and item["capabilities"]:
        raise EntitlementCommerceError("历史方案只能使用封存兼容能力集合。")
    readiness = item.get("readiness")
    if any(commerce.values()):
        _validate_readiness(item, readiness)
    elif readiness is not None:
        _validate_readiness(item, readiness)


def _validate_readiness(
    item: Mapping[str, Any],
    readiness: Any,
) -> None:
    if not isinstance(readiness, dict) or set(readiness) != READINESS_KEYS:
        raise EntitlementCommerceError("开放商业动作必须提供完整 readiness proof。")
    capabilities = item["capabilities"] or item["compatibility_capabilities"]
    digest = capability_set_sha256(capabilities)
    if readiness["capability_set_sha256"] != digest:
        raise EntitlementCommerceError("readiness capability proof 已被篡改。")
    if not isinstance(readiness["evidence_ref"], str) or not readiness["evidence_ref"].strip():
        raise EntitlementCommerceError("readiness evidence reference 无效。")


def plan_commerce_decision(
    policy: Mapping[str, Any],
    plan: str,
    action: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    control = COMMERCE_ACTIONS.get(action)
    if control is None:
        raise EntitlementCommerceError("未知会员商业动作。")
    item = next((entry for entry in policy.get("plans", []) if entry.get("key") == plan), None)
    if not isinstance(item, dict):
        return {"allowed": False, "reason": "unknown_plan", "lifecycle": None}
    lifecycle = str(item.get("lifecycle") or "")
    commerce = item.get("commerce")
    if lifecycle in {"sales_paused", "retired_legacy"}:
        return {"allowed": False, "reason": lifecycle, "lifecycle": lifecycle}
    if lifecycle == "hidden_beta" and action != "admin_grant":
        return {"allowed": False, "reason": "hidden_beta", "lifecycle": lifecycle}
    if not isinstance(commerce, Mapping) or commerce.get(control) is not True:
        return {"allowed": False, "reason": f"{control}_disabled", "lifecycle": lifecycle}
    readiness = item.get("readiness")
    try:
        _validate_readiness(item, readiness)
    except (EntitlementCommerceError, ValueError, TypeError, KeyError):
        return {"allowed": False, "reason": "readiness_invalid", "lifecycle": lifecycle}
    return {"allowed": True, "reason": "allowed", "lifecycle": lifecycle}


def _parse_aware(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EntitlementCommerceError(f"{label} 无效。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EntitlementCommerceError(f"{label} 无效。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EntitlementCommerceError(f"{label} 必须包含时区。")
    return parsed.astimezone(UTC)
