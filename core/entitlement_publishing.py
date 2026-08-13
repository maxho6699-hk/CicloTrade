"""Authorized, atomic membership-policy publication."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Callable, Mapping


def publish(
    conn: Any, value: Mapping[str, Any], *, effective_at: datetime, created_by: int | None,
    reviewer_id: int | None, readiness_evidence_ref: str | None, idempotency_key: str | None,
    created_at: datetime | None, validate: Callable[..., dict], iso: Callable, canonical_json: Callable,
    error_type: type[ValueError], publish_locked: Callable, published: Callable,
) -> tuple[Any, bool]:
    normalized = validate(value, require_current_contract=False)
    if created_by is None or reviewer_id is None or not idempotency_key:
        raise error_type("发布会员策略必须提供授权发布者、readiness 回执和幂等键。")
    evidence = str(readiness_evidence_ref or "").strip()
    if not evidence or not 8 <= len(str(idempotency_key).strip()) <= 128:
        raise error_type("会员策略发布的证据引用或幂等键无效。")
    request = {"policy": normalized, "effective_at": iso(effective_at), "receipt_id": int(reviewer_id), "readiness_evidence_ref": evidence}
    owns = not bool(getattr(conn, "in_transaction", False))
    if owns:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute("SAVEPOINT entitlement_policy_publish")
    try:
        _require_publisher(conn, int(created_by))
        _consume_review(conn, normalized, int(reviewer_id), evidence, iso, canonical_json, error_type)
        digest = hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()
        event = conn.execute("SELECT * FROM membership_entitlement_policy_admin_events WHERE actor_id=? AND idempotency_key=?", (created_by, str(idempotency_key).strip())).fetchone()
        if event:
            if event["request_sha256"] != digest:
                raise error_type("会员策略发布幂等键已用于不同请求。")
            row = conn.execute("SELECT * FROM membership_entitlement_policy_versions WHERE policy_key=? AND version=?", (event["policy_key"], event["policy_version"])).fetchone()
            if row is None:
                raise error_type("会员策略发布回执引用不存在的版本。")
            result = (published(row), False)
        else:
            result = publish_locked(conn, value, effective_at=effective_at, created_by=created_by, created_at=created_at)
            policy, _ = result
            _bind_review(conn, policy, int(reviewer_id), evidence, iso, error_type)
            conn.execute("""INSERT INTO membership_entitlement_policy_admin_events(actor_id,idempotency_key,request_sha256,policy_key,policy_version,policy_sha256,created_at) VALUES (?,?,?,?,?,?,?)""", (created_by, str(idempotency_key).strip(), digest, policy.policy_key, policy.version, policy.policy_sha256, iso(created_at)))
        conn.commit() if owns else conn.execute("RELEASE entitlement_policy_publish")
        return result
    except Exception:
        if owns:
            conn.rollback()
        else:
            conn.execute("ROLLBACK TO entitlement_policy_publish")
            conn.execute("RELEASE entitlement_policy_publish")
        raise


def _require_publisher(conn: Any, actor_id: int) -> None:
    row = conn.execute("""SELECT u.is_admin,u.is_active,r.role FROM users u LEFT JOIN admin_roles r ON r.user_id=u.id WHERE u.id=?""", (actor_id,)).fetchone()
    if not row or not row["is_admin"] or not row["is_active"] or row["role"] != "super_admin":
        raise PermissionError("会员策略发布授权无效。")


def _consume_review(conn: Any, policy: Mapping, receipt_id: int, evidence: str, iso: Callable, canonical_json: Callable, error_type: type[ValueError]) -> None:
    row = conn.execute("SELECT * FROM membership_entitlement_readiness_receipts WHERE id=?", (receipt_id,)).fetchone()
    if not row or row["evidence_ref"] != evidence or row["candidate_sha256"] != hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest() or str(row["valid_until"]) <= iso():
        raise error_type("readiness 回执不存在、已过期或不匹配候选策略。")


def _bind_review(conn: Any, policy: Any, receipt_id: int, evidence: str, iso: Callable, error_type: type[ValueError]) -> None:
    refs = {item["readiness"]["evidence_ref"] for item in policy.policy["plans"] if item["readiness"] and any(item["commerce"].values())}
    if refs != {evidence}:
        raise error_type("readiness 审查证据必须精确覆盖发布策略。")
    receipt = conn.execute("SELECT reviewer_id FROM membership_entitlement_readiness_receipts WHERE id=?", (receipt_id,)).fetchone()
    conn.execute("""INSERT OR IGNORE INTO membership_entitlement_readiness_reviews(evidence_ref,policy_key,policy_version,policy_sha256,reviewer_id,reviewed_at) VALUES (?,?,?,?,?,?)""", (evidence, policy.policy_key, policy.version, policy.policy_sha256, receipt["reviewer_id"], iso()))


def create_review(
    conn: Any, value: Mapping[str, Any], *, reviewer_id: int, evidence_ref: str,
    valid_until: datetime, idempotency_key: str, validate: Callable[..., dict],
    iso: Callable, canonical_json: Callable, error_type: type[ValueError],
) -> int:
    policy = validate(value, require_current_contract=False)
    if not evidence_ref.strip() or not 8 <= len(idempotency_key.strip()) <= 128 or valid_until <= datetime.now(valid_until.tzinfo):
        raise error_type("readiness 审查证据、有效期或幂等键无效。")
    row = conn.execute("""SELECT u.is_admin,u.is_active,r.role FROM users u LEFT JOIN admin_roles r ON r.user_id=u.id WHERE u.id=?""", (reviewer_id,)).fetchone()
    if not row or not row["is_admin"] or not row["is_active"] or row["role"] != "risk_audit":
        raise PermissionError("readiness 审查授权无效。")
    digest = hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()
    request = hashlib.sha256(canonical_json([digest, evidence_ref, iso(valid_until)]).encode("utf-8")).hexdigest()
    existing = conn.execute("SELECT * FROM membership_entitlement_readiness_receipts WHERE reviewer_id=? AND idempotency_key=?", (reviewer_id, idempotency_key)).fetchone()
    if existing:
        if existing["request_sha256"] != request:
            raise error_type("readiness 审查幂等键已用于不同请求。")
        return int(existing["id"])
    cursor = conn.execute("""INSERT INTO membership_entitlement_readiness_receipts(candidate_sha256,evidence_ref,reviewer_id,valid_until,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?,?)""", (digest, evidence_ref, reviewer_id, iso(valid_until), idempotency_key, request, iso()))
    return int(cursor.lastrowid)
