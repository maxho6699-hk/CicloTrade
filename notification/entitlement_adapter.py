"""Fail-closed published-policy access for Telegram surfaces."""

from __future__ import annotations

from typing import Any

from core.entitlement_policy import current_plan_commerce_decision, current_policy, policy_can


def _reviewed_policy(conn: Any):
    """Return a currently effective policy only while its bound review remains valid."""
    policy = current_policy(conn)
    if policy is None:
        return None
    row = conn.execute(
        """SELECT 1 FROM membership_entitlement_readiness_reviews review
           JOIN membership_entitlement_readiness_receipts receipt ON receipt.id=review.receipt_id
           WHERE review.policy_key=? AND review.policy_version=? AND review.policy_sha256=?
             AND receipt.candidate_sha256=? AND datetime(receipt.valid_until)>datetime('now')
           LIMIT 1""",
        (policy.policy_key, policy.version, policy.policy_sha256, policy.policy_sha256),
    ).fetchone()
    return policy if row else None


def commerce_plan_allowed(conn: Any, plan: str, action: str) -> bool:
    """Make a commercial decision only from a reviewed, unexpired published policy."""
    if _reviewed_policy(conn) is None:
        return False
    action = {
        "purchasable": "purchase",
        "renewable": "renew",
        "admin_grantable": "admin_grant",
        "upgrade_target": "upgrade",
    }.get(action, action)
    try:
        _, decision = current_plan_commerce_decision(conn, plan, action)
    except (ValueError, TypeError):
        return False
    return decision.get("allowed") is True


def public_commerce_plans(database: Any, action: str) -> tuple[dict[str, Any], ...]:
    """Return only currently published, customer-visible plans allowed for one action."""
    with database.transaction() as conn:
        policy = _reviewed_policy(conn)
        if policy is None:
            return ()
        return tuple(
            dict(item)
            for item in policy.policy["plans"]
            if item["lifecycle"] == "active_public" and commerce_plan_allowed(conn, str(item["key"]), action)
        )


def policy_allows(database: Any, account: dict[str, Any] | None, capability: str) -> bool:
    """Resolve an effective member capability against the current published policy only."""
    if not account:
        return False
    plan = str(account.get("plan_type") or "免费版")
    with database.transaction() as conn:
        return _reviewed_policy(conn) is not None and policy_can(conn, plan, capability)
