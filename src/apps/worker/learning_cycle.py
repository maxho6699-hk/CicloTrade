"""Point-in-time autonomous challenger cycles with review-only promotion receipts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Protocol, Sequence

from core.compat import UTC
from src.apps.worker._compat import StrEnum
from src.apps.worker.quant_learning import (
    EvaluationMetrics,
    ModelState,
    PromotionProposal,
    PromotionPolicy,
    evaluate_for_promotion,
)


class AssetClass(StrEnum):
    EQUITY = "equity"
    OPTION = "option"


FORBIDDEN_FEATURE_PREFIXES = ("mystic_", "x_post_", "threads_", "social_mystic_")
EQUITY_REQUIRED = {"price", "volume", "atr", "realized_volatility"}
OPTION_REQUIRED = {
    "underlying_price", "bid", "ask", "open_interest", "implied_volatility",
    "delta", "gamma", "theta", "vega",
}


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@dataclass(frozen=True)
class PointInTimeSample:
    asset_class: AssetClass
    symbol: str
    observed_at: str
    available_at: str
    decision_at: str
    features: dict[str, float]
    source_snapshot_id: str
    includes_delisted_universe: bool
    option_contract_id: str | None = None


@dataclass(frozen=True)
class CandidateArtifact:
    model_id: str
    version: str
    artifact_hash: str
    hypothesis: str
    parameter_count: int


@dataclass(frozen=True)
class WalkForwardResult:
    fold_count: int
    sample_size: int
    out_of_sample_ratio: float
    stress_expectancy: float
    max_drawdown: float
    parameter_stability: float
    regimes_passed: int
    data_leakage_check_passed: bool
    survivorship_check_passed: bool
    current_data_passed: bool
    slippage_multiplier: float

    def metrics(self) -> EvaluationMetrics:
        return EvaluationMetrics(
            sample_size=self.sample_size,
            out_of_sample_ratio=self.out_of_sample_ratio,
            stress_expectancy=self.stress_expectancy,
            max_drawdown=self.max_drawdown,
            parameter_stability=self.parameter_stability,
            regimes_passed=self.regimes_passed,
            data_leakage_check_passed=self.data_leakage_check_passed,
            survivorship_check_passed=self.survivorship_check_passed,
            current_data_passed=self.current_data_passed,
        )


class CandidateTrainer(Protocol):
    def train(self, samples: Sequence[PointInTimeSample]) -> CandidateArtifact: ...

    def walk_forward(
        self, artifact: CandidateArtifact, samples: Sequence[PointInTimeSample]
    ) -> WalkForwardResult: ...


@dataclass(frozen=True)
class LearningCycleReceipt:
    cycle_id: str
    asset_class: AssetClass
    model_id: str
    model_version: str
    artifact_hash: str
    dataset_hash: str
    sample_count: int
    feature_names: tuple[str, ...]
    fold_count: int
    slippage_multiplier: float
    metrics: EvaluationMetrics
    promotion_proposal: PromotionProposal
    created_at: str


def validate_point_in_time_samples(
    samples: Sequence[PointInTimeSample], *, maximum_age_seconds: int
) -> AssetClass:
    if not samples:
        raise ValueError("learning cycle requires point-in-time samples")
    if maximum_age_seconds <= 0:
        raise ValueError("maximum_age_seconds must be positive")
    asset_class = samples[0].asset_class
    required = OPTION_REQUIRED if asset_class is AssetClass.OPTION else EQUITY_REQUIRED
    for sample in samples:
        if sample.asset_class is not asset_class:
            raise ValueError("one learning cycle cannot mix asset classes")
        if not sample.symbol.strip() or not sample.source_snapshot_id.strip():
            raise ValueError("sample identity is incomplete")
        observed = _timestamp(sample.observed_at)
        available = _timestamp(sample.available_at)
        decision = _timestamp(sample.decision_at)
        if observed > available or available > decision:
            raise ValueError("point-in-time leakage detected")
        if (decision - available).total_seconds() > maximum_age_seconds:
            raise ValueError("sample evidence is stale at decision time")
        names = set(sample.features)
        if required - names:
            raise ValueError(f"sample is missing required features: {', '.join(sorted(required - names))}")
        if any(name.lower().startswith(FORBIDDEN_FEATURE_PREFIXES) for name in names):
            raise ValueError("mystic and social editorial data cannot enter trading features")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in sample.features.values()):
            raise ValueError("sample features must be finite numbers")
        if not sample.includes_delisted_universe:
            raise ValueError("survivorship-bias-free universe is required")
        if asset_class is AssetClass.OPTION:
            if not sample.option_contract_id:
                raise ValueError("option sample requires a contract id")
            if sample.features["bid"] < 0 or sample.features["ask"] <= 0:
                raise ValueError("option quotes are invalid")
            if sample.features["ask"] < sample.features["bid"]:
                raise ValueError("option ask cannot be below bid")
            if sample.features["open_interest"] <= 0:
                raise ValueError("zero-liquidity option contracts cannot be trained as tradeable")
    return asset_class


def _dataset_hash(samples: Sequence[PointInTimeSample]) -> str:
    payload = [asdict(sample) for sample in samples]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_learning_cycle(
    samples: Sequence[PointInTimeSample],
    trainer: CandidateTrainer,
    *,
    maximum_age_seconds: int,
    policy: PromotionPolicy | None = None,
) -> LearningCycleReceipt:
    asset_class = validate_point_in_time_samples(samples, maximum_age_seconds=maximum_age_seconds)
    artifact = trainer.train(samples)
    if not artifact.model_id or not artifact.version or len(artifact.artifact_hash) < 32:
        raise ValueError("trainer returned an invalid model artifact")
    if not artifact.hypothesis.strip():
        raise ValueError("candidate must state its hypothesis before validation")
    if not 1 <= artifact.parameter_count <= 6:
        raise ValueError("candidate parameter count is outside the anti-overfitting limit")
    result = trainer.walk_forward(artifact, samples)
    if result.fold_count < 3:
        raise ValueError("walk-forward evaluation requires at least three folds")
    if result.slippage_multiplier < 1.5:
        raise ValueError("stress evaluation must use at least 1.5x expected slippage")
    decision = evaluate_for_promotion(
        ModelState.SHADOW,
        result.metrics(),
        policy=policy,
    )
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    dataset_hash = _dataset_hash(samples)
    cycle_id = hashlib.sha256(
        f"{artifact.model_id}:{artifact.version}:{artifact.artifact_hash}:{dataset_hash}:{created_at}".encode()
    ).hexdigest()[:24]
    return LearningCycleReceipt(
        cycle_id=cycle_id,
        asset_class=asset_class,
        model_id=artifact.model_id,
        model_version=artifact.version,
        artifact_hash=artifact.artifact_hash,
        dataset_hash=dataset_hash,
        sample_count=len(samples),
        feature_names=tuple(sorted(samples[0].features)),
        fold_count=result.fold_count,
        slippage_multiplier=result.slippage_multiplier,
        metrics=result.metrics(),
        promotion_proposal=decision,
        created_at=created_at,
    )
