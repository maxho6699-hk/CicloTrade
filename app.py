# -*- coding: utf-8 -*-
"""CicloTrade Streamlit 多用户入口。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from core.compat import UTC
import html
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st
import yaml

from core.auth import AuthError, AuthService, email_verification_required
from core.database import get_database
from core.plans import effective_plan
from notification.email_sender import send_email, smtp_configured
from ui.components import brand_bar, disclaimer, load_styles
from ui.pages import (
    account,
    actions,
    admin,
    backtest,
    dashboard,
    emergency,
    growth,
    help,
    legal,
    logs,
    markets,
    monitor,
    recommendations,
    research,
    roadmap,
    settings,
    strategies,
    subscription,
    templates,
    terminal,
    trading,
)


PROJECT_ROOT = Path(__file__).resolve().parent
LOCALE_SCRIPT_VERSION = (PROJECT_ROOT / "static" / "tradeai_locale.js").stat().st_mtime_ns
load_dotenv(PROJECT_ROOT / ".env")
with (PROJECT_ROOT / "config.yaml").open("r", encoding="utf-8") as handle:
    CONFIG = yaml.safe_load(handle) or {}

st.set_page_config(
    page_title="CicloTrade | 量化决策研究",
    page_icon=":material/candlestick_chart:",
    layout="wide",
    initial_sidebar_state="auto",
)
load_styles()
st.html(
    "<script>document.documentElement.lang='zh-Hant';"
    "document.querySelector('meta[name=theme-color]')?.setAttribute('content','#101214');"
    "const main=document.querySelector('[data-testid=\"stMain\"]');"
    "if(main){main.id='tradeai-main';main.tabIndex=-1;main.setAttribute('role','main');}</script>"
    f'<script src="/app/static/tradeai_locale.js?v={LOCALE_SCRIPT_VERSION}"></script>',
    unsafe_allow_javascript=True,
)
st.html('<a class="skip-link" href="#tradeai-main">跳至主要内容</a>')


def _init_state() -> None:
    defaults = {
        "access_token": None,
        "refresh_token": None,
        "user": None,
        "auth_redirect_pending": "",
        "auth_reset_open": False,
        "auth_verify_open": False,
        "paused": False,
        "market_live": None,
        "selected_strategy": "买入 Call",
        "strategy_category": "全部",
        "risk": deepcopy(CONFIG.get("risk", {})),
        "weights": deepcopy(CONFIG.get("strategy_weights", {})),
        "tg_events": deepcopy(CONFIG.get("telegram", {}).get("events", {})),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _client_context() -> tuple[str, str]:
    headers = {str(key).lower(): str(value) for key, value in st.context.headers.items()}
    trust_proxy = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
    forwarded = headers.get("x-forwarded-for", "") if trust_proxy else ""
    if forwarded:
        ip_address = forwarded.split(",")[0].strip()
    else:
        ip_address = headers.get("x-real-ip", "127.0.0.1") if trust_proxy else "127.0.0.1"
    return ip_address or "127.0.0.1", headers.get("user-agent", "unknown")


def _clear_auth() -> None:
    st.session_state.access_token = None
    st.session_state.refresh_token = None
    st.session_state.user = None


def _restore_user(auth: AuthService) -> bool:
    token = st.session_state.access_token
    if not token:
        return False
    try:
        st.session_state.user = auth.verify(token)
        return True
    except AuthError:
        refresh_token = st.session_state.refresh_token
        if refresh_token:
            try:
                access_token, rotated_refresh_token = auth.refresh(refresh_token)
                st.session_state.access_token = access_token
                st.session_state.refresh_token = rotated_refresh_token
                st.session_state.user = auth.verify(st.session_state.access_token)
                return True
            except AuthError:
                pass
    _clear_auth()
    return False


@st.dialog("政策与协议", width="large", icon=":material/gavel:")
def _public_legal() -> None:
    st.caption("注册、付款和使用核心功能前适用；正式上线前仍需香港执业律师审阅。")
    legal.render_policy_content()


def _auth_screen(auth: AuthService) -> None:
    st.html(
        '<header class="auth-masthead"><b class="auth-logo" aria-hidden="true">C<i>T</i></b>'
        '<div><strong>CicloTrade</strong><small>美股 · A 股 · 期权决策研究</small></div>'
        '<span>策略证据 · 风险纪律 · 操作日志</span></header>'
    )
    intro_col, panel_col = st.columns([1.55, 1], gap="large", vertical_alignment="center")
    intro_col.html(
        '<section class="auth-shell">'
        '<section class="auth-intro"><span class="page-kicker">DECISION INTELLIGENCE / US + CN</span>'
        '<h1>CicloTrade<br><span>量化研究系统</span></h1>'
        '<p><strong>把复杂市场变成一张清楚的行动单。</strong>系统整理行情、策略和风险，呈现等待、买入、持有或卖出的下一步。</p>'
        '<div class="auth-scope" role="list" aria-label="覆盖范围">'
        '<span role="listitem">美股 + A 股</span><span role="listitem">正股 + 期权</span>'
        '<span role="listitem">决策日志可追溯</span></div></section>'
        '<section class="auth-decision" aria-labelledby="auth-decision-title">'
        '<header><span>研究输出结构</span><b>功能示例 · 非实时行情</b></header>'
        '<div class="auth-decision-summary"><div><small>美股期权 · AAPL</small>'
        '<strong id="auth-decision-title">等待真实数据接入</strong></div><span>暂不操作</span></div>'
        '<dl><div><dt>方向</dt><dd>待策略</dd></div><div><dt>入场</dt><dd>待行情</dd></div>'
        '<div><dt>风险</dt><dd>待风控</dd></div><div><dt>行动</dt><dd>待信号</dd></div></dl>'
        '<footer><span>行情数据</span><i aria-hidden="true"></i><span>策略验证</span>'
        '<i aria-hidden="true"></i><span>风险闸门</span><i aria-hidden="true"></i><span>操作方案</span></footer>'
        '</section></section>'
    )
    with panel_col.container(key="login_panel"):
        st.html(
            '<header class="auth-panel-title"><span>安全登录</span>'
            '<h2>进入研究工作台</h2><p>登入后继续查看你的研究路线、预警与交易日志。</p></header>'
        )
        mode = st.segmented_control(
            "账户操作",
            ["登录", "注册"],
            default="登录",
            required=True,
            key="auth_mode",
            label_visibility="collapsed",
            width="stretch",
        )
        if mode == "登录":
            with st.form("email_login", border=False):
                email = st.text_input("邮箱", autocomplete="email", icon=":material/mail:")
                password = st.text_input("密码", type="password", autocomplete="current-password", icon=":material/lock:")
                submitted = st.form_submit_button("登录 CicloTrade", type="primary", icon=":material/login:", width="stretch")
            with st.container(horizontal=True, horizontal_alignment="center"):
                reset_label = "收起重设密码" if st.session_state.auth_reset_open else "重设密码"
                if st.button(reset_label, type="tertiary", key="toggle_password_reset", width="content"):
                    st.session_state.auth_reset_open = not st.session_state.auth_reset_open
                    st.session_state.auth_verify_open = False
            if submitted:
                ip_address, user_agent = _client_context()
                try:
                    result = auth.login(email, password, ip_address, user_agent)
                    st.session_state.access_token = result.access_token
                    st.session_state.refresh_token = result.refresh_token
                    st.session_state.user = result.user
                    st.session_state.auth_redirect_pending = str(st.query_params.get("next", ""))
                    if result.new_ip and smtp_configured():
                        try:
                            send_email(
                                result.user["email"],
                                "CicloTrade 新 IP 登录提醒",
                                f"您的账户刚从新的 IP 登录。\n\n时间：{datetime.now(UTC).isoformat(timespec='seconds')}"
                                f"\nIP：{ip_address}\n设备：{user_agent[:160]}\n\n如非本人操作，请立即重设密码。",
                            )
                        except RuntimeError:
                            pass
                    st.rerun()
                except AuthError as exc:
                    if "邮箱验证" in str(exc):
                        st.session_state.auth_verify_open = True
                    st.error(str(exc), icon=":material/error:")
            if st.session_state.auth_reset_open:
                st.caption("重设链接有效期 30 分钟。为防止邮箱枚举，无论账户是否存在都显示相同结果。")
                with st.form("request_reset", border=False):
                    reset_email = st.text_input("注册邮箱", autocomplete="email", icon=":material/mail:")
                    requested = st.form_submit_button("发送重设链接", icon=":material/outgoing_mail:", width="stretch")
                if requested:
                    ip_address, _ = _client_context()
                    token = auth.request_password_reset(reset_email, ip_address)
                    if token and smtp_configured():
                        base_url = os.getenv("APP_BASE_URL", "http://localhost:8501")
                        try:
                            send_email(reset_email, "CicloTrade 密码重设", f"请在 30 分钟内打开 CicloTrade，并在重设密码页输入此验证码：\n\n{token}\n\n{base_url}")
                        except RuntimeError:
                            pass
                    st.success("如账户存在且 SMTP 已配置，重设验证码已经发送。")
                with st.form("apply_reset", border=False):
                    reset_token = st.text_input("重设验证码", autocomplete="one-time-code")
                    new_password = st.text_input("新密码", type="password", autocomplete="new-password")
                    applied = st.form_submit_button("更新密码", type="primary", icon=":material/key:", width="stretch")
                if applied:
                    try:
                        auth.reset_password(reset_token, new_password)
                        st.success("密码已更新，所有旧会话均已失效。")
                    except AuthError as exc:
                        st.error(str(exc), icon=":material/error:")
            if st.session_state.auth_verify_open:
                st.caption("正式环境必须先验证注册邮箱；验证码有效期 30 分钟。")
                with st.form("request_email_verification", border=False):
                    verify_email = st.text_input("注册邮箱", autocomplete="email", icon=":material/mail:")
                    requested = st.form_submit_button("发送邮箱验证码", icon=":material/outgoing_mail:", width="stretch")
                if requested:
                    ip_address, _ = _client_context()
                    token = auth.request_email_verification(verify_email, ip_address)
                    if token and smtp_configured():
                        try:
                            send_email(
                                verify_email,
                                "CicloTrade 注册邮箱验证",
                                f"请在 30 分钟内输入以下验证码：\n\n{token}\n\n如非本人操作，请忽略此邮件。",
                            )
                        except RuntimeError:
                            pass
                    st.success("如账户尚未验证且邮件服务可用，验证码已经发送。")
                with st.form("apply_email_verification", border=False):
                    verification_token = st.text_input("邮箱验证码", autocomplete="one-time-code")
                    verified = st.form_submit_button("完成邮箱验证", type="primary", icon=":material/verified:", width="stretch")
                if verified:
                    try:
                        auth.verify_email(verification_token)
                        st.session_state.auth_verify_open = False
                        st.success("邮箱验证完成，现在可以登录。")
                    except AuthError as exc:
                        st.error(str(exc), icon=":material/error:")
        else:
            st.session_state.auth_reset_open = False
            referral = str(st.query_params.get("ref", ""))
            with st.form("email_register", border=False):
                display_name = st.text_input("显示名称", autocomplete="name", max_chars=80)
                email = st.text_input("邮箱", autocomplete="email", icon=":material/mail:")
                password = st.text_input("密码", type="password", autocomplete="new-password", help="至少 12 个字符，并包含字母和数字。")
                referral = st.text_input("推荐码（可选）", value=referral, autocomplete="off", max_chars=20)
                agreed = st.checkbox("我同意用户协议、隐私政策、退款政策与风险披露")
                submitted = st.form_submit_button("建立免费账户", type="primary", icon=":material/person_add:", width="stretch")
            if st.button("查看政策与协议", type="tertiary", icon=":material/gavel:", key="open_public_legal", width="stretch"):
                _public_legal()
            if submitted:
                if email_verification_required() and not smtp_configured():
                    st.error("正式注册暂不可用：SMTP 邮件服务尚未配置。", icon=":material/error:")
                else:
                    try:
                        ip_address, _ = _client_context()
                        auth.register(
                            email,
                            password,
                            display_name,
                            agreed,
                            referral,
                            ip_address=ip_address,
                        )
                        if email_verification_required():
                            token = auth.request_email_verification(email, ip_address)
                            if token:
                                send_email(
                                    email,
                                    "CicloTrade 注册邮箱验证",
                                    f"请在 30 分钟内输入以下验证码：\n\n{token}\n\n如非本人操作，请忽略此邮件。",
                                )
                            st.session_state.auth_verify_open = True
                            st.success(
                                "如果邮箱可用于注册，验证码已经发送。请切换到登录并完成邮箱验证。",
                                icon=":material/check_circle:",
                            )
                        else:
                            st.success(
                                "如果邮箱可用于注册，账户已经建立。请切换到登录并尝试进入。",
                                icon=":material/check_circle:",
                            )
                    except (AuthError, RuntimeError) as exc:
                        st.error(str(exc), icon=":material/error:")
        st.html(
            '<p class="auth-support">需要协助？'
            '<a href="https://t.me/Maxooo8" target="_blank" rel="noopener noreferrer">Telegram</a>'
            '<a href="mailto:support@ciclotrade.com">电子邮件</a></p>'
        )


def _dismiss_onboarding() -> None:
    st.session_state.onboarding_dismissed = True


@st.dialog("完成第一次策略回测", on_dismiss=_dismiss_onboarding)
def _first_run_onboarding(user_id: int, backtest_page: object) -> None:
    st.write("使用默认参数即可完成第一次研究；结果会保存到账户，之后可再调整标的、区间和策略。")
    st.markdown("1. 选择美股或 A 股标的\n2. 选择策略与时间范围\n3. 运行回测并查看风险指标")
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("稍后", icon=":material/schedule:", key="dismiss_onboarding"):
            _dismiss_onboarding()
            st.rerun(scope="app")
        if st.button("开始第一次回测", type="primary", icon=":material/play_arrow:", key="start_onboarding"):
            get_database().execute(
                "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (user_id, "ONBOARDING_STARTED", "用户进入第一次回测引导", datetime.now(UTC).isoformat(timespec="seconds")),
            )
            st.switch_page(backtest_page)


_init_state()
if os.getenv("MARKET_DATA_ENABLED", "false").strip().lower() != "true":
    st.session_state.market_live = False
auth_service = AuthService()
try:
    auth_service.bootstrap_admin()
except AuthError as exc:
    st.error(f"后台安全配置错误：{exc}", icon=":material/gpp_bad:")
    st.stop()
if not _restore_user(auth_service):
    _auth_screen(auth_service)
    st.stop()

user = st.session_state.user
plan = effective_plan(user)
st.session_state.current_plan = plan
control = get_database().fetch_one("SELECT opening_paused FROM user_controls WHERE user_id=?", (user["id"],))
st.session_state.paused = bool(control and control["opening_paused"])
if st.session_state.get("settings_loaded_for") != user["id"]:
    saved = get_database().fetch_one("SELECT settings_json FROM user_settings WHERE user_id=?", (user["id"],))
    if saved:
        values = json.loads(saved["settings_json"])
        st.session_state.risk = values.get("risk", st.session_state.risk)
        st.session_state.weights = values.get("weights", st.session_state.weights)
        st.session_state.tg_events = values.get("tg_events", st.session_state.tg_events)
    st.session_state.settings_loaded_for = user["id"]

start_pages = [
    st.Page(recommendations.render, title="量化推荐", icon=":material/recommend:", url_path="recommendations"),
    st.Page(actions.render, title="行动建议", icon=":material/assistant_direction:", url_path="actions"),
    st.Page(terminal.render, title="市场数据", icon=":material/monitoring:", url_path="terminal"),
    st.Page(dashboard.render, title="资产与持仓", icon=":material/space_dashboard:", url_path="dashboard"),
]
research_pages = [
    st.Page(markets.render, title="行情与预警", icon=":material/candlestick_chart:", url_path="markets"),
    st.Page(strategies.render, title="策略研究", icon=":material/query_stats:", url_path="strategies"),
    st.Page(templates.render, title="策略模板", icon=":material/library_books:", url_path="templates"),
    st.Page(backtest.render, title="策略回测", icon=":material/history:", url_path="backtest"),
    st.Page(research.render, title="研究名片", icon=":material/article:", url_path="research"),
]
execution_pages = [
    st.Page(trading.render, title="交易执行", icon=":material/order_approve:", url_path="trading"),
    st.Page(monitor.render, title="通道监控", icon=":material/monitor_heart:", url_path="monitor"),
    st.Page(emergency.render, title="紧急控制", icon=":material/emergency:", url_path="emergency"),
]
support_pages = [
    st.Page(subscription.render, title="订阅与账单", icon=":material/credit_card:", url_path="subscription"),
    st.Page(account.render, title="账户与安全", icon=":material/manage_accounts:", url_path="account"),
    st.Page(settings.render, title="风险与通知", icon=":material/tune:", url_path="settings"),
    st.Page(help.render, title="帮助中心", icon=":material/help_center:", url_path="help"),
]
more_pages = [
    st.Page(growth.render, title="推荐与奖励", icon=":material/redeem:", url_path="rewards"),
    st.Page(roadmap.render, title="功能路线图", icon=":material/route:", url_path="roadmap"),
    st.Page(logs.render, title="系统记录", icon=":material/receipt_long:", url_path="logs"),
    st.Page(legal.render, title="政策与协议", icon=":material/gavel:", url_path="legal"),
]
navigation = {
    "开始使用": start_pages,
    "专业研究": research_pages,
    "专业操作": execution_pages,
    "账户与支持": support_pages,
    "更多": more_pages,
}
if user.get("is_admin"):
    navigation["客服后台"] = [st.Page(admin.render, title="用户与订单", icon=":material/admin_panel_settings:", url_path="admin")]

current_page = st.navigation(navigation, position="hidden")
redirect_path = st.session_state.pop("auth_redirect_pending", "")
page_by_path = {
    "recommendations": start_pages[0],
    "actions": start_pages[1],
    "terminal": start_pages[2],
    "dashboard": start_pages[3],
    "markets": research_pages[0],
    "strategies": research_pages[1],
    "templates": research_pages[2],
    "backtest": research_pages[3],
    "research": research_pages[4],
    "trading": execution_pages[0],
    "monitor": execution_pages[1],
    "emergency": execution_pages[2],
    "subscription": support_pages[0],
    "account": support_pages[1],
    "settings": support_pages[2],
    "help": support_pages[3],
    "rewards": more_pages[0],
    "roadmap": more_pages[1],
    "logs": more_pages[2],
    "legal": more_pages[3],
}
if user.get("is_admin"):
    page_by_path["admin"] = navigation["客服后台"][0]
if redirect_path in page_by_path:
    st.switch_page(page_by_path[redirect_path])

with st.sidebar:
    st.html(
        '<section class="sidebar-brand"><b class="brand-mark" aria-hidden="true">C<i>T</i></b>'
        '<div><strong>CicloTrade</strong><small>量化决策终端</small></div></section>'
    )
    market_live = st.session_state.market_live
    market_label = "行情在线" if market_live is True else "行情连接中" if market_live is None else "行情离线"
    market_tone = "live" if market_live is True else "warn" if market_live is None else "error"
    risk_label = "暂停开仓" if st.session_state.paused else "风控正常"
    st.html(
        f'<div class="sidebar-mode" role="status" aria-live="polite" aria-atomic="true" '
        f'aria-label="{market_label}，{risk_label}，交易默认模拟">'
        f'<span class="dot {market_tone}" aria-hidden="true"></span><b>{market_label}</b>'
        f'<small>{risk_label} · 模拟默认</small></div>'
    )
    for section, pages in navigation.items():
        st.html(f'<div class="nav-section">{section}</div>')
        for page in pages:
            st.page_link(page, width="stretch")
    st.html(
        '<p class="sidebar-user"><span data-no-localize>'
        f"{html.escape(str(user.get('display_name') or user['email']))}</span> · "
        f"{html.escape(plan)}</p>"
    )
    if st.button("安全退出", icon=":material/logout:", width="stretch"):
        auth_service.logout(st.session_state.access_token)
        _clear_auth()
        st.rerun()

brand_slot = st.container()
current_page.run()
with brand_slot:
    brand_bar(st.session_state.paused, st.session_state.market_live)
# The first-run prompt belongs to the terminal/backtest workflow; showing it on
# research, billing, or account pages blocks navigation after slow data loads.
active_page_path = str(getattr(current_page, "url_path", "") or "")
if active_page_path in {"", "terminal", "backtest"} and not st.session_state.get("onboarding_dismissed"):
    onboarding_done = get_database().fetch_one(
        """SELECT 1 FROM user_action_logs WHERE user_id=? AND action_type='ONBOARDING_STARTED'
           UNION ALL SELECT 1 FROM strategy_action_logs WHERE user_id=? AND action='BACKTEST' LIMIT 1""",
        (user["id"], user["id"]),
    )
    if not onboarding_done:
        _first_run_onboarding(int(user["id"]), research_pages[3])
disclaimer()
