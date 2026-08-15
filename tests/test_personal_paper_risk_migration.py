from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from core.database import DatabaseManager


MIGRATION_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATIONS = (
    MIGRATION_DIR / "0034_personal_paper.sql",
    MIGRATION_DIR / "0035_entitlement_policy_versions.sql",
    MIGRATION_DIR / "0036_personal_paper_risk_proofs.sql",
)
COMPUTED_AT = "2026-08-15T01:02:03.456789Z"
EXPIRES_AT = "2026-08-15T01:02:18.456789Z"


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE user_membership_logs(id INTEGER PRIMARY KEY,admin_id INTEGER)"
    )
    connection.execute(
        "CREATE TABLE subscription_orders(id INTEGER PRIMARY KEY,order_no TEXT UNIQUE,created_at TEXT)"
    )
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    connection.executemany("INSERT INTO users(id) VALUES (?)", ((1,), (2,)))
    connection.execute(
        """INSERT INTO personal_paper_seasons
           (id,user_id,season_number,state,currency,initial_cash_minor,started_at,created_at)
           VALUES('season-1',1,1,'active','USD',1000000,?,?)""",
        (COMPUTED_AT, COMPUTED_AT),
    )
    connection.execute(
        """INSERT INTO personal_paper_quote_proofs
           (public_id,user_id,season_id,schema_version,nonce,claims_json,signature_sha256,
            issued_at,expires_at,created_at)
           VALUES('quote-1',1,'season-1','q1','quote_nonce_000001','{}',?, ?, ?, ?)""",
        ("a" * 64, COMPUTED_AT, EXPIRES_AT, COMPUTED_AT),
    )
    return connection


def _insert_risk_proof(connection: sqlite3.Connection, **changes: str | int) -> None:
    values: dict[str, str | int] = {
        "public_id": "risk-proof-1",
        "user_id": 1,
        "season_id": "season-1",
        "quote_id": "quote-1",
        "account_version": 0,
        "draft_sha256": "b" * 64,
        "schema_version": "r1",
        "computed_at": COMPUTED_AT,
        "marks_as_of": COMPUTED_AT,
        "created_at": COMPUTED_AT,
        "expires_at": EXPIRES_AT,
        "decision": "review",
        "risk_level": "moderate",
        "data_state": "missing",
        "checks_json": "[]",
        "blocking_reasons_json": "[]",
        "warnings_json": "[]",
        "proof_payload_json": "{}",
        "proof_sha256": "d" * 64,
    }
    values.update(changes)
    columns = tuple(values)
    connection.execute(
        f"INSERT INTO personal_paper_risk_proofs ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _insert_consumption(connection: sqlite3.Connection, **changes: str | int) -> None:
    values: dict[str, str | int] = {
        "proof_id": "risk-proof-1",
        "user_id": 1,
        "season_id": "season-1",
        "draft_sha256": "b" * 64,
        "idempotency_key": "risk-consumption-1",
        "consumed_at": COMPUTED_AT,
    }
    values.update(changes)
    columns = tuple(values)
    connection.execute(
        f"INSERT INTO personal_paper_risk_proof_consumptions ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _insert_event(connection: sqlite3.Connection, **changes: str | int) -> None:
    values: dict[str, str | int] = {
        "public_id": "risk-event-1",
        "proof_id": "risk-proof-1",
        "user_id": 1,
        "season_id": "season-1",
        "event_type": "ISSUED",
        "payload_json": "{}",
        "occurred_at": COMPUTED_AT,
        "payload_sha256": "c" * 64,
    }
    values.update(changes)
    columns = tuple(values)
    connection.execute(
        f"INSERT INTO personal_paper_risk_proof_events ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def test_personal_paper_risk_migration_runs_after_0034_and_0035_without_dropping_prior_schema():
    assert tuple(migration.name for migration in MIGRATIONS) == (
        "0034_personal_paper.sql",
        "0035_entitlement_policy_versions.sql",
        "0036_personal_paper_risk_proofs.sql",
    )
    connection = _database()
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "personal_paper_seasons",
        "personal_paper_quote_proofs",
        "personal_paper_risk_proofs",
        "personal_paper_risk_proof_consumptions",
        "personal_paper_risk_proof_events",
        "membership_entitlement_policy_versions",
    } <= tables
    assert {
        row["name"]
        for row in connection.execute("PRAGMA table_info(subscription_orders)")
    } >= {
        "entitlement_policy_key_snapshot",
        "entitlement_policy_version_snapshot",
        "entitlement_policy_sha256_snapshot",
        "entitlement_purchase_action_snapshot",
    }


def test_database_runner_records_0034_through_0036_once_in_order(tmp_path):
    database_path = str(tmp_path / "risk-migration-order.db")
    database = DatabaseManager(database_path)
    expected = [migration.name for migration in MIGRATIONS]
    versions = database.fetch_all(
        """SELECT version FROM schema_migrations
           WHERE version IN (?,?,?) ORDER BY rowid""",
        tuple(expected),
    )
    assert [row["version"] for row in versions] == expected

    reopened = DatabaseManager(database_path)
    counts = reopened.fetch_all(
        """SELECT version,COUNT(*) AS count FROM schema_migrations
           WHERE version IN (?,?,?) GROUP BY version ORDER BY version""",
        tuple(expected),
    )
    assert counts == [{"version": version, "count": 1} for version in expected]


def test_risk_proof_quote_foreign_key_and_owner_are_bound():
    connection = _database()
    foreign_keys = {
        (row["from"], row["table"], row["to"])
        for row in connection.execute(
            "PRAGMA foreign_key_list(personal_paper_risk_proofs)"
        )
    }
    assert ("quote_id", "personal_paper_quote_proofs", "public_id") in foreign_keys

    with pytest.raises(sqlite3.IntegrityError, match="proof owner mismatch"):
        _insert_risk_proof(connection, public_id="risk-proof-cross-owner", user_id=2)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_risk_proof(connection, public_id="risk-proof-missing-quote", quote_id="missing")


def test_risk_proof_consumption_and_event_owner_mismatch_fail_closed():
    connection = _database()
    _insert_risk_proof(connection)
    with pytest.raises(sqlite3.IntegrityError, match="owner mismatch"):
        _insert_consumption(connection, user_id=2)
    with pytest.raises(sqlite3.IntegrityError, match="owner mismatch"):
        _insert_event(connection, user_id=2)


def test_risk_proof_time_ordering_is_enforced():
    connection = _database()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_risk_proof(
            connection,
            public_id="risk-proof-expired",
            expires_at=COMPUTED_AT,
        )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_risk_proof(
            connection,
            public_id="risk-proof-future-mark",
            marks_as_of=EXPIRES_AT,
        )


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("checks_json", "not-json"),
        ("blocking_reasons_json", "not-json"),
        ("warnings_json", "not-json"),
        ("proof_payload_json", "[]"),
    ),
)
def test_risk_proof_json_fields_have_required_types(column: str, value: str):
    connection = _database()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_risk_proof(
            connection,
            public_id=f"risk-proof-invalid-{column}",
            **{column: value},
        )


def test_risk_proof_event_payload_must_be_an_object():
    connection = _database()
    _insert_risk_proof(connection)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_event(connection, payload_json="[]")


def test_risk_proof_tables_are_append_only():
    connection = _database()
    _insert_risk_proof(connection)
    _insert_consumption(connection)
    _insert_event(connection)
    statements = (
        "UPDATE personal_paper_risk_proofs SET warnings_json='[]' WHERE public_id='risk-proof-1'",
        "DELETE FROM personal_paper_risk_proofs WHERE public_id='risk-proof-1'",
        "UPDATE personal_paper_risk_proof_consumptions SET idempotency_key='changed' WHERE proof_id='risk-proof-1'",
        "DELETE FROM personal_paper_risk_proof_consumptions WHERE proof_id='risk-proof-1'",
        "UPDATE personal_paper_risk_proof_events SET payload_json='{}' WHERE public_id='risk-event-1'",
        "DELETE FROM personal_paper_risk_proof_events WHERE public_id='risk-event-1'",
    )
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
