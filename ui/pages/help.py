# -*- coding: utf-8 -*-
"""Task-oriented help and integration status."""

from __future__ import annotations

import streamlit as st

from core.database import get_database
from data.datasource import market_data_status
from ui.components import experience_hero, section_label


def render() -> None:
    experience_hero(
        "HELP / INTEGRATIONS",
        "帮助中心",
        "不用先学习量化术语，按一条清楚路线完成选股、理解方案、设置提醒与模拟验证。",
        "凭证仅从安全设置接入",
        (
            ("选择市场", "美股或 A 股"),
            ("查看方案", "方向、价格与风险"),
            ("设置提醒", "网页或 Telegram"),
            ("模拟验证", "确认后再执行"),
        ),
    )
    section_label("第一次使用", "先看建议，再决定是否进入模拟盘")
    with st.container(border=True):
        st.markdown("1. 在**实时数据看板**搜索美股或 A 股代码，确认数据源与更新时间。\n2. 在**策略研究**先看方向、止损和目标条件，再使用损益实验室。\n3. 在**行情与预警**建立单条件或组合预警。\n4. 在**交易执行**用模拟盘验证订单与风控；未配置券商时不会发送实盘订单。")
    section_label("数据与券商接入", "没有凭证时只展示清晰的未配置状态")
    with st.container(border=True):
        status = market_data_status()
        st.caption(f"行情数据源：{status['source']} · {status['freshness']}。{status['detail']}。")
        st.caption("Tiger / Alpaca / IBKR：完整接入流程会保留展示；管理员总开关关闭时，券商账户与 API 资料输入会锁定并引导联系客服。")
        st.caption("任何状态下都不要通过网页聊天、Telegram 或邮件发送券商密码、私钥或 Token。")
        st.caption("Futu / QMT / PTrade：需要定制部署、券商授权和本机网关，当前不会伪造连接成功。")
        st.warning("外部服务尚未配置时，系统会保留研究与模拟盘功能，并明确显示不可用原因。", icon=":material/cloud_off:")
    section_label("Telegram 绑定", "必须由用户同意并验证 Chat ID")
    with st.container(border=True):
        st.write("在账户与安全页面输入自己的 Chat ID，完成一次性验证码验证后，系统只向已验证的个人目的地发送有权限的事件。")
        st.caption("公开频道与会员群组需要管理员在环境变量中配置邀请链接；未配置时不会显示虚假链接。")
    section_label("风险边界", "所有策略、回测和玄学内容均为参考")
    st.error("历史表现不代表未来结果。CicloTrade 不保证收益，不替代持牌投资顾问，也不会在未完成额外签约与人工确认时自动下单。", icon=":material/gpp_maybe:")
    db = get_database()
    status = db.fetch_all("SELECT event_type,component,message,created_at FROM system_events ORDER BY created_at DESC LIMIT 5")
    if status:
        with st.expander("最近系统状态", icon=":material/monitor_heart:"):
            display_status = [
                {"类型": row["event_type"], "组件": row["component"], "消息": row["message"], "时间": row["created_at"]}
                for row in status
            ]
            st.dataframe(display_status, hide_index=True, width="stretch")
