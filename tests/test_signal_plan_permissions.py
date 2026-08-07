from datetime import UTC, datetime, timedelta

import pytest

from core.plans import CAPABILITIES, PLAN_ORDER, PLANS, can, effective_plan, trading_limits


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


def test_signal_and_trading_capabilities_follow_final_plan_matrix():
    assert can("高级版", "stock_signal_telegram")
    assert can("高级版", "stock_auto")
    assert can("专业版", "option_signal_telegram")
    assert not can("专业版", "option_auto")


def test_every_plan_keeps_basic_strategy_access_and_broker_limits_are_distinct():
    assert all("strategy_basic" in CAPABILITIES[plan] for plan in CAPABILITIES)
    assert trading_limits("专业版")["brokers"] == 3
    assert trading_limits("专业版")["broker_accounts"] == 50


def test_higher_plans_include_every_lower_plan_capability():
    all_capabilities = set().union(*CAPABILITIES.values())
    previous: set[str] = set()
    for plan in PLAN_ORDER:
        current = {capability for capability in all_capabilities if can(plan, capability)}
        assert previous <= current
        previous = current


def test_progressive_alert_backtest_push_and_auto_trade_access():
    assert can("标准版", "alert_basic") and can("标准版", "backtest_1y")
    assert can("高级版", "alerts_10") and can("高级版", "backtest_3y")
    assert can("专业版", "stock_signal_telegram") and can("专业版", "option_signal_telegram")
    assert can("定制版", "real_trade") and can("定制版", "option_auto")


def test_pricing_copy_declares_inherited_entitlements_and_current_limits():
    assert "包含免费版全部权益" in PLANS["标准版"]["features"]
    assert "包含标准版全部权益" in PLANS["高级版"]["features"]
    assert "包含高级版全部权益" in PLANS["专业版"]["features"]
    assert "包含专业版全部权益" in PLANS["定制版"]["features"]
    assert "最多 3 个组合条件" in " ".join(PLANS["标准版"]["features"])
    assert "最多 5 个组合条件" in " ".join(PLANS["高级版"]["features"])


def test_expired_subscription_loses_signal_permissions():
    expired = effective_plan(
        {
            "plan_type": "专业版",
            "subscription_expire": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    )

    assert expired == "免费版"
    assert not any(can(expired, capability) for capability in SIGNAL_CAPABILITIES)
