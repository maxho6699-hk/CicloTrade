from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from core.compat import UTC
from core.earnings_forecast_contracts import EarningsContractError
from core.earnings_option_research import (
    OptionLegQuote,
    evaluate_defined_risk_structure,
)


DECISION_AT = datetime(2026, 8, 11, 20, 0, tzinfo=UTC)


def _leg(right: str, strike: float, contract_id: str) -> OptionLegQuote:
    return OptionLegQuote(
        contract_id=contract_id,
        right=right,
        strike=strike,
        expiry="2026-08-21",
        quantity=1,
        multiplier=100,
        bid=4.8,
        ask=5.2,
        implied_volatility=0.48,
        delta=0.5 if right == "CALL" else -0.5,
        gamma=0.04,
        theta=-0.18,
        vega=0.22,
        volume=500,
        open_interest=2_000,
        quote_at="2026-08-11T19:58:00Z",
        available_at="2026-08-11T19:58:02Z",
    )


def test_long_straddle_is_finite_loss_current_snapshot_research():
    samples = [160.0 + index * 0.1 for index in range(801)]
    result = evaluate_defined_risk_structure(
        structure_type="LONG_STRADDLE",
        spot=200.0,
        decision_at=DECISION_AT,
        legs=[_leg("CALL", 200.0, "AAPL-C-200"), _leg("PUT", 200.0, "AAPL-P-200")],
        terminal_price_samples=samples,
        commission_per_contract=0.65,
        slippage_per_contract=0.35,
        model_expected_move_pct=12.0,
    )

    assert result.research_only is True
    assert result.execution_eligible is False
    assert result.automatic_ordering is False
    assert result.evidence_mode == "current_snapshot_research_estimate"
    assert result.historical_oos_validated is False
    assert result.max_loss == result.total_premium + result.commission_cost + result.slippage_cost
    assert result.lower_breakeven < 200 < result.upper_breakeven
    assert 0 <= result.probability_outside_breakeven <= 1
    assert result.call_zero_coverage is not None
    assert result.put_zero_coverage is not None
    assert [scenario.relative_iv_change_pct for scenario in result.iv_crush_scenarios] == [-20, -40, -60]
    assert all(
        scenario.method == "first_order_vega_current_snapshot_estimate"
        and scenario.spot_held_constant
        and scenario.time_decay_excluded
        for scenario in result.iv_crush_scenarios
    )


def test_long_strangle_requires_ordered_strikes_and_no_short_or_naked_legs():
    call = _leg("CALL", 210.0, "AAPL-C-210")
    put = _leg("PUT", 190.0, "AAPL-P-190")
    result = evaluate_defined_risk_structure(
        structure_type="LONG_STRANGLE",
        spot=200.0,
        decision_at=DECISION_AT,
        legs=[call, put],
        terminal_price_samples=[150.0 + index * 0.1 for index in range(1_001)],
        commission_per_contract=0.65,
        slippage_per_contract=0.35,
        model_expected_move_pct=13.0,
    )
    assert result.lower_breakeven < put.strike < call.strike < result.upper_breakeven

    with pytest.raises(EarningsContractError, match="positive long quantity"):
        evaluate_defined_risk_structure(
            structure_type="LONG_CALL",
            spot=200.0,
            decision_at=DECISION_AT,
            legs=[replace(call, quantity=-1)],
            terminal_price_samples=[190.0 + index * 0.1 for index in range(200)],
            commission_per_contract=0.65,
            slippage_per_contract=0.35,
            model_expected_move_pct=8.0,
        )

    with pytest.raises(EarningsContractError, match="requires one call and one put"):
        evaluate_defined_risk_structure(
            structure_type="LONG_STRADDLE",
            spot=200.0,
            decision_at=DECISION_AT,
            legs=[call],
            terminal_price_samples=[190.0 + index * 0.1 for index in range(200)],
            commission_per_contract=0.65,
            slippage_per_contract=0.35,
            model_expected_move_pct=8.0,
        )


def test_stale_illiquid_or_wide_quotes_fail_closed():
    call = _leg("CALL", 200.0, "AAPL-C-200")
    samples = [190.0 + index * 0.1 for index in range(200)]

    for unsafe, message in [
        (replace(call, available_at="2026-08-11T20:01:00Z"), "after decision_at"),
        (replace(call, open_interest=0), "liquidity"),
        (replace(call, bid=1.0, ask=5.2), "spread"),
    ]:
        with pytest.raises(EarningsContractError, match=message):
            evaluate_defined_risk_structure(
                structure_type="LONG_CALL",
                spot=200.0,
                decision_at=DECISION_AT,
                legs=[unsafe],
                terminal_price_samples=samples,
                commission_per_contract=0.65,
                slippage_per_contract=0.35,
                model_expected_move_pct=8.0,
            )
