import pandas as pd

import ui.pages.terminal as terminal
from ui.pages.terminal import _candlestick, _indicators, _merge_symbols, _volume_profile


def _bars() -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=20, freq="5min", tz="UTC")
    close = pd.Series(range(100, 120), index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000,
        },
        index=index,
    )


def test_indicators_add_technical_series_without_changing_bars():
    frame = _indicators(_bars())

    assert len(frame) == 20
    assert {"EMA20", "EMA50", "VWAP", "RSI", "ATR"}.issubset(frame.columns)
    assert frame["VWAP"].iloc[-1] > frame["VWAP"].iloc[0]
    assert frame["RSI"].iloc[-1] > 50


def test_volume_profile_returns_readable_price_buckets():
    profile = _volume_profile(_bars())

    assert list(profile.columns) == ["价格区间", "成交量"]
    assert len(profile) >= 4
    assert profile["成交量"].sum() == 20_000


def test_watchlist_merge_normalizes_and_deduplicates_each_market():
    assert _merge_symbols(("AAPL", "msft"), ("AAPL", "BRK-B"), market="美股") == (
        "AAPL",
        "MSFT",
        "BRK-B",
    )
    assert _merge_symbols(("600519",), ("600519.SS", "300750.SZ"), market="A股") == (
        "600519",
        "300750",
    )


def test_terminal_bars_use_selected_adapter(monkeypatch):
    expected = _bars()

    class Feed:
        def bars(self, symbol, period, interval):
            assert (symbol, period, interval) == ("AAPL", "5d", "5m")
            return expected

    terminal._bars.clear()
    monkeypatch.setattr(
        "ui.pages.terminal.get_data_source",
        lambda name: Feed() if name == "premium" else None,
    )
    frame, updated_at = terminal._bars("AAPL", "5d", "5m", "premium")

    assert frame.equals(expected)
    assert updated_at == expected.index[-1].to_pydatetime()


def test_candlestick_marks_active_and_superseded_ledger_events():
    frame = _indicators(_bars())
    events = [
        {
            "id": 1,
            "source": "pytest",
            "external_event_id": "buy-1",
            "strategy_name": "趋势策略",
            "strategy_version": "v1",
            "occurred_at": frame.index[5].isoformat(),
            "active": True,
            "leg": {
                "instrument_type": "stock",
                "instrument_key": "US:STOCK:AAPL",
                "symbol": "AAPL",
                "quantity_delta": 2.0,
                "price": 104.5,
            },
        },
        {
            "id": 2,
            "source": "pytest",
            "external_event_id": "old-1",
            "strategy_name": "趋势策略",
            "strategy_version": "v1",
            "occurred_at": frame.index[8].isoformat(),
            "active": False,
            "leg": {
                "instrument_type": "stock",
                "instrument_key": "US:STOCK:AAPL",
                "symbol": "AAPL",
                "quantity_delta": -1.0,
                "price": 107.5,
            },
        },
    ]

    names = {trace.name for trace in _candlestick(frame, "AAPL", "5m", events=events).data}

    assert {"正股买入", "已更正 / 撤销"}.issubset(names)
