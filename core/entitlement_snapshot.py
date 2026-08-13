"""Immutable membership-order policy snapshot validation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping, TypeVar


T = TypeVar("T")


def validate_order_policy_snapshot(
    conn: Any,
    order: Mapping[str, Any],
    *,
    policy_key: str,
    parse_aware: Callable[[Any, str], datetime],
    load_published: Callable[[Any], T],
    load_current: Callable[..., T | None],
    error_type: type[ValueError],
) -> T:
    key = order.get("entitlement_policy_key_snapshot")
    version = order.get("entitlement_policy_version_snapshot")
    digest = order.get("entitlement_policy_sha256_snapshot")
    if not key and version is None and not digest:
        legacy = conn.execute(
            "SELECT recorded_at FROM membership_entitlement_legacy_orders WHERE order_no=?",
            (str(order.get("order_no") or ""),),
        ).fetchone()
        if legacy is None:
            raise error_type("订单缺少完整的会员策略快照。")
        created_moment = parse_aware(order.get("created_at"), "订单创建时间")
        recorded_moment = parse_aware(legacy["recorded_at"], "历史订单登记时间")
        if created_moment > recorded_moment:
            raise error_type("历史订单创建时间晚于迁移登记时间。")
        row = conn.execute(
            """SELECT * FROM membership_entitlement_policy_versions
               WHERE policy_key=? AND version=1""",
            (policy_key,),
        ).fetchone()
        if row is None:
            raise error_type("旧订单无法绑定 bootstrap v1 会员策略。")
        bootstrap = load_published(row)
        conn.execute(
            """UPDATE subscription_orders SET entitlement_policy_key_snapshot=?,
               entitlement_policy_version_snapshot=?,entitlement_policy_sha256_snapshot=?
               WHERE order_no=? AND entitlement_policy_key_snapshot IS NULL""",
            (
                bootstrap.policy_key,
                bootstrap.version,
                bootstrap.policy_sha256,
                order["order_no"],
            ),
        )
        return bootstrap
    if not key or version is None or not digest:
        raise error_type("订单会员策略快照不完整。")
    row = conn.execute(
        """SELECT * FROM membership_entitlement_policy_versions
           WHERE policy_key=? AND version=?""",
        (str(key), int(version)),
    ).fetchone()
    if row is None:
        raise error_type("订单引用的会员策略版本不存在。")
    published = load_published(row)
    if published.policy_sha256 != str(digest):
        raise error_type("订单会员策略快照哈希不一致。")
    created_at = parse_aware(order.get("created_at"), "订单创建时间")
    effective = load_current(conn, as_of=created_at)
    if effective is None or (
        effective.policy_key,
        effective.version,
        effective.policy_sha256,
    ) != (published.policy_key, published.version, published.policy_sha256):
        raise error_type("订单会员策略快照不是创建时的有效版本。")
    return published
