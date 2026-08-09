from dataclasses import replace

import pytest

from src.apps.worker.learning_cycle import (
    AssetClass,
    CandidateArtifact,
    PointInTimeSample,
    WalkForwardResult,
    independently_review_cycle,
    run_learning_cycle,
)
from src.apps.worker.quant_learning import ModelState


def equity_sample(**overrides):
    values = {
        "asset_class": AssetClass.EQUITY,
        "symbol": "AAPL",
        "observed_at": "2026-08-09T13:59:00+00:00",
        "available_at": "2026-08-09T14:00:00+00:00",
        "decision_at": "2026-08-09T14:01:00+00:00",
        "features": {"price": 200.0, "volume": 1_000_000.0, "atr": 5.0, "realized_volatility": 0.28},
        "source_snapshot_id": "equity-snapshot-1",
        "includes_delisted_universe": True,
    }
    values.update(overrides)
    return PointInTimeSample(**values)


def option_sample(**overrides):
    values = {
        "asset_class": AssetClass.OPTION,
        "symbol": "AAPL",
        "observed_at": "2026-08-09T14:00:00+00:00",
        "available_at": "2026-08-09T14:00:03+00:00",
        "decision_at": "2026-08-09T14:00:10+00:00",
        "features": {
            "underlying_price": 200.0, "bid": 2.4, "ask": 2.5, "open_interest": 900,
            "implied_volatility": 0.32, "delta": 0.55, "gamma": 0.04,
            "theta": -0.08, "vega": 0.16,
        },
        "source_snapshot_id": "option-snapshot-1",
        "includes_delisted_universe": True,
        "option_contract_id": "AAPL-20260918-C-210",
    }
    values.update(overrides)
    return PointInTimeSample(**values)


class QualifiedTrainer:
    def train(self, samples):
        self.trained = len(samples)
        return CandidateArtifact(
            model_id="equity-stability", version="challenger-8",
            artifact_hash="a" * 64,
            hypothesis="liquid trend continuation survives pessimistic execution",
            parameter_count=4,
        )

    def walk_forward(self, artifact, samples):
        assert artifact.version == "challenger-8"
        assert samples
        return WalkForwardResult(
            fold_count=5,
            sample_size=240,
            out_of_sample_ratio=0.72,
            stress_expectancy=0.012,
            max_drawdown=0.14,
            parameter_stability=0.81,
            regimes_passed=4,
            data_leakage_check_passed=True,
            survivorship_check_passed=True,
            current_data_passed=True,
            slippage_multiplier=2.0,
        )


def test_autonomous_cycle_trains_and_evaluates_but_cannot_self_promote():
    trainer = QualifiedTrainer()

    receipt = run_learning_cycle([equity_sample()], trainer, maximum_age_seconds=300)

    assert trainer.trained == 1
    assert receipt.promotion.approved is False
    assert receipt.promotion.next_state is ModelState.SHADOW
    assert "independent promotion approval is required" in receipt.promotion.reasons
    assert len(receipt.dataset_hash) == 64


def test_independent_review_can_approve_a_qualified_paper_candidate():
    receipt = run_learning_cycle([equity_sample()], QualifiedTrainer(), maximum_age_seconds=300)

    reviewed = independently_review_cycle(receipt, approver_id="risk-reviewer-17")

    assert reviewed.promotion.approved is True
    assert reviewed.promotion.next_state is ModelState.APPROVED
    assert reviewed.independent_approver_id == "risk-reviewer-17"


def test_point_in_time_leakage_and_stale_evidence_are_rejected():
    leaked = equity_sample(available_at="2026-08-09T14:02:00+00:00")
    stale = equity_sample(decision_at="2026-08-09T15:00:00+00:00")

    with pytest.raises(ValueError, match="leakage"):
        run_learning_cycle([leaked], QualifiedTrainer(), maximum_age_seconds=300)
    with pytest.raises(ValueError, match="stale"):
        run_learning_cycle([stale], QualifiedTrainer(), maximum_age_seconds=300)


def test_mystic_and_social_editorial_features_can_never_enter_models():
    sample = equity_sample(features={
        "price": 200.0, "volume": 1_000_000.0, "atr": 5.0,
        "realized_volatility": 0.28, "threads_heat": 88.0,
    })

    with pytest.raises(ValueError, match="mystic"):
        run_learning_cycle([sample], QualifiedTrainer(), maximum_age_seconds=300)


def test_option_training_requires_real_contract_liquidity_and_greeks():
    illiquid = option_sample(features={**option_sample().features, "open_interest": 0})
    missing_greek = option_sample(features={
        key: value for key, value in option_sample().features.items() if key != "theta"
    })

    with pytest.raises(ValueError, match="zero-liquidity"):
        run_learning_cycle([illiquid], QualifiedTrainer(), maximum_age_seconds=60)
    with pytest.raises(ValueError, match="theta"):
        run_learning_cycle([missing_greek], QualifiedTrainer(), maximum_age_seconds=60)


def test_fragile_training_protocol_is_rejected_before_receipt():
    class FragileTrainer(QualifiedTrainer):
        def train(self, samples):
            return replace(super().train(samples), parameter_count=9)

    with pytest.raises(ValueError, match="overfitting"):
        run_learning_cycle([equity_sample()], FragileTrainer(), maximum_age_seconds=300)


def test_stress_backtest_requires_pessimistic_slippage():
    class OptimisticTrainer(QualifiedTrainer):
        def walk_forward(self, artifact, samples):
            return replace(super().walk_forward(artifact, samples), slippage_multiplier=1.0)

    with pytest.raises(ValueError, match="slippage"):
        run_learning_cycle([equity_sample()], OptimisticTrainer(), maximum_age_seconds=300)
