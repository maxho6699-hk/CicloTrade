import json
from datetime import datetime, timedelta, timezone

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from core.earnings_forecast_journal import EarningsForecastJournal
from core.personal_paper import PersonalPaperRiskRejected, PersonalPaperService
from core.personal_paper.quote_proof import QuoteProofSignerVerifier


NOW = datetime(2026, 8, 15, 1, 2, 3, tzinfo=timezone.utc)
SECRET = b"personal-paper-risk-proof-test-key-32bytes!"


def _base(tmp_path, clock=None):
    database = DatabaseManager(str(tmp_path / "risk.db"))
    user = AuthService(database).register("risk@example.com", "StrongPass123", "Risk", True)
    signer = QuoteProofSignerVerifier(database, SECRET)
    service = PersonalPaperService(database, signer, clock=clock or (lambda: NOW))
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


def _quote(service, user_id, symbol, bid_minor, ask_minor):
    return service.quote_verifier.issue(
        user_id=user_id, market="US", symbol=symbol, bid_minor=bid_minor,
        ask_minor=ask_minor, last_minor=(bid_minor + ask_minor) // 2, as_of=NOW, now=NOW,
    )


def _draft(season, quote_id, symbol="AAPL", **changes):
    value = {
        "season_id": season["id"], "market": "US", "symbol": symbol, "side": "BUY",
        "order_type": "MARKET", "quantity": 1, "limit_price": None, "stop_price": None,
        "time_in_force": "DAY", "quote_id": quote_id, "account_version": season["version"],
        "source_context": {"kind": "manual", "reference_id": None},
    }
    value.update(changes)
    return value


def test_risk_proof_shape_and_unknown_boundaries_are_explicit(tmp_path):
    _, user_id, service, _, draft = _base(tmp_path)
    proof = service.issue_risk_proof(user_id, draft)
    assert {
        "id", "schema_version", "season_id", "quote_id", "account_version", "draft_sha256",
        "created_at", "expires_at", "decision", "risk_level", "data_state", "checks",
        "blocking_reasons", "warnings",
    }.issubset(proof)
    assert proof["schema_version"] == "r1"
    assert len(proof["proof_sha256"]) == 64
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


def test_sector_uses_each_positions_own_mark_and_drawdown_includes_initial_cash(tmp_path):
    _, user_id, service, season, _ = _base(tmp_path)
    msft_quote = _quote(service, user_id, "MSFT", 5_000, 10_000)
    msft = _draft(season, msft_quote, "MSFT")
    msft_proof = service.issue_risk_proof(user_id, msft)
    filled = service.submit_stock_order(
        user_id, {**msft, "idempotency_key": "msft-first", "risk_proof_id": msft_proof["id"]},
    )
    aapl_quote = _quote(service, user_id, "AAPL", 10_000, 10_000)
    aapl = _draft(
        {**season, "version": filled["account"]["account_version"]}, aapl_quote, "AAPL",
    )
    proof = service.issue_risk_proof(user_id, aapl)
    checks = {item["code"]: item for item in proof["checks"]}
    sector_value = json.loads(checks["sector_concentration"]["value"])
    drawdown_value = json.loads(checks["drawdown"]["value"])
    assert sector_value["usd"] == 150
    assert drawdown_value["peak_usd"] == 10_000
    assert drawdown_value["current_usd"] == 9_950
    assert drawdown_value["pct"] == pytest.approx(0.5)
    assert proof["marks_as_of"] == "2026-08-15T01:02:03Z"


def test_target_stock_uses_current_quote_for_add_and_limit_sell_projection(tmp_path):
    _, user_id, service, season, _ = _base(tmp_path)
    initial_quote = _quote(service, user_id, "AAPL", 5_000, 5_000)
    initial = _draft(season, initial_quote, quantity=2)
    initial_proof = service.issue_risk_proof(user_id, initial)
    filled = service.submit_stock_order(
        user_id,
        {**initial, "idempotency_key": "aapl-two", "risk_proof_id": initial_proof["id"]},
    )
    current_quote = _quote(service, user_id, "AAPL", 10_000, 10_000)
    current_season = {**season, "version": filled["account"]["account_version"]}

    add_proof = service.issue_risk_proof(
        user_id, _draft(current_season, current_quote, quantity=1)
    )
    add_checks = {
        item["code"]: json.loads(item["value"])
        for item in add_proof["checks"]
        if item["code"] in {"position_concentration", "sector_concentration"}
    }
    assert add_checks["position_concentration"]["usd"] == 300
    assert add_checks["sector_concentration"]["usd"] == 300

    sell_proof = service.issue_risk_proof(
        user_id,
        _draft(
            current_season,
            current_quote,
            side="SELL",
            order_type="LIMIT",
            quantity=1,
            limit_price=150,
        ),
    )
    sell_checks = {
        item["code"]: json.loads(item["value"])
        for item in sell_proof["checks"]
        if item["code"] in {"position_concentration", "sector_concentration"}
    }
    assert sell_checks["position_concentration"]["usd"] == 100
    assert sell_checks["sector_concentration"]["usd"] == 100


def test_risk_proof_uses_one_captured_clock_instant(tmp_path):
    ticks = iter((NOW, NOW, NOW + timedelta(seconds=1)))
    _, user_id, service, _, draft = _base(tmp_path, clock=lambda: next(ticks))

    proof = service.issue_risk_proof(user_id, draft)

    assert proof["computed_at"] == "2026-08-15T01:02:03Z"
    assert proof["marks_as_of"] == proof["computed_at"]


def test_future_unavailable_earnings_revision_does_not_hide_visible_revision(tmp_path):
    database, user_id, service, season, draft = _base(tmp_path)
    journal = EarningsForecastJournal(database)
    first = journal.record_event_revision(
        {
            "event_key": "US:AAPL:2026Q3", "revision_no": 1, "market": "US",
            "symbol": "AAPL", "fiscal_period": "2026Q3",
            "scheduled_at": "2026-08-18T20:15:00Z", "exchange_timezone": "America/New_York",
            "timing": "AMC", "status": "CONFIRMED", "source": "company-ir",
            "source_event_id": "aapl-2026q3", "observed_at": "2026-08-01T12:00:00Z",
            "available_at": "2026-08-01T12:01:00Z", "recorded_at": "2026-08-01T12:02:00Z",
            "supersedes_revision_id": None,
        },
        idempotency_key="event-visible-r1",
    )
    journal.record_event_revision(
        {
            "event_key": "US:AAPL:2026Q3", "revision_no": 2, "market": "US",
            "symbol": "AAPL", "fiscal_period": "2026Q3",
            "scheduled_at": "2026-08-19T20:15:00Z", "exchange_timezone": "America/New_York",
            "timing": "AMC", "status": "CONFIRMED", "source": "company-ir",
            "source_event_id": "aapl-2026q3", "observed_at": "2026-08-16T12:00:00Z",
            "available_at": "2026-08-16T12:01:00Z", "recorded_at": "2026-08-16T12:02:00Z",
            "supersedes_revision_id": first["id"],
        },
        idempotency_key="event-future-r2",
    )
    proof = service.issue_risk_proof(user_id, draft)
    event = next(item for item in proof["checks"] if item["code"] == "event_gap")
    assert json.loads(event["value"])["revision_id"] == first["id"]


@pytest.mark.parametrize("side", ("SELL", "COVER"))
def test_sell_and_cover_without_owned_position_get_reject_proof(tmp_path, side):
    _, user_id, service, _, draft = _base(tmp_path)
    proof = service.issue_risk_proof(user_id, {**draft, "side": side})
    assert proof["decision"] == "reject"
    assert any("持仓" in reason for reason in proof["blocking_reasons"])


def test_tampered_persisted_proof_fails_integrity_check(tmp_path):
    database, user_id, service, _, draft = _base(tmp_path)
    proof = service.issue_risk_proof(user_id, draft)
    with database._get_connection() as connection:
        connection.execute("DROP TRIGGER trg_personal_paper_risk_proofs_no_update")
        connection.execute(
            "UPDATE personal_paper_risk_proofs SET decision='allow' WHERE public_id=?",
            (proof["id"],),
        )
        connection.commit()
    with pytest.raises(PersonalPaperRiskRejected, match="完整性"):
        service.submit_stock_order(
            user_id, {**draft, "idempotency_key": "tampered-proof", "risk_proof_id": proof["id"]},
        )
