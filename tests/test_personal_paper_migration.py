from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "0034_personal_paper.sql"


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY); INSERT INTO users(id) VALUES(1),(2);"
    )
    connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    return connection


def test_personal_paper_migration_is_isolated_and_first_season_is_10k() -> None:
    connection = _database()
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'personal_paper_%'"
        )
    }
    assert {
        "personal_paper_seasons",
        "personal_paper_quote_proofs",
        "personal_paper_quote_consumptions",
        "personal_paper_orders",
        "personal_paper_order_events",
        "personal_paper_fills",
        "personal_paper_account_events",
        "personal_paper_risk_events",
        "personal_paper_equity_events",
    } <= tables
    connection.execute(
        """INSERT INTO personal_paper_seasons
           (id,user_id,season_number,state,currency,initial_cash_minor,started_at,created_at)
           VALUES('season-1',1,1,'active','USD',1000000,'2026-08-13T00:00:00Z','2026-08-13T00:00:00Z')"""
    )
    try:
        connection.execute(
            """INSERT INTO personal_paper_seasons
               (id,user_id,season_number,state,currency,initial_cash_minor,started_at,created_at)
               VALUES('bad',2,1,'active','USD',999999,'2026-08-13T00:00:00Z','2026-08-13T00:00:00Z')"""
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("first personal paper season must be exactly USD 10,000")


def test_personal_paper_ledger_rows_are_append_only() -> None:
    connection = _database()
    connection.execute(
        """INSERT INTO personal_paper_seasons
           (id,user_id,season_number,state,currency,initial_cash_minor,started_at,created_at)
           VALUES('season-1',1,1,'active','USD',1000000,'2026-08-13T00:00:00Z','2026-08-13T00:00:00Z')"""
    )
    connection.execute(
        """INSERT INTO personal_paper_account_events
           (public_id,season_id,sequence,event_type,cash_delta_minor,occurred_at,payload_sha256)
           VALUES('event-1','season-1',0,'SEASON_OPENED',0,'2026-08-13T00:00:00Z,','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"""
    )
    for statement in (
        "UPDATE personal_paper_account_events SET cash_delta_minor=1 WHERE public_id='event-1'",
        "DELETE FROM personal_paper_account_events WHERE public_id='event-1'",
    ):
        try:
            connection.execute(statement)
        except sqlite3.IntegrityError:
            continue
        raise AssertionError("personal paper ledger must be append-only")


def test_quote_proof_schema_binds_owner_and_is_append_only() -> None:
    connection = _database()
    connection.execute(
        """INSERT INTO personal_paper_seasons
           (id,user_id,season_number,state,currency,initial_cash_minor,started_at,created_at)
           VALUES('season-1',1,1,'active','USD',1000000,
                  '2026-08-13T00:00:00.000001Z','2026-08-13T00:00:00.000001Z')"""
    )
    connection.execute(
        """INSERT INTO personal_paper_quote_proofs
           (public_id,user_id,season_id,schema_version,nonce,claims_json,signature_sha256,
            issued_at,expires_at,created_at)
           VALUES('proof-1',1,'season-1','q1','nonce_1234567890','{}',?,
                  '2026-08-13T00:00:00.000001Z','2026-08-13T00:00:15.000001Z',
                  '2026-08-13T00:00:00.000001Z')""",
        ("a" * 64,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="proof owner mismatch"):
        connection.execute(
            """INSERT INTO personal_paper_quote_proofs
               (public_id,user_id,season_id,schema_version,nonce,claims_json,signature_sha256,
                issued_at,expires_at,created_at)
               VALUES('proof-cross',2,'season-1','q1','nonce_1234567891','{}',?,
                      '2026-08-13T00:00:00.000001Z','2026-08-13T00:00:15.000001Z',
                      '2026-08-13T00:00:00.000001Z')""",
            ("a" * 64,),
        )
    for statement in (
        "UPDATE personal_paper_quote_proofs SET claims_json='[]' WHERE public_id='proof-1'",
        "DELETE FROM personal_paper_quote_proofs WHERE public_id='proof-1'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
    with pytest.raises(sqlite3.IntegrityError, match="owner mismatch"):
        connection.execute(
            """INSERT INTO personal_paper_quote_consumptions
               (proof_id,user_id,season_id,request_sha256,consumed_at)
               VALUES('proof-1',2,'season-1',?,'2026-08-13T00:00:01.000001Z')""",
            ("b" * 64,),
        )
