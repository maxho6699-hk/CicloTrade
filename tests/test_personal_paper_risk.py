from datetime import datetime, timezone

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from core.personal_paper import PersonalPaperRiskRejected, PersonalPaperService
from core.personal_paper.quote_proof import QuoteProofSignerVerifier


NOW = datetime(2026, 8, 15, 1, 2, 3, tzinfo=timezone.utc)
SECRET = b"personal-paper-risk-proof-test-key-32bytes!"


def _base(tmp_path):
    database = DatabaseManager(str(tmp_path / "risk.db"))
    user = AuthService(database).register("risk@example.com", "StrongPass123", "Risk", True)
    signer = QuoteProofSignerVerifier(database, SECRET)
    service = PersonalPaperService(database, signer, clock=lambda: NOW)
    season = service.create_first_season(int(user["id"]))
    quote_id = signer.issue(
        user_id=int(user["id"]), market="US", symbol="AAPL", bid_minor=9_900,
        ask_minor=10_000, last_minor=9_950, as_of=NOW, now=NOW,
    )
    draft = {
        "season_id": season["id"], "market": "US", "symbol": "AAPL", "side": "BUY",
        "order_type": "MARKET", "quantity": 1, "limit_price": None, "stop_price": None,
        "time_in_force": "DAY", "quote_id": quote_id, "account_version": 0,
        "source_context": {"kind": "manual", "reference_id": None},
    }
    return database, int(user["id"]), service, season, draft


def test_risk_proof_shape_and_unknown_boundaries_are_explicit(tmp_path):
    _, user_id, service, _, draft = _base(tmp_path)
    proof = service.issue_risk_proof(user_id, draft)
    assert {
        "id", "schema_version", "season_id", "quote_id", "account_version", "draft_sha256",
        "created_at", "expires_at", "decision", "risk_level", "data_state", "checks",
        "blocking_reasons", "warnings",
    }.issubset(proof)
    assert proof["schema_version"] == "r1"
    assert proof["decision"] == "review"
    assert proof["data_state"] == "missing"
    assert [item["code"] for item in proof["checks"]] == [
        "buying_power", "max_loss", "position_concentration", "sector_concentration",
        "drawdown", "event_gap", "liquidity",
    ]
    assert all(set(item) == {"code", "status", "title", "detail", "value", "limit", "data_state"} for item in proof["checks"])
    assert all(isinstance(item["value"], (str, type(None))) for item in proof["checks"])


def test_risk_proof_binds_order_fields_and_submit_requires_it(tmp_path):
    _, user_id, service, season, draft = _base(tmp_path)
    with pytest.raises(PersonalPaperRiskRejected):
        service.submit_stock_order(user_id, {**draft, "idempotency_key": "missing-risk"})
    proof = service.issue_risk_proof(user_id, draft)
    request = {**draft, "idempotency_key": "risk-order-1", "risk_proof_id": proof["id"]}
    result = service.submit_stock_order(user_id, request)
    assert result["order"]["status"] == "FILLED"
    with pytest.raises(PersonalPaperRiskRejected, match="字段已变化"):
        service.submit_stock_order(
            user_id,
            {**request, "idempotency_key": "risk-order-2", "quantity": 2, "account_version": result["account"]["account_version"]},
        )
    assert service.account_snapshot(user_id, season["id"])["account_version"] == 1


def test_short_is_blocked_as_unbounded_and_different_consumption_fails(tmp_path):
    database, user_id, service, season, draft = _base(tmp_path)
    short = {**draft, "side": "SHORT", "symbol": "MSFT"}
    quote_id = service.quote_verifier.issue(
        user_id=user_id, market="US", symbol="MSFT", bid_minor=9_900, ask_minor=10_000,
        last_minor=9_950, as_of=NOW, now=NOW,
    )
    short["quote_id"] = quote_id
    proof = service.issue_risk_proof(user_id, short)
    assert proof["decision"] == "reject"
    assert proof["risk_level"] == "blocked"
    assert any(item["code"] == "max_loss" and item["status"] == "fail" for item in proof["checks"])
    with pytest.raises(PersonalPaperRiskRejected, match="拒绝"):
        service.submit_stock_order(user_id, {**short, "idempotency_key": "short-1", "risk_proof_id": proof["id"]})

    # A proof is not reusable for a different request, even if the account is unchanged.
    long = {**draft, "symbol": "MSFT", "quote_id": quote_id}
    with pytest.raises(PersonalPaperRiskRejected):
        service.submit_stock_order(user_id, {**long, "idempotency_key": "wrong-draft", "risk_proof_id": proof["id"]})
    assert database.fetch_one("SELECT COUNT(*) AS count FROM personal_paper_risk_proof_consumptions")["count"] == 0
