"""Reviewed membership copy and sealed legacy capability snapshots."""

PUBLIC_PLAN_DISPLAY_NAMES = {
    "免费版": "免費會員",
    "标准版": "標準會員",
    "高级版": "高級會員",
    "专业版": "專業會員（歷史）",
    "定制版": "定制會員（歷史）",
}
PUBLIC_PLAN_CAPABILITY_REMOVALS = {
    "高级版": frozenset({"auto_control_account_1"}),
}
PUBLIC_PLAN_CAPABILITY_ADDITIONS = {
    "高级版": frozenset({"broker_access_apply", "option_live_beta_apply"}),
}
OPTION_LIVE_BETA_STATES = (
    "planned", "beta_eligible", "approved", "runtime_ready", "paused", "revoked",
)

PUBLIC_PLAN_COPY = {
    "免费版": {
        "summary": "先看懂基础策略与风险边界",
        "features": (
            "1 种基础策略", "模板结构示例", "1 条单条件价格预警",
            "近 1 年历史样本范围", "回测参数草稿（引擎接入后计算）",
            "延迟 15 分钟行情",
        ),
    },
    "标准版": {
        "summary": "完整策略研究与实时正股工具",
        "features": (
            "包含免费版全部权益", "全部 8 种策略", "一句话策略每日 3 次",
            "全部策略模板", "10 条预警（最多 3 个组合条件）",
            "近 3 年历史样本范围与参数草稿", "网站实时正股行情与 K 线",
            "网页正式建议与量化事件日志",
        ),
    },
    "高级版": {
        "summary": "即时正股提醒、深度研究与受控项目申请",
        "features": (
            "包含标准版全部权益", "不限预警（最多 5 个组合条件）",
            "一句话策略每日 10 次", "近 10 年历史样本范围与参数草稿",
            "CSV 导入与策略绩效追踪", "Telegram 即时正股建议",
            "美股多空策略研究与官方验证",
            "真实期权自动交易项目申请入口（不保证审批、权限或运行）",
        ),
    },
}

SEALED_LEGACY_CAPABILITIES = {
    "专业版": (
        "earnings_forecast", "earnings_option_defined_risk", "option_auto_paper_official",
        "option_chain", "option_greeks", "option_iv", "option_quote_chart",
        "option_strategy", "option_strategy_multi_leg", "reports", "short_research",
        "tg_option_signal",
    ),
    "定制版": (
        "earnings_forecast", "earnings_option_defined_risk", "option_auto_paper_official",
        "option_chain", "option_greeks", "option_iv", "option_quote_chart",
        "option_strategy", "option_strategy_multi_leg", "reports", "short_research",
        "tg_option_signal",
    ),
}
