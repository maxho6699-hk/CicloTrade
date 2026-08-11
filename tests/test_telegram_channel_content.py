from notification.channel_content import (
    ChannelRenderPolicy,
    MISSING_FIELD,
    RESEARCH_ONLY_WARNING,
    RecommendationChange,
    RecommendationState,
    classify_recommendation_change,
    recommendation_from_event,
    recommendation_render_payload,
)
from notification.templates import telegram_price_alert


EVENT = {
    "id": 7,
    "event_type": "signal",
    "occurred_at": "2026-08-11T01:30:00+00:00",
    "recorded_at": "2026-08-11T01:31:00+00:00",
}
LEG = {
    "market": "US",
    "instrument_type": "stock",
    "instrument_key": "US:STOCK:AAPL",
    "symbol": "AAPL",
    "currency": "USD",
    "target_quantity": 10,
    "quantity_delta": 10,
    "price": 200,
}


def test_quote_safety_requires_verified_actionable_opend_without_fallback():
    safe = recommendation_from_event(
        EVENT,
        LEG,
        {
            "quote": {
                "source": "OpenD",
                "quote_at": "2026-08-11T01:29:59+00:00",
                "freshness": "OpenD 快照 · 美股 LV2 实时权限已验证",
                "verification": "opend_qot_right_lv2",
                "is_realtime": True,
                "actionable_quote": True,
                "fallback_from": None,
            }
        },
    )
    assert safe.quote.supports_immediate_action is True
    assert safe.as_channel_payload()["quote"]["supports_immediate_action"] is False
    assert safe.as_channel_payload(EVENT["recorded_at"])["quote"]["supports_immediate_action"] is True
    assert safe.as_channel_payload("not-a-time")["quote"]["supports_immediate_action"] is False
    assert safe.as_channel_payload("2026-08-11T02:00:00+00:00")["quote"]["supports_immediate_action"] is False
    immediate_payload = recommendation_render_payload(
        safe,
        RecommendationChange.NEW_OPPORTUNITY,
        ChannelRenderPolicy(EVENT["recorded_at"], immediate_action_allowed=True),
    )
    delayed_payload = recommendation_render_payload(
        safe,
        RecommendationChange.NEW_OPPORTUNITY,
        ChannelRenderPolicy(
            EVENT["recorded_at"],
            immediate_action_allowed=True,
            delivery_delay_minutes=15,
        ),
    )
    assert immediate_payload["schema_version"] == 1
    assert immediate_payload["quote"]["supports_immediate_action"] is True
    assert immediate_payload["delivery"]["research_only"] is False
    assert immediate_payload["delivery"]["final_actionable"] is True
    assert immediate_payload["delivery"]["final_actionability"] == "immediate"
    assert delayed_payload["delivery"]["research_only"] is True
    assert delayed_payload["delivery"]["final_actionable"] is False
    assert delayed_payload["delivery"]["final_actionability"] == "research_only"
    assert delayed_payload["quote"]["supports_immediate_action"] is False
    assert "<b>" not in str(immediate_payload) and "callback_data" not in str(immediate_payload)

    verified_status = recommendation_from_event(
        EVENT,
        LEG,
        {
            "quote": {
                "source": "Futu OpenD",
                "quote_at": "2026-08-11T01:29:59+00:00",
                "freshness": "实时",
                "verification": "verified_realtime",
                "is_realtime": True,
                "actionable_quote": True,
                "fallback_from": None,
            }
        },
    )
    assert verified_status.quote.supports_immediate_action is True

    yahoo = recommendation_from_event(
        EVENT,
        LEG,
        {"quote": {"source": "Yahoo Finance", "is_realtime": True, "actionable_quote": True}},
    )
    fallback = recommendation_from_event(
        EVENT,
        LEG,
        {"quote": {"source": "OpenD", "is_realtime": True, "actionable_quote": True, "fallback_from": "Yahoo"}},
    )
    unverified = recommendation_from_event(
        EVENT,
        LEG,
        {
            "quote": {
                "source": "OpenD",
                "is_realtime": True,
                "actionable_quote": True,
                "verification": "opend_snapshot_realtime_unverified",
            }
        },
    )
    assert all(item.quote.supports_immediate_action is False for item in (yahoo, fallback, unverified))
    assert yahoo.quote.safety_text == RESEARCH_ONLY_WARNING

    missing_verification = recommendation_from_event(
        EVENT,
        LEG,
        {
            "quote": {
                "source": "OpenD",
                "quote_at": "2026-08-11T01:29:59+00:00",
                "is_realtime": True,
                "actionable_quote": True,
            }
        },
    )
    missing_time = recommendation_from_event(
        EVENT,
        LEG,
        {
            "quote": {
                "source": "OpenD",
                "verification": "verified_realtime",
                "is_realtime": True,
                "actionable_quote": True,
            }
        },
    )
    unsafe_immediate_payload = recommendation_render_payload(
        missing_verification,
        RecommendationChange.NEW_OPPORTUNITY,
        ChannelRenderPolicy(EVENT["recorded_at"], immediate_action_allowed=True),
    )
    assert unsafe_immediate_payload["delivery"]["research_only"] is True
    assert unsafe_immediate_payload["delivery"]["final_actionable"] is False
    assert missing_verification.quote.supports_immediate_action is False
    assert missing_time.quote.supports_immediate_action is False

    safe_quote = {
        "source": "OpenD",
        "quote_at": "2026-08-11T01:29:59+00:00",
        "freshness": "实时",
        "verification": "opend_qot_right_lv2",
        "is_realtime": True,
        "actionable_quote": True,
        "fallback_from": None,
    }
    unsafe_quotes = (
        {**safe_quote, "source": "fake-opend-proxy"},
        {**safe_quote, "quote_at": "not-a-time"},
        {**safe_quote, "freshness": "stale cached quote"},
        {**safe_quote, "freshness": None},
        {key: value for key, value in safe_quote.items() if key != "fallback_from"},
        {**safe_quote, "quote_at": "1970-01-01T00:00:00+00:00"},
        {**safe_quote, "quote_at": "2099-01-01T00:00:00+00:00"},
    )
    assert all(
        recommendation_from_event(EVENT, LEG, {"quote": quote}).quote.supports_immediate_action is False
        for quote in unsafe_quotes
    )


def test_recommendation_contract_uses_recorded_event_values_and_never_guesses_missing_fields():
    content = recommendation_from_event(
        EVENT,
        LEG,
        {
            "risk_levels": {"US:STOCK:AAPL": {"stop_loss": 180, "target_price": 240}},
            "reason": "正式事件理由",
        },
    )
    assert content.state == RecommendationState.ENTRY
    assert content.direction_label == "做多"
    assert content.entry_price == 200 and content.quantity == 10
    assert content.stop_price == 180 and content.target_price == 240
    assert content.max_risk is None and content.invalidation_condition is None
    assert content.quote.source is None and content.quote.safety_text == RESEARCH_ONLY_WARNING
    assert content.as_channel_payload()["quote"]["safety_text"] == RESEARCH_ONLY_WARNING
    assert MISSING_FIELD == "未记录"


def test_difference_classification_suppresses_timestamp_only_refreshes():
    wait = recommendation_from_event(EVENT, {**LEG, "target_quantity": 0, "quantity_delta": 0}, {"decision_state": "wait"})
    entry = recommendation_from_event(EVENT, LEG, {})
    invalidated = recommendation_from_event(
        {**EVENT, "event_type": "reversal", "id": 8},
        {**LEG, "target_quantity": 0, "quantity_delta": -10, "price": None},
        {},
    )
    risk_changed = recommendation_from_event(
        EVENT,
        LEG,
        {"risk_levels": {"US:STOCK:AAPL": {"stop_loss": 175, "target_price": 250}}},
    )
    timestamp_only = recommendation_from_event(
        {**EVENT, "occurred_at": "2026-08-11T02:00:00+00:00"},
        LEG,
        {},
    )

    assert classify_recommendation_change(None, entry) == RecommendationChange.NEW_OPPORTUNITY
    assert classify_recommendation_change(wait, entry) == RecommendationChange.WAIT_TO_ENTRY
    assert classify_recommendation_change(entry, invalidated) == RecommendationChange.ENTRY_TO_INVALIDATED
    assert classify_recommendation_change(entry, risk_changed) == RecommendationChange.RISK_CHANGED
    assert classify_recommendation_change(entry, timestamp_only) is None


def test_formal_non_trade_conclusions_are_channel_states_when_explicitly_recorded():
    for value, expected_change in (
        ("no_trade", RecommendationChange.NO_TRADE),
        ("data_insufficient", RecommendationChange.DATA_INSUFFICIENT),
        ("risk_paused", RecommendationChange.RISK_PAUSED),
    ):
        content = recommendation_from_event(EVENT, None, {"decision_state": value})
        assert classify_recommendation_change(None, content) == expected_change


def test_option_contract_and_price_alert_are_channel_safe_and_non_executing():
    option = recommendation_from_event(
        EVENT,
        {
            "market": "US",
            "instrument_type": "option",
            "instrument_key": "US:OPTION:AAPL:2026-09-18:PUT:180",
            "symbol": "AAPL",
            "currency": "USD",
            "option_expiry": "2026-09-18",
            "option_right": "PUT",
            "option_strike": 180,
            "target_quantity": -2,
            "quantity_delta": -2,
            "price": 4.5,
        },
        {"decision_state": "wait"},
    )

    assert option.state == RecommendationState.WAIT
    assert option.direction_label == "做空"
    assert option.as_channel_payload()["instrument_type"] == "option"
    assert option.as_channel_payload()["option_right"] == "PUT"
    assert "只提醒，不會自動買賣" in telegram_price_alert("AAPL 接近預警價")


def test_option_contract_details_are_material_action_changes_even_with_legacy_stable_key():
    call = recommendation_from_event(
        EVENT,
        {
            **LEG,
            "instrument_type": "option",
            "instrument_key": "legacy-option-key",
            "option_expiry": "2026-09-18",
            "option_right": "CALL",
            "option_strike": 200,
        },
        {},
    )
    put = recommendation_from_event(
        {**EVENT, "id": 8},
        {
            **LEG,
            "instrument_type": "option",
            "instrument_key": "legacy-option-key",
            "option_expiry": "2026-10-16",
            "option_right": "PUT",
            "option_strike": 180,
        },
        {},
    )

    assert classify_recommendation_change(call, put) == RecommendationChange.ACTION_CHANGED
