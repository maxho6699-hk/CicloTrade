"""Published-policy commerce, capability, and order-snapshot accessors."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from core.entitlement_commerce import plan_commerce_decision
from core.compat import UTC


def aware(value: Any, label: str) -> datetime:
    from core.compat import UTC

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label}必须是有效且包含时区的 ISO 8601 时间。") from exc
    else:
        raise ValueError(f"{label}必须是有效且包含时区的 ISO 8601 时间。")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label}必须包含时区。")
    return parsed.astimezone(UTC)


def commerce_decision(conn: Any, policy: Any, plan: str, action: str, *, as_of: datetime | None = None) -> dict[str, Any]:
    decision = plan_commerce_decision(policy.policy, plan, action, as_of=as_of)
    if decision["allowed"] and not _reviewed(conn, policy, plan, as_of=as_of):
        return {**decision, "allowed": False, "reason": "readiness_unreviewed"}
    return decision


def _reviewed(conn: Any, policy: Any, plan: str, *, as_of: datetime | None = None) -> bool:
    item = next((entry for entry in policy.policy["plans"] if entry["key"] == plan), None)
    readiness = item.get("readiness") if isinstance(item, dict) else None
    if not readiness:
        return False
    checked_at = (as_of or datetime.now(UTC)).astimezone(UTC).isoformat()
    return conn.execute(
        """SELECT 1
           FROM membership_entitlement_readiness_reviews AS review
           JOIN membership_entitlement_readiness_receipts AS receipt
             ON receipt.id=review.receipt_id
           WHERE review.evidence_ref=? AND review.policy_key=?
             AND review.policy_version=? AND review.policy_sha256=?
             AND datetime(receipt.valid_until) >= datetime(?)
           LIMIT 1""",
        (
            readiness.get("evidence_ref"), policy.policy_key, policy.version,
            policy.policy_sha256, checked_at,
        ),
    ).fetchone() is not None


def capabilities(policy: Any, plan: str, *, retired: frozenset[str]) -> set[str]:
    if plan in retired:
        item = next((entry for entry in policy.policy["plans"] if entry["key"] == plan), None)
        return set(item["compatibility_capabilities"]) if item else set()
    order = tuple(policy.policy["public_plan_order"])
    index = order.index(plan) if plan in order else 0
    matrix = {item["key"]: set(item["capabilities"]) for item in policy.policy["plans"]}
    return {capability for level in order[: index + 1] for capability in matrix[level]}


def can(conn: Any, plan: str, capability: str, *, as_of: datetime | None, current: Callable, retired: frozenset[str]) -> bool:
    from core.plans import CAPABILITY_ALIASES

    policy = current(conn, as_of=as_of)
    if policy is None:
        return False
    canonical = {**CAPABILITY_ALIASES, "option_auto": "option_auto_paper_official", "option_live_beta": "option_live_beta_apply"}.get(capability, capability)
    if plan not in retired and not _reviewed(conn, policy, plan, as_of=as_of):
        return False
    return canonical in capabilities(policy, plan, retired=retired)


def validate_snapshot(conn: Any, order: Mapping[str, Any], *, policy_key: str, parse_aware: Callable, load_published: Callable, load_current: Callable, error_type: type[ValueError]) -> Any:
    from core.entitlement_snapshot import validate_order_policy_snapshot

    return validate_order_policy_snapshot(conn, order, policy_key=policy_key, parse_aware=parse_aware, load_published=load_published, load_current=load_current, error_type=error_type)
