"""TradeAI core checks for strategy math and real-price paper valuation."""

import pandas as pd

from ui.data import (
    STRATEGIES,
    paper_account_from_trades,
    paper_equity_curve_from_trades,
    portfolio_snapshot,
    strategy_curve,
)


def test_strategy_catalog_has_eight_entries():
    assert len(STRATEGIES) == 8
    assert {item["name"] for item in STRATEGIES} == {
        "买入 Call", "买入 Put", "牛市价差", "熊市价差",
        "买入跨式", "蝶式", "备兑看涨", "现金担保看跌",
    }


def test_strategy_curve_changes_with_quantity():
    strategy = STRATEGIES[0]
    one = strategy_curve(strategy, 0, 45, 1)
    two = strategy_curve(strategy, 0, 45, 2)
    assert len(one) == 240
    assert (two["预计损益"].abs() >= one["预计损益"].abs()).all()


def test_spreads_have_limited_payoff_and_paper_portfolio_uses_latest_prices():
    bull_spread = strategy_curve(STRATEGIES[2], 0, 45, 1)
    assert bull_spread["预计损益"].max() < 10_000
    assert bull_spread["预计损益"].min() > -10_000

    closes = pd.DataFrame(
        {"AAPL": [190.0, 200.0], "MSFT": [420.0, 430.0], "NVDA": [110.0, 120.0]},
        index=pd.date_range("2026-08-01", periods=2),
    )
    paper_positions = (
        {"symbol": "AAPL", "quantity": 50, "cost": 180.0},
        {"symbol": "MSFT", "quantity": 20, "cost": 420.0},
        {"symbol": "NVDA", "quantity": 30, "cost": 120.0},
    )
    account, positions = portfolio_snapshot(closes, paper_positions, 69_304.15)
    assert positions.set_index("标的").loc["AAPL", "最新价"] == 200.0
    assert account["positions_value"] == 50 * 200 + 20 * 430 + 30 * 120


def test_user_paper_trades_rebuild_positions_cash_and_equity():
    trades = [
        {"trade_time": "2026-08-01T15:00:00+00:00", "symbol": "AAPL", "side": "BUY", "quantity": 2, "price": 100, "commission": 0},
        {"trade_time": "2026-08-02T15:00:00+00:00", "symbol": "AAPL", "side": "SELL", "quantity": 1, "price": 120, "commission": 0},
    ]
    closes = pd.DataFrame(
        {"AAPL": [110.0, 120.0, 130.0]},
        index=pd.date_range("2026-08-01", periods=3),
    )

    paper_positions, cash = paper_account_from_trades(trades, 100_000)
    account, positions = portfolio_snapshot(closes, paper_positions, cash)
    curve = paper_equity_curve_from_trades(closes, trades, 100_000)

    assert paper_positions == ({"symbol": "AAPL", "quantity": 1.0, "cost": 100.0},)
    assert cash == 99_920
    assert account["assets"] == 100_050
    assert positions.iloc[0]["浮动盈亏"] == 30
    assert curve.iloc[-1]["账户净值"] == account["assets"]
