from notification.templates import auth_email, telegram_daily_summary, telegram_order_message, telegram_quant_message


def test_email_template_has_plain_and_html_versions():
    subject, text, html = auth_email("verify", "ABC123", "https://ciclotrade.com")
    assert "CicloTrade" in subject and "ABC123" in text
    assert "<!doctype html>" in html and "ABC123" in html and "letter-spacing:0" in html


def test_telegram_templates_keep_decision_fields_in_first_screen():
    order = telegram_order_message("paper", "BUY", 10, "AAPL", 200, "FILLED")
    assert "🟢" in order and "AAPL" in order and "模擬盤" in order
    event = {
        "id": 7, "event_type": "signal", "strategy_name": "趨勢", "strategy_version": "v1",
        "occurred_at": "2026-08-07T12:00:00+00:00", "corrects_event_id": None,
    }
    legs = [{
        "market": "US", "instrument_type": "option", "instrument_key": "US:OPTION:AAPL:20260918:CALL:210",
        "symbol": "AAPL", "option_expiry": "2026-09-18", "option_right": "CALL", "option_strike": 210,
        "currency": "USD", "price": 5, "quantity_delta": 1, "target_quantity": 1,
    }]
    message = telegram_quant_message(event, legs, {"stop_loss": 3.5, "target_price": 8})
    assert "模擬帳戶已執行的交易建議" in message and "止損 $3.50" in message and "目標 $8.00" in message
    assert "本次建議成交" in message
    assert "美股 🟢 AAPL" in message and "US:OPTION" not in message and "catalog" not in message.lower()
    assert "📦 數量　1 張" in message
    assert message.count("<blockquote>") == 1


def test_daily_summary_directs_detailed_pnl_queries_to_private_bot():
    snapshot = {
        "total_pnl": 125,
        "total_equity": 100_125,
        "initial_cash": 100_000,
        "currency": "USD",
        "cash": 95_000,
        "market_value": 5_125,
        "captured_at": "2026-08-07T20:00:00+00:00",
    }

    message = telegram_daily_summary([(snapshot, {})], 0)

    assert "🔎 盈利／虧損明細，請私聊機器人查詢。" in message
