"""Calibration and paper-research metrics for completed earnings events."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from core.earnings_forecast_contracts import EarningsContractError


DIRECTIONS = ("UP", "DOWN", "FLAT")


@dataclass(frozen=True)
class ForecastMetricObservation:
    p_up: float
    p_down: float
    p_flat: float
    actual_return_pct: float
    flat_band_pct: float
    price_p10: float
    price_p90: float
    actual_price: float
    paper_pnl_net: float


@dataclass(frozen=True)
class ForecastMetrics:
    sample_size: int
    direction_accuracy: float
    multiclass_brier_score: float
    log_loss: float
    expected_calibration_error: float
    average_confidence_gap: float
    interval_coverage: float
    average_interval_width: float
    overconfidence_rate: float
    high_confidence_sample_size: int
    paper_total_pnl: float
    paper_max_drawdown: float


def _finite(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EarningsContractError(f"{label} must be finite")
    return float(value)


def _validate(item: ForecastMetricObservation) -> ForecastMetricObservation:
    if not isinstance(item, ForecastMetricObservation):
        raise EarningsContractError("metrics require ForecastMetricObservation values")
    probabilities = tuple(_finite(value, "probability") for value in (item.p_up, item.p_down, item.p_flat))
    if any(value < 0 or value > 1 for value in probabilities) or not math.isclose(
        sum(probabilities), 1.0, abs_tol=1e-9
    ):
        raise EarningsContractError("metric probabilities must sum to one")
    flat_band = _finite(item.flat_band_pct, "flat_band_pct")
    p10 = _finite(item.price_p10, "price_p10")
    p90 = _finite(item.price_p90, "price_p90")
    actual_price = _finite(item.actual_price, "actual_price")
    if flat_band < 0 or p10 <= 0 or actual_price <= 0 or p90 < p10:
        raise EarningsContractError("metric interval or flat band is invalid")
    return ForecastMetricObservation(
        *probabilities,
        _finite(item.actual_return_pct, "actual_return_pct"),
        flat_band,
        p10,
        p90,
        actual_price,
        _finite(item.paper_pnl_net, "paper_pnl_net"),
    )


def _actual_direction(item: ForecastMetricObservation) -> int:
    if item.actual_return_pct > item.flat_band_pct:
        return 0
    if item.actual_return_pct < -item.flat_band_pct:
        return 1
    return 2


def _predicted_direction(probabilities: tuple[float, float, float]) -> int:
    maximum = max(probabilities)
    tied = [index for index, value in enumerate(probabilities) if math.isclose(value, maximum)]
    return 2 if 2 in tied else tied[0]


def _ece(confidences: list[float], correctness: list[int], bins: int) -> float:
    total = len(confidences)
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            member for member, confidence in enumerate(confidences)
            if lower <= confidence < upper or index == bins - 1 and confidence == 1.0
        ]
        if not members:
            continue
        mean_confidence = sum(confidences[member] for member in members) / len(members)
        mean_accuracy = sum(correctness[member] for member in members) / len(members)
        error += len(members) / total * abs(mean_confidence - mean_accuracy)
    return error


def _max_drawdown(pnls: Iterable[float], starting_equity: float) -> float:
    equity = starting_equity
    peak = starting_equity
    maximum = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak)
    return maximum


def compute_forecast_metrics(
    observations: Iterable[ForecastMetricObservation],
    *,
    starting_equity: float,
    bins: int = 10,
    overconfidence_threshold: float = 0.70,
) -> ForecastMetrics:
    """Aggregate one headline observation per earnings event.

    Callers should use the D-1 snapshot for headline metrics. Other countdown
    days are separate cohorts so one event is not counted seven times.
    """
    starting_equity = _finite(starting_equity, "starting_equity")
    if starting_equity <= 0:
        raise EarningsContractError("starting_equity must be positive")
    if isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 100:
        raise EarningsContractError("bins must be between 1 and 100")
    if not 0 <= overconfidence_threshold <= 1:
        raise EarningsContractError("overconfidence_threshold must be between zero and one")
    items = [_validate(item) for item in observations]
    if not items:
        return ForecastMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)

    correctness: list[int] = []
    confidences: list[float] = []
    brier = 0.0
    log_loss = 0.0
    covered = 0
    interval_width = 0.0
    high_confidence_wrong = 0
    high_confidence_count = 0
    pnls: list[float] = []
    for item in items:
        probabilities = (item.p_up, item.p_down, item.p_flat)
        actual = _actual_direction(item)
        predicted = _predicted_direction(probabilities)
        correct = int(predicted == actual)
        confidence = max(probabilities)
        correctness.append(correct)
        confidences.append(confidence)
        brier += sum(
            (probability - int(index == actual)) ** 2
            for index, probability in enumerate(probabilities)
        )
        log_loss -= math.log(max(probabilities[actual], 1e-15))
        covered += int(item.price_p10 <= item.actual_price <= item.price_p90)
        interval_width += item.price_p90 - item.price_p10
        if confidence >= overconfidence_threshold:
            high_confidence_count += 1
            high_confidence_wrong += 1 - correct
        pnls.append(item.paper_pnl_net)

    count = len(items)
    return ForecastMetrics(
        sample_size=count,
        direction_accuracy=sum(correctness) / count,
        multiclass_brier_score=brier / count,
        log_loss=log_loss / count,
        expected_calibration_error=_ece(confidences, correctness, bins),
        average_confidence_gap=sum(
            confidence - correct for confidence, correct in zip(confidences, correctness)
        ) / count,
        interval_coverage=covered / count,
        average_interval_width=interval_width / count,
        overconfidence_rate=(
            high_confidence_wrong / high_confidence_count if high_confidence_count else 0.0
        ),
        high_confidence_sample_size=high_confidence_count,
        paper_total_pnl=sum(pnls),
        paper_max_drawdown=_max_drawdown(pnls, starting_equity),
    )
