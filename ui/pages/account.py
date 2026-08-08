# -*- coding: utf-8 -*-
"""用户资料、会话与 IP 安全。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.auth import AuthService
from core.database import get_database
from core.plans import effective_plan
from core.user_settings import load_user_settings, merge_user_settings
from notification.telegram_bot import confirm_verification, issue_verification_token, send_telegram, telegram_configured
from notification.templates import telegram_binding
from ui.components import metric_grid, page_heading, section_label


def render() -> None:
    service = AuthService()
    db = get_database()
    user = service.get_user(st.session_state.user["id"])
    st.session_state.user = user
    plan = effective_plan(user)
    page_heading(
        "ACCOUNT / SECURITY",
        "账户与安全",
        "管理个人资料、登录设备和已绑定 IP。新设备登录会让旧会话立即失效。",
        "SINGLE SESSION · MAX 3 IP",
    )
    sessions = db.fetch_all(
        "SELECT ip_address,user_agent,login_time,last_active,is_active FROM user_sessions WHERE user_id=? ORDER BY login_time DESC LIMIT 10",
        (user["id"],),
    )
    metric_grid(
        (
            ("当前方案", plan, "订阅过期自动降级", "positive" if plan != "免费版" else ""),
            ("已绑定 IP", str(len([row for row in service.list_ips(user["id"]) if row["is_active"]])), "最多 3 个", ""),
            ("登录会话", str(len(sessions)), "仅最新会话有效", ""),
            ("上次登录", str(user.get("last_login") or "首次登录")[:19], "UTC", ""),
        )
    )

    profile_tab, security_tab, telegram_tab = st.tabs([":material/person: 个人资料", ":material/security: 登录安全", ":material/notifications: Telegram"])
    with profile_tab:
        section_label("个人资料", "邮箱不可在此直接变更")
        with st.form("profile_form"):
            st.text_input("登录邮箱", value=user["email"], disabled=True)
            display_name = st.text_input("显示名称", value=user.get("display_name") or "", max_chars=80)
            submitted = st.form_submit_button("保存个人资料", type="primary", icon=":material/save:")
        if submitted:
            service.update_profile(user["id"], display_name)
            st.session_state.user = service.get_user(user["id"])
            st.success("个人资料已保存。", icon=":material/check_circle:")
    with security_tab:
        section_label("IP 白名单", "新 IP 在未满 3 个时自动加入")
        ips = service.list_ips(user["id"])
        if ips:
            frame = pd.DataFrame(ips)[["ip_address", "first_seen", "last_used", "is_active"]]
            frame.columns = ["IP 地址", "首次登录", "最后使用", "有效"]
            st.dataframe(frame, hide_index=True, width="stretch")
        else:
            st.info("当前账户尚未记录 IP。", icon=":material/info:")
        section_label("最近会话", "旧会话会在新设备登录时失效")
        if sessions:
            frame = pd.DataFrame(sessions)
            frame.columns = ["IP 地址", "设备", "登录时间", "最后活动", "有效"]
            st.dataframe(frame, hide_index=True, width="stretch")
        st.caption("如需删除已绑定 IP，请联系 Telegram @Maxooo8 或 support@ciclotrade.com。")
    with telegram_tab:
        section_label("绑定个人 Telegram", "约 1 分钟完成 · 网站与 Bot 共用同一份通知设置")
        settings = load_user_settings(user["id"], db)
        channel = settings.get("telegram") if isinstance(settings.get("telegram"), dict) else {}
        if channel.get("verified"):
            st.success(f"已验证 Chat ID：{str(channel.get('chat_id'))[:4]}…", icon=":material/verified:")
            if st.button("解除 Telegram 绑定", icon=":material/link_off:"):
                merge_user_settings(user["id"], {"telegram": {"consent": False, "verified": False, "chat_id": ""}}, db)
                st.rerun()
        else:
            st.markdown(
                "1. 点击下方按钮打开 **@Tradeai8_bot**。\n"
                "2. 点击 **Start / 开始**，再发送 `/id`。\n"
                "3. Bot 会回复一串纯数字，这就是你的 **Chat ID**。\n"
                "4. 把数字粘贴到下方，勾选同意并点击 **发送验证码**。\n"
                "5. 把 Bot 收到的验证码粘贴回网站，点击 **确认绑定**。"
            )
            st.link_button(
                "打开 CicloTrade Bot",
                "https://t.me/Tradeai8_bot",
                icon=":material/open_in_new:",
                type="primary",
            )
            st.caption("Chat ID 不是用户名、手机号或群组 ID；私人 Chat ID 只包含数字。")
            with st.form("telegram_bind_request"):
                consent = st.checkbox("我同意 CicloTrade 按会员权限向我的 Telegram 发送通知", value=bool(channel.get("consent")))
                chat_id = st.text_input(
                    "Telegram Chat ID",
                    value=str(channel.get("chat_id") or ""),
                    autocomplete="off",
                    placeholder="例如 123456789…",
                    help="在 @Tradeai8_bot 私聊发送 /id 即可取得。",
                )
                request = st.form_submit_button("发送验证码", type="primary", icon=":material/send:")
            if request:
                try:
                    if not consent:
                        raise ValueError("请先勾选通知同意。")
                    token = issue_verification_token(db, user["id"], chat_id, consent)
                    merge_user_settings(user["id"], {"telegram": {"consent": True, "verified": False, "chat_id": str(chat_id).strip()}}, db)
                    if telegram_configured(chat_id):
                        send_telegram(telegram_binding(token), chat_id=str(chat_id).strip())
                        st.success("验证码已发送到你的 Telegram，请粘贴回来确认。", icon=":material/mark_email_read:")
                    else:
                        st.warning("Telegram Bot 尚未配置，无法完成真实验证；当前只保存申请，不会发送任何通知。", icon=":material/cloud_off:")
                except (ValueError, RuntimeError) as exc:
                    st.error(str(exc), icon=":material/error:")
            with st.form("telegram_bind_confirm"):
                token = st.text_input(
                    "Bot 验证码",
                    autocomplete="one-time-code",
                    max_chars=80,
                    placeholder="粘贴 Bot 发来的验证码…",
                )
                confirm = st.form_submit_button("确认绑定", icon=":material/verified:")
            if confirm:
                try:
                    verified_chat_id = confirm_verification(db, user["id"], token)
                    merge_user_settings(user["id"], {"telegram": {"consent": True, "verified": True, "chat_id": verified_chat_id}}, db)
                    st.success("Telegram 已完成验证。", icon=":material/check_circle:")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc), icon=":material/error:")
