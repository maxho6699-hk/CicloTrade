from notification.templates import auth_email, telegram_order_message, telegram_quant_message


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
        "instrument_type": "option", "instrument_key": "US:OPTION:AAPL:20260918:CALL:210",
        "currency": "USD", "price": 5, "quantity_delta": 1, "target_quantity": 1,
    }]
    message = telegram_quant_message(event, legs, {"stop_loss": 3.5, "target_price": 8})
    assert "期權新操作" in message and "止損" in message and "目標" in message
