from datetime import datetime, timedelta
from core.compat import UTC

import pytest

from core.plans import (
    CAPABILITIES, PLAN_ORDER, PLANS, can, effective_plan, trading_limits,
    web_market_data_visibility,
)


SIGNAL_CAPABILITIES = (
    "signal_web",
    "stock_signal_telegram",
    "option_signal_telegram",
)


@pytest.mark.parametrize(
    ("plan", "expected"),
    (
        ("免费版", set()),
        ("标准版", {"signal_web"}),
        ("高级版", {"signal_web", "stock_signal_telegram"}),
        ("专业版", set(SIGNAL_CAPABILITIES)),
        ("定制版", set(SIGNAL_CAPABILITIES)),
    ),
)
def test_signal_capability_matrix(plan, expected):
    assert {capability for capability in SIGNAL_CAPABILITIES if can(plan, capability)} == expected


def test_website_market_data_delay_is_independent_from_telegram_delivery_limits():
    assert [web_market_data_visibility(plan, "stock")["delivery_delay_minutes"] for plan in PLAN_ORDER] == [15, 15, 0, 0, 0]
    assert [web_market_data_visibility(plan, "option")["delivery_delay_minutes"] for plan in PLAN_ORDER] == [15, 15, 0, 0, 0]
    with pytest.raises(ValueError):
        web_market_data_visibility("免费版", "future")


def test_signal_and_trading_capabilities_follow_final_plan_matrix():
    assert can("高级版", "stock_signal_telegram")
    assert can("高级版", "short_research")
    assert not can("高级版", "stock_auto")
    assert not can("定制版", "real_trade")
    assert not can("定制版", "short_trading")
    assert can("专业版", "option_signal_telegram")
    assert can("专业版", "option_auto")
    assert can("专业版", "option_auto_paper_official")
    assert can("专业版", "option_auto_live")


def test_every_plan_keeps_basic_strategy_access_and_controlled_account_limits_match_product():
    assert all("strategy_basic" in CAPABILITIES[plan] for plan in CAPABILITIES)
    assert [trading_limits(plan)["auto_control_accounts"] for plan in PLAN_ORDER] == [0, 0, 1, 5, 5]
    assert [trading_limits(plan)["broker_accounts"] for plan in PLAN_ORDER] == [0, 0, 1, 5, 5]
    assert [trading_limits(plan)["instruments"] for plan in PLAN_ORDER] == [
        ("stock",),
        ("stock",),
        ("stock",),
        ("stock", "option"),
        ("stock", "option"),
    ]


def test_option_research_and_multileg_strategies_start_at_professional():
    option_capabilities = {
        "option_chain",
        "option_quote_chart",
        "option_greeks",
        "option_iv",
        "option_strategy",
        "option_strategy_multi_leg",
    }
    assert not any(can("高级版", capability) for capability in option_capabilities)
    assert all(can("专业版", capability) for capability in option_capabilities)
    assert all(can("定制版", capability) for capability in option_capabilities)


def test_earnings_and_option_automation_eligibility_start_at_professional():
    professional_capabilities = {
        "earnings_forecast",
        "earnings_option_defined_risk",
        "option_auto_paper_official",
        "option_auto_live",
    }
    assert not any(can("高级版", capability) for capability in professional_capabilities)
    assert all(can("专业版", capability) for capability in professional_capabilities)
    assert all(can("定制版", capability) for capability in professional_capabilities)


def test_higher_plans_include_every_lower_plan_capability():
    all_capabilities = set().union(*CAPABILITIES.values())
    previous: set[str] = set()
    for plan in PLAN_ORDER:
        current = {capability for capability in all_capabilities if can(plan, capability)}
        assert previous <= current
        previous = current


def test_progressive_alert_backtest_and_push_access():
    assert can("标准版", "alert_basic") and can("标准版", "backtest_1y")
    assert can("高级版", "alerts_10") and can("高级版", "backtest_3y")
    assert can("专业版", "stock_signal_telegram") and can("专业版", "option_signal_telegram")
    assert can("专业版", "option_auto") and can("定制版", "option_auto")


def test_pricing_copy_declares_inherited_entitlements_and_current_limits():
    assert "包含免费版全部权益" in PLANS["标准版"]["features"]
    assert "包含标准版全部权益" in PLANS["高级版"]["features"]
    assert "包含高级版全部权益" in PLANS["专业版"]["features"]
    assert "包含专业版全部权益" in PLANS["定制版"]["features"]
    assert "最多 3 个组合条件" in " ".join(PLANS["标准版"]["features"])
    assert "最多 5 个组合条件" in " ".join(PLANS["高级版"]["features"])
    assert "期权链" not in " ".join(PLANS["高级版"]["features"])
    assert "期权链" in " ".join(PLANS["专业版"]["features"])
    assert "最多 5 个自动交易控制账号" in " ".join(PLANS["专业版"]["features"])


def test_expired_subscription_loses_signal_permissions():
    expired = effective_plan(
        {
            "plan_type": "专业版",
            "subscription_expire": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )

    assert expired == "免费版"
    assert not any(can(expired, capability) for capability in SIGNAL_CAPABILITIES)
    assert not can(expired, "earnings_forecast")
    assert not can(expired, "earnings_option_defined_risk")
    assert not can(expired, "option_auto_paper_official")
    assert not can(expired, "option_auto_live")
