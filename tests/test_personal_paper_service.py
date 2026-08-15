from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from core.personal_paper import (
    PersonalPaperConflict,
    PersonalPaperRiskRejected,
    PersonalPaperService,
    VerifiedQuote,
)


NOW = datetime(2026, 8, 13, 1, 2, 3, tzinfo=timezone.utc)


class Quotes:
    def verify_and_consume(
        self, quote_id, *, user_id, season_id, market, symbol, now, connection,
        request_sha256,
    ):
        assert quote_id.startswith("quote-") and market == "US"
        assert user_id > 0 and season_id.startswith("pps_")
        assert connection.in_transaction and len(request_sha256) == 64
        return VerifiedQuote(
            proof_id=quote_id,
            market=market,
            symbol=symbol,
            bid_minor=9_900,
            ask_minor=10_000,
            last_minor=9_950,
            as_of=NOW,
            state="fresh",
            commission_minor=0,
        )

    def verify(self, quote_id, *, user_id, season_id, market, symbol, now, connection, request_sha256):
        return self.verify_and_consume(
            quote_id, user_id=user_id, season_id=season_id, market=market, symbol=symbol,
            now=now, connection=connection, request_sha256=request_sha256,
        )


@pytest.fixture
def personal(tmp_path):
    database = DatabaseManager(str(tmp_path / "personal.db"))
    user = AuthService(database).register("paper@example.com", "StrongPass123", "Paper", True)
    service = PersonalPaperService(database, Quotes(), clock=lambda: NOW)
    return database, user["id"], service


def _order(service, user_id, season, **changes):
    value = {
        "idempotency_key": "order-key-001",
        "season_id": season["id"],
        "market": "US",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": 1,
        "limit_price": None,
        "stop_price": None,
        "time_in_force": "DAY",
        "quote_id": "quote-001",
        "account_version": 0,
        "source_context": {"kind": "manual", "reference_id": None},
    }
    value.update(changes)
    stamp = NOW.isoformat().replace("+00:00", "Z")
    service.database.execute(
        """INSERT OR IGNORE INTO personal_paper_quote_proofs
           (public_id,user_id,season_id,schema_version,nonce,claims_json,signature_sha256,
            issued_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (value["quote_id"], user_id, season["id"], "q1", value["quote_id"].replace("-", "_") + "_test_nonce",
         "{}", "0" * 64, stamp, stamp, stamp),
    )
    draft = {key: item for key, item in value.items() if key != "idempotency_key"}
    proof = service.issue_risk_proof(user_id, draft)
    value["risk_proof_id"] = proof["id"]
    return value


def test_first_season_is_idempotent_independent_and_market_order_fills(personal):
    database, user_id, service = personal
    season = service.create_first_season(user_id)
    assert season == service.create_first_season(user_id)
    assert season["initial_cash"] == 10_000 and season["currency"] == "USD"

    result = service.submit_stock_order(user_id, _order(service, user_id, season))
    assert result["order"]["status"] == "FILLED"
    assert result["account"]["cash"] == 9_900
    assert result["account"]["market_value"] == 99
    assert result["account"]["total_equity"] == 9_999
    assert result["account"]["unrealized_pnl"] == -1
    assert not ({"cash_minor", "_positions", "buying_power_minor"} & set(result["account"]))
    assert result["account"]["positions"] == [{"market": "US", "symbol": "AAPL", "quantity": 1.0}]
    assert database.fetch_one("SELECT COUNT(*) count FROM orders")["count"] == 0
    assert database.fetch_one("SELECT COUNT(*) count FROM official_paper_events_v2")["count"] == 0


def test_idempotency_replays_same_payload_and_conflicts_on_change(personal):
    _, user_id, service = personal
    season = service.create_first_season(user_id)
    request = _order(service, user_id, season)
    first = service.submit_stock_order(user_id, request)
    replay = service.submit_stock_order(user_id, request)
    assert replay["order"] == first["order"] and replay["replayed"] is True
    with pytest.raises(PersonalPaperConflict):
        service.submit_stock_order(user_id, _order(service, user_id, season, quantity=2))


def test_sell_cover_and_opposite_direction_rules_fail_closed(personal):
    _, user_id, service = personal
    season = service.create_first_season(user_id)
    with pytest.raises(PersonalPaperRiskRejected):
        service.submit_stock_order(user_id, _order(service, user_id, season, side="SELL"))
    with pytest.raises(PersonalPaperRiskRejected, match="拒绝"):
        service.submit_stock_order(
            user_id,
            _order(service, user_id, season, idempotency_key="short-1", side="SHORT", symbol="MSFT",
                   quote_id="quote-short"),
        )


def test_begin_immediate_and_account_version_prevent_double_spend(personal):
    _, user_id, service = personal
    season = service.create_first_season(user_id)

    def place(index):
        return service.submit_stock_order(
            user_id,
            _order(service, user_id, season, idempotency_key=f"concurrent-{index}", quantity=100,
                   quote_id=f"quote-{index}"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(place, index) for index in range(2)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result()["order"]["status"])
        except (PersonalPaperConflict, PersonalPaperRiskRejected):
            outcomes.append("REJECTED")
    assert sorted(outcomes) == ["FILLED", "REJECTED"]


def test_users_cannot_read_or_submit_against_another_users_season(personal):
    database, user_id, service = personal
    other = AuthService(database).register("other@example.com", "StrongPass123", "Other", True)
    season = service.create_first_season(user_id)
    with pytest.raises(PersonalPaperConflict):
        service.account_snapshot(other["id"], season["id"])
    with pytest.raises(PersonalPaperConflict):
        service.submit_stock_order(other["id"], _order(service, user_id, season))


def test_pending_order_can_be_cancelled_once_and_releases_cash(personal):
    _, user_id, service = personal
    season = service.create_first_season(user_id)
    pending = service.submit_stock_order(
        user_id,
        _order(service, user_id, season, order_type="LIMIT", limit_price=90, quote_id="quote-limit"),
    )
    assert pending["order"]["status"] == "PENDING"
    assert pending["account"]["reserved_cash"] == 90
    cancelled = service.cancel_stock_order(
        user_id,
        {"season_id": season["id"], "order_id": pending["order"]["id"], "account_version": 1},
    )
    assert cancelled["order"]["status"] == "CANCELLED"
    assert cancelled["account"]["reserved_cash"] == 0
    replay = service.cancel_stock_order(
        user_id,
        {"season_id": season["id"], "order_id": pending["order"]["id"], "account_version": 2},
    )
    assert replay["replayed"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    (("bid_minor", True), ("ask_minor", 100.5), ("last_minor", float("nan")),
     ("commission_minor", False)),
)
def test_quote_minor_units_are_strict_integers(personal, field, value):
    _, user_id, service = personal
    season = service.create_first_season(user_id)
    original = service.quote_verifier.verify

    def invalid(*args, **kwargs):
        quote = original(*args, **kwargs)
        values = dict(quote.__dict__)
        values[field] = value
        return VerifiedQuote(**values)

    service.quote_verifier.verify = invalid
    with pytest.raises(PersonalPaperRiskRejected):
        service.submit_stock_order(user_id, _order(service, user_id, season))
