# -*- coding: utf-8 -*-
"""CicloTrade 套餐、价格与功能权限。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


PLAN_ORDER = ("免费版", "标准版", "高级版", "专业版", "定制版")

PLANS: dict[str, dict[str, Any]] = {
    "免费版": {
        "prices": {"monthly": 0, "quarterly": 0, "yearly": 0},
        "summary": "先完成第一次策略研究",
        "features": ("1 种基础策略", "模板结构示例", "1 条单条件价格预警", "近 1 年回测", "延迟 15 分钟行情"),
    },
    "标准版": {
        "prices": {"monthly": 298, "quarterly": 850, "yearly": 2_980},
        "summary": "完整策略研究与 3 年回测",
        "features": ("包含免费版全部权益", "全部 8 种策略", "一句话策略每日 3 次", "全部策略模板", "10 条预警（最多 3 个组合条件）", "近 3 年回测", "网页正式操作与量化交易日志"),
    },
    "高级版": {
        "prices": {"monthly": 698, "quarterly": 1_980, "yearly": 6_980},
        "summary": "深度回测、期权链与自动化研究",
        "features": ("包含标准版全部权益", "不限预警（最多 5 个组合条件）", "一句话策略每日 10 次", "近 10 年回测与参数优化", "期权链研究", "CSV 导入与策略绩效追踪", "正股操作 Telegram 推送", "玄学娱乐参考", "正股实盘（需另行签约）"),
    },
    "专业版": {
        "prices": {"monthly": 2_980, "quarterly": 8_500, "yearly": 29_800},
        "summary": "多账户、API 与受控交易",
        "features": ("包含高级版全部权益", "不限次一句话策略与复杂条件", "代码与 API 信号导入", "正股与期权 Telegram 推送", "专业 API", "正股实盘（套餐内含，仍需券商与风控配置）", "最多 50 个券商账户", "团队协作", "99.9% SLA 与专业报告"),
    },
    "定制版": {
        "prices": {"project": 30_000},
        "summary": "专属实施与私有化方案",
        "features": ("包含专业版全部权益", "不限券商账户", "策略保存为模板", "期权自动交易（即将上线）", "私有云或本地部署（即将上线）", "专属实施支持"),
    },
}

CAPABILITIES: dict[str, set[str]] = {
    "免费版": {"dashboard", "strategy_basic", "strategy_templates_view", "alert_basic", "backtest_1y"},
    "标准版": {"strategy_basic", "strategy_all", "payoff", "alerts_10", "backtest_3y", "signal_web", "tg_system", "strategy_generate", "strategy_templates_use"},
    "高级版": {"strategy_basic", "alerts_unlimited", "backtest_10y", "option_chain", "mystic", "real_trade", "tg_stock_signal", "csv_import", "strategy_tracking", "strategy_template_parameters"},
    "专业版": {"strategy_basic", "reports", "api_access", "multi_account", "tg_option_signal", "code_import", "api_signal_import", "team_collaboration", "strategy_generate_complex"},
    "定制版": {"strategy_basic", "option_auto", "private_deploy", "liquidate_all", "strategy_template_save"},
}

# Existing callers use these names. Keep one canonical matrix while accepting
# old names during the migration so entitlement checks cannot diverge.
CAPABILITY_ALIASES = {
    "api": "api_access",
    "stock_auto": "real_trade",
    "stock_signal_telegram": "tg_stock_signal",
    "option_signal_telegram": "tg_option_signal",
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


def trading_limits(plan: str) -> dict[str, Any]:
    """Return conservative live-broker limits for the effective plan."""
    return {
        "免费版": {"brokers": 0, "broker_accounts": 0, "daily_orders": 0, "single_notional": 0, "daily_notional": 0, "api_per_minute": 0, "instruments": ("view",)},
        "标准版": {"brokers": 1, "broker_accounts": 1, "daily_orders": 5, "single_notional": 10_000, "daily_notional": 50_000, "api_per_minute": 10, "instruments": ("stock",)},
        "高级版": {"brokers": 1, "broker_accounts": 1, "daily_orders": 20, "single_notional": 50_000, "daily_notional": 200_000, "api_per_minute": 30, "instruments": ("stock", "etf")},
        "专业版": {"brokers": 3, "broker_accounts": 50, "daily_orders": 100, "single_notional": 500_000, "daily_notional": 2_000_000, "api_per_minute": 100, "instruments": ("stock", "etf", "option")},
        "定制版": {"brokers": None, "broker_accounts": None, "daily_orders": None, "single_notional": None, "daily_notional": None, "api_per_minute": None, "instruments": ("all",)},
    }.get(plan, {"brokers": 0, "broker_accounts": 0, "daily_orders": 0, "single_notional": 0, "daily_notional": 0, "api_per_minute": 0, "instruments": ("view",)})


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
