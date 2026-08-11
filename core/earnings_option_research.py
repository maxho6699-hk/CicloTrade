"""Defined-risk option research calculations for earnings snapshots.

The module only models purchased options. It has no order, broker, publisher, or
official recommendation capability. Until point-in-time option history exists,
every result is explicitly a current-snapshot research estimate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hmac
import math
import secrets
from statistics import fmean
from typing import Sequence

from core.earnings_forecast_contracts import (
    EarningsContractError,
    canonical_json,
    parse_timestamp,
    sha256_json,
    timestamp,
)


STRUCTURES = {"LONG_CALL", "LONG_PUT", "LONG_STRADDLE", "LONG_STRANGLE"}
_RESULT_SEAL_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class OptionLegQuote:
    contract_id: str
    right: str
    strike: float
    expiry: str
    quantity: int
    multiplier: int
    bid: float
    ask: float
    implied_volatility: float
    delta: float
    gamma: float
    theta: float
    vega: float
    volume: int
    open_interest: int
    quote_at: str
    available_at: str


@dataclass(frozen=True)
class OneLegCoverage:
    covering_leg: str
    required_terminal_price: float
    probability: float
    possible: bool


@dataclass(frozen=True)
class IvCrushScenario:
    relative_iv_change_pct: float
    estimated_structure_value: float
    estimated_pnl_after_costs: float
    method: str
    spot_held_constant: bool
    time_decay_excluded: bool


@dataclass(frozen=True)
class DefinedRiskOptionResult:
    structure_type: str
    evidence_mode: str
    historical_oos_validated: bool
    research_only: bool
    execution_eligible: bool
    automatic_ordering: bool
    contracts: tuple[OptionLegQuote, ...]
    total_premium: float
    commission_cost: float
    spread_cost: float
    slippage_cost: float
    max_loss: float
    lower_breakeven: float | None
    upper_breakeven: float | None
    required_move_pct: float
    model_expected_move_pct: float
    iv_implied_move_pct: float
    probability_outside_breakeven: float
    expected_value_net_costs: float
    call_zero_coverage: OneLegCoverage | None
    put_zero_coverage: OneLegCoverage | None
    iv_crush_scenarios: tuple[IvCrushScenario, ...]
    terminal_sample_size: int
    decision_at: str
    payload_sha256: str
    _validation_receipt: str

    def as_dict(self) -> dict:
        value = asdict(self)
        value.pop("_validation_receipt", None)
        return value


def verify_defined_risk_result(result: object) -> dict:
    """Return the evaluator payload only when its process-local seal is intact."""
    if type(result) is not DefinedRiskOptionResult:
        raise EarningsContractError(
            "option research must be a sealed DefinedRiskOptionResult"
        )
    value = asdict(result)
    supplied = value.pop("_validation_receipt", "")
    raw = {name: item for name, item in value.items() if name != "payload_sha256"}
    expected = hmac.digest(
        _RESULT_SEAL_KEY, canonical_json(raw).encode("utf-8"), "sha256"
    ).hex()
    if not hmac.compare_digest(str(supplied), expected):
        raise EarningsContractError("option research evaluator seal is invalid")
    if value["payload_sha256"] != sha256_json(raw):
        raise EarningsContractError("option research payload hash is invalid")
    return value


def _finite(value: float, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EarningsContractError(f"{label} must be finite")
    result = float(value)
    if minimum is not None and result < minimum:
        raise EarningsContractError(f"{label} is below its minimum")
    return result


def _validate_leg(
    leg: OptionLegQuote,
    decision: datetime,
    *,
    max_quote_age_seconds: int,
    max_relative_spread: float,
) -> OptionLegQuote:
    if not isinstance(leg, OptionLegQuote):
        raise EarningsContractError("option legs must use the OptionLegQuote contract")
    if leg.right not in {"CALL", "PUT"}:
        raise EarningsContractError("option right must be CALL or PUT")
    if isinstance(leg.quantity, bool) or not isinstance(leg.quantity, int) or leg.quantity <= 0:
        raise EarningsContractError("options require a positive long quantity")
    if isinstance(leg.multiplier, bool) or not isinstance(leg.multiplier, int) or leg.multiplier <= 0:
        raise EarningsContractError("option multiplier must be positive")
    strike = _finite(leg.strike, "strike", minimum=0.000001)
    bid = _finite(leg.bid, "bid", minimum=0)
    ask = _finite(leg.ask, "ask", minimum=0.000001)
    if ask < bid:
        raise EarningsContractError("option ask cannot be below bid")
    if (ask - bid) / ask > max_relative_spread:
        raise EarningsContractError("option spread exceeds the research liquidity gate")
    if leg.volume <= 0 or leg.open_interest <= 0:
        raise EarningsContractError("option liquidity requires positive volume and open interest")
    implied_volatility = _finite(leg.implied_volatility, "implied_volatility", minimum=0.000001)
    for name in ("delta", "gamma", "theta", "vega"):
        _finite(getattr(leg, name), name)
    quote_at = parse_timestamp(leg.quote_at, "option quote_at")
    available_at = parse_timestamp(leg.available_at, "option available_at")
    if quote_at > available_at:
        raise EarningsContractError("option quote_at is after available_at")
    if available_at > decision:
        raise EarningsContractError("option available_at is after decision_at")
    if (decision - available_at).total_seconds() > max_quote_age_seconds:
        raise EarningsContractError("option quote is stale")
    try:
        expiry = date.fromisoformat(leg.expiry)
    except ValueError as exc:
        raise EarningsContractError("option expiry is invalid") from exc
    if expiry <= decision.date():
        raise EarningsContractError("option expiry must be after decision_at")
    if not leg.contract_id or len(leg.contract_id) > 128:
        raise EarningsContractError("option contract_id is invalid")
    return OptionLegQuote(
        contract_id=leg.contract_id,
        right=leg.right,
        strike=strike,
        expiry=expiry.isoformat(),
        quantity=leg.quantity,
        multiplier=leg.multiplier,
        bid=bid,
        ask=ask,
        implied_volatility=implied_volatility,
        delta=float(leg.delta),
        gamma=float(leg.gamma),
        theta=float(leg.theta),
        vega=float(leg.vega),
        volume=leg.volume,
        open_interest=leg.open_interest,
        quote_at=timestamp(quote_at, "option quote_at"),
        available_at=timestamp(available_at, "option available_at"),
    )


def _structure_legs(
    structure_type: str, legs: tuple[OptionLegQuote, ...]
) -> tuple[OptionLegQuote | None, OptionLegQuote | None]:
    calls = [leg for leg in legs if leg.right == "CALL"]
    puts = [leg for leg in legs if leg.right == "PUT"]
    if structure_type == "LONG_CALL":
        if len(calls) != 1 or puts or len(legs) != 1:
            raise EarningsContractError("LONG_CALL requires exactly one purchased call")
        return calls[0], None
    if structure_type == "LONG_PUT":
        if len(puts) != 1 or calls or len(legs) != 1:
            raise EarningsContractError("LONG_PUT requires exactly one purchased put")
        return None, puts[0]
    if len(calls) != 1 or len(puts) != 1 or len(legs) != 2:
        raise EarningsContractError(f"{structure_type} requires one call and one put")
    call, put = calls[0], puts[0]
    if call.expiry != put.expiry or call.multiplier != put.multiplier:
        raise EarningsContractError("two-leg research requires one expiry and multiplier")
    if call.quantity != 1 or put.quantity != 1:
        raise EarningsContractError("two-leg research is limited to one long contract per leg")
    if structure_type == "LONG_STRADDLE" and not math.isclose(call.strike, put.strike):
        raise EarningsContractError("LONG_STRADDLE requires the same strike")
    if structure_type == "LONG_STRANGLE" and not put.strike < call.strike:
        raise EarningsContractError("LONG_STRANGLE requires put strike below call strike")
    return call, put


def _terminal_samples(values: Sequence[float]) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or len(values) < 100:
        raise EarningsContractError("terminal distribution requires at least 100 samples")
    samples = tuple(_finite(item, "terminal price", minimum=0.000001) for item in values)
    return samples


def _payoff(price: float, leg: OptionLegQuote) -> float:
    intrinsic = max(price - leg.strike, 0) if leg.right == "CALL" else max(leg.strike - price, 0)
    return intrinsic * leg.quantity * leg.multiplier


def _coverage(
    *, covering_leg: str, threshold: float, samples: tuple[float, ...], lower_tail: bool
) -> OneLegCoverage:
    possible = threshold > 0
    hits = sum(price <= threshold if lower_tail else price >= threshold for price in samples)
    return OneLegCoverage(
        covering_leg=covering_leg,
        required_terminal_price=max(threshold, 0.0),
        probability=hits / len(samples) if possible else 0.0,
        possible=possible,
    )


def _iv_crush_scenarios(
    legs: tuple[OptionLegQuote, ...],
    relative_changes: Sequence[float],
    *,
    all_in_cost: float,
) -> tuple[IvCrushScenario, ...]:
    if isinstance(relative_changes, (str, bytes)) or not relative_changes:
        raise EarningsContractError("iv crush scenarios must be non-empty")
    current_mid_value = sum(
        (leg.bid + leg.ask) / 2 * leg.quantity * leg.multiplier for leg in legs
    )
    output = []
    for raw_change in relative_changes:
        relative_change = _finite(raw_change, "relative IV change")
        if not -100 <= relative_change <= 0:
            raise EarningsContractError("IV crush scenarios must be between -100% and 0%")
        first_order_change = sum(
            leg.vega
            * (leg.implied_volatility * relative_change / 100 * 100)
            * leg.quantity
            * leg.multiplier
            for leg in legs
        )
        estimated_value = max(0.0, current_mid_value + first_order_change)
        output.append(IvCrushScenario(
            relative_iv_change_pct=relative_change,
            estimated_structure_value=estimated_value,
            estimated_pnl_after_costs=max(estimated_value - all_in_cost, -all_in_cost),
            method="first_order_vega_current_snapshot_estimate",
            spot_held_constant=True,
            time_decay_excluded=True,
        ))
    return tuple(output)


def evaluate_defined_risk_structure(
    *,
    structure_type: str,
    spot: float,
    decision_at: datetime | str,
    legs: Sequence[OptionLegQuote],
    terminal_price_samples: Sequence[float],
    commission_per_contract: float,
    slippage_per_contract: float,
    model_expected_move_pct: float,
    iv_crush_scenarios_pct: Sequence[float] = (-20.0, -40.0, -60.0),
    max_quote_age_seconds: int = 15 * 60,
    max_relative_spread: float = 0.25,
) -> DefinedRiskOptionResult:
    """Return a finite-loss research estimate; never an order or official action."""
    if structure_type not in STRUCTURES:
        raise EarningsContractError("unsupported or unlimited-loss option structure")
    decision = parse_timestamp(decision_at, "decision_at")
    spot = _finite(spot, "spot", minimum=0.000001)
    commission = _finite(commission_per_contract, "commission_per_contract", minimum=0)
    slippage = _finite(slippage_per_contract, "slippage_per_contract", minimum=0)
    expected_move = _finite(model_expected_move_pct, "model_expected_move_pct", minimum=0)
    if max_quote_age_seconds <= 0 or not 0 < max_relative_spread <= 1:
        raise EarningsContractError("option liquidity policy is invalid")
    normalized_legs = tuple(
        _validate_leg(
            leg,
            decision,
            max_quote_age_seconds=max_quote_age_seconds,
            max_relative_spread=max_relative_spread,
        )
        for leg in legs
    )
    call, put = _structure_legs(structure_type, normalized_legs)
    samples = _terminal_samples(terminal_price_samples)
    contract_count = sum(leg.quantity for leg in normalized_legs)
    total_premium = sum(leg.ask * leg.quantity * leg.multiplier for leg in normalized_legs)
    spread_cost = sum(
        (leg.ask - leg.bid) / 2 * leg.quantity * leg.multiplier for leg in normalized_legs
    )
    commission_cost = commission * contract_count
    slippage_cost = slippage * contract_count
    max_loss = total_premium + commission_cost + slippage_cost
    if not math.isfinite(max_loss) or max_loss <= 0:
        raise EarningsContractError("option maximum loss must be finite and positive")

    if call and put:
        debit_per_share = max_loss / call.multiplier
        lower_breakeven = put.strike - debit_per_share
        upper_breakeven = call.strike + debit_per_share
    elif call:
        lower_breakeven = None
        upper_breakeven = call.strike + max_loss / (call.quantity * call.multiplier)
    else:
        assert put is not None
        lower_breakeven = put.strike - max_loss / (put.quantity * put.multiplier)
        upper_breakeven = None
    distances = [
        abs(level - spot) / spot * 100
        for level in (lower_breakeven, upper_breakeven)
        if level is not None
    ]
    required_move = min(distances)
    outside = sum(
        (lower_breakeven is not None and price <= lower_breakeven)
        or (upper_breakeven is not None and price >= upper_breakeven)
        for price in samples
    ) / len(samples)
    expected_payoff = fmean(sum(_payoff(price, leg) for leg in normalized_legs) for price in samples)
    expected_value = expected_payoff - max_loss
    expiry = date.fromisoformat(normalized_legs[0].expiry)
    days = max((expiry - decision.date()).days, 1)
    implied_move = fmean(leg.implied_volatility for leg in normalized_legs) * math.sqrt(days / 365) * 100
    crush_scenarios = _iv_crush_scenarios(
        normalized_legs, iv_crush_scenarios_pct, all_in_cost=max_loss
    )

    call_zero = None
    put_zero = None
    if call and put:
        debit_per_share = max_loss / call.multiplier
        call_zero = _coverage(
            covering_leg="PUT",
            threshold=put.strike - debit_per_share,
            samples=samples,
            lower_tail=True,
        )
        put_zero = _coverage(
            covering_leg="CALL",
            threshold=call.strike + debit_per_share,
            samples=samples,
            lower_tail=False,
        )
    raw = {
        "structure_type": structure_type,
        "evidence_mode": "current_snapshot_research_estimate",
        "historical_oos_validated": False,
        "research_only": True,
        "execution_eligible": False,
        "automatic_ordering": False,
        "contracts": [asdict(leg) for leg in normalized_legs],
        "total_premium": total_premium,
        "commission_cost": commission_cost,
        "spread_cost": spread_cost,
        "slippage_cost": slippage_cost,
        "max_loss": max_loss,
        "lower_breakeven": lower_breakeven,
        "upper_breakeven": upper_breakeven,
        "required_move_pct": required_move,
        "model_expected_move_pct": expected_move,
        "iv_implied_move_pct": implied_move,
        "probability_outside_breakeven": outside,
        "expected_value_net_costs": expected_value,
        "call_zero_coverage": asdict(call_zero) if call_zero else None,
        "put_zero_coverage": asdict(put_zero) if put_zero else None,
        "iv_crush_scenarios": [asdict(scenario) for scenario in crush_scenarios],
        "terminal_sample_size": len(samples),
        "decision_at": timestamp(decision, "decision_at"),
    }
    validation_receipt = hmac.digest(
        _RESULT_SEAL_KEY, canonical_json(raw).encode("utf-8"), "sha256"
    ).hex()
    return DefinedRiskOptionResult(
        structure_type=structure_type,
        evidence_mode=raw["evidence_mode"],
        historical_oos_validated=False,
        research_only=True,
        execution_eligible=False,
        automatic_ordering=False,
        contracts=normalized_legs,
        total_premium=total_premium,
        commission_cost=commission_cost,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        max_loss=max_loss,
        lower_breakeven=lower_breakeven,
        upper_breakeven=upper_breakeven,
        required_move_pct=required_move,
        model_expected_move_pct=expected_move,
        iv_implied_move_pct=implied_move,
        probability_outside_breakeven=outside,
        expected_value_net_costs=expected_value,
        call_zero_coverage=call_zero,
        put_zero_coverage=put_zero,
        iv_crush_scenarios=crush_scenarios,
        terminal_sample_size=len(samples),
        decision_at=raw["decision_at"],
        payload_sha256=sha256_json(raw),
        _validation_receipt=validation_receipt,
    )
