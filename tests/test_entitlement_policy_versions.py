from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.compat import UTC
from core.entitlement_policy import (
    EntitlementPolicyError,
    canonical_public_policy,
    capability_contracts,
    current_policy,
    policy_can,
    publish_policy,
    runtime_capability_evidence,
    seed_canonical_policy,
    validate_order_policy_snapshot,
)
from core.plans import CAPABILITIES, trading_limits


MIGRATION = Path(__file__).parents[1] / "migrations" / "0035_entitlement_policy_versions.sql"
PUBLIC_PLAN_ORDER = ("免费版", "标准版", "高级版")


def _database(
    legacy_orders: tuple[tuple[str, str], ...] = (),
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    conn.execute(
        """CREATE TABLE subscription_orders(
               id INTEGER PRIMARY KEY,order_no TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL
           )"""
    )
    conn.executemany(
        "INSERT INTO subscription_orders(order_no,created_at) VALUES (?,?)",
        legacy_orders,
    )
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    return conn


def _publish(conn: sqlite3.Connection, when: datetime | None = None):
    moment = when or datetime(2026, 8, 14, tzinfo=UTC)
    return publish_policy(
        conn,
        canonical_public_policy(),
        effective_at=moment,
        created_at=moment,
    )[0]


def test_public_policy_has_exactly_three_tiers_and_no_retired_promises():
    policy = canonical_public_policy()
    assert tuple(policy["public_plan_order"]) == PUBLIC_PLAN_ORDER
    assert [item["key"] for item in policy["plans"]] == list(PUBLIC_PLAN_ORDER)
    serialized = str(policy)
    for retired in (
        "team_collaboration",
        "private_deploy",
        "multi_account",
        "auto_control_account_1",
        "auto_control_account_5",
        "liquidate_all",
        "option_auto_live",
    ):
        assert retired not in serialized


def test_live_option_is_an_application_program_not_a_membership_grant():
    conn = _database()
    now = datetime.now(UTC)
    seed_canonical_policy(conn, now=now)
    assert policy_can(conn, "高级版", "option_live_beta_apply", as_of=now)
    assert not policy_can(conn, "高级版", "option_auto_live", as_of=now)
    assert not policy_can(conn, "高级版", "option_auto", as_of=now)
    dynamic = canonical_public_policy()["dynamic_programs"]["option_live_beta"]
    assert dynamic == {
        "application_capability": "option_live_beta_apply",
        "eligible_plan": "高级版",
        "states": [
            "planned", "beta_eligible", "approved", "runtime_ready",
            "paused", "revoked",
        ],
        "membership_grants_runtime": False,
        "telegram_binding_required": True,
        "per_strategy_confirmation_required": True,
        "multi_leg_atomic_confirmation": True,
        "defined_risk_only": True,
    }
    # Legacy runtime helpers are intentionally unchanged in this first slice.
    assert trading_limits("专业版")["auto_control_accounts"] == 5


def test_retired_plans_map_to_advanced_read_compatibility_only():
    conn = _database()
    now = datetime.now(UTC)
    seed_canonical_policy(conn, now=now)
    assert CAPABILITIES["定制版"]  # legacy matrix remains untouched until consumer cutover
    for retired in ("专业版", "定制版"):
        assert policy_can(conn, retired, "tg_stock_signal", as_of=now)
        assert policy_can(conn, retired, "option_live_beta_apply", as_of=now)
        for denied in (
            "option_chain", "option_auto_live", "code_import",
            "team_collaboration", "private_deploy", "strategy_template_save",
        ):
            assert not policy_can(conn, retired, denied, as_of=now)


def test_policy_versions_are_append_only_idempotent_and_point_in_time():
    conn = _database()
    first_at = datetime(2026, 8, 14, tzinfo=UTC)
    first, created = publish_policy(
        conn, canonical_public_policy(), effective_at=first_at, created_at=first_at,
    )
    replay, replay_created = publish_policy(
        conn, canonical_public_policy(), effective_at=first_at, created_at=first_at,
    )
    assert created is True
    assert replay_created is False
    assert replay.policy_sha256 == first.policy_sha256
    assert current_policy(conn, as_of=first_at - timedelta(seconds=1)) is None
    assert current_policy(conn, as_of=first_at).version == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE membership_entitlement_policy_versions SET version=2 WHERE id=1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM membership_entitlement_policy_versions WHERE id=1")


def test_policy_effective_time_is_monotonic_and_timezone_required():
    conn = _database()
    first_at = datetime(2026, 8, 14, tzinfo=UTC)
    _publish(conn, first_at)
    second, created = publish_policy(
        conn,
        canonical_public_policy(),
        effective_at=first_at + timedelta(days=1),
        created_at=first_at,
    )
    assert created is True
    assert second.version == 2
    with pytest.raises(EntitlementPolicyError, match="生效时间必须晚于"):
        publish_policy(
            conn,
            canonical_public_policy(),
            effective_at=first_at + timedelta(hours=1),
            created_at=first_at,
        )
    with pytest.raises(EntitlementPolicyError, match="包含时区"):
        publish_policy(
            _database(),
            canonical_public_policy(),
            effective_at=datetime(2026, 8, 14),
        )


def test_policy_validation_rejects_live_grants_contract_drift_and_non_finite_json():
    policy = canonical_public_policy()
    policy["dynamic_programs"]["option_live_beta"]["membership_grants_runtime"] = True
    with pytest.raises(EntitlementPolicyError, match="不得直接授予"):
        publish_policy(
            _database(), policy, effective_at=datetime(2026, 8, 14, tzinfo=UTC),
        )

    policy = canonical_public_policy()
    policy["plans"][2]["capabilities"].append("real_trade")
    with pytest.raises(EntitlementPolicyError, match="公开合同完全一致"):
        publish_policy(
            _database(), policy, effective_at=datetime(2026, 8, 14, tzinfo=UTC),
        )

    policy = canonical_public_policy()
    policy["plans"][1]["prices"]["monthly"] = float("nan")
    with pytest.raises(EntitlementPolicyError, match="有限 JSON"):
        publish_policy(
            _database(), policy, effective_at=datetime(2026, 8, 14, tzinfo=UTC),
        )


def test_capability_projection_fails_closed_without_fresh_runtime_evidence():
    conn = _database()
    policy = _publish(conn)
    fixed_now = datetime(2026, 8, 14, 0, 5, tzinfo=UTC)
    evidence = {
        "tg_stock_signal": {
            "data_state": "ready",
            "health": "healthy",
            "verified_at": "2026-08-14T00:00:00+00:00",
        }
    }
    states = {
        item["key"]: item
        for item in capability_contracts(
            "高级版", policy=policy, runtime_evidence=evidence, now=fixed_now,
        )
    }
    assert states["option_live_beta_apply"]["status"] == "application_required"
    expired = {
        item["key"]: item
        for item in capability_contracts(
            "高级版", policy=policy, runtime_evidence=evidence,
            now=fixed_now + timedelta(seconds=1),
        )
    }
    assert expired["tg_stock_signal"]["status"] == "unavailable"
    missing = {
        item["key"]: item
        for item in capability_contracts("高级版", policy=policy, now=fixed_now)
    }
    assert missing["tg_stock_signal"]["reason_code"] == "runtime_evidence_missing"
    assert missing["tg_stock_signal"]["data_state"] == "missing"


def test_published_policy_is_historical_and_not_rebuilt_from_future_globals():
    conn = _database()
    published = _publish(conn)
    CAPABILITIES["高级版"].add("future_only_capability")
    try:
        loaded = current_policy(conn, as_of=datetime(2026, 8, 14, tzinfo=UTC))
        assert loaded.policy_sha256 == published.policy_sha256
        assert "future_only_capability" not in loaded.policy["plans"][2]["capabilities"]
    finally:
        CAPABILITIES["高级版"].discard("future_only_capability")


def test_seed_is_trusted_idempotent_and_policy_can_fails_closed_before_seed():
    conn = _database()
    assert policy_can(conn, "高级版", "tg_stock_signal") is False
    now = datetime.now(UTC)
    first = seed_canonical_policy(conn, now=now)
    second = seed_canonical_policy(conn, now=now)
    assert first.version == second.version == 1
    assert first.policy_sha256 == second.policy_sha256
    assert policy_can(conn, "高级版", "tg_stock_signal") is True


def test_seed_rejects_a_preexisting_bootstrap_v1_with_different_content():
    conn = _database()
    conn.execute(
        """INSERT INTO membership_entitlement_policy_versions(
               policy_key,version,policy_json,policy_sha256,effective_at,created_by,created_at
           ) VALUES (?,?,?,?,?,NULL,?)""",
        (
            "public_membership_v1", 1, "{}", "0" * 64,
            "2026-08-14T00:00:00+00:00", "2026-08-14T00:00:00+00:00",
        ),
    )
    with pytest.raises(EntitlementPolicyError):
        seed_canonical_policy(conn)


def test_migration_allows_staged_rollout_but_rejects_partial_or_mutated_snapshot():
    conn = _database()
    conn.execute(
        "INSERT INTO subscription_orders(order_no,created_at) VALUES (?,?)",
        ("STAGED-WITHOUT-SNAPSHOT", "2026-08-14T00:00:00+00:00"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="must be complete"):
        conn.execute(
            """INSERT INTO subscription_orders(
                   order_no,created_at,entitlement_policy_key_snapshot
               ) VALUES (?,?,?)""",
            ("PARTIAL", "2026-08-14T00:00:00+00:00", "public_membership_v1"),
        )
    conn.execute(
        """INSERT INTO subscription_orders(
               order_no,created_at,entitlement_policy_key_snapshot,
               entitlement_policy_version_snapshot,entitlement_policy_sha256_snapshot
           ) VALUES (?,?,?,?,?)""",
        ("BOUND", "2026-08-14T00:00:00+00:00", "public_membership_v1", 1, "a" * 64),
    )
    with pytest.raises(sqlite3.IntegrityError, match="snapshot is immutable"):
        conn.execute(
            "UPDATE subscription_orders SET entitlement_policy_version_snapshot=2 WHERE order_no='BOUND'"
        )


def test_admin_event_must_reference_an_exact_published_policy_proof():
    conn = _database()
    published = _publish(conn)
    conn.execute("INSERT INTO users(id) VALUES (1)")
    with pytest.raises(sqlite3.IntegrityError, match="proof is invalid"):
        conn.execute(
            """INSERT INTO membership_entitlement_policy_admin_events(
                   actor_id,idempotency_key,request_sha256,policy_key,policy_version,
                   policy_sha256,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (1, "request-1", "a" * 64, published.policy_key,
             published.version, "0" * 64, "2026-08-14T00:00:00+00:00"),
        )


def test_legacy_allowlist_is_sealed_and_binds_only_bootstrap_v1():
    conn = _database((("LEGACY", "2026-08-13T00:00:00+00:00"),))
    for operation in (
        "INSERT INTO membership_entitlement_legacy_orders(order_no,recorded_at) VALUES ('X','2026-08-14T00:00:00Z')",
        "UPDATE membership_entitlement_legacy_orders SET recorded_at=recorded_at WHERE order_no='LEGACY'",
        "DELETE FROM membership_entitlement_legacy_orders WHERE order_no='LEGACY'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="allowlist is sealed"):
            conn.execute(operation)

    first = seed_canonical_policy(conn, now=datetime(2026, 8, 14, tzinfo=UTC))
    publish_policy(
        conn,
        canonical_public_policy(),
        effective_at=datetime(2026, 8, 15, tzinfo=UTC),
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    order = dict(conn.execute("SELECT * FROM subscription_orders WHERE order_no='LEGACY'").fetchone())
    bound = validate_order_policy_snapshot(conn, order)
    assert bound.version == first.version == 1
    stored = conn.execute(
        "SELECT entitlement_policy_version_snapshot FROM subscription_orders WHERE order_no='LEGACY'"
    ).fetchone()
    assert stored["entitlement_policy_version_snapshot"] == 1


def test_order_snapshot_rejects_missing_partial_and_tampered_proof():
    conn = _database()
    published = _publish(conn)
    complete = {
        "order_no": "ORDER-1",
        "created_at": "2026-08-14T00:00:00+00:00",
        "entitlement_policy_key_snapshot": published.policy_key,
        "entitlement_policy_version_snapshot": published.version,
        "entitlement_policy_sha256_snapshot": published.policy_sha256,
    }
    assert validate_order_policy_snapshot(conn, complete).version == 1
    partial = dict(complete, entitlement_policy_sha256_snapshot=None)
    with pytest.raises(EntitlementPolicyError, match="不完整"):
        validate_order_policy_snapshot(conn, partial)
    tampered = dict(complete, entitlement_policy_sha256_snapshot="0" * 64)
    with pytest.raises(EntitlementPolicyError, match="哈希不一致"):
        validate_order_policy_snapshot(conn, tampered)


def test_runtime_evidence_reads_only_persisted_successful_observations():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """CREATE TABLE official_option_sim_event_legs(
               id INTEGER PRIMARY KEY,quote_at TEXT NOT NULL
           );
           CREATE TABLE earnings_data_snapshots(
               id INTEGER PRIMARY KEY,observed_at TEXT NOT NULL,dq_status TEXT NOT NULL
           );"""
    )
    conn.execute(
        "INSERT INTO official_option_sim_event_legs(quote_at) VALUES (?)",
        ("2026-08-14T00:00:00+00:00",),
    )
    conn.execute(
        "INSERT INTO earnings_data_snapshots(observed_at,dq_status) VALUES (?,?)",
        ("2026-08-14T00:01:00+00:00", "FAIL"),
    )
    evidence = runtime_capability_evidence(conn)
    assert evidence["option_chain"]["verified_at"] == "2026-08-14T00:00:00+00:00"
    assert "earnings_forecast" not in evidence
    assert "signal_web" not in evidence
