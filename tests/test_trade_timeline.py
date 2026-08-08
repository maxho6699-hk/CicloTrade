from core.trade_timeline import project_trade_cycles, summarize_trade_cycles


def _leg(
    delta,
    target,
    price,
    *,
    instrument_type="stock",
    commission=0,
    symbol="AAPL",
    expiry="2026-09-18",
    right="CALL",
    strike=220,
):
    if instrument_type == "option":
        key = f"US:OPTION:{symbol}:{expiry}:{right}:{strike}"
        multiplier = 100
    else:
        key = f"US:STOCK:{symbol}"
        multiplier = 1
    return {
        "market": "US",
        "instrument_type": instrument_type,
        "instrument_key": key,
        "symbol": symbol,
        "currency": "USD",
        "option_expiry": expiry if instrument_type == "option" else None,
        "option_right": right if instrument_type == "option" else None,
        "option_strike": strike if instrument_type == "option" else None,
        "target_quantity": target,
        "quantity_delta": delta,
        "price": price,
        "multiplier": multiplier,
        "commission": commission,
    }


def _event(index, leg, *, active=True):
    return {
        "id": index,
        "active": active,
        "occurred_at": f"2026-08-{index:02d}T14:30:00+00:00",
        "strategy_name": "趋势交叉",
        "legs": [leg],
    }


def test_zero_to_zero_is_one_trade_and_reopen_starts_another():
    events = [
        _event(1, _leg(10, 10, 100, commission=1)),
        _event(2, _leg(5, 15, 110, commission=1)),
        _event(3, _leg(-8, 7, 120, commission=1)),
        _event(4, _leg(-7, 0, 130, commission=1)),
        _event(5, _leg(2, 2, 125, commission=0.5)),
    ]
    cycles = project_trade_cycles(events, "stock", marks={"US:STOCK:AAPL": 130})

    assert len(cycles) == 2
    reopened, closed = cycles
    assert reopened["closed_at"] is None and reopened["current_quantity"] == 2
    assert reopened["unrealized_pnl"] == 10
    assert closed["opened_quantity"] == 15 and closed["closed_quantity"] == 15
    assert closed["realized_pnl"] == 316
    assert closed["return"] == 316 / 1550

    summary = summarize_trade_cycles(cycles)
    assert summary["profitable"] == 1 and summary["losing"] == 0
    assert summary["open"] == 1 and summary["currencies"]["USD"]["unrealized_pnl"] == 10


def test_sign_crossing_closes_old_trade_and_opens_opposite_trade():
    events = [
        _event(1, _leg(5, 5, 100, commission=1)),
        _event(2, _leg(-8, -3, 110, commission=3)),
    ]
    cycles = project_trade_cycles(events, "stock", marks={"US:STOCK:AAPL": 100})

    assert len(cycles) == 2
    short_cycle, long_cycle = cycles
    assert short_cycle["direction"] == "short" and short_cycle["current_quantity"] == -3
    assert short_cycle["commission"] == 1.125 and short_cycle["unrealized_pnl"] == 30
    assert long_cycle["closed_at"] is not None
    assert long_cycle["realized_pnl"] == 47.125


def test_options_use_contract_multiplier_and_stay_separate_from_stocks():
    events = [
        _event(1, _leg(1, 1, 5, instrument_type="option", commission=1)),
        _event(2, _leg(-1, 0, 6, instrument_type="option", commission=1)),
        _event(3, _leg(1, 1, 200, instrument_type="stock")),
    ]
    options = project_trade_cycles(events, "option")
    stocks = project_trade_cycles(events, "stock")

    assert len(options) == 1 and options[0]["realized_pnl"] == 98
    assert options[0]["instrument_key"] == "US:OPTION:AAPL:2026-09-18:CALL:220"
    assert len(stocks) == 1 and stocks[0]["instrument_type"] == "stock"


def test_superseded_events_are_not_counted_twice():
    events = [
        _event(1, _leg(10, 10, 100), active=False),
        _event(2, _leg(10, 10, 110)),
        _event(3, _leg(-10, 0, 120)),
    ]
    cycles = project_trade_cycles(events, "stock")

    assert len(cycles) == 1
    assert cycles[0]["entry_notional"] == 1100
    assert cycles[0]["realized_pnl"] == 100


def test_open_trade_without_fresh_mark_does_not_fabricate_pnl():
    cycles = project_trade_cycles([_event(1, _leg(10, 10, 100))], "stock")
    summary = summarize_trade_cycles(cycles)

    assert cycles[0]["unrealized_pnl"] is None
    assert summary["currencies"]["USD"]["open_missing_marks"] == 1
