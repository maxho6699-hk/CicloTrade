"""Fail-closed consumer facade for the versioned entitlement policy.

UI and API consumers must not derive capabilities or commerce actions from the
legacy ``core.plans`` matrix.  This module keeps that migration boundary small
and deliberately has no persistence side effects.
"""

from __future__ import annotations

from datetime import datetime
import sqlite3
from typing import Any

from core.compat import UTC
from core.entitlement_policy import (
    EntitlementPolicyError,
    current_plan_commerce_decision,
    current_policy,
    policy_can,
    policy_capabilities,
)
from core.membership import membership_purchase_state


class EntitlementConsumerUnavailable(RuntimeError):
    """The published policy is missing, stale, or fails integrity checks."""


def verified_capabilities(
    conn: Any, plan: str, *, as_of: datetime | None = None,
) -> set[str]:
    """Return policy capabilities, or an empty set when proof is unavailable."""
    try:
        policy = current_policy(conn, as_of=as_of)
        if policy is None:
            return set()
        item = next((entry for entry in policy.policy["plans"] if entry.get("key") == plan), None)
        if item is None:
            return set()
        if item.get("lifecycle") != "retired_legacy":
            readiness = item.get("readiness") or {}
            checked_at = (as_of or datetime.now(UTC)).astimezone(UTC).isoformat()
            reviewed = conn.execute(
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
            ).fetchone()
            if not reviewed:
                return set()
        return policy_capabilities(policy, plan)
    except (EntitlementPolicyError, KeyError, TypeError, ValueError, sqlite3.Error):
        return set()


def verified_can(
    conn: Any, plan: str, capability: str, *, as_of: datetime | None = None,
) -> bool:
    """Check one capability against the current verified policy."""
    try:
        if not verified_capabilities(conn, plan, as_of=as_of):
            return False
        return bool(policy_can(conn, plan, capability, as_of=as_of))
    except (EntitlementPolicyError, KeyError, TypeError, ValueError, sqlite3.Error):
        return False


def require_commerce(
    conn: Any,
    plan: str,
    action: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return an allowed policy decision or fail closed for a write."""
    try:
        _, decision = current_plan_commerce_decision(conn, plan, action, as_of=as_of)
    except (EntitlementPolicyError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise EntitlementConsumerUnavailable("当前会员策略无法核验。") from exc
    if not decision.get("allowed"):
        raise PermissionError("当前会员策略不允许此商业动作。")
    return decision


def commerce_decision(
    conn: Any,
    plan: str,
    action: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return a public-safe denied decision when policy proof is unavailable."""
    try:
        _, decision = current_plan_commerce_decision(conn, plan, action, as_of=as_of)
        return dict(decision)
    except (EntitlementPolicyError, KeyError, TypeError, ValueError, sqlite3.Error):
        return {"allowed": False, "reason": "policy_unavailable", "lifecycle": None}


def membership_purchase_state_from_policy(
    conn: Any,
    current_plan: str,
    requested_plan: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Combine ledger coverage with the policy's purchase/renew/upgrade gate."""
    state = membership_purchase_state(current_plan, requested_plan)
    if not state["can_purchase"]:
        return state
    action = str(state["purchase_action"])
    decision = commerce_decision(conn, requested_plan, action, as_of=as_of)
    if decision.get("allowed"):
        return state
    reason = str(decision.get("reason") or "policy_unavailable")
    return {
        **state,
        "can_purchase": False,
        "purchase_action": "unavailable",
        "blocked_reason": "当前会员策略不允许此商业动作。" if reason != "policy_unavailable" else "当前会员策略无法核验。",
    }


def policy_account_limit(conn: Any, plan: str) -> int:
    """Expose only account capacities explicitly present in published policy."""
    if verified_can(conn, plan, "auto_control_account_5"):
        return 5
    if verified_can(conn, plan, "auto_control_account_1"):
        return 1
    return 0


__all__ = [
    "EntitlementConsumerUnavailable",
    "commerce_decision",
    "membership_purchase_state_from_policy",
    "policy_account_limit",
    "require_commerce",
    "verified_can",
    "verified_capabilities",
]
