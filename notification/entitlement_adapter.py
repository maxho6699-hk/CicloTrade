"""Fail-closed published-policy access for Telegram surfaces."""

from __future__ import annotations

from typing import Any

from core.entitlement_policy import current_policy, policy_can


def public_commerce_plans(database: Any, action: str) -> tuple[dict[str, Any], ...]:
    """Return only currently published, customer-visible plans allowed for one action."""
    with database.transaction() as conn:
        policy = current_policy(conn)
        if policy is None:
            return ()
        return tuple(
            dict(item)
            for item in policy.policy["plans"]
            if item["lifecycle"] == "active_public"
            and item["commerce"].get("public_visible") is True
            and item["commerce"].get(action) is True
        )


def policy_allows(database: Any, account: dict[str, Any] | None, capability: str) -> bool:
    """Resolve an effective member capability against the current published policy only."""
    if not account:
        return False
    plan = str(account.get("plan_type") or "免费版")
    with database.transaction() as conn:
        return policy_can(conn, plan, capability)
