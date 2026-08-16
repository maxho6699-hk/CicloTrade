from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from core.compat import UTC
from core.entitlement_commerce import plan_commerce_decision, readiness_proof
from core.entitlement_policy import (
    EntitlementPolicyError,
    canonical_public_policy,
    create_readiness_review,
    current_policy,
    publish_policy,
    validate_policy,
)


NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
MIGRATION = Path(__file__).parents[1] / "migrations" / "0035_entitlement_policy_versions.sql"


def _plan(policy, key):
    return next(item for item in policy["plans"] if item["key"] == key)


def _database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,is_admin INTEGER,is_active INTEGER)")
    conn.execute("CREATE TABLE admin_roles(user_id INTEGER PRIMARY KEY,role TEXT)")
    conn.execute("CREATE TABLE subscription_orders(id INTEGER PRIMARY KEY,order_no TEXT UNIQUE,created_at TEXT)")
    conn.execute("CREATE TABLE user_membership_logs(id INTEGER PRIMARY KEY,admin_id INTEGER)")
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    conn.executemany("INSERT INTO users VALUES (?,?,?)", ((1, 1, 1), (2, 1, 1)))
    conn.executemany("INSERT INTO admin_roles VALUES (?,?)", ((1, "super_admin"), (2, "risk_audit")))
    return conn


def _publish(conn, policy=None, *, at=NOW, key="policy-key-1"):
    policy = policy or canonical_public_policy()
    for item in policy["plans"]:
        if item["readiness"]:
            item["readiness"] = readiness_proof(item["key"], item["lifecycle"], item["capabilities"], evidence_ref="review-813")
    receipt = create_readiness_review(
        conn, policy, reviewer_id=2, evidence_ref="review-813",
        valid_until=NOW + timedelta(days=30), idempotency_key=f"review-{key}",
    )
    return publish_policy(
        conn, policy, effective_at=at, created_at=NOW, created_by=1,
        reviewer_id=receipt, readiness_evidence_ref="review-813", idempotency_key=key,
    )


def test_retired_lifecycle_denies_commerce_but_keeps_compatibility_snapshot():
    policy = validate_policy(canonical_public_policy())
    assert [item["key"] for item in policy["plans"]][-2:] == ["专业版", "定制版"]
    for key in ("专业版", "定制版"):
        assert _plan(policy, key)["lifecycle"] == "retired_legacy"
        assert _plan(policy, key)["compatibility_capabilities"]
        for action in ("purchase", "renew", "admin_grant", "upgrade"):
            assert not plan_commerce_decision(policy, key, action, as_of=NOW)["allowed"]


def test_publish_requires_split_authority_atomic_audit_and_idempotency():
    conn = _database()
    with pytest.raises(EntitlementPolicyError, match="授权发布者"):
        publish_policy(conn, canonical_public_policy(), effective_at=NOW)
    published, created = _publish(conn)
    assert created and published.version == 1
    assert conn.execute("SELECT COUNT(*) FROM membership_entitlement_policy_admin_events").fetchone()[0] == 1
    replay, created = _publish(conn)
    assert not created and replay.version == 1
    with pytest.raises(EntitlementPolicyError, match="不同请求"):
        _publish(conn, key="policy-key-1", at=NOW + timedelta(days=1))


def test_readiness_receipt_is_consumed_once_but_publish_replay_is_safe():
    conn = _database()
    policy = canonical_public_policy()
    for item in policy["plans"]:
        if item["readiness"]:
            item["readiness"] = readiness_proof(item["key"], item["lifecycle"], item["capabilities"], evidence_ref="single-use")
    receipt = create_readiness_review(
        conn, policy, reviewer_id=2, evidence_ref="single-use",
        valid_until=NOW + timedelta(days=30), idempotency_key="single-review-001",
    )
    arguments = dict(
        effective_at=NOW, created_at=NOW, created_by=1, reviewer_id=receipt,
        readiness_evidence_ref="single-use", idempotency_key="single-publish-001",
    )
    first, created = publish_policy(conn, policy, **arguments)
    replay, replay_created = publish_policy(conn, policy, **arguments)
    assert created and not replay_created and replay.version == first.version
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        publish_policy(
            conn, policy, effective_at=NOW + timedelta(days=1), created_at=NOW,
            created_by=1, reviewer_id=receipt, readiness_evidence_ref="single-use",
            idempotency_key="single-publish-002",
        )
    assert conn.execute("SELECT COUNT(*) FROM membership_entitlement_policy_versions").fetchone()[0] == 1


def test_readiness_receipt_requires_reviewer_and_rolls_back_outer_transaction():
    conn = _database()
    policy = canonical_public_policy()
    valid_until = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(PermissionError, match="审查授权"):
        create_readiness_review(
            conn, policy, reviewer_id=1, evidence_ref="review-813",
            valid_until=valid_until, idempotency_key="bad-review-001",
        )
    assert conn.execute("SELECT COUNT(*) FROM membership_entitlement_readiness_receipts").fetchone()[0] == 0
    conn.commit()
    conn.execute("BEGIN")
    receipt = create_readiness_review(
        conn, policy, reviewer_id=2, evidence_ref="review-813",
        valid_until=valid_until, idempotency_key="review-tx-001",
    )
    assert receipt > 0 and conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM membership_entitlement_readiness_receipts").fetchone()[0] == 0


def test_unreviewed_or_wrong_readiness_proof_fails_closed():
    conn = _database()
    policy = canonical_public_policy()
    for item in policy["plans"]:
        if item["readiness"]:
            item["readiness"] = readiness_proof(item["key"], item["lifecycle"], item["capabilities"], evidence_ref="unreviewed")
    with pytest.raises(EntitlementPolicyError, match="回执"):
        publish_policy(conn, policy, effective_at=NOW, created_at=NOW, created_by=1,
                       reviewer_id=999, readiness_evidence_ref="unreviewed", idempotency_key="policy-key-x")


def test_lifecycle_can_pause_or_hide_without_reordering_public_history():
    conn = _database()
    _publish(conn)
    policy = canonical_public_policy()
    advanced = _plan(policy, "高级版")
    advanced["lifecycle"] = "sales_paused"
    advanced["commerce"] = {key: False for key in advanced["commerce"]}
    advanced["readiness"] = None
    paused = validate_policy(policy, require_current_contract=False)
    published, created = _publish(conn, paused, at=NOW + timedelta(days=1), key="policy-key-2")
    assert created and published.version == 2
    assert current_policy(conn, as_of=NOW).version == 1
    assert current_policy(conn, as_of=NOW + timedelta(days=1)).policy["public_plan_order"] == ["免费版", "标准版", "高级版"]


def test_professional_can_be_restored_by_new_reviewed_policy_without_schema_change():
    conn = _database()
    _publish(conn)
    policy = canonical_public_policy()
    professional = _plan(policy, "专业版")
    professional["lifecycle"] = "active_public"
    professional["commerce"] = {key: True for key in professional["commerce"]}
    professional["capabilities"] = professional["compatibility_capabilities"]
    professional["compatibility_capabilities"] = []
    professional["readiness"] = readiness_proof("专业版", "active_public", professional["capabilities"], evidence_ref="review-813")
    policy["public_plan_order"].append("专业版")
    published, created = _publish(conn, validate_policy(policy, require_current_contract=False), at=NOW + timedelta(days=1), key="policy-key-restore")
    assert created and published.version == 2
    assert current_policy(conn, as_of=NOW + timedelta(days=1)).policy["public_plan_order"][-1] == "专业版"
    assert deepcopy(_plan(canonical_public_policy(), "专业版")["compatibility_capabilities"])


def test_five_account_auto_live_eligibility_cannot_leak_into_lower_public_plans():
    policy = canonical_public_policy()
    standard = _plan(policy, "标准版")
    standard["capabilities"].append("auto_control_account_5")
    standard["readiness"] = readiness_proof(
        "标准版",
        "active_public",
        standard["capabilities"],
        evidence_ref="invalid-lower-plan-review",
    )
    with pytest.raises(EntitlementPolicyError, match="仅允许.*专业或定制"):
        validate_policy(policy, require_current_contract=False)
