from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from data.akshare_adapter import AKShareAdapter
from data.datasource import market_data_status
from data.yfinance_adapter import YahooOptionExpiryUnavailableError, YFinanceAdapter


def test_akshare_search_and_bars_are_normalized_without_network(monkeypatch):
    class FakeAKShare:
        @staticmethod
        def stock_zh_a_spot_em():
            return pd.DataFrame({"代码": ["600519", "000001"], "名称": ["贵州茅台", "平安银行"]})

        @staticmethod
        def stock_zh_a_hist(**kwargs):
            assert kwargs["symbol"] == "600519" and kwargs["period"] == "daily"
            return pd.DataFrame({
                "日期": ["2026-08-07", "2026-08-08"], "开盘": [1500, 1501],
                "最高": [1510, 1512], "最低": [1490, 1499], "收盘": [1505, 1510], "成交量": [10, 20],
            })

    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.setattr("data.akshare_adapter.ak", FakeAKShare())
    adapter = AKShareAdapter()

    assert adapter.search("茅台") == [{
        "symbol": "600519", "name": "贵州茅台", "exchange": "上海", "type": "股票",
    }]
    bars = adapter.bars("600519.SS", "1mo", "1d")
    assert list(bars.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert bars.iloc[-1].to_dict() == {"Open": 1501, "High": 1512, "Low": 1499, "Close": 1510, "Volume": 20}
    quote = adapter.stock_quote("600519")
    assert quote["source"] == "AKShare" and quote["last"] == 1510.0
    assert quote["bid"] is quote["ask"] is quote["spread"] is None
    assert quote["is_realtime"] is False and quote["actionable_quote"] is False


def test_yahoo_research_quote_has_no_executable_bid_or_ask(monkeypatch):
    class FakeTicker:
        def history(self, **kwargs):
            assert kwargs == {"period": "5d", "interval": "1d", "auto_adjust": False, "prepost": False}
            return pd.DataFrame(
                {"Open": [100, 101], "High": [102, 103], "Low": [99, 100], "Close": [101, 102], "Volume": [10, 20]},
                index=pd.to_datetime(["2026-08-07T00:00:00Z", "2026-08-08T00:00:00Z"]),
            )

    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.setattr("data.yfinance_adapter.yf.Ticker", lambda symbol: FakeTicker())
    quote = YFinanceAdapter().stock_quote("AAPL")

    assert quote["last"] == 102.0 and quote["prev_close"] == 101.0
    assert quote["bid"] is quote["ask"] is quote["spread"] is None
    assert quote["is_realtime"] is False and quote["actionable_quote"] is False
    assert quote["verification"] == "delayed_research_quote"


def test_yahoo_option_chain_requires_the_exact_requested_expiry(monkeypatch):
    requested = []

    class FakeTicker:
        options = ("2026-09-18", "2026-10-16")

        def option_chain(self, expiry):
            requested.append(expiry)
            calls = pd.DataFrame([{"contractSymbol": "AAPL261016C00210000"}])
            puts = pd.DataFrame([{"contractSymbol": "AAPL261016P00210000"}])
            return SimpleNamespace(calls=calls, puts=puts)

    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.setattr("data.yfinance_adapter.yf.Ticker", lambda symbol: FakeTicker())
    adapter = YFinanceAdapter()

    selected, expiries, calls, puts = adapter.option_chain_with_expiries("AAPL", "2026-10-16")
    assert selected == "2026-10-16" and expiries == ["2026-09-18", "2026-10-16"]
    assert requested == ["2026-10-16"]
    assert calls.iloc[0]["contractSymbol"] == "AAPL261016C00210000"
    assert puts.iloc[0]["contractSymbol"] == "AAPL261016P00210000"

    with pytest.raises(YahooOptionExpiryUnavailableError):
        adapter.option_chain_with_expiries("AAPL", "2026-12-18")
    assert requested == ["2026-10-16"]


def test_yahoo_option_bars_only_request_the_exact_contract_symbol(monkeypatch):
    received = []

    class FakeTicker:
        def __init__(self, symbol):
            received.append(symbol)

        def history(self, **kwargs):
            assert kwargs == {"period": "6mo", "interval": "1d", "auto_adjust": False, "prepost": False}
            return pd.DataFrame(
                {"Open": [5], "High": [6], "Low": [4.5], "Close": [5.5], "Volume": [120]},
                index=pd.to_datetime(["2026-08-08T00:00:00Z"]),
            )

    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.setattr("data.yfinance_adapter.yf.Ticker", FakeTicker)

    frame = YFinanceAdapter().option_bars("AAPL260918C00210000", "6mo", "1d")
    assert received == ["AAPL260918C00210000"]
    assert frame.iloc[0]["Close"] == 5.5


def test_status_keeps_successful_opend_request_unverified_even_when_configured(monkeypatch):
    class OpenDSource:
        name = "Futu OpenD"
        supports_realtime = True
        delay_minutes = None

    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    status = market_data_status(source=OpenDSource(), request_succeeded=True, realtime_verified=False)

    assert status["source"] == "Futu OpenD"
    assert status["configuration_allows_realtime"] is True
    assert status["request_succeeded"] is True
    assert status["is_realtime"] is False
    assert status["verification"] == "unverified_realtime"
