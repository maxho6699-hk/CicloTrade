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
    assert "CicloTrade · 官方模擬帳戶正式事件" in message
    assert "本次正式結論" in message
    assert "止損　$3.50" in message and "目標　$8.00" in message
    assert "數量　1 張" in message
    assert "報價安全　<b>仅供研究，不用于立即交易</b>" in message
    assert "美股 AAPL" in message and "US:OPTION" not in message and "catalog" not in message.lower()
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


def test_realtime_quote_becomes_research_only_when_stale_or_delayed():
    event = {
        "id": 8,
        "event_type": "signal",
        "strategy_name": "趨勢",
        "strategy_version": "v1",
        "occurred_at": "2026-08-11T01:30:00+00:00",
        "recorded_at": "2026-08-11T01:30:10+00:00",
        "corrects_event_id": None,
    }
    legs = [{
        "market": "US",
        "instrument_type": "stock",
        "instrument_key": "US:STOCK:AAPL",
        "symbol": "AAPL",
        "currency": "USD",
        "price": 200,
        "quantity_delta": 10,
        "target_quantity": 10,
    }]
    metadata = {
        "quote": {
            "source": "OpenD",
            "quote_at": "2026-08-11T01:29:59+00:00",
            "freshness": "实时",
            "verification": "opend_qot_right_lv2",
            "is_realtime": True,
            "actionable_quote": True,
            "fallback_from": None,
        }
    }

    immediate = telegram_quant_message(
        event,
        legs,
        metadata,
        rendered_at="2026-08-11T01:30:30+00:00",
        immediate_action_allowed=True,
    )
    stale = telegram_quant_message(
        event,
        legs,
        metadata,
        rendered_at="2026-08-11T01:40:00+00:00",
        immediate_action_allowed=True,
    )
    delayed = telegram_quant_message(
        event,
        legs,
        metadata,
        delay_note="正股建議延遲 1 小時",
        rendered_at="2026-08-11T01:30:30+00:00",
        delivery_delay_minutes=60,
        immediate_action_allowed=True,
    )

    assert "已验证实时报价，可核对即时行动描述" in immediate
    assert "仅供研究，不用于立即交易" in stale
    assert "仅供研究，不用于立即交易" in delayed
