from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from core.auth import AuthService
import core.database as database_module
from core.database import DatabaseManager


LATEST_MIGRATION = "0033_membership_promotion_settlement.sql"
LATEST_TABLES = {
    "referral_bonus_periods",
    "referral_bonus_contributors",
    "referral_bonus_award_events",
}
LATEST_TRIGGERS = {
    "trg_referral_bonus_periods_policy_immutable",
    "trg_referral_bonus_periods_no_delete",
}
LATEST_INDEXES = {
    "idx_referral_bonus_contributors_period",
    "idx_referral_bonus_awards_due",
}


def _latest_schema(database: DatabaseManager) -> dict[str, object]:
    order_columns = {
        row["name"]: (row["type"], row["notnull"], row["dflt_value"])
        for row in database.fetch_all("PRAGMA table_info(subscription_orders)")
        if row["name"] in {
            "referral_bonus_policy_snapshot", "refunded_minor", "promotion_snapshot_sha256",
            "referral_attribution_id_snapshot", "referral_referrer_user_id_snapshot",
            "referral_referred_user_id_snapshot",
        }
    }
    withdrawal_columns = {
        row["name"]: (row["type"], row["notnull"], row["dflt_value"])
        for row in database.fetch_all("PRAGMA table_info(referral_withdrawal_requests)")
        if row["name"] == "enhanced_review_required"
    }
    objects = {
        row["name"]: " ".join(str(row["sql"] or "").split())
        for row in database.fetch_all(
            """SELECT name,sql FROM sqlite_master
               WHERE name IN (?,?,?,?,?,?,?) ORDER BY name""",
            tuple(sorted(LATEST_TABLES | LATEST_TRIGGERS | LATEST_INDEXES)),
        )
    }
    return {
        "order_columns": order_columns,
        "withdrawal_columns": withdrawal_columns,
        "objects": objects,
    }


def _copy_legacy_migrations(target: Path) -> None:
    target.mkdir()
    for source in sorted(database_module.MIGRATIONS_DIR.glob("*.sql")):
        if source.name >= LATEST_MIGRATION:
            continue
        shutil.copy2(source, target / source.name)


def test_membership_promotion_migration_upgrades_recorded_0032_without_data_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_migrations = database_module.MIGRATIONS_DIR
    legacy_migrations = tmp_path / "legacy-migrations"
    _copy_legacy_migrations(legacy_migrations)
    database_path = tmp_path / "legacy.db"

    monkeypatch.setattr(database_module, "MIGRATIONS_DIR", legacy_migrations)
    legacy = DatabaseManager(str(database_path))
    user = AuthService(legacy).register(
        "promotion-migration@example.com", "CorrectHorse123", "Promotion Migration", True
    )
    legacy.execute(
        """INSERT INTO subscription_orders
           (order_no,user_id,plan_type,billing_cycle,amount,currency,pay_method,status,created_at,amount_minor)
           VALUES ('PROMO-LEGACY-ORDER',?,'标准版','monthly',298.0,'HKD','paypal','paid',CURRENT_TIMESTAMP,29800)""",
        (user["id"],),
    )
    legacy.execute(
        """INSERT INTO referral_withdrawal_requests
           (public_id,user_id,amount_minor,currency,status,idempotency_key,request_fingerprint,submitted_at)
           VALUES ('WDRLEGACYPROMOTION0000000001',?,20000,'HKD','submitted','legacy-promotion-withdrawal',?,CURRENT_TIMESTAMP)""",
        (user["id"], "a" * 64),
    )
    qualification_id = legacy.execute(
        """INSERT INTO referral_bonus_qualifications
           (referrer_user_id,period_key,qualified_count,status,policy_version)
           VALUES (?,'2026-08',2,'disabled','1')""",
        (user["id"],),
    )
    legacy.execute(
        """INSERT INTO referral_bonus_awards
           (qualification_id,amount_minor,status,available_at)
           VALUES (?,10000,'pending',CURRENT_TIMESTAMP)""",
        (qualification_id,),
    )
    assert legacy.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0032_membership_promotions.sql'"
    )
    assert not legacy.fetch_one(
        "SELECT version FROM schema_migrations WHERE version=?", (LATEST_MIGRATION,)
    )

    monkeypatch.setattr(database_module, "MIGRATIONS_DIR", repository_migrations)
    upgraded = DatabaseManager(str(database_path))
    DatabaseManager(str(database_path))

    assert upgraded.fetch_one(
        "SELECT version FROM schema_migrations WHERE version=?", (LATEST_MIGRATION,)
    )
    assert upgraded.fetch_one(
        "SELECT COUNT(*) count FROM schema_migrations WHERE version=?", (LATEST_MIGRATION,)
    )["count"] == 1
    assert upgraded.fetch_one(
        """SELECT order_no,amount_minor,referral_bonus_policy_snapshot,refunded_minor
           FROM subscription_orders WHERE order_no='PROMO-LEGACY-ORDER'"""
    ) == {
        "order_no": "PROMO-LEGACY-ORDER",
        "amount_minor": 29800,
        "referral_bonus_policy_snapshot": None,
        "refunded_minor": 0,
    }
    assert upgraded.fetch_one(
        """SELECT public_id,amount_minor,enhanced_review_required
           FROM referral_withdrawal_requests WHERE public_id='WDRLEGACYPROMOTION0000000001'"""
    ) == {
        "public_id": "WDRLEGACYPROMOTION0000000001",
        "amount_minor": 20000,
        "enhanced_review_required": 0,
    }
    assert upgraded.fetch_one(
        "SELECT qualified_count,status FROM referral_bonus_qualifications WHERE id=?",
        (qualification_id,),
    ) == {"qualified_count": 2, "status": "disabled"}
    assert upgraded.fetch_one(
        "SELECT amount_minor,status FROM referral_bonus_awards WHERE qualification_id=?",
        (qualification_id,),
    ) == {"amount_minor": 10000, "status": "pending"}


def test_membership_promotion_fresh_and_upgraded_schema_match_and_lock_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_migrations = database_module.MIGRATIONS_DIR
    legacy_migrations = tmp_path / "legacy-migrations"
    _copy_legacy_migrations(legacy_migrations)

    monkeypatch.setattr(database_module, "MIGRATIONS_DIR", legacy_migrations)
    upgraded_path = tmp_path / "upgraded.db"
    DatabaseManager(str(upgraded_path))
    monkeypatch.setattr(database_module, "MIGRATIONS_DIR", repository_migrations)
    upgraded = DatabaseManager(str(upgraded_path))
    fresh = DatabaseManager(str(tmp_path / "fresh.db"))

    assert _latest_schema(upgraded) == _latest_schema(fresh)
    assert set(_latest_schema(fresh)["objects"]) == LATEST_TABLES | LATEST_TRIGGERS | LATEST_INDEXES

    owner = AuthService(upgraded).register(
        "promotion-period-owner@example.com", "CorrectHorse123", "Promotion Period Owner", True
    )
    snapshot = {"enabled": True, "hold_days": 30, "tiers": [], "version": 1}
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    upgraded.execute(
        """INSERT INTO referral_bonus_periods
           (referrer_user_id,period_key,policy_version,policy_snapshot_json,policy_sha256,hold_days,locked_at)
           VALUES (?,'2026-08','1',?,?,30,CURRENT_TIMESTAMP)""",
        (owner["id"], canonical, digest),
    )
    with pytest.raises(sqlite3.IntegrityError, match="policy is immutable"):
        with upgraded.transaction() as conn:
            conn.execute(
                "UPDATE referral_bonus_periods SET policy_version='2' WHERE referrer_user_id=?",
                (owner["id"],),
            )
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        with upgraded.transaction() as conn:
            conn.execute(
                "DELETE FROM referral_bonus_periods WHERE referrer_user_id=?", (owner["id"],)
            )


def test_production_0034_state_accepts_late_0032_0033_without_personal_data_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_migrations = database_module.MIGRATIONS_DIR
    production_migrations = tmp_path / "production-migrations"
    production_migrations.mkdir()
    for source in sorted(repository_migrations.glob("*.sql")):
        if source.name <= "0025_referral_affiliate_cash.sql" or source.name == "0034_personal_paper.sql":
            shutil.copy2(source, production_migrations / source.name)

    database_path = tmp_path / "production-0034.db"
    monkeypatch.setattr(database_module, "MIGRATIONS_DIR", production_migrations)
    production = DatabaseManager(str(database_path))
    owner = AuthService(production).register(
        "promotion-production@example.com", "CorrectHorse123", "Promotion Production", True
    )
    production.execute(
        """INSERT INTO personal_paper_seasons
           (id,user_id,season_number,state,currency,initial_cash_minor,started_at,created_at)
           VALUES ('PPS-PRODUCTION-KEEP',?,1,'active','USD',1000000,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
        (owner["id"],),
    )
    before = production.fetch_one(
        "SELECT id,user_id,initial_cash_minor FROM personal_paper_seasons WHERE id='PPS-PRODUCTION-KEEP'"
    )
    assert production.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0034_personal_paper.sql'"
    )
    assert not production.fetch_one(
        "SELECT version FROM schema_migrations WHERE version='0032_membership_promotions.sql'"
    )

    shutil.copy2(
        repository_migrations / "0032_membership_promotions.sql",
        production_migrations / "0032_membership_promotions.sql",
    )
    shutil.copy2(
        repository_migrations / "0033_membership_promotion_settlement.sql",
        production_migrations / "0033_membership_promotion_settlement.sql",
    )
    upgraded = DatabaseManager(str(database_path))
    DatabaseManager(str(database_path))

    assert upgraded.fetch_one(
        "SELECT id,user_id,initial_cash_minor FROM personal_paper_seasons WHERE id='PPS-PRODUCTION-KEEP'"
    ) == before
    versions = upgraded.fetch_all(
        """SELECT version,COUNT(*) count FROM schema_migrations
           WHERE version IN ('0032_membership_promotions.sql',
                             '0033_membership_promotion_settlement.sql',
                             '0034_personal_paper.sql')
           GROUP BY version ORDER BY version"""
    )
    assert versions == [
        {"version": "0032_membership_promotions.sql", "count": 1},
        {"version": "0033_membership_promotion_settlement.sql", "count": 1},
        {"version": "0034_personal_paper.sql", "count": 1},
    ]
