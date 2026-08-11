import pytest

from src.apps.worker.quant_learning import (
    EvaluationMetrics,
    ModelState,
    evaluate_for_promotion,
)


def qualified_metrics(**overrides):
    values = {
        "sample_size": 240,
        "out_of_sample_ratio": 0.72,
        "stress_expectancy": 0.012,
        "max_drawdown": 0.14,
        "parameter_stability": 0.81,
        "regimes_passed": 4,
        "data_leakage_check_passed": True,
        "survivorship_check_passed": True,
        "current_data_passed": True,
    }
    values.update(overrides)
    return EvaluationMetrics(**values)


def test_challenger_can_only_propose_human_review():
    decision = evaluate_for_promotion(ModelState.SHADOW, qualified_metrics())

    assert decision.eligible_for_human_review is True
    assert decision.requires_human_approval is True
    assert decision.next_state is ModelState.SHADOW
    assert decision.reasons == ()


def test_worker_cannot_turn_a_paper_candidate_into_approved():
    decision = evaluate_for_promotion(ModelState.PAPER_QUALIFIED, qualified_metrics())

    assert decision.eligible_for_human_review is False
    assert decision.next_state is ModelState.SHADOW
    assert "only shadow challengers may produce a review proposal" in decision.reasons


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"sample_size": 99}, "insufficient evaluated trades"),
        ({"out_of_sample_ratio": 0.30}, "out-of-sample performance collapsed"),
        ({"stress_expectancy": -0.001}, "strategy failed pessimistic execution costs"),
        ({"max_drawdown": 0.35}, "maximum drawdown exceeds policy"),
        ({"data_leakage_check_passed": False}, "point-in-time leakage check failed"),
        ({"current_data_passed": False}, "inputs are stale, incomplete, or unlicensed"),
    ],
)
def test_failed_quality_gate_keeps_model_in_shadow(overrides, reason):
    decision = evaluate_for_promotion(ModelState.SHADOW, qualified_metrics(**overrides))

    assert decision.eligible_for_human_review is False
    assert decision.next_state is ModelState.SHADOW
    assert reason in decision.reasons


def test_non_finite_metrics_are_rejected():
    with pytest.raises(ValueError, match="finite"):
        evaluate_for_promotion(ModelState.SHADOW, qualified_metrics(stress_expectancy=float("nan")))
