# -*- coding: utf-8 -*-
"""真实配置与运行状态监控。"""

from __future__ import annotations

from datetime import datetime
import platform
import shutil
from pathlib import Path

import streamlit as st

from core.admin_service import AdminService
from core.database import get_database
from core.user_settings import load_user_settings
from notification.telegram_bot import telegram_configured, verified_user_target
from payment.paddle_client import PaddleClient
from payment.paypal_client import PayPalClient
from trading.tiger_api import TigerAPI
from data.datasource import market_data_status
from ui.components import metric_grid, page_heading, section_label, terminal


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _status_card(name: str, endpoint: str, status: str, detail: str, tone: str) -> str:
    return (
        '<article class="status-card"><header>'
        f'<strong><i class="dot {tone}"></i>{name}</strong><b>{status}</b></header><dl>'
        f'<div><dt>服务</dt><dd>{endpoint}</dd></div><div><dt>说明</dt><dd>{detail}</dd></div>'
        '</dl></article>'
    )


def render() -> None:
    user = st.session_state.user
    db = get_database()
    can_view_system = False
    if user.get("is_admin"):
        try:
            role = AdminService(db).role_for(int(user["id"]))
            can_view_system = AdminService.has_permission(role, "system")
        except PermissionError:
            pass
    page_heading(
        "SYSTEM / CONNECTIONS",
        "通道监控",
        "区分在线、已配置和未配置。只有完成真实请求验证的通道才显示在线。",
        "HEALTH · CONFIG · RUNTIME",
    )
    market_live = st.session_state.get("market_live")
    data_status = market_data_status()
    market_status = "在线" if market_live else "待检查" if market_live is None else "离线"
    market_tone = "" if market_live else "warn" if market_live is None else "error"
    tiger = TigerAPI()
    paddle = PaddleClient()
    paypal = PayPalClient()
    telegram_target = verified_user_target(load_user_settings(user["id"], db))
    section_label("外部服务", "在线表示已完成实际请求；已配置不等于联调通过")
    st.html(
        '<section class="status-grid">'
        + _status_card("市场数据", str(data_status["source"]), market_status, str(data_status["detail"]), market_tone)
        + _status_card("交易通道", "Tiger OpenAPI", "已配置" if tiger.configured else "未配置", "实盘总开关保持关闭" if tiger.configured else "不会提交真实订单", "warn")
        + _status_card(
            "Telegram",
            "个人通知",
            "已连接" if telegram_target and telegram_configured(telegram_target) else "未连接",
            "仅发送到已验证的个人目的地" if telegram_target else "只保留站内记录，不使用平台共用 Chat ID",
            "warn",
        )
        + _status_card("Paddle", "Billing API", "已配置" if paddle.configured else "未配置", "需通过沙箱回调验证" if paddle.configured else "购买入口会明确报错", "warn")
        + _status_card("PayPal", "Orders v2", "已配置" if paypal.configured else "未配置", "全球备用通道" if paypal.configured else "购买入口会明确报错", "warn")
        + _status_card("FPS", "人工核对", "可建单", "管理员确认银行入账后生效", "warn")
        + '</section>'
    )
    if not can_view_system:
        st.info("运行环境与系统错误仅对具备系统权限的后台角色开放。", icon=":material/security:")
        return
    journal = db.fetch_one("PRAGMA journal_mode") or {}
    disk = shutil.disk_usage(PROJECT_ROOT)
    section_label("运行环境", "本机实际信息")
    metric_grid(
        (
            ("Python", platform.python_version(), "当前解释器", ""),
            ("Streamlit", st.__version__, "ASGI 运行时可用", ""),
            ("SQLite", str(journal.get("journal_mode", "unknown")).upper(), "数据库日志模式", "positive"),
            ("可用磁盘", f"{disk.free / 1024**3:.1f} GB", f"总计 {disk.total / 1024**3:.0f} GB", ""),
        )
    )
    events = db.get_system_events(100)
    rows = [(row["created_at"][11:19], row.get("event_type", "INFO"), row["component"], row["message"]) for row in events]
    if not rows:
        now = datetime.now().strftime("%H:%M:%S")
        rows = [
            (now, "INFO", "DATABASE", f"SQLite {str(journal.get('journal_mode', 'unknown')).upper()} 已就绪"),
            (now, "INFO" if market_live else "WARN", "MARKET", f"{data_status['source']} {market_status} · {data_status['freshness']}"),
            (now, "WARN", "TRADING", "老虎实盘总开关未验证"),
        ]
    section_label("系统事件", "最近 100 条")
    terminal(rows)
