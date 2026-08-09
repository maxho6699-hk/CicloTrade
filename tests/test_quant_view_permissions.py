from __future__ import annotations

import pandas as pd

from core.database import DatabaseManager
from core.quant_journal import QuantJournal
from ui.pages.actions import _history_rows, _professional_frame, _safe_action_frame
from ui.pages.logs import _quant_rows
from ui.pages.terminal import _candlestick, _events_for_symbol, _indicators


def _stock(symbol: str, target: float, delta: float, price: float) -> dict:
    return {
        "market": "US",
        "instrument_type": "stock",
        "symbol": symbol,
        "target_quantity": target,
        "quantity_delta": delta,
        "price": price,
    }


def _option(target: float, delta: float, price: float) -> dict:
    return {
        "market": "US",
        "instrument_type": "option",
        "symbol": "AAPL",
        "option_expiry": "2026-09-18",
        "option_right": "CALL",
        "option_strike": 210,
        "target_quantity": target,
        "quantity_delta": delta,
        "price": price,
    }


def test_views_use_execution_deltas_and_keep_option_payloads_out(tmp_path, monkeypatch):
    database = DatabaseManager(str(tmp_path / "quant-view.db"))
    journal = QuantJournal(database)
    opened = journal.append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="open-aapl",
        strategy_name="trend",
        strategy_version="1",
        occurred_at="2026-08-06T12:00:00+00:00",
        legs=[_stock("AAPL", 10, 10, 100)],
    )
    journal.append_reversal(
        source="pytest",
        external_event_id="reverse-aapl",
        corrects_event_id=opened["id"],
        occurred_at="2026-08-06T12:05:00+00:00",
        metadata={"strike": 210},
    )
    option_event = journal.append_event(
        ledger_key="tradeai-system",
        source="pytest",
        external_event_id="open-option",
        strategy_name="long-call",
        strategy_version="1",
        occurred_at="2026-08-06T12:10:00+00:00",
        legs=[_option(1, 1, 2.5)],
        metadata={"option_strike": 210},
    )
    events = journal.list_events("tradeai-system")

    action_rows = _history_rows(events, journal, include_options=False)
    assert "撤销 · 卖出 / 减持" in set(action_rows["动作"])
    assert -10 in set(action_rows["数量变化"].dropna())
    assert "期权事件（升级后查看）" in set(action_rows["动作"])
    assert "210" not in action_rows.to_string()

    log_rows = _quant_rows(events, journal, include_options=False)
    assert -10 in set(log_rows["数量变化"].dropna())
    assert "期权事件（升级后查看）" in set(log_rows["动作"])
    assert "210" not in log_rows.to_string()

    monkeypatch.setenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
    terminal_rows = _events_for_symbol("AAPL", "美股", journal, include_options=False)
    assert [row["leg"]["quantity_delta"] for row in terminal_rows] == [10, -10]
    assert all("legs" not in row for row in terminal_rows)
    assert all(row["metadata"] == {} for row in terminal_rows)
    assert all(row["id"] != option_event["id"] for row in terminal_rows)

    index = pd.date_range("2026-08-06T12:00:00Z", periods=3, freq="5min")
    close = pd.Series([100.0, 101.0, 102.0], index=index)
    bars = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000,
        },
        index=index,
    )
    figure = _candlestick(_indicators(bars), "AAPL", "5m", events=terminal_rows)
    inactive = next(trace for trace in figure.data if trace.name == "已更正 / 撤销")
    assert any("记录价 --" in text for text in inactive.text)


def test_action_payloads_are_tier_filtered_before_rendering():
    frame = pd.DataFrame(
        [
            {
                "市场": "美股",
                "标的": "AAPL",
                "最新价": 200,
                "货币": "USD",
                "评分": 70,
                "观点": "强势偏多",
                "正股建议": "观察",
                "期权策略": "买入 Call",
                "期权建议": "45 DTE · 买入 210 Call",
                "DTE": 45,
                "行权价偏移": 3,
                "止损参考": 180,
                "目标参考": 220,
                "依据": "量价",
                "供应商原始字段": "secret",
            }
        ]
    )

    free = _safe_action_frame(frame, "免费版")
    assert "供应商原始字段" not in free.columns
    assert "DTE" not in free.columns and "行权价偏移" not in free.columns
    assert "210" not in free.to_string()

    standard = _safe_action_frame(frame, "标准版")
    assert "买入 Call" in standard.iloc[0]["期权建议"]
    assert "210" not in standard.to_string()

    professional = _professional_frame(frame, "专业版")
    assert "供应商原始字段" not in professional.columns
    assert professional.iloc[0]["期权建议"] == "45 DTE · 买入 210 Call"
