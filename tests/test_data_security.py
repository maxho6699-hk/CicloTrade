"""Data-provider credentials must not be placed in request URLs."""

import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from data.polygon_adapter import PolygonAdapter
from data.datasource import DataSourceError, market_data_status
from data.yfinance_adapter import YFinanceAdapter
from ui.data import load_market_history


def test_polygon_api_key_uses_authorization_header(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"results": [{"t": 1_700_000_000_000, "c": 100, "v": 50}]}).encode()

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setenv("POLYGON_API_KEY", "sensitive-key")
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.setattr("data.polygon_adapter.urlopen", fake_urlopen)
    PolygonAdapter().history(("AAPL",))

    request, timeout = requests[0]
    assert "sensitive-key" not in request.full_url
    assert "apiKey" not in request.full_url
    assert request.get_header("Authorization") == "Bearer sensitive-key"
    assert timeout == 20


def test_polygon_history_uses_requested_backtest_period(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"results": [{"t": 1_700_000_000_000, "c": 100, "v": 50}]}).encode()

    def fake_urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setenv("POLYGON_API_KEY", "sensitive-key")
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.setattr("data.polygon_adapter.urlopen", fake_urlopen)
    PolygonAdapter().history(("AAPL",), period="3y")

    path_parts = urlparse(requests[0].full_url).path.rstrip("/").split("/")
    start, end = date.fromisoformat(path_parts[-2]), date.fromisoformat(path_parts[-1])
    assert (end - start).days >= 1_090


def test_market_status_requires_adapter_and_deployment_realtime_opt_in(monkeypatch):
    class PremiumFeed:
        name = "Premium Feed"
        supports_realtime = True
        delay_minutes = None

    monkeypatch.setattr("data.datasource.get_data_source", lambda _=None: PremiumFeed())
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    monkeypatch.delenv("MARKET_DATA_REALTIME", raising=False)
    assert market_data_status()["is_realtime"] is False

    monkeypatch.setenv("MARKET_DATA_REALTIME", "true")
    status = market_data_status()
    assert status["is_realtime"] is True
    assert status["freshness"] == "实时"


def test_market_data_is_frozen_by_default_before_any_vendor_call(monkeypatch):
    monkeypatch.delenv("MARKET_DATA_ENABLED", raising=False)
    monkeypatch.setenv("POLYGON_API_KEY", "sensitive-key")
    monkeypatch.setattr("data.yfinance_adapter.yf.Search", lambda *args, **kwargs: pytest.fail("Yahoo search called"))
    monkeypatch.setattr("data.yfinance_adapter.yf.Ticker", lambda *args, **kwargs: pytest.fail("Yahoo ticker called"))
    monkeypatch.setattr("data.polygon_adapter.urlopen", lambda *args, **kwargs: pytest.fail("Polygon called"))

    yahoo = YFinanceAdapter()
    for call in (
        lambda: yahoo.search("AAPL"),
        lambda: yahoo.history(("AAPL",)),
        lambda: yahoo.bars("AAPL", "1mo", "1d"),
        lambda: yahoo.option_chain("AAPL"),
        lambda: PolygonAdapter().history(("AAPL",)),
    ):
        with pytest.raises(DataSourceError, match="行情資料模組已停用"):
            call()

    assert YFinanceAdapter.normalize_symbol("600519") == "600519.SS"
    assert market_data_status()["freshness"] == "已停用"


def test_market_history_uses_adapter_and_reports_last_bar_time(monkeypatch):
    index = pd.to_datetime(["2026-08-05", "2026-08-06"])
    closes = pd.DataFrame({"AAPL": [100.0, 101.0]}, index=index)
    volumes = pd.DataFrame({"AAPL": [10, 20]}, index=index)

    class Feed:
        def history(self, symbols, period, interval):
            assert symbols == ("AAPL",)
            assert (period, interval) == ("3mo", "1d")
            return closes, volumes

    monkeypatch.setattr("ui.data.get_data_source", lambda _: Feed())
    result_closes, _, updated_at = load_market_history(("AAPL",), "premium")

    assert result_closes.equals(closes)
    assert updated_at == index[-1].to_pydatetime()
