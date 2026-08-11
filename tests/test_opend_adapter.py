from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from core.compat import UTC
from data.datasource import DataSourceError
from data.opend_adapter import OpenDAdapter
from data.opend_control import OpenDStatus


class OpenD:
    update_time = "2026-08-07 16:00:00"

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
                "code": codes, "update_time": [self.update_time] * len(codes),
                "last_price": [5.2] * len(codes), "bid_price": [5.1] * len(codes),
                "ask_price": [5.3] * len(codes), "volume": [100] * len(codes),
                "option_open_interest": [500] * len(codes), "option_implied_volatility": [0.31] * len(codes),
                "option_delta": [0.5] * len(codes), "option_gamma": [0.02] * len(codes),
                "option_theta": [-0.1] * len(codes), "option_vega": [0.2] * len(codes),
                "option_rho": [0.05] * len(codes),
            }
        )

    def get_user_info(self, _fields):
        from futu import RET_OK

        return RET_OK, {"us_qot_right": "LV2", "us_option_qot_right": "LV1"}


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


def test_opend_stock_quote_uses_runtime_entitlements_not_environment_claims(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    adapter = OpenDAdapter()
    monkeypatch.setattr(adapter, "_context", lambda: OpenD())
    monkeypatch.setattr(
        adapter, "_utc_now", lambda: datetime(2026, 8, 7, 20, 4, tzinfo=UTC), raising=False
    )

    quote = adapter.stock_quote("AAPL")

    assert quote["us_qot_right"] == "LV2"
    assert quote["us_option_qot_right"] == "LV1"
    assert quote["us_realtime_entitlement"] is True
    assert quote["us_option_realtime_entitlement"] is True
    assert quote["actionable_snapshot"] is True
    assert quote["quote_at"] == "2026-08-07T16:00:00-04:00"


def test_opend_unparseable_quote_timestamp_fails_closed():
    assert OpenDAdapter._us_market_timestamp("not-a-time") is None


@pytest.mark.parametrize(
    ("update_time", "expected_actionable"),
    (
        ("2026-08-07 15:56:00", True),
        ("2026-08-07 15:54:59", False),
        ("2026-08-07 16:00:01", True),
        ("2026-08-07 16:01:01", False),
        ("not-a-time", False),
    ),
)
def test_opend_actionable_snapshot_allows_only_parseable_recent_time_with_60s_clock_skew(
    monkeypatch, update_time, expected_actionable
):
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    context = OpenD()
    context.update_time = update_time
    adapter = OpenDAdapter()
    monkeypatch.setattr(adapter, "_context", lambda: context)
    monkeypatch.setattr(
        adapter, "_utc_now", lambda: datetime(2026, 8, 7, 20, 0, tzinfo=UTC), raising=False
    )

    quote = adapter.stock_quote("AAPL")

    assert quote["actionable_snapshot"] is expected_actionable


def test_opend_naive_us_bars_are_localized_to_the_exchange_timezone(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_ENABLED", "true")
    adapter = OpenDAdapter()
    monkeypatch.setattr(adapter, "_context", lambda: OpenD())

    bars = adapter.bars("AAPL", "5d", "1d")

    assert str(bars.index.tz) == "America/New_York"
    assert bars.index[-1].isoformat() == "2026-08-07T16:00:00-04:00"


@pytest.mark.parametrize(
    ("state", "message", "expected_phase"),
    [
        ("verification_required", "OpenD 正在等待图形验证码。", "图形验证"),
        (
            "phone_verification_required",
            "OpenD 正在等待手机验证码。",
            "手机验证",
        ),
    ],
)
def test_opend_context_fails_fast_when_gateway_waits_for_authentication(
    monkeypatch, state, message, expected_phase
):
    monkeypatch.setattr(
        "data.opend_adapter.probe_opend_status",
        lambda *_args, **_kwargs: OpenDStatus(state, message),
    )

    with pytest.raises(DataSourceError, match=rf"等待登录或{expected_phase}"):
        OpenDAdapter()._context()


def test_opend_context_connects_synchronously_only_after_probe_is_ready(monkeypatch):
    context = OpenD()
    options = {}
    monkeypatch.setattr(
        "data.opend_adapter.probe_opend_status",
        lambda *_args, **_kwargs: OpenDStatus("ready", "OpenD 已连接。"),
    )
    monkeypatch.setattr(
        "futu.OpenQuoteContext", lambda **kwargs: options.update(kwargs) or context
    )

    assert OpenDAdapter()._context() is context
    assert options == {"host": "127.0.0.1", "port": 11111}
