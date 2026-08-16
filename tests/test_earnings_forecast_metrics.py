from __future__ import annotations

import math

from core.earnings_forecast_metrics import (
    ForecastMetricObservation,
    compute_forecast_metrics,
)


def test_metrics_cover_accuracy_calibration_interval_and_paper_drawdown():
    observations = [
        ForecastMetricObservation(0.8, 0.1, 0.1, 4.0, 1.0, 90.0, 115.0, 110.0, 200.0),
        ForecastMetricObservation(0.7, 0.2, 0.1, -5.0, 1.0, 90.0, 112.0, 95.0, -100.0),
        ForecastMetricObservation(0.2, 0.2, 0.6, 0.2, 1.0, 96.0, 104.0, 101.0, 50.0),
    ]

    metrics = compute_forecast_metrics(observations, starting_equity=1_000.0, bins=5)

    assert metrics.sample_size == 3
    assert math.isclose(metrics.direction_accuracy, 2 / 3)
    assert math.isclose(metrics.interval_coverage, 1.0)
    assert math.isclose(metrics.overconfidence_rate, 0.5)
    assert math.isclose(metrics.paper_total_pnl, 150.0)
    assert math.isclose(metrics.paper_max_drawdown, 100 / 1200)
    assert 0 < metrics.multiclass_brier_score < 1
    assert 0 < metrics.log_loss
    assert 0 <= metrics.expected_calibration_error <= 1


def test_metrics_are_empty_safe():
    metrics = compute_forecast_metrics([], starting_equity=1_000.0)

    assert metrics.sample_size == 0
    assert metrics.direction_accuracy == 0
    assert metrics.paper_total_pnl is None
    assert metrics.paper_max_drawdown is None


def test_metrics_mark_paper_performance_unavailable_when_equity_inputs_are_missing():
    observation = ForecastMetricObservation(
        p_up=0.6, p_down=0.2, p_flat=0.2,
        actual_return_pct=2.0, flat_band_pct=1.0,
        price_p10=95.0, price_p90=110.0, actual_price=105.0,
        paper_pnl_net=None,
    )

    metrics = compute_forecast_metrics([observation], starting_equity=1_000.0)

    assert metrics.sample_size == 1
    assert metrics.paper_total_pnl is None
    assert metrics.paper_max_drawdown is None
