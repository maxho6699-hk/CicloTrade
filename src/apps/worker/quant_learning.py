"""Promotion gates for autonomously trained stock and option challengers."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from src.apps.worker._compat import StrEnum


class ModelState(StrEnum):
    DRAFT = "draft"
    TRAINED = "trained"
    SHADOW = "shadow"
    PAPER_QUALIFIED = "paper_qualified"
    APPROVED = "approved"
    ACTIVE = "active"
    DEGRADED = "degraded"
    RETIRED = "retired"


@dataclass(frozen=True)
class EvaluationMetrics:
    sample_size: int
    out_of_sample_ratio: float
    stress_expectancy: float
    max_drawdown: float
    parameter_stability: float
    regimes_passed: int
    data_leakage_check_passed: bool
    survivorship_check_passed: bool
    current_data_passed: bool


@dataclass(frozen=True)
class PromotionPolicy:
    minimum_sample_size: int = 200
    minimum_out_of_sample_ratio: float = 0.50
    maximum_drawdown: float = 0.25
    minimum_parameter_stability: float = 0.70
    minimum_regimes_passed: int = 3


@dataclass(frozen=True)
class PromotionProposal:
    eligible_for_human_review: bool
    requires_human_approval: bool
    next_state: ModelState
    reasons: tuple[str, ...]


def _validate_metrics(metrics: EvaluationMetrics) -> None:
    numeric_values = (
        metrics.out_of_sample_ratio,
        metrics.stress_expectancy,
        metrics.max_drawdown,
        metrics.parameter_stability,
    )
    if metrics.sample_size < 0 or metrics.regimes_passed < 0:
        raise ValueError("sample and regime counts must be non-negative")
    if not all(isfinite(value) for value in numeric_values):
        raise ValueError("evaluation metrics must be finite")
    if not 0 <= metrics.out_of_sample_ratio <= 1:
        raise ValueError("out_of_sample_ratio must be between zero and one")
    if not 0 <= metrics.max_drawdown <= 1:
        raise ValueError("max_drawdown must be between zero and one")
    if not 0 <= metrics.parameter_stability <= 1:
        raise ValueError("parameter_stability must be between zero and one")


def evaluate_for_promotion(
    state: ModelState,
    metrics: EvaluationMetrics,
    *,
    policy: PromotionPolicy | None = None,
) -> PromotionProposal:
    """Build a review proposal; Worker code can never approve or activate a model."""
    _validate_metrics(metrics)
    active_policy = policy or PromotionPolicy()
    reasons: list[str] = []

    if state is not ModelState.SHADOW:
        reasons.append("only shadow challengers may produce a review proposal")
    if metrics.sample_size < active_policy.minimum_sample_size:
        reasons.append("insufficient evaluated trades")
    if metrics.out_of_sample_ratio < active_policy.minimum_out_of_sample_ratio:
        reasons.append("out-of-sample performance collapsed")
    if metrics.stress_expectancy <= 0:
        reasons.append("strategy failed pessimistic execution costs")
    if metrics.max_drawdown > active_policy.maximum_drawdown:
        reasons.append("maximum drawdown exceeds policy")
    if metrics.parameter_stability < active_policy.minimum_parameter_stability:
        reasons.append("parameters are not stable across neighboring values")
    if metrics.regimes_passed < active_policy.minimum_regimes_passed:
        reasons.append("insufficient market-regime coverage")
    if not metrics.data_leakage_check_passed:
        reasons.append("point-in-time leakage check failed")
    if not metrics.survivorship_check_passed:
        reasons.append("survivorship-bias check failed")
    if not metrics.current_data_passed:
        reasons.append("inputs are stale, incomplete, or unlicensed")
    return PromotionProposal(
        eligible_for_human_review=not reasons,
        requires_human_approval=True,
        next_state=ModelState.SHADOW,
        reasons=tuple(reasons),
    )
