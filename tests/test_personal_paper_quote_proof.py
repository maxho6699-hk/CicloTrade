from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from core.personal_paper.quote_proof import QuoteProofError, QuoteProofSignerVerifier
from core.personal_paper.service import PersonalPaperService, PersonalPaperRiskRejected, VerifiedQuote


NOW = datetime(2026, 8, 13, 1, 2, 3, 456789, tzinfo=timezone.utc)
SECRET = b"personal-paper-quote-proof-test-key"


def _account(tmp_path, *, email="proof@example.com"):
    database = DatabaseManager(str(tmp_path / "proof.db"))
    user = AuthService(database).register(email, "StrongPass123", "Proof", True)
    season_id = f"season-{user['id']}"
    stamp = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    database.execute(
        """INSERT INTO personal_paper_seasons
           (id,user_id,season_number,state,currency,initial_cash_minor,version,started_at,created_at)
           VALUES(?,?,1,'active','USD',1000000,0,?,?)""",
        (season_id, user["id"], stamp, stamp),
    )
    return database, int(user["id"]), season_id


def _signer(database, nonce="quote_nonce_20260813"):
    return QuoteProofSignerVerifier(database, SECRET, nonce_factory=lambda: nonce)


def _issue(signer, user_id, **changes):
    values = {
        "user_id": user_id,
        "market": "US",
        "symbol": "AAPL",
        "bid_minor": 19_990,
        "ask_minor": 20_000,
        "last_minor": 19_995,
        "as_of": NOW,
        "now": NOW,
    }
    values.update(changes)
    return signer.issue(**values)


def _consume(database, signer, proof_id, user_id, season_id, **changes):
    values = {
        "user_id": user_id,
        "season_id": season_id,
        "market": "US",
        "symbol": "AAPL",
        "now": NOW,
        "request_sha256": "a" * 64,
    }
    values.update(changes)
    with database._get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = signer.verify_and_consume(proof_id, connection=connection, **values)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def test_persisted_proof_round_trips_after_signer_restart_and_keeps_microseconds(tmp_path):
    database, user_id, season_id = _account(tmp_path)
    proof_id = _issue(_signer(database), user_id)
    result = _consume(database, QuoteProofSignerVerifier(database, SECRET), proof_id, user_id, season_id)

    assert result == VerifiedQuote(
        proof_id=proof_id,
        market="US",
        symbol="AAPL",
        bid_minor=19_990,
        ask_minor=20_000,
        last_minor=19_995,
        as_of=NOW,
        state="fresh",
        commission_minor=0,
    )
    row = database.fetch_one(
        "SELECT user_id,season_id,issued_at,expires_at FROM personal_paper_quote_proofs"
    )
    assert row["user_id"] == user_id and row["season_id"] == season_id
    assert row["issued_at"] == "2026-08-13T01:02:03.456789Z"
    assert row["expires_at"] == "2026-08-13T01:02:18.456789Z"


def test_real_order_transaction_consumes_proof_only_when_order_commits(tmp_path):
    database = DatabaseManager(str(tmp_path / "order-proof.db"))
    user = AuthService(database).register("order-proof@example.com", "StrongPass123", "Proof", True)
    signer = _signer(database)
    service = PersonalPaperService(database, signer, clock=lambda: NOW)
    season = service.create_first_season(int(user["id"]))
    proof_id = _issue(signer, int(user["id"]))
    request = {
        "idempotency_key": "proof-order-001",
        "season_id": season["id"],
        "market": "US",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 1,
        "limit_price": None,
        "stop_price": None,
        "time_in_force": "DAY",
        "quote_id": proof_id,
        "account_version": 0,
        "source_context": {"kind": "manual", "reference_id": None},
    }
    risk_proof = service.issue_risk_proof(
        int(user["id"]), {key: value for key, value in request.items() if key != "idempotency_key"}
    )
    request["risk_proof_id"] = risk_proof["id"]

    result = service.submit_stock_order(int(user["id"]), request)
    assert result["order"]["status"] == "FILLED"
    assert database.fetch_one(
        "SELECT proof_id,user_id,season_id FROM personal_paper_quote_consumptions"
    ) == {"proof_id": proof_id, "user_id": user["id"], "season_id": season["id"]}
    with pytest.raises(PersonalPaperRiskRejected):
        service.submit_stock_order(
            int(user["id"]), {**request, "idempotency_key": "proof-order-replay", "account_version": 1},
        )


def test_proof_is_bound_to_user_and_active_account(tmp_path):
    database, owner, season_id = _account(tmp_path)
    other = AuthService(database).register("other@example.com", "StrongPass123", "Other", True)
    other_season = f"season-{other['id']}"
    stamp = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    database.execute(
        """INSERT INTO personal_paper_seasons
           (id,user_id,season_number,state,currency,initial_cash_minor,version,started_at,created_at)
           VALUES(?,?,1,'active','USD',1000000,0,?,?)""",
        (other_season, other["id"], stamp, stamp),
    )
    proof_id = _issue(_signer(database), owner)

    with pytest.raises(QuoteProofError):
        _consume(database, _signer(database), proof_id, int(other["id"]), other_season)
    with pytest.raises(QuoteProofError):
        _consume(database, _signer(database), proof_id, owner, other_season)

    database.execute(
        "UPDATE personal_paper_seasons SET state='closed',closed_at=? WHERE id=?",
        (stamp, season_id),
    )
    with pytest.raises(QuoteProofError):
        _consume(database, _signer(database), proof_id, owner, season_id)


def test_proof_can_be_consumed_once_and_rollback_does_not_burn_it(tmp_path):
    database, user_id, season_id = _account(tmp_path)
    proof_id = _issue(_signer(database), user_id)

    with database._get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _signer(database).verify_and_consume(
            proof_id, user_id=user_id, season_id=season_id, market="US", symbol="AAPL",
            now=NOW, connection=connection, request_sha256="b" * 64,
        )
        connection.rollback()

    assert _consume(database, _signer(database), proof_id, user_id, season_id)
    with pytest.raises(QuoteProofError):
        _consume(database, _signer(database), proof_id, user_id, season_id)
    assert database.fetch_one("SELECT COUNT(*) count FROM personal_paper_quote_consumptions")["count"] == 1


def test_concurrent_consumers_have_exactly_one_winner(tmp_path):
    database, user_id, season_id = _account(tmp_path)
    proof_id = _issue(_signer(database), user_id)

    def consume(index):
        try:
            _consume(
                database, QuoteProofSignerVerifier(database, SECRET), proof_id, user_id, season_id,
                request_sha256=f"{index + 1:064x}",
            )
            return "consumed"
        except QuoteProofError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, range(2)))
    assert sorted(outcomes) == ["consumed", "rejected"]


def test_tampering_forgery_replay_and_direct_cross_owner_sql_fail_closed(tmp_path):
    database, user_id, season_id = _account(tmp_path)
    signer = _signer(database)
    proof_id = _issue(signer, user_id)
    tampered = proof_id[:-1] + ("0" if proof_id[-1] != "0" else "1")
    with pytest.raises(QuoteProofError):
        _consume(database, signer, tampered, user_id, season_id)

    with database._get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM personal_paper_quote_proofs WHERE public_id=?", (proof_id,)
        ).fetchone()
        claims = json.loads(row["claims_json"])
        claims["ask_minor"] += 1
        forged = dict(row)
        forged["public_id"] = "q1.forged_quote_nonce_01." + "f" * 64
        forged["nonce"] = "forged_quote_nonce_01"
        forged["claims_json"] = json.dumps(claims, sort_keys=True, separators=(",", ":"))
        forged["signature_sha256"] = "f" * 64
        connection.execute(
            """INSERT INTO personal_paper_quote_proofs
               (public_id,user_id,season_id,schema_version,nonce,claims_json,signature_sha256,
                issued_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            tuple(forged[key] for key in (
                "public_id", "user_id", "season_id", "schema_version", "nonce", "claims_json",
                "signature_sha256", "issued_at", "expires_at", "created_at",
            )),
        )
        connection.commit()
    with pytest.raises(QuoteProofError):
        _consume(database, signer, forged["public_id"], user_id, season_id)

    other = AuthService(database).register("sql-other@example.com", "StrongPass123", "Other", True)
    with database._get_connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="owner mismatch"):
            connection.execute(
                """INSERT INTO personal_paper_quote_consumptions
                   (proof_id,user_id,season_id,request_sha256,consumed_at) VALUES(?,?,?,?,?)""",
                (proof_id, other["id"], season_id, "c" * 64, NOW.isoformat()),
            )


def test_expiry_and_quote_age_use_full_precision(tmp_path):
    database, user_id, season_id = _account(tmp_path)
    signer = _signer(database)
    proof_id = _issue(signer, user_id, ttl_seconds=15)
    before_expiry = NOW + timedelta(seconds=14, microseconds=999999)
    assert _consume(database, signer, proof_id, user_id, season_id, now=before_expiry)

    second = _issue(_signer(database, "quote_nonce_20260814"), user_id, ttl_seconds=15)
    with pytest.raises(QuoteProofError):
        _consume(database, signer, second, user_id, season_id, now=NOW + timedelta(seconds=15))
    with pytest.raises(QuoteProofError):
        _issue(_signer(database, "quote_nonce_20260815"), user_id, as_of=NOW - timedelta(seconds=30, microseconds=1))


def test_issue_requires_account_and_strict_inputs(tmp_path):
    database = DatabaseManager(str(tmp_path / "empty.db"))
    user = AuthService(database).register("empty@example.com", "StrongPass123", "Empty", True)
    with pytest.raises(QuoteProofError, match="个人模拟账户"):
        _issue(_signer(database), int(user["id"]))
    for secret in (b"", b"x" * 31, "x" * 32):
        with pytest.raises(QuoteProofError):
            QuoteProofSignerVerifier(database, secret)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    (
        {"market": "CN"}, {"symbol": "aapl"}, {"bid_minor": True},
        {"ask_minor": 20_000.5}, {"last_minor": 0}, {"bid_minor": 20_001},
        {"now": NOW.replace(tzinfo=None)}, {"ttl_seconds": True}, {"ttl_seconds": 31},
    ),
)
def test_issue_rejects_invalid_claims(tmp_path, changes):
    database, user_id, _ = _account(tmp_path)
    with pytest.raises(QuoteProofError):
        _issue(_signer(database), user_id, **changes)
