# Governed Quant Learning Contract

## Objective

Continuously improve the ranking of relatively robust stock and option opportunities. "Safer" means lower modeled risk under explicit constraints; it never means risk-free, guaranteed, or automatically suitable for every user.

## Separation of responsibilities

```text
market/fundamental/options data
        -> feature snapshots with availability timestamps
        -> candidate generation
        -> stock or option scoring
        -> risk and data-quality gates
        -> shadow recommendation
        -> walk-forward and stress evaluation
        -> paper-trading observation
        -> independent promotion decision
        -> official versioned recommendation
        -> outcome ledger and drift monitoring
```

Training, evaluation, promotion, recommendation publication, and order execution are separate capabilities. A model may create a shadow candidate but cannot promote itself, edit an official event, send an entitled Telegram recommendation, or submit an order.

## Stock ranking

Rank eligible stocks using dated evidence across:

- business quality and financial durability
- valuation relative to history and peers
- trend, momentum, ATR, realized volatility, and drawdown
- liquidity, spread, turnover, and gap risk
- market regime, sector breadth, event and sentiment pressure
- institutional/flow evidence when licensed data exists

The stability score must penalize stale or missing inputs, illiquidity, extreme volatility, large drawdowns, concentrated event risk, and unstable parameters. It must display the strongest positive and negative contributors.

## Option ranking

An option contract is not eligible without current bid/ask, volume, open interest, IV, Greeks, contract metadata, and underlying context. Score:

- liquidity and bid/ask width
- IV rank, volatility risk premium, skew, and term structure
- delta/gamma/theta/vega fit for the intended strategy
- maximum loss, breakeven, probability model, and stress P&L
- underlying trend, ATR buffer, support/resistance, and event risk

Zero-liquidity, stale, ambiguous, or very wide-spread contracts are non-tradeable rather than merely low-ranked. Short options always expose assignment, margin, and gap risk.

## Evaluation and anti-overfitting gates

- point-in-time features only; every value carries `available_at`
- survivorship-bias-aware universes including delisted names where data licensing permits
- expanding walk-forward validation and untouched out-of-sample periods
- realistic and 1.5-2x stressed slippage, commission, rejection, and partial-fill models
- evaluation across bull, bear, high/low volatility, trending, and sideways regimes
- parameter sensitivity seeks stable plateaus, not a single optimal point
- at least 30 trades for exploration, 100 preferred, and 200+ for high confidence
- multiple-testing correction and a registered hypothesis before experiments
- calibration, drawdown, turnover, tail loss, and stability matter more than headline return

Models with missing data, insufficient samples, severe drift, unstable parameters, or out-of-sample collapse are rejected or held in shadow mode.

## Model lifecycle

Allowed states:

`draft -> trained -> shadow -> paper_qualified -> approved -> active -> degraded -> retired`

Every version records code/data snapshot hashes, feature schema, market universe, training and validation windows, parameters, costs, metrics by regime, reviewer, approval receipt, activation time, and rollback target.

Promotion requires an independent gate. The active model remains unchanged when a challenger fails. Rollback is immediate and never rewrites historical recommendations.

## User-facing output

Display recommendation range, confidence/calibration, data freshness, risk grade, target/stop framework, scenario outcomes, negative evidence, and the exact active model version. Do not expose an unexplained AI score.

Mystic and social-media editorial content are never model features for an official trading recommendation. They may appear only in the isolated entertainment/sentiment surface.

## Initial market scope

Train and validate separate models for US equities and A-shares because their calendars, price limits, liquidity, disclosure, and price-color conventions differ. Hong Kong equities and crypto remain disabled capability adapters until licensed data, point-in-time histories, risk models, and market-specific validation exist.
