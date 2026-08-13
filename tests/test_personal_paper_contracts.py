from __future__ import annotations

from decimal import Decimal

import pytest

from core.personal_paper.contracts import (
    PersonalPaperValidationError,
    enforce_defined_risk_option_limit,
    normalize_stock_order,
)


def _request(**changes):
    value = {
        "idempotency_key": "order-key-001",
        "season_id": "season-001",
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
    return value


@pytest.mark.parametrize(
    ("order_type", "limit_price", "stop_price"),
    (("MARKET", None, None), ("LIMIT", 100, None), ("STOP", None, 101),
     ("STOP_LIMIT", 100, 101)),
)
def test_stock_order_types_have_exact_price_contracts(order_type, limit_price, stop_price):
    result = normalize_stock_order(
        _request(order_type=order_type, limit_price=limit_price, stop_price=stop_price)
    )
    assert result["order_type"] == order_type


def test_stock_order_rejects_unknown_fields_and_invalid_side_semantics():
    with pytest.raises(PersonalPaperValidationError):
        normalize_stock_order(_request(auto_submit=True))
    with pytest.raises(PersonalPaperValidationError):
        normalize_stock_order(_request(side="PUT"))


def test_defined_risk_option_limit_is_exactly_ten_percent_and_includes_costs():
    assert enforce_defined_risk_option_limit(
        pre_order_equity_minor=1_000_000,
        max_loss_minor=95_000,
        fees_minor=2_000,
        conservative_slippage_minor=3_000,
    ) == 100_000
    with pytest.raises(PersonalPaperValidationError):
        enforce_defined_risk_option_limit(
            pre_order_equity_minor=1_000_000,
            max_loss_minor=95_001,
            fees_minor=2_000,
            conservative_slippage_minor=3_000,
        )
    with pytest.raises(PersonalPaperValidationError):
        enforce_defined_risk_option_limit(
            pre_order_equity_minor=1_000_000,
            max_loss_minor=None,
            fees_minor=0,
            conservative_slippage_minor=0,
        )


def test_money_and_quantity_are_normalized_without_binary_float_authority():
    result = normalize_stock_order(_request(quantity=Decimal("12"), limit_price=None))
    assert result["quantity_micros"] == 12_000_000
    with pytest.raises(PersonalPaperValidationError):
        normalize_stock_order(_request(quantity=Decimal("1.125"), limit_price=None))
