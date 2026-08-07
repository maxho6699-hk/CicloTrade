import pandas as pd
import pytest
import yfinance as yf

import ui.recommendations as recommendations
from ui.recommendations import load_recommendations, score_candidates
from data.yfinance_adapter import YFinanceAdapter


def test_recommendations_turn_trend_data_into_actionable_candidates():
    index = pd.date_range("2025-01-01", periods=80, freq="B")
    closes = pd.DataFrame(
        {
            "UP": [100 + value for value in range(80)],
            "DOWN": [200 - value for value in range(80)],
        },
        index=index,
        dtype=float,
    )
    volumes = pd.DataFrame({"UP": 1_000_000, "DOWN": 900_000}, index=index)

    result = score_candidates(closes, volumes).set_index("标的")

    assert result.loc["UP", "评分"] >= 60
    assert result.loc["UP", "期权策略"] == "买入 Call"
    assert result.loc["DOWN", "评分"] <= -60
    assert result.loc["DOWN", "期权策略"] == "买入 Put"


def test_a_share_symbols_and_option_boundaries_are_explicit():
    assert YFinanceAdapter.normalize_symbol("600519") == "600519.SS"
    assert YFinanceAdapter.normalize_symbol("300750") == "300750.SZ"
    index = pd.date_range("2025-01-01", periods=80, freq="B")
    closes = pd.DataFrame({"600519": range(100, 180), "510300": range(10, 90)}, index=index, dtype=float)
    volumes = pd.DataFrame({"600519": 1_000_000, "510300": 2_000_000}, index=index)

    result = score_candidates(closes, volumes, "A股").set_index("标的")

    assert result.loc["600519", "期权策略"] == "暂不买期权"
    assert "期权链未接入" in result.loc["600519", "期权建议"]
    assert "上交所期权链复核" in result.loc["510300", "期权建议"]
    assert set(result["货币"]) == {"CNY"}


def test_symbol_search_keeps_only_requested_market(monkeypatch):
    quotes = [
        {"symbol": "MSFT", "quoteType": "EQUITY", "longname": "Microsoft", "exchDisp": "NASDAQ"},
        {"symbol": "MSF.DE", "quoteType": "EQUITY", "longname": "Microsoft", "exchDisp": "XETRA"},
        {"symbol": "MSFTX-USD", "quoteType": "CRYPTOCURRENCY", "longname": "Token", "exchDisp": "CCC"},
        {"symbol": "600519.SS", "quoteType": "EQUITY", "longname": "Kweichow Moutai", "exchDisp": "Shanghai"},
    ]

    class SearchResult:
        def __init__(self, *args, **kwargs):
            self.quotes = quotes

    monkeypatch.setattr(yf, "Search", SearchResult)

    assert [item["symbol"] for item in YFinanceAdapter.search("Microsoft", "美股")] == ["MSFT"]
    assert [item["symbol"] for item in YFinanceAdapter.search("600519", "A股")] == ["600519.SS"]


def test_recommendation_publish_gate_runs_before_cached_data(monkeypatch):
    class StoppedAdminService:
        def control_enabled(self, key, default):
            return False

    monkeypatch.setattr(recommendations, "AdminService", StoppedAdminService)

    with pytest.raises(RuntimeError, match="研究后台暂停"):
        load_recommendations("美股")
