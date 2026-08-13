# -*- coding: utf-8 -*-
"""Versioned public membership policy and dynamic-program boundaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from core.compat import UTC
from core.plans import CAPABILITIES, PLANS


POLICY_KEY = "public_membership_v1"
POLICY_SCHEMA_VERSION = 1
POLICY_V1_PUBLIC_PLAN_ORDER = ("免费版", "标准版", "高级版", "专业版")
PUBLIC_PLAN_DISPLAY_NAMES = {
    "免费版": "免費會員",
    "标准版": "標準會員",
    "高级版": "高級會員",
    "专业版": "專業會員",
}
PUBLIC_PLAN_CAPABILITY_REMOVALS = {
    "高级版": frozenset({"auto_control_account_1"}),
    "专业版": frozenset({
        "multi_account", "team_collaboration", "option_auto_live",
        "auto_control_account_5",
    }),
}
PUBLIC_PLAN_CAPABILITY_ADDITIONS = {
    "专业版": frozenset({
        "strategy_template_save", "broker_access_apply", "option_live_beta_apply",
    }),
}
OPTION_LIVE_BETA_STATES = (
    "planned",
    "beta_eligible",
    "approved",
    "runtime_ready",
    "paused",
    "revoked",
)
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


@dataclass(frozen=True)
class CapabilityRuntimeEvidence:
    data_state: str
    health: str
    verified_at: str


def _iso(value: datetime | None = None) -> str:
    moment = value or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _aware_datetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise EntitlementPolicyError(f"{label}必须是有效且包含时区的 ISO 8601 时间。") from exc
    else:
        raise EntitlementPolicyError(f"{label}必须是有效且包含时区的 ISO 8601 时间。")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EntitlementPolicyError(f"{label}必须包含时区。")
    return parsed.astimezone(UTC)


def _table_exists(conn: Any, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def runtime_capability_evidence(conn: Any) -> dict[str, dict[str, str]]:
    """Read only successful, persisted observations; never probe providers."""
    evidence: dict[str, dict[str, str]] = {}

    def publish(capabilities: tuple[str, ...], observed_at: Any) -> None:
        try:
            verified_at = _aware_datetime(observed_at, "运行证据时间").isoformat(timespec="seconds")
        except EntitlementPolicyError:
            return
        item = {"data_state": "ready", "health": "healthy", "verified_at": verified_at}
        for capability in capabilities:
            evidence[capability] = dict(item)

    if _table_exists(conn, "official_option_sim_event_legs"):
        row = conn.execute(
            "SELECT quote_at FROM official_option_sim_event_legs ORDER BY datetime(quote_at) DESC,id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            publish(
                ("option_chain", "option_quote_chart", "option_greeks", "option_iv", "tg_option_signal"),
                row["quote_at"],
            )
    if _table_exists(conn, "earnings_data_snapshots"):
        row = conn.execute(
            """SELECT observed_at FROM earnings_data_snapshots
               WHERE dq_status='PASS' ORDER BY datetime(observed_at) DESC,id DESC LIMIT 1"""
        ).fetchone()
        if row is not None:
            publish(("earnings_forecast", "earnings_option_defined_risk"), row["observed_at"])
    return evidence


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
    """Return the customer-facing four-tier contract.

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
        plans.append(
            {
                "key": key,
                "display_name": PUBLIC_PLAN_DISPLAY_NAMES[key],
                "summary": definition["summary"],
                "prices": dict(definition["prices"]),
                "features": list(definition["features"]),
                "capabilities": sorted(capabilities),
            }
        )
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_key": POLICY_KEY,
        "public_plan_order": list(POLICY_V1_PUBLIC_PLAN_ORDER),
        "plans": plans,
        "dynamic_programs": {
            "option_live_beta": {
                "application_capability": "option_live_beta_apply",
                "eligible_plan": "专业版",
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
    source_plans = policy.policy["plans"] if policy is not None else canonical_public_policy()["plans"]
    all_capabilities = sorted({
        str(capability)
        for item in source_plans
        for capability in item["capabilities"]
    })
    data_bound = {
        "signal_web", "tg_stock_signal", "tg_option_signal", "option_chain",
        "option_quote_chart", "option_greeks", "option_iv", "earnings_forecast",
        "earnings_option_defined_risk",
    }
    result: list[dict[str, Any]] = []
    for capability in all_capabilities:
        included = capability in policy_capabilities(policy, plan) if policy is not None else False
        application = capability == "option_live_beta_apply" and included
        data_state = "not_applicable"
        reason_code = "runtime_approval_required" if application else "included" if included else "upgrade_required"
        status = "application_required" if application else "available" if included else "locked"
        if included and capability in data_bound:
            evidence = (runtime_evidence or {}).get(capability)
            if not isinstance(evidence, Mapping):
                status, reason_code, data_state = "unavailable", "runtime_evidence_missing", "missing"
            else:
                data_state = str(evidence.get("data_state") or "missing")
                health = str(evidence.get("health") or "unknown")
                verified_at = evidence.get("verified_at")
                verified_moment: datetime | None = None
                if isinstance(verified_at, str):
                    try:
                        verified_moment = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
                        if verified_moment.tzinfo is None:
                            verified_moment = None
                        else:
                            verified_moment = verified_moment.astimezone(UTC)
                    except ValueError:
                        verified_moment = None
                current = (now or datetime.now(UTC)).astimezone(UTC)
                fresh = bool(
                    verified_moment is not None
                    and 0 <= (current - verified_moment).total_seconds() <= maximum_age_seconds
                )
                if data_state != "ready" or health != "healthy" or not fresh:
                    status, reason_code = "unavailable", "runtime_evidence_invalid"
        result.append({
            "key": capability,
            "status": status,
            "reason_code": reason_code,
            "limit": None,
            "data_state": data_state,
        })
    return result


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
    if tuple(policy.get("public_plan_order") or ()) != POLICY_V1_PUBLIC_PLAN_ORDER:
        raise EntitlementPolicyError("公开会员必须严格为免费、标准、高级、专业四级。")
    plans = policy.get("plans")
    if not isinstance(plans, list) or not all(isinstance(item, dict) for item in plans):
        raise EntitlementPolicyError("会员方案必须是对象数组。")
    if [item.get("key") for item in plans] != list(POLICY_V1_PUBLIC_PLAN_ORDER):
        raise EntitlementPolicyError("会员方案与公开等级顺序不一致。")
    plan_keys = {"key", "display_name", "summary", "prices", "features", "capabilities"}
    for item in plans:
        if set(item) != plan_keys:
            raise EntitlementPolicyError("会员方案字段不完整或包含未知字段。")
        if not isinstance(item["display_name"], str) or not item["display_name"].strip():
            raise EntitlementPolicyError("会员方案显示名称无效。")
        if not isinstance(item["summary"], str) or not item["summary"].strip():
            raise EntitlementPolicyError("会员方案摘要无效。")
        if not isinstance(item["prices"], dict) or not all(
            isinstance(key, str)
            and isinstance(amount, (int, float)) and not isinstance(amount, bool)
            and amount >= 0
            for key, amount in item["prices"].items()
        ):
            raise EntitlementPolicyError("会员方案价格无效。")
        if not isinstance(item["features"], list) or not all(
            isinstance(feature, str) and feature.strip() for feature in item["features"]
        ):
            raise EntitlementPolicyError("会员方案说明无效。")
        if not isinstance(item["capabilities"], list) or len(item["capabilities"]) != len(set(item["capabilities"])) or not all(
            isinstance(capability, str) and capability.strip() for capability in item["capabilities"]
        ):
            raise EntitlementPolicyError("会员方案能力无效。")
    dynamic_programs = policy.get("dynamic_programs")
    if not isinstance(dynamic_programs, dict) or set(dynamic_programs) != {"option_live_beta"}:
        raise EntitlementPolicyError("会员策略动态项目不完整或包含未知项目。")
    dynamic = dynamic_programs.get("option_live_beta", {})
    if not isinstance(dynamic, dict) or set(dynamic) != OPTION_LIVE_BETA_KEYS:
        raise EntitlementPolicyError("真实期权 Beta 字段不完整或包含未知字段。")
    if dynamic.get("application_capability") != "option_live_beta_apply":
        raise EntitlementPolicyError("真实期权 Beta 申请能力无效。")
    if dynamic.get("eligible_plan") != "专业版":
        raise EntitlementPolicyError("真实期权 Beta 仅允许专业会员申请。")
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
    created_at: datetime | None = None,
) -> tuple[PublishedPolicy, bool]:
    """Append one policy version; identical content is idempotent."""
    owns_transaction = not bool(getattr(conn, "in_transaction", False))
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        result = _publish_policy_locked(
            conn, value, effective_at=effective_at,
            created_by=created_by, created_at=created_at,
        )
        if owns_transaction:
            conn.commit()
        return result
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise


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
    policy = validate_policy(value)
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
            int(created_by) if created_by is not None else None,
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


def policy_capabilities(policy: PublishedPolicy, plan: str) -> set[str]:
    """Resolve one tier from a verified published policy."""
    order = tuple(str(item) for item in policy.policy["public_plan_order"])
    effective = order[-1] if plan == "定制版" else plan
    try:
        plan_index = order.index(effective)
    except ValueError:
        plan_index = 0
    matrix = {
        str(item["key"]): set(str(value) for value in item["capabilities"])
        for item in policy.policy["plans"]
    }
    capabilities = {
        capability
        for level in order[: plan_index + 1]
        for capability in matrix[level]
    }
    if plan == "定制版":
        capabilities.difference_update({
            "alert_basic", "alerts_10", "alerts_unlimited", "backtest_1y",
            "backtest_3y", "backtest_10y", "broker_access_apply", "code_import",
            "api_signal_import", "strategy_template_save", "option_live_beta_apply",
            "option_auto_paper_official", "csv_import", "strategy_generate",
            "strategy_generate_complex", "strategy_tracking",
            "strategy_template_parameters", "strategy_templates_use",
        })
    return capabilities


def policy_can(
    conn: Any, plan: str, capability: str, *, as_of: datetime | None = None
) -> bool:
    from core.plans import CAPABILITY_ALIASES

    policy = current_policy(conn, as_of=as_of)
    if policy is None:
        return False
    canonical = {
        **CAPABILITY_ALIASES,
        "option_auto": "option_auto_paper_official",
        "option_live_beta": "option_live_beta_apply",
    }.get(capability, capability)
    return canonical in policy_capabilities(policy, plan)


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
    """Verify the immutable policy proof before granting paid membership."""
    key = order.get("entitlement_policy_key_snapshot")
    version = order.get("entitlement_policy_version_snapshot")
    digest = order.get("entitlement_policy_sha256_snapshot")
    if not key and version is None and not digest:
        legacy = conn.execute(
            "SELECT recorded_at FROM membership_entitlement_legacy_orders WHERE order_no=?",
            (str(order.get("order_no") or ""),),
        ).fetchone()
        if legacy is None:
            raise EntitlementPolicyError("订单缺少完整的会员策略快照。")
        created_moment = _aware_datetime(order.get("created_at"), "订单创建时间")
        recorded_moment = _aware_datetime(legacy["recorded_at"], "历史订单登记时间")
        if created_moment > recorded_moment:
            raise EntitlementPolicyError("历史订单创建时间晚于迁移登记时间。")
        row = conn.execute(
            """SELECT * FROM membership_entitlement_policy_versions
               WHERE policy_key=? AND version=1""",
            (POLICY_KEY,),
        ).fetchone()
        if row is None:
            raise EntitlementPolicyError("旧订单无法绑定 bootstrap v1 会员策略。")
        bootstrap = _published(row)
        conn.execute(
            """UPDATE subscription_orders SET entitlement_policy_key_snapshot=?,
               entitlement_policy_version_snapshot=?,entitlement_policy_sha256_snapshot=?
               WHERE order_no=? AND entitlement_policy_key_snapshot IS NULL""",
            (bootstrap.policy_key, bootstrap.version, bootstrap.policy_sha256, order["order_no"]),
        )
        return bootstrap
    if not key or version is None or not digest:
        raise EntitlementPolicyError("订单会员策略快照不完整。")
    row = conn.execute(
        """SELECT * FROM membership_entitlement_policy_versions
           WHERE policy_key=? AND version=?""",
        (str(key), int(version)),
    ).fetchone()
    if row is None:
        raise EntitlementPolicyError("订单引用的会员策略版本不存在。")
    published = _published(row)
    if published.policy_sha256 != str(digest):
        raise EntitlementPolicyError("订单会员策略快照哈希不一致。")
    created_at = _aware_datetime(order.get("created_at"), "订单创建时间")
    effective = current_policy(conn, as_of=created_at)
    if effective is None or (
        effective.policy_key,
        effective.version,
        effective.policy_sha256,
    ) != (published.policy_key, published.version, published.policy_sha256):
        raise EntitlementPolicyError("订单会员策略快照不是创建时的有效版本。")
    return published
