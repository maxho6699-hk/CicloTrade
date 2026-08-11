# -*- coding: utf-8 -*-
"""Membership entitlement ledger and effective-plan resolution."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from core.compat import UTC
from core.plans import PLAN_ORDER, PLANS


class MembershipPlanConflict(ValueError):
    """Raised when a membership change would lower an active plan."""


def _as_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _plan_rank(plan: str) -> int:
    try:
        return PLAN_ORDER.index(str(plan))
    except ValueError:
        return 0


def _coverage_end(
    rows: Iterable[Any], plan: str, moment: datetime
) -> datetime | None:
    intervals: list[tuple[datetime, datetime]] = []
    for row in rows:
        if str(row["plan_type"]) != plan:
            continue
        starts_at = _as_utc(row["starts_at"])
        expires_at = _as_utc(row["expires_at"])
        if starts_at is None or expires_at is None or expires_at <= starts_at:
            continue
        intervals.append((starts_at, expires_at))
    intervals.sort(key=lambda item: (item[0], item[1]))

    cursor: datetime | None = None
    for starts_at, expires_at in intervals:
        if starts_at <= moment < expires_at:
            cursor = max(cursor or expires_at, expires_at)
    if cursor is None:
        return None

    changed = True
    while changed:
        changed = False
        for starts_at, expires_at in intervals:
            if starts_at <= cursor < expires_at:
                cursor = expires_at
                changed = True
    return cursor


def _active_rows(conn: Any, user_id: int) -> list[Any]:
    return list(
        conn.execute(
            """SELECT plan_type,starts_at,expires_at FROM membership_entitlements
               WHERE user_id=? AND status='active'""",
            (int(user_id),),
        ).fetchall()
    )


def _reflow_plan_entitlements(conn: Any, user_id: int, plan: str) -> None:
    """Close gaps after a same-tier entitlement is revoked."""
    rows = conn.execute(
        """SELECT id,source_kind,starts_at,expires_at,duration_days,created_at
           FROM membership_entitlements
           WHERE user_id=? AND plan_type=? AND status='active'
           ORDER BY created_at,id""",
        (int(user_id), str(plan)),
    ).fetchall()
    cursor: datetime | None = None
    for row in rows:
        starts_at = _as_utc(row["starts_at"])
        expires_at = _as_utc(row["expires_at"])
        granted_at = _as_utc(row["created_at"])
        duration_days = row["duration_days"]
        if starts_at is None or expires_at is None:
            continue
        if duration_days is None or granted_at is None:
            cursor = max(cursor or expires_at, expires_at)
            continue
        starts_at = max(granted_at, cursor) if cursor is not None else granted_at
        expires_at = starts_at + timedelta(days=int(duration_days))
        conn.execute(
            "UPDATE membership_entitlements SET starts_at=?,expires_at=? WHERE id=?",
            (_iso(starts_at), _iso(expires_at), int(row["id"])),
        )
        cursor = expires_at


def ensure_cached_entitlement(
    conn: Any, user_id: int, now: datetime | None = None
) -> None:
    """Preserve a legacy/admin cache value before the cache is recalculated."""
    moment = now or datetime.now(UTC)
    user = conn.execute(
        "SELECT plan_type,subscription_expire FROM users WHERE id=?", (int(user_id),)
    ).fetchone()
    if not user:
        raise ValueError("会员权益关联用户不存在。")
    plan = str(user["plan_type"] or "免费版")
    expires_at = _as_utc(user["subscription_expire"])
    if plan not in PLANS or plan == "免费版" or expires_at is None or expires_at <= moment:
        return

    rows = _active_rows(conn, user_id)
    represented_until = _coverage_end(rows, plan, moment)
    if represented_until is not None and represented_until >= expires_at:
        return

    source_ref = f"user:{int(user_id)}:{plan}:{_iso(expires_at)}"
    conn.execute(
        """INSERT OR IGNORE INTO membership_entitlements
           (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
           VALUES (?,?,?,?,NULL,?,?, 'active',?)""",
        (
            int(user_id),
            plan,
            _iso(moment),
            _iso(expires_at),
            "legacy_cache",
            source_ref,
            _iso(moment),
        ),
    )


def resolve_membership(
    conn: Any,
    user_id: int,
    now: datetime | None = None,
    *,
    sync_cache: bool = True,
    preserve_cache: bool = True,
) -> dict[str, str | None]:
    """Resolve one effective plan from overlapping, time-bounded entitlements."""
    moment = now or datetime.now(UTC)
    if preserve_cache:
        ensure_cached_entitlement(conn, user_id, moment)
    resolved = resolve_membership_snapshot(conn, user_id, moment)
    resolved_plan = str(resolved["plan_type"])
    expiry_text = resolved["subscription_expire"]
    if sync_cache:
        conn.execute(
            "UPDATE users SET plan_type=?,subscription_expire=? WHERE id=?",
            (resolved_plan, expiry_text, int(user_id)),
        )
    return {"plan_type": resolved_plan, "subscription_expire": expiry_text}


def resolve_membership_snapshot(
    conn: Any,
    user_id: int,
    now: datetime | None = None,
    *,
    cached_plan: str | None = None,
    cached_expiry: str | None = None,
) -> dict[str, str | None]:
    """Read the effective membership without mutating a read-only connection."""
    moment = now or datetime.now(UTC)
    return _resolve_membership_rows(
        _active_rows(conn, user_id),
        moment,
        cached_plan=cached_plan,
        cached_expiry=cached_expiry,
    )


def _resolve_membership_rows(
    entitlement_rows: Iterable[Any],
    moment: datetime,
    *,
    cached_plan: str | None,
    cached_expiry: str | None,
) -> dict[str, str | None]:
    rows: list[Any] = list(entitlement_rows)
    expires_at = _as_utc(cached_expiry)
    if (
        not rows
        and cached_plan in PLANS
        and cached_plan != "免费版"
        and expires_at is not None
        and expires_at > moment
    ):
        rows.append(
            {
                "plan_type": cached_plan,
                "starts_at": _iso(moment),
                "expires_at": _iso(expires_at),
            }
        )
    resolved_plan = "免费版"
    resolved_expiry: datetime | None = None
    for plan in PLAN_ORDER[1:]:
        coverage_end = _coverage_end(rows, plan, moment)
        if coverage_end is not None and _plan_rank(plan) >= _plan_rank(resolved_plan):
            resolved_plan = plan
            resolved_expiry = coverage_end

    expiry_text = _iso(resolved_expiry) if resolved_expiry is not None else None
    return {"plan_type": resolved_plan, "subscription_expire": expiry_text}


def authoritative_membership_user(
    database: Any, user: Any, now: datetime | None = None
) -> dict[str, Any]:
    """Overlay a user row with the ledger result for non-transactional callers."""
    snapshot = dict(user or {})
    user_id = snapshot.get("id")
    if user_id is None:
        return snapshot
    rows = database.fetch_all(
        """SELECT plan_type,starts_at,expires_at FROM membership_entitlements
           WHERE user_id=? AND status='active'""",
        (int(user_id),),
    )
    resolved = _resolve_membership_rows(
        rows,
        now or datetime.now(UTC),
        cached_plan=str(snapshot.get("plan_type") or "免费版"),
        cached_expiry=snapshot.get("subscription_expire"),
    )
    snapshot.update(resolved)
    return snapshot


def authoritative_membership_row(
    conn: Any, user: Any, now: datetime | None = None
) -> dict[str, Any]:
    """Resolve a user row through an existing transaction/connection."""
    snapshot = dict(user or {})
    user_id = snapshot.get("id")
    if user_id is None:
        return snapshot
    rows = conn.execute(
        """SELECT plan_type,starts_at,expires_at FROM membership_entitlements
           WHERE user_id=? AND status='active'""",
        (int(user_id),),
    ).fetchall()
    snapshot.update(
        _resolve_membership_rows(
            rows,
            now or datetime.now(UTC),
            cached_plan=str(snapshot.get("plan_type") or "免费版"),
            cached_expiry=snapshot.get("subscription_expire"),
        )
    )
    return snapshot


def assert_plan_not_lower(current_plan: str, requested_plan: str) -> None:
    state = membership_purchase_state(current_plan, requested_plan)
    if not state["can_purchase"]:
        raise MembershipPlanConflict(
            str(
                state["blocked_reason"]
                or "当前会员已覆盖该方案，不能购买或生效低等级会员。"
            )
        )


def membership_purchase_state(
    current_plan: str, requested_plan: str
) -> dict[str, str | bool | None]:
    """Return the canonical purchase action for one effective membership.

    Consumers must use this explicit contract instead of inferring rank from a
    plan array. One user always has one effective feature set even when the
    ledger contains overlapping upgrade and fallback intervals.
    """
    current = str(current_plan) if current_plan in PLANS else "免费版"
    requested = str(requested_plan)
    if requested not in PLANS or requested == "免费版":
        return {
            "can_purchase": False,
            "purchase_action": "unavailable",
            "blocked_reason": "请选择可购买的订阅方案。",
        }
    if _plan_rank(requested) < _plan_rank(current):
        return {
            "can_purchase": False,
            "purchase_action": "covered",
            "blocked_reason": "当前会员已覆盖该方案，不能购买或生效低等级会员。",
        }
    if requested == current:
        return {
            "can_purchase": True,
            "purchase_action": "renew",
            "blocked_reason": None,
        }
    return {
        "can_purchase": True,
        "purchase_action": "upgrade",
        "blocked_reason": None,
    }


def add_membership_entitlement(
    conn: Any,
    user_id: int,
    plan: str,
    days: int,
    *,
    source_kind: str,
    source_ref: str,
    now: datetime | None = None,
) -> dict[str, str | None]:
    """Add an upgrade or renewal without starting duplicate feature instances."""
    moment = now or datetime.now(UTC)
    if plan not in PLANS or plan == "免费版" or int(days) < 1:
        raise ValueError("会员权益无效。")
    ensure_cached_entitlement(conn, user_id, moment)
    current = resolve_membership(
        conn, user_id, moment, sync_cache=False, preserve_cache=False
    )
    assert_plan_not_lower(str(current["plan_type"]), plan)

    rows = _active_rows(conn, user_id)
    coverage_end = _coverage_end(rows, plan, moment)
    starts_at = coverage_end if current["plan_type"] == plan and coverage_end else moment
    expires_at = starts_at + timedelta(days=int(days))
    conn.execute(
        """INSERT OR IGNORE INTO membership_entitlements
           (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
           VALUES (?,?,?,?,?,?,?, 'active',?)""",
        (
            int(user_id),
            plan,
            _iso(starts_at),
            _iso(expires_at),
            int(days),
            str(source_kind),
            str(source_ref),
            _iso(moment),
        ),
    )
    return resolve_membership(
        conn, user_id, moment, sync_cache=True, preserve_cache=False
    )


def revoke_membership_entitlement(
    conn: Any,
    user_id: int,
    *,
    source_kind: str,
    source_ref: str,
    now: datetime | None = None,
) -> bool:
    moment = now or datetime.now(UTC)
    ensure_cached_entitlement(conn, user_id, moment)
    entitlement = conn.execute(
        """SELECT plan_type FROM membership_entitlements
           WHERE user_id=? AND source_kind=? AND source_ref=? AND status='active'""",
        (int(user_id), str(source_kind), str(source_ref)),
    ).fetchone()
    changed = conn.execute(
        """UPDATE membership_entitlements
           SET status='revoked',revoked_at=?
           WHERE user_id=? AND source_kind=? AND source_ref=? AND status='active'""",
        (_iso(moment), int(user_id), str(source_kind), str(source_ref)),
    )
    if changed.rowcount > 0 and entitlement:
        _reflow_plan_entitlements(conn, user_id, str(entitlement["plan_type"]))
    resolve_membership(conn, user_id, moment, sync_cache=True, preserve_cache=False)
    return changed.rowcount > 0


def replace_membership_entitlements(
    conn: Any,
    user_id: int,
    plan: str,
    days: int | None,
    *,
    source_kind: str,
    source_ref: str,
    now: datetime | None = None,
) -> dict[str, str | None]:
    """Apply an audited administrator reset, the only allowed forced downgrade."""
    moment = now or datetime.now(UTC)
    ensure_cached_entitlement(conn, user_id, moment)
    conn.execute(
        """UPDATE membership_entitlements SET status='revoked',revoked_at=?
           WHERE user_id=? AND status='active'""",
        (_iso(moment), int(user_id)),
    )
    if plan == "免费版":
        return resolve_membership(
            conn, user_id, moment, sync_cache=True, preserve_cache=False
        )
    if plan not in PLANS or days is None or int(days) < 1:
        raise ValueError("管理员会员调整无效。")
    expires_at = moment + timedelta(days=int(days))
    conn.execute(
        """INSERT INTO membership_entitlements
           (user_id,plan_type,starts_at,expires_at,duration_days,source_kind,source_ref,status,created_at)
           VALUES (?,?,?,?,?,?,?, 'active',?)""",
        (
            int(user_id),
            plan,
            _iso(moment),
            _iso(expires_at),
            int(days),
            str(source_kind),
            str(source_ref),
            _iso(moment),
        ),
    )
    return resolve_membership(
        conn, user_id, moment, sync_cache=True, preserve_cache=False
    )
