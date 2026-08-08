from __future__ import annotations

import pandas as pd
import pytest

from data.datasource import DataSourceError
from data.opend_adapter import OpenDAdapter


class OpenD:
    def close(self):
        pass

    def request_history_kline(self, *_args, **_kwargs):
        from futu import RET_OK

        return RET_OK, pd.DataFrame(
            {
                "time_key": ["2026-08-06 16:00:00", "2026-08-07 16:00:00"],
                "open": [199, 201], "high": [202, 204], "low": [198, 200],
                "close": [201, 203], "volume": [1000, 1200],
            }
        ), None

    def get_option_chain(self, *_args, **_kwargs):
        from futu import RET_OK

        return RET_OK, pd.DataFrame(
            {
                "code": ["US.AAPL260918C210000", "US.AAPL260918P210000"],
                "strike_time": ["2026-09-18", "2026-09-18"],
                "strike_price": [210, 210],
                "option_type": ["CALL", "PUT"],
            }
        )

    def get_stock_basicinfo(self, *_args, **_kwargs):
        from futu import RET_OK

        return RET_OK, pd.DataFrame(
            {"code": ["US.AAPL", "US.MSFT"], "name": ["Apple Inc.", "Microsoft Corp."]}
        )

    def get_market_snapshot(self, codes):
        from futu import RET_OK

        return RET_OK, pd.DataFrame(
            {
                "code": codes, "update_time": ["2026-08-07 16:00:00"] * len(codes),
                "last_price": [5.2] * len(codes), "bid_price": [5.1] * len(codes),
                "ask_price": [5.3] * len(codes), "volume": [100] * len(codes),
                "option_open_interest": [500] * len(codes), "option_implied_volatility": [0.31] * len(codes),
                "option_delta": [0.5] * len(codes), "option_gamma": [0.02] * len(codes),
                "option_theta": [-0.1] * len(codes), "option_vega": [0.2] * len(codes),
                "option_rho": [0.05] * len(codes),
            }
        )


def test_opend_history_and_option_greeks(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    adapter = OpenDAdapter()
    monkeypatch.setattr(adapter, "_context", lambda: OpenD())
    closes, volumes = adapter.history(("AAPL",), period="5d")
    assert closes["AAPL"].iloc[-1] == 203 and volumes["AAPL"].iloc[-1] == 1200
    expiry, calls, puts = adapter.option_chain("AAPL")
    assert expiry == "2026-09-18" and len(calls) == len(puts) == 1
    assert calls.iloc[0]["impliedVolatility"] == 0.31 and calls.iloc[0]["delta"] == 0.5
    assert adapter.search("Apple") == [
        {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "Futu OpenD", "type": "股票"}
    ]


def test_opend_context_fails_fast_when_gateway_waits_for_authentication(monkeypatch):
    class Connecting:
        closed = False

        def _is_ready(self):
            return False

        def close(self):
            self.closed = True

    context = Connecting()
    options = {}
    monkeypatch.setenv("OPEND_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr("futu.OpenQuoteContext", lambda **kwargs: options.update(kwargs) or context)
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr("data.opend_adapter.monotonic", lambda: next(ticks))
    monkeypatch.setattr("data.opend_adapter.sleep", lambda _seconds: None)

    with pytest.raises(DataSourceError, match="等待登录或图形验证"):
        OpenDAdapter()._context()

    assert context.closed is True
    assert options["is_async_connect"] is True
