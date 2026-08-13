# -*- coding: utf-8 -*-
"""Versioned public membership policy and dynamic-program boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from core.compat import UTC
from core.entitlement_commerce import (
    EntitlementCommerceError,
    readiness_proof,
    validate_policy_transition,
    validate_plan_records,
)
from core.entitlement_projection import (
    capability_contracts as _project_capability_contracts,
)
from core.entitlement_access import aware as _aware_datetime
from core.entitlement_projection import (
    runtime_capability_evidence as _runtime_capability_evidence,
)
from core.entitlement_policy_catalog import (
    OPTION_LIVE_BETA_STATES,
    PUBLIC_PLAN_CAPABILITY_ADDITIONS,
    PUBLIC_PLAN_CAPABILITY_REMOVALS,
    PUBLIC_PLAN_COPY,
    PUBLIC_PLAN_DISPLAY_NAMES,
    SEALED_LEGACY_CAPABILITIES,
)
from core.plans import CAPABILITIES, PLANS


POLICY_KEY = "public_membership_v1"
POLICY_SCHEMA_VERSION = 1
POLICY_V1_PUBLIC_PLAN_ORDER = ("免费版", "标准版", "高级版")
RETIRED_PLAN_KEYS = frozenset({"专业版", "定制版"})
POLICY_V1_PLAN_ORDER = (*POLICY_V1_PUBLIC_PLAN_ORDER, "专业版", "定制版")
POLICY_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "policy_key", "public_plan_order", "plans",
    "dynamic_programs", "always_available",
})
OPTION_LIVE_BETA_KEYS = frozenset({
    "application_capability", "eligible_plan", "states",
    "membership_grants_runtime", "telegram_binding_required",
    "per_strategy_confirmation_required", "multi_leg_atomic_confirmation",
    "defined_risk_only",
})


class EntitlementPolicyError(ValueError):
    """Raised when a policy cannot be validated or published."""


@dataclass(frozen=True)
class PublishedPolicy:
    policy_key: str
    version: int
    policy_sha256: str
    effective_at: str
    created_at: str
    policy: dict[str, Any]


def _iso(value: datetime | None = None) -> str:
    moment = value or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def runtime_capability_evidence(conn: Any) -> dict[str, dict[str, str]]:
    return _runtime_capability_evidence(conn, parse_aware=_aware_datetime)
def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EntitlementPolicyError("会员策略必须是可序列化的有限 JSON。") from exc
def policy_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def canonical_public_policy() -> dict[str, Any]:
    """Return the versioned three-tier public and sealed legacy contract.

    Live option execution is intentionally represented as a dynamic program,
    not a static subscription capability.
    """
    plans: list[dict[str, Any]] = []
    for key in POLICY_V1_PUBLIC_PLAN_ORDER:
        definition = PLANS[key]
        capabilities = (
            set(CAPABILITIES[key])
            - set(PUBLIC_PLAN_CAPABILITY_REMOVALS.get(key, ()))
        ) | set(PUBLIC_PLAN_CAPABILITY_ADDITIONS.get(key, ()))
        item = {
                "key": key,
                "display_name": PUBLIC_PLAN_DISPLAY_NAMES[key],
                "summary": PUBLIC_PLAN_COPY[key]["summary"],
                "prices": dict(definition["prices"]),
                "features": list(PUBLIC_PLAN_COPY[key]["features"]),
                "capabilities": sorted(capabilities),
                "compatibility_capabilities": [],
                "lifecycle": "active_public",
                "commerce": {
                    "public_visible": True,
                    "purchasable": key != "免费版",
                    "renewable": key != "免费版",
                    "admin_grantable": key != "免费版",
                    "upgrade_target": key != "免费版",
                },
            }
        item["readiness"] = readiness_proof(
            key, item["lifecycle"], item["capabilities"],
            evidence_ref="bootstrap-contract-review-20260814",
        )
        plans.append(item)
    for key in ("专业版", "定制版"):
        plans.append({
            "key": key,
            "display_name": PUBLIC_PLAN_DISPLAY_NAMES[key],
            "summary": "僅保留未到期歷史權益，不公開、不新售、不續費。",
            "prices": dict(PLANS[key]["prices"]),
            "features": ["歷史訂單與未到期權益兼容"],
            "capabilities": [],
            "compatibility_capabilities": list(SEALED_LEGACY_CAPABILITIES[key]),
            "lifecycle": "retired_legacy",
            "commerce": {key: False for key in (
                "public_visible", "purchasable", "renewable",
                "admin_grantable", "upgrade_target",
            )},
            "readiness": None,
        })
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_key": POLICY_KEY,
        "public_plan_order": list(POLICY_V1_PUBLIC_PLAN_ORDER),
        "plans": plans,
        "dynamic_programs": {
            "option_live_beta": {
                "application_capability": "option_live_beta_apply",
                "eligible_plan": "高级版",
                "states": list(OPTION_LIVE_BETA_STATES),
                "membership_grants_runtime": False,
                "telegram_binding_required": True,
                "per_strategy_confirmation_required": True,
                "multi_leg_atomic_confirmation": True,
                "defined_risk_only": True,
            }
        },
        "always_available": [
            "risk_disclosure",
            "data_freshness",
            "cancel_order",
            "reduce_position",
            "close_position",
            "billing_history",
            "help_and_support",
        ],
    }


def capability_contracts(
    plan: str,
    *,
    policy: PublishedPolicy | None = None,
    runtime_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
    maximum_age_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Project public capabilities with explicit customer-visible states."""
    source_plans = (
        policy.policy["plans"] if policy is not None
        else canonical_public_policy()["plans"]
    )
    included = policy_capabilities(policy, plan) if policy is not None else set()
    return _project_capability_contracts(
        plan,
        source_plans=source_plans,
        included_capabilities=included,
        runtime_evidence=runtime_evidence,
        now=now,
        maximum_age_seconds=maximum_age_seconds,
    )


def validate_policy(
    value: Mapping[str, Any], *, require_current_contract: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EntitlementPolicyError("会员策略必须是对象。")
    policy = json.loads(_canonical_json(value))
    if set(policy) != POLICY_TOP_LEVEL_KEYS:
        raise EntitlementPolicyError("会员策略顶层字段不完整或包含未知字段。")
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise EntitlementPolicyError("会员策略 schema_version 无效。")
    if policy.get("policy_key") != POLICY_KEY:
        raise EntitlementPolicyError("会员策略 policy_key 无效。")
    public_order = tuple(policy.get("public_plan_order") or ())
    if require_current_contract and public_order != POLICY_V1_PUBLIC_PLAN_ORDER:
        raise EntitlementPolicyError("当前公开会员必须严格为免费、标准、高级三级。")
    plans = policy.get("plans")
    try:
        validate_plan_records(
            plans,
            expected_keys=POLICY_V1_PLAN_ORDER,
            public_plan_order=public_order,
            parse_aware=_aware_datetime,
        )
    except EntitlementCommerceError as exc:
        raise EntitlementPolicyError(str(exc)) from exc
    dynamic_programs = policy.get("dynamic_programs")
    if not isinstance(dynamic_programs, dict) or set(dynamic_programs) != {"option_live_beta"}:
        raise EntitlementPolicyError("会员策略动态项目不完整或包含未知项目。")
    dynamic = dynamic_programs.get("option_live_beta", {})
    if not isinstance(dynamic, dict) or set(dynamic) != OPTION_LIVE_BETA_KEYS:
        raise EntitlementPolicyError("真实期权 Beta 字段不完整或包含未知字段。")
    if dynamic.get("application_capability") != "option_live_beta_apply":
        raise EntitlementPolicyError("真实期权 Beta 申请能力无效。")
    if dynamic.get("eligible_plan") != "高级版":
        raise EntitlementPolicyError("真实期权项目申请入口仅允许高级会员使用。")
    if dynamic.get("membership_grants_runtime") is not False:
        raise EntitlementPolicyError("会员身份不得直接授予真实期权运行权限。")
    if tuple(dynamic.get("states") or ()) != OPTION_LIVE_BETA_STATES:
        raise EntitlementPolicyError("真实期权 Beta 状态机无效。")
    required_true = (
        "telegram_binding_required",
        "per_strategy_confirmation_required",
        "multi_leg_atomic_confirmation",
        "defined_risk_only",
    )
    if any(dynamic.get(key) is not True for key in required_true):
        raise EntitlementPolicyError("真实期权 Beta 安全门不得关闭。")
    always_available = policy.get("always_available")
    if not isinstance(always_available, list) or len(always_available) != len(set(always_available)) or not all(
        isinstance(item, str) and item.strip() for item in always_available
    ):
        raise EntitlementPolicyError("永久开放能力无效。")
    if require_current_contract:
        canonical = canonical_public_policy()
        if plans != canonical["plans"]:
            raise EntitlementPolicyError("会员方案内容必须与已审查的公开合同完全一致。")
        if always_available != canonical["always_available"]:
            raise EntitlementPolicyError("永久开放能力不得修改。")
    forbidden = {
        "team_collaboration",
        "private_deploy",
        "multi_account",
        "auto_control_account_1",
        "auto_control_account_5",
        "liquidate_all",
        "option_auto_live",
        "real_trade",
        "stock_auto",
        "short_trading",
    }
    published = {
        capability
        for item in plans
        for capability in item.get("capabilities", [])
    }
    leaked = sorted(forbidden & published)
    if leaked:
        raise EntitlementPolicyError(f"公开会员含有禁止能力：{','.join(leaked)}")
    return policy


def publish_policy(
    conn: Any,
    value: Mapping[str, Any],
    *,
    effective_at: datetime,
    created_by: int | None = None,
    reviewer_id: int | None = None,
    readiness_evidence_ref: str | None = None,
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
) -> tuple[PublishedPolicy, bool]:
    """Append one policy version; identical content is idempotent."""
    from core.entitlement_publishing import publish

    return publish(
        conn, value, effective_at=effective_at, created_by=created_by,
        reviewer_id=reviewer_id, readiness_evidence_ref=readiness_evidence_ref,
        idempotency_key=idempotency_key, created_at=created_at,
        validate=validate_policy, iso=_iso, canonical_json=_canonical_json,
        error_type=EntitlementPolicyError, publish_locked=_publish_policy_locked,
        published=_published,
    )


def create_readiness_review(
    conn: Any, value: Mapping[str, Any], *, reviewer_id: int, evidence_ref: str,
    valid_until: datetime, idempotency_key: str,
) -> int:
    """Create a risk-auditor receipt for a candidate policy."""
    from core.entitlement_publishing import create_review

    return create_review(conn, value, reviewer_id=reviewer_id, evidence_ref=evidence_ref,
                         valid_until=valid_until, idempotency_key=idempotency_key,
                         validate=validate_policy, iso=_iso, canonical_json=_canonical_json,
                         error_type=EntitlementPolicyError)


def seed_canonical_policy(conn: Any, *, now: datetime | None = None) -> PublishedPolicy:
    """Install the reviewed bootstrap contract from trusted DB initialization.

    This is deliberately separate from customer order creation and from the
    administrator publisher. It only acts when no policy row exists.
    """
    moment = now or datetime.now(UTC)
    policy = validate_policy(canonical_public_policy())
    serialized = _canonical_json(policy)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    effective_text = _iso(moment)
    conn.execute(
        """INSERT OR IGNORE INTO membership_entitlement_policy_versions
           (policy_key,version,policy_json,policy_sha256,effective_at,created_by,created_at)
           VALUES (?,?,?,?,?,NULL,?)""",
        (POLICY_KEY, 1, serialized, digest, effective_text, effective_text),
    )
    row = conn.execute(
        """SELECT * FROM membership_entitlement_policy_versions
           WHERE policy_key=? AND version=1""",
        (POLICY_KEY,),
    ).fetchone()
    if row is None:
        raise EntitlementPolicyError("bootstrap v1 会员策略无法建立。")
    published = _published(row)
    if published.policy_sha256 != digest or published.policy != policy:
        raise EntitlementPolicyError("bootstrap v1 会员策略与审查合同不一致。")
    conn.execute(
        """INSERT OR IGNORE INTO membership_entitlement_readiness_reviews(
               evidence_ref,policy_key,policy_version,policy_sha256,reviewer_id,reviewed_at)
           VALUES (?,?,?,?,NULL,?)""",
        ("bootstrap-contract-review-20260814", POLICY_KEY, 1, digest, effective_text),
    )
    return published


def _publish_policy_locked(
    conn: Any,
    value: Mapping[str, Any],
    *,
    effective_at: datetime,
    created_by: int | None = None,
    created_at: datetime | None = None,
) -> tuple[PublishedPolicy, bool]:
    effective_at = _aware_datetime(effective_at, "会员策略生效时间")
    policy = validate_policy(value, require_current_contract=False)
    previous_row = conn.execute(
        """SELECT * FROM membership_entitlement_policy_versions
           WHERE policy_key=? ORDER BY version DESC LIMIT 1""",
        (POLICY_KEY,),
    ).fetchone()
    previous = _published(previous_row).policy if previous_row is not None else None
    try:
        validate_policy_transition(
            previous, policy, fixed_plan_order=POLICY_V1_PLAN_ORDER,
        )
    except EntitlementCommerceError as exc:
        raise EntitlementPolicyError(str(exc)) from exc
    serialized = _canonical_json(policy)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    existing = conn.execute(
        """SELECT * FROM membership_entitlement_policy_versions
           WHERE policy_key=? AND policy_sha256=? AND effective_at=?""",
        (POLICY_KEY, digest, _iso(effective_at)),
    ).fetchone()
    if existing is not None:
        return _published(existing), False
    row = conn.execute(
        """SELECT COALESCE(MAX(version),0) AS version,MAX(effective_at) AS effective_at
           FROM membership_entitlement_policy_versions WHERE policy_key=?""",
        (POLICY_KEY,),
    ).fetchone()
    version = int(row["version"] if row is not None else 0) + 1
    effective_text = _iso(effective_at)
    previous_effective = str(row["effective_at"] or "") if row is not None else ""
    if previous_effective and effective_text <= previous_effective:
        raise EntitlementPolicyError("新会员策略的生效时间必须晚于已发布版本。")
    reference_now = created_at or datetime.now(UTC)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=UTC)
    if effective_at.astimezone(UTC) < reference_now.astimezone(UTC):
        raise EntitlementPolicyError("会员策略不得回填为过去生效。")
    created_text = _iso(created_at)
    conn.execute(
        """INSERT INTO membership_entitlement_policy_versions
           (policy_key,version,policy_json,policy_sha256,effective_at,created_by,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            POLICY_KEY,
            version,
            serialized,
            digest,
            effective_text,
            int(created_by),
            created_text,
        ),
    )
    inserted = conn.execute(
        """SELECT * FROM membership_entitlement_policy_versions
           WHERE policy_key=? AND version=?""",
        (POLICY_KEY, version),
    ).fetchone()
    return _published(inserted), True




def current_policy(conn: Any, *, as_of: datetime | None = None) -> PublishedPolicy | None:
    if as_of is not None:
        as_of = _aware_datetime(as_of, "会员策略查询时间")
    row = conn.execute(
        """SELECT * FROM membership_entitlement_policy_versions
           WHERE policy_key=? AND datetime(effective_at)<=datetime(?)
           ORDER BY datetime(effective_at) DESC,version DESC LIMIT 1""",
        (POLICY_KEY, _iso(as_of)),
    ).fetchone()
    return _published(row) if row is not None else None


def current_plan_commerce_decision(
    conn: Any,
    plan: str,
    action: str,
    *,
    as_of: datetime | None = None,
) -> tuple[PublishedPolicy, dict[str, Any]]:
    """Return one decision and the exact policy that authorized it."""
    policy = current_policy(conn, as_of=as_of)
    if policy is None:
        raise EntitlementPolicyError("当前没有已生效且可验证的会员策略。")
    return policy, published_plan_commerce_decision(conn, policy, plan, action, as_of=as_of)


def published_plan_commerce_decision(
    conn: Any,
    policy: PublishedPolicy,
    plan: str,
    action: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    from core.entitlement_access import commerce_decision

    return commerce_decision(conn, policy, plan, action, as_of=as_of)


def policy_capabilities(policy: PublishedPolicy, plan: str) -> set[str]:
    """Resolve one tier from a verified published policy."""
    from core.entitlement_access import capabilities

    return capabilities(policy, plan, retired=RETIRED_PLAN_KEYS)


def policy_can(
    conn: Any, plan: str, capability: str, *, as_of: datetime | None = None
) -> bool:
    from core.entitlement_access import can

    return can(conn, plan, capability, as_of=as_of, current=current_policy, retired=RETIRED_PLAN_KEYS)


def _published(row: Any) -> PublishedPolicy:
    value = dict(row)
    policy = validate_policy(
        json.loads(str(value["policy_json"])), require_current_contract=False,
    )
    actual_sha256 = policy_sha256(policy)
    if actual_sha256 != str(value["policy_sha256"]):
        raise EntitlementPolicyError("会员策略内容与哈希不一致。")
    return PublishedPolicy(
        policy_key=str(value["policy_key"]),
        version=int(value["version"]),
        policy_sha256=str(value["policy_sha256"]),
        effective_at=str(value["effective_at"]),
        created_at=str(value["created_at"]),
        policy=policy,
    )


def validate_order_policy_snapshot(conn: Any, order: Mapping[str, Any]) -> PublishedPolicy:
    """Verify immutable policy proof before granting paid membership."""
    from core.entitlement_access import validate_snapshot

    return validate_snapshot(conn, order, policy_key=POLICY_KEY, parse_aware=_aware_datetime, load_published=_published, load_current=current_policy, error_type=EntitlementPolicyError)
