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


def policy_alert_limit(conn: Any, plan: str) -> int | None:
    """Return the published-policy alert capacity, failing closed at zero."""
    capabilities = verified_capabilities(conn, plan)
    if not capabilities:
        return 0
    if "alerts_unlimited" in capabilities:
        return None
    if "alerts_10" in capabilities:
        return 10
    return 1


def policy_market_data_delay(conn: Any, plan: str, instrument_type: str = "stock") -> int:
    """Resolve website data delay from reviewed capabilities only."""
    kind = str(instrument_type).strip().lower()
    if kind not in {"stock", "option"}:
        raise ValueError("instrument_type must be stock or option")
    capabilities = verified_capabilities(conn, plan)
    if kind == "stock":
        return 0 if "signal_web" in capabilities else 15
    return 0 if {"option_chain", "option_quote_chart"} <= capabilities else 15


def policy_recommendation_delay(conn: Any, plan: str, instrument_type: str = "stock") -> int:
    """Resolve website recommendation release delay from reviewed policy."""
    kind = str(instrument_type).strip().lower()
    if kind not in {"stock", "option"}:
        raise ValueError("instrument_type must be stock or option")
    capabilities = verified_capabilities(conn, plan)
    capability = "tg_stock_signal" if kind == "stock" else "tg_option_signal"
    return 0 if capability in capabilities else 60 if kind == "stock" else 15


def policy_trading_limits(conn: Any, plan: str) -> dict[str, Any]:
    """Project compatibility limits without granting broker or live rights."""
    capabilities = verified_capabilities(conn, plan)
    account_limit = policy_account_limit(conn, plan) if capabilities else 0
    return {
        "brokers": account_limit,
        "broker_accounts": account_limit,
        "auto_control_accounts": account_limit,
        "daily_orders": 100 if capabilities else 0,
        "single_notional": 500_000 if capabilities else 0,
        "daily_notional": 2_000_000 if capabilities else 0,
        "api_per_minute": 100 if capabilities else 0,
        "instruments": ("stock", "option") if "option_auto_live" in capabilities else ("stock",) if capabilities else (),
    }


__all__ = [
    "EntitlementConsumerUnavailable",
    "commerce_decision",
    "membership_purchase_state_from_policy",
    "policy_account_limit",
    "policy_alert_limit",
    "policy_market_data_delay",
    "policy_recommendation_delay",
    "policy_trading_limits",
    "require_commerce",
    "verified_can",
    "verified_capabilities",
]
