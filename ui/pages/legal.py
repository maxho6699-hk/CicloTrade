# -*- coding: utf-8 -*-
"""用户协议、隐私、风险与退款政策。"""

from __future__ import annotations

import streamlit as st

from ui.components import page_heading, section_label


POLICY_SECTIONS = (
    (
        "用户协议",
        "适用于 CicloTrade QUANT V5.1",
        """CicloTrade 提供量化研究、策略教学、预警、回测与受控交易接口。用户必须提供真实、合法的账户资料，妥善保管登录凭证，并对其账户发出的操作负责。未经书面授权，不得绕过订阅限制、复制服务、攻击系统或将账户提供给第三方共同使用。

平台可因安全、合规、数据授权或服务维护暂停部分能力。涉及自动交易、期权交易、企业 API 或私有部署的功能，必须完成额外签约、券商适当性评估与技术联调后才会启用。客服：Telegram @Maxooo8（https://t.me/Maxooo8），邮箱 support@ciclotrade.com。""",
    ),
    (
        "隐私政策",
        "数据最小化与账户安全",
        """平台处理邮箱、显示名称、订阅与订单、登录 IP、设备信息、操作审计、回测和预警数据，用于身份认证、服务交付、安全防护、合规审计与客服支持。密码只保存 bcrypt 哈希，支付卡资料由 Paddle 或 PayPal 处理，平台不保存完整卡号。

登录会话采用 JWT 并关联服务端有效会话；每个账户最多绑定 3 个 IP，新登录会使旧会话失效。用户可联系客服申请访问、更正或删除资料；法律、交易与财务记录在适用保留期内除外。""",
    ),
    (
        "风险披露",
        "策略与市场数据",
        """所有策略、信号、回测、期权损益图与玄学参考均不构成投资建议、收益承诺或代客理财。市场数据可能延迟、缺失或被供应商修订。免费历史数据不包含完整历史期权报价时，回测会明确使用代理权利金模型，不能等同真实可成交结果。

期权可能损失全部权利金，卖方策略可能产生重大损失；实际交易还受流动性、滑点、佣金、税费、停牌、系统故障和券商风控影响。用户必须独立判断并只使用可承受损失的资金。""",
    ),
    (
        "不退款政策",
        "付款前必须明确同意",
        """CicloTrade 属即时开通的数码研究服务。除支付平台强制逆转、重复扣款、未经授权交易或适用法律明确要求外，付款完成后不接受用户主动退款。购买按钮仅在用户明确同意本政策、用户协议、隐私政策与风险披露后启用，并保存当时适用的条款版本与同意时间。

拒绝主动退款不限制消费者依法不可排除的权利。退款、争议或拒付一旦由支付平台正式确认，平台会通过幂等回调撤销对应权益并保留审计记录。FPS 订单必须使用订单号作为付款备注。""",
    ),
)


def render_policy_content() -> None:
    tabs = st.tabs([title for title, _, _ in POLICY_SECTIONS])
    for tab, (title, meta, content) in zip(tabs, POLICY_SECTIONS, strict=True):
        with tab:
            section_label(title, meta)
            st.markdown(content)


def render() -> None:
    page_heading(
        "LEGAL / DISCLOSURE",
        "政策与协议",
        "注册、付款和使用核心功能前适用的政策摘要。正式上线前仍需香港执业律师审阅。",
        "VERSION · 2026-08-07",
    )
    render_policy_content()
