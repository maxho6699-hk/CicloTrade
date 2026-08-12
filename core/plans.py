# -*- coding: utf-8 -*-
"""CicloTrade 套餐、价格与功能权限。"""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
from typing import Any


PLAN_ORDER = ("免费版", "标准版", "高级版", "专业版", "定制版")
PLAN_DISPLAY_NAMES = {
    "免费版": "免費會員",
    "标准版": "標準會員",
    "高级版": "高級會員",
    "专业版": "專業會員",
    "定制版": "定制會員",
}
TELEGRAM_CHANNEL_NAMES = {
    "daily": "免費頻道",
    "advanced": "高級頻道",
    "professional": "專業頻道",
}
TELEGRAM_SUGGESTION_NAMES = {
    "stock": "正股建議",
    "option": "期權建議",
}

PLANS: dict[str, dict[str, Any]] = {
    "免费版": {
        "prices": {"monthly": 0, "quarterly": 0, "yearly": 0},
        "summary": "先看懂基础策略与风险边界",
        "features": ("1 种基础策略", "模板结构示例", "1 条单条件价格预警", "近 1 年历史样本范围", "回测参数草稿（引擎接入后计算）", "延迟 15 分钟行情"),
    },
    "标准版": {
        "prices": {"monthly": 298, "quarterly": 850, "yearly": 2_980},
        "summary": "完整策略研究与近 3 年历史样本范围",
        "features": ("包含免费版全部权益", "全部 8 种策略", "一句话策略每日 3 次", "全部策略模板", "10 条预警（最多 3 个组合条件）", "近 3 年历史样本范围与参数草稿", "网页正式建议与量化事件日志"),
    },
    "高级版": {
        "prices": {"monthly": 698, "quarterly": 1_980, "yearly": 6_980},
        "summary": "正股即时提醒、历史样本研究与受控账号治理",
        "features": ("包含标准版全部权益", "不限预警（最多 5 个组合条件）", "一句话策略每日 10 次", "近 10 年历史样本范围与参数草稿", "CSV 导入与策略绩效追踪", "Telegram 即時正股建議", "美股多空策略研究与官方验证", "1 个自动交易控制账号名额（仍需主动授权券商）"),
    },
    "专业版": {
        "prices": {"monthly": 2_980, "quarterly": 8_500, "yearly": 29_800},
        "summary": "完整期权研究、多账户、API 与受控交易",
        "features": ("包含高级版全部权益", "期权链、期权报价 K 线、Greeks 与 IV", "单腿与多腿期权组合研究", "未来 7 天业绩预测、量化区间与有限亏损期权研究", "官方模拟账户自动期权组合", "真实期权自动交易资格（仍需券商授权、风险门禁与独立启用）", "不限次一句话策略与复杂条件", "代码与 API 信号导入", "Telegram 即時正股與期權建議", "专业 API", "美股做空与多空策略研究", "最多 5 个自动交易控制账号名额（仍需主动授权券商）", "团队协作", "99.9% SLA 与专业报告"),
    },
    "定制版": {
        "prices": {"project": 30_000},
        "summary": "专属实施与私有化方案",
        "features": ("包含专业版全部权益", "不限研究工作区", "策略保存为模板", "继承专业版受控期权自动交易资格", "私有云或本地部署（即将上线）", "专属实施支持"),
    },
}

CAPABILITIES: dict[str, set[str]] = {
    "免费版": {"dashboard", "strategy_basic", "strategy_templates_view", "alert_basic", "backtest_1y"},
    "标准版": {"strategy_basic", "strategy_all", "payoff", "alerts_10", "backtest_3y", "signal_web", "tg_system", "strategy_generate", "strategy_templates_use"},
    # 订阅只授予研究与内容权益。实盘连接、保证金、可借券和逐单授权
    # 属于用户自己的券商账户状态，不应从会员等级推导。
    "高级版": {"strategy_basic", "alerts_unlimited", "backtest_10y", "mystic", "short_research", "tg_stock_signal", "csv_import", "strategy_tracking", "strategy_template_parameters", "auto_control_account_1"},
    "专业版": {"strategy_basic", "reports", "api_access", "multi_account", "short_research", "tg_option_signal", "code_import", "api_signal_import", "team_collaboration", "strategy_generate_complex", "option_chain", "option_quote_chart", "option_greeks", "option_iv", "option_strategy", "option_strategy_multi_leg", "earnings_forecast", "earnings_option_defined_risk", "option_auto_paper_official", "option_auto_live", "auto_control_account_5"},
    "定制版": {"strategy_basic", "private_deploy", "liquidate_all", "strategy_template_save"},
}

# Existing callers use these names. Keep one canonical matrix while accepting
# old names during the migration so entitlement checks cannot diverge.
CAPABILITY_ALIASES = {
    "api": "api_access",
    "stock_auto": "real_trade",
    "stock_signal_telegram": "tg_stock_signal",
    "option_signal_telegram": "tg_option_signal",
    # Legacy callers used one ambiguous option_auto flag. Keep compatibility,
    # while the product contract distinguishes official paper automation from
    # separately gated live broker execution.
    "option_auto": "option_auto_live",
}


def effective_plan(user: dict[str, Any]) -> str:
    """订阅过期时立即降级为免费版。"""
    plan = str(user.get("plan_type") or "免费版")
    expiry = user.get("subscription_expire")
    if plan == "免费版" or not expiry:
        return plan if plan in PLANS else "免费版"
    try:
        expires_at = datetime.fromisoformat(str(expiry))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return "免费版"
    except ValueError:
        return "免费版"
    return plan if plan in PLANS else "免费版"


def can(plan: str, capability: str) -> bool:
    canonical = CAPABILITY_ALIASES.get(capability, capability)
    try:
        plan_index = PLAN_ORDER.index(plan)
    except ValueError:
        plan_index = 0
    return any(canonical in CAPABILITIES[level] for level in PLAN_ORDER[: plan_index + 1])


def plan_display_name(plan: str) -> str:
    """Return the single customer-facing membership name."""
    return PLAN_DISPLAY_NAMES.get(str(plan), PLAN_DISPLAY_NAMES["免费版"])


def telegram_suggestion_name(instrument_type: str) -> str:
    """Return the canonical customer-facing Telegram suggestion label."""
    return TELEGRAM_SUGGESTION_NAMES.get(str(instrument_type), "交易建議")


def telegram_timeline_limits(plan: str) -> dict[str, int]:
    """Bound private Bot timeline access by the effective membership."""
    return {
        "免费版": {"stock": 10, "option": 0, "stock_delay_minutes": 60, "option_delay_minutes": 15, "pnl_stock": 10, "pnl_option": 10, "pnl_stock_delay_minutes": 60, "pnl_option_delay_minutes": 15, "per_minute": 2, "per_day": 5},
        "标准版": {"stock": 30, "option": 0, "stock_delay_minutes": 60, "option_delay_minutes": 15, "pnl_stock": 30, "pnl_option": 30, "pnl_stock_delay_minutes": 60, "pnl_option_delay_minutes": 15, "per_minute": 6, "per_day": 30},
        "高级版": {"stock": 100, "option": 0, "stock_delay_minutes": 0, "option_delay_minutes": 15, "pnl_stock": 100, "pnl_option": 30, "pnl_stock_delay_minutes": 0, "pnl_option_delay_minutes": 15, "per_minute": 10, "per_day": 100},
        "专业版": {"stock": 100, "option": 100, "stock_delay_minutes": 0, "option_delay_minutes": 0, "pnl_stock": 100, "pnl_option": 100, "pnl_stock_delay_minutes": 0, "pnl_option_delay_minutes": 0, "per_minute": 12, "per_day": 200},
        "定制版": {"stock": 100, "option": 100, "stock_delay_minutes": 0, "option_delay_minutes": 0, "pnl_stock": 100, "pnl_option": 100, "pnl_stock_delay_minutes": 0, "pnl_option_delay_minutes": 0, "per_minute": 12, "per_day": 200},
    }.get(
        str(plan),
        {"stock": 10, "option": 0, "stock_delay_minutes": 60, "option_delay_minutes": 15, "pnl_stock": 10, "pnl_option": 10, "pnl_stock_delay_minutes": 60, "pnl_option_delay_minutes": 15, "per_minute": 2, "per_day": 5},
    )


def web_market_data_visibility(plan: str, instrument_type: str = "stock") -> dict[str, int]:
    """Return the customer-visible website market-data delay.

    This intentionally does *not* reuse ``telegram_timeline_limits``.  Telegram
    recommendation release delays are a distribution policy, whereas this
    contract controls the data values returned by the website API.  Keeping the
    two policies separate prevents a change to one channel from accidentally
    exposing a different channel's data early.
    """
    level = str(plan) if str(plan) in PLANS else "免费版"
    kind = str(instrument_type).strip().lower()
    if kind not in {"stock", "option"}:
        raise ValueError("instrument_type must be stock or option")
    delays = {
        "stock": {
            "免费版": 15,
            "标准版": 0,
            "高级版": 0,
            "专业版": 0,
            "定制版": 0,
        },
        "option": {
            "免费版": 15,
            "标准版": 0,
            "高级版": 0,
            "专业版": 0,
            "定制版": 0,
        },
    }
    return {"delivery_delay_minutes": delays[kind][level]}


def web_recommendation_visibility(plan: str, instrument_type: str = "stock") -> dict[str, int]:
    """Return the server-enforced website recommendation release delay.

    This is deliberately separate from both market-data visibility and Telegram
    delivery. A delayed quote is not a delayed recommendation, and changing a
    Bot policy must never make website action content visible sooner.
    """
    level = str(plan) if str(plan) in PLANS else "免费版"
    kind = str(instrument_type).strip().lower()
    if kind not in {"stock", "option"}:
        raise ValueError("instrument_type must be stock or option")
    delays = {
        "stock": {"免费版": 60, "标准版": 60, "高级版": 0, "专业版": 0, "定制版": 0},
        "option": {"免费版": 15, "标准版": 15, "高级版": 15, "专业版": 0, "定制版": 0},
    }
    return {"delivery_delay_minutes": delays[kind][level]}


def trading_limits(plan: str) -> dict[str, Any]:
    """Return execution safety caps and the plan's authorized account capacity.

    Membership only defines how many user-authorized broker accounts CicloTrade
    may control. It never connects a broker, grants margin, or grants short
    eligibility by itself. Those permissions still come from the user's broker.
    """
    auto_control_accounts = {
        "高级版": 1,
        "专业版": 5,
        "定制版": 5,
    }.get(str(plan), 0)
    return {
        "brokers": auto_control_accounts,
        # Compatibility key used by the existing broker-account service.
        "broker_accounts": auto_control_accounts,
        "auto_control_accounts": auto_control_accounts,
        "daily_orders": 100,
        "single_notional": 500_000,
        "daily_notional": 2_000_000,
        "api_per_minute": 100,
        "instruments": ("stock", "option") if can(str(plan), "option_auto_live") else ("stock",),
    }


def alert_limit(plan: str) -> int | None:
    if plan == "免费版":
        return 1
    if plan == "标准版":
        return 10
    return None


def backtest_years(plan: str) -> int:
    if plan == "免费版":
        return 1
    if plan == "标准版":
        return 3
    return 10


def strategy_generation_limit(plan: str) -> int | None:
    """Daily natural-language generation quota; None means unlimited."""
    if plan in {"专业版", "定制版"}:
        return None
    return {"免费版": 0, "标准版": 3, "高级版": 10}.get(plan, 0)


def strategy_condition_limit(plan: str) -> int:
    """Maximum conditions accepted from natural-language strategy input."""
    return {"免费版": 0, "标准版": 1, "高级版": 5, "专业版": 20, "定制版": 20}.get(plan, 0)


def csv_import_limit(plan: str) -> int | None:
    """Daily CSV import quota; None means unlimited."""
    return 3 if plan == "高级版" else None if can(plan, "csv_import") else 0


def referral_code(user_id: int) -> str:
    return f"TAI{user_id:08d}"


def parse_referral_code(value: str) -> int | None:
    cleaned = value.strip().upper()
    return int(cleaned[3:]) if cleaned.startswith("TAI") and cleaned[3:].isdigit() else None
