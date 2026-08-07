# -*- coding: utf-8 -*-
"""Session-scoped risk and notification settings."""

from __future__ import annotations

from datetime import UTC, datetime
import json

import streamlit as st

from core.database import get_database
from core.plans import can, effective_plan, trading_limits
from core.sandbox import SandboxClient
from core.signal_imports import SignalImportService
from core.user_settings import load_user_settings, merge_user_settings
from notification.telegram_bot import telegram_configured, verified_user_target
from ui.components import page_heading, section_label


def _persist() -> None:
    user_id = st.session_state.user["id"]
    merge_user_settings(
        user_id,
        {"risk": st.session_state.risk, "weights": st.session_state.weights, "tg_events": st.session_state.tg_events},
    )
    now = datetime.now(UTC).isoformat(timespec="seconds")
    db = get_database()
    db.execute(
        "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
        (user_id, "SETTINGS_UPDATE", "用户更新风控/评分/通知设置", now),
    )


def render(config: dict | None = None) -> None:
    del config
    plan = effective_plan(st.session_state.user)
    page_heading(
        "RISK / CONFIGURATION",
        "风险参数",
        "参数保存到用户账户，并写入审计记录。所有订单仍必须经过统一风控。",
        "PERSISTED · AUDITED",
    )

    risk_tab, score_tab, notify_tab, import_tab = st.tabs(
        [":material/shield: 风控阈值", ":material/analytics: 策略评分", ":material/notifications: 通知事件", ":material/upload_file: 策略导入"]
    )

    with risk_tab:
        limits = trading_limits(plan)
        broker_access = (
            "不限"
            if limits["brokers"] is None
            else f"{limits['brokers']} 家 / {limits['broker_accounts']} 账户"
        )
        with st.container(horizontal=True):
            st.metric("券商接入", broker_access, border=True)
            st.metric("每日实盘订单", "不限" if limits["daily_orders"] is None else str(limits["daily_orders"]), border=True)
            st.metric("单笔实盘上限", "不限" if limits["single_notional"] is None else f"HK${limits['single_notional']:,.0f}", border=True)
            st.metric("API / 分钟", "不限" if limits["api_per_minute"] is None else str(limits["api_per_minute"]), border=True)
        section_label("分市场风控过滤器", "美股与 A 股按各自币种独立计算敞口和当日盈亏")
        current = st.session_state.risk
        with st.form("risk_form"):
            left, right = st.columns(2, gap="small")
            with left:
                st.markdown("**美股 / USD**")
                max_symbol_usd = st.number_input(
                    "单标的仓位上限（USD）", 1_000.0, 1_000_000.0,
                    float(current.get("max_position_per_symbol", 5_000)), 1_000.0,
                )
                max_total_usd = st.number_input(
                    "账户总仓位上限（USD）", 5_000.0, 5_000_000.0,
                    float(current.get("max_total_position", 50_000)), 5_000.0,
                )
                max_loss_usd = st.number_input(
                    "单日最大亏损（USD）", 500.0, 500_000.0,
                    float(current.get("max_daily_loss", 2_000)), 500.0,
                )
            with right:
                st.markdown("**A 股 / CNY**")
                max_symbol_cny = st.number_input(
                    "单标的仓位上限（CNY）", 5_000.0, 7_000_000.0,
                    float(current.get("max_position_per_symbol_cny", float(current.get("max_position_per_symbol", 5_000)) * 7)), 5_000.0,
                )
                max_total_cny = st.number_input(
                    "账户总仓位上限（CNY）", 10_000.0, 35_000_000.0,
                    float(current.get("max_total_position_cny", float(current.get("max_total_position", 50_000)) * 7)), 10_000.0,
                )
                max_loss_cny = st.number_input(
                    "单日最大亏损（CNY）", 1_000.0, 3_500_000.0,
                    float(current.get("max_daily_loss_cny", float(current.get("max_daily_loss", 2_000)) * 7)), 1_000.0,
                )
            controls = st.columns(2, gap="small")
            with controls[0]:
                cooldown = st.number_input(
                    "连续亏损冷却（分钟）", 5, 240,
                    int(current.get("cooldown_minutes", 30)), 5,
                )
            with controls[1]:
                loss_streak = st.number_input(
                    "触发冷却的连续亏损次数", 2, 10,
                    int(current.get("consecutive_loss_limit", 3)), 1,
                )
            submitted = st.form_submit_button("保存风控阈值", type="primary", icon=":material/save:")
        if submitted:
            st.session_state.risk = {
                "max_position_per_symbol": max_symbol_usd,
                "max_total_position": max_total_usd,
                "max_daily_loss": max_loss_usd,
                "max_position_per_symbol_cny": max_symbol_cny,
                "max_total_position_cny": max_total_cny,
                "max_daily_loss_cny": max_loss_cny,
                "cooldown_minutes": cooldown,
                "consecutive_loss_limit": loss_streak,
            }
            _persist()
            st.success("风控阈值已保存到账户。", icon=":material/check_circle:")

    with score_tab:
        section_label("策略评分权重", "合计必须等于 100%")
        current = st.session_state.weights
        with st.form("weights_form"):
            first_row = st.columns(2, gap="small")
            second_row = st.columns(2, gap="small")
            fields = (
                (first_row[0], "收益率", "return"),
                (first_row[1], "最大回撤", "drawdown"),
                (second_row[0], "盈亏比", "ratio"),
                (second_row[1], "连续亏损", "loss"),
            )
            values: dict[str, float] = {}
            for column, label, key in fields:
                with column:
                    values[key] = st.slider(label, 0.0, 1.0, float(current.get(key, .25)), .05)
            total = sum(values.values())
            st.progress(min(total, 1.0), text=f"当前合计 {total:.0%}")
            submitted = st.form_submit_button("保存评分权重", type="primary", icon=":material/save:")
        if submitted:
            if abs(total - 1) > .001:
                st.error("权重合计必须等于 100%，请调整后再保存。", icon=":material/error:")
            else:
                st.session_state.weights = values
                _persist()
                st.success("评分权重已保存到账户。", icon=":material/check_circle:")

    with notify_tab:
        section_label("Telegram 事件", "通道未连接时只保留本地设置")
        current = st.session_state.tg_events if isinstance(st.session_state.tg_events, dict) else {}
        event_labels = []
        if can(plan, "tg_stock_signal"):
            event_labels.extend((("price_alert", "价格预警触发"), ("order_submitted", "订单已提交"), ("order_filled", "订单已成交"), ("risk_rejected", "订单被风控拦截"), ("force_liquidation", "触发强制平仓")))
        if can(plan, "tg_system"):
            event_labels.append(("system_exception", "系统异常"))
        signal_labels = []
        if can(plan, "stock_signal_telegram"):
            signal_labels.append(("stock_signal", "收藏标的正股量化操作"))
        if can(plan, "option_signal_telegram"):
            signal_labels.append(("option_signal", "收藏标的期权与组合操作"))
        with st.form("notification_form"):
            selected = dict(current)
            for key, label in (*event_labels, *signal_labels):
                selected[key] = st.toggle(label, value=bool(current.get(key, key not in {"stock_signal", "option_signal"})))
            submitted = st.form_submit_button("保存通知事件", type="primary", icon=":material/save:")
        if submitted:
            st.session_state.tg_events = selected
            _persist()
            st.success("通知事件已保存；连接 Telegram 后才会发送。", icon=":material/check_circle:")
        if not can(plan, "stock_signal_telegram"):
            st.caption("高级版开放收藏标的正股操作推送；专业版进一步开放期权与组合操作推送。")
        elif not can(plan, "option_signal_telegram"):
            st.caption("当前方案已开放正股操作推送；期权与组合操作推送从专业版开放。")
        telegram_target = verified_user_target(load_user_settings(st.session_state.user["id"]))
        if telegram_target and telegram_configured(telegram_target):
            st.success("个人 Telegram 目的地已验证；仅会发送上方启用的事件。", icon=":material/notifications_active:")
        else:
            st.info(
                "个人 Telegram 尚未完成同意与目的地验证，系统不会把账户资料发送到平台共用 Chat ID。",
                icon=":material/notifications_paused:",
            )

    with import_tab:
        service = SignalImportService()
        user_id = int(st.session_state.user["id"])
        section_label("CSV 交易記錄", "標的、日期、操作、數量、價格 · UTF-8 · 單檔 256 KB")
        csv_file = st.file_uploader(
            "選擇 CSV 檔案", type=["csv"], key="strategy_csv", max_upload_size=1
        )
        if st.button(
            "校驗並匯入 CSV", type="primary", icon=":material/upload_file:",
            disabled=not can(plan, "csv_import") or csv_file is None,
        ):
            try:
                result = service.import_csv(user_id, plan, csv_file.getvalue(), csv_file.name)
                state = "已建立" if result["created"] else "重複批次，未再次寫入"
                st.success(f"{state} · 任務 #{result['job_id']} · {result['row_count']} 筆有效記錄。")
            except (PermissionError, ValueError) as exc:
                st.error(f"CSV 匯入失敗：{exc}", icon=":material/error:")
        if not can(plan, "csv_import"):
            st.caption("CSV 匯入從高級版開放；高級版每日 3 次，專業版以上不限。")

        section_label("Python / Backtrader 代碼", "僅送往獨立隔離服務；主應用不執行上傳代碼")
        code_file = st.file_uploader(
            "選擇 Python 策略", type=["py"], key="strategy_code", max_upload_size=1
        )
        if st.button(
            "安全檢查並送入隔離佇列", icon=":material/security:",
            disabled=not can(plan, "code_import") or code_file is None,
        ):
            try:
                source = code_file.getvalue().decode("utf-8")
                result = SandboxClient().submit(user_id, plan, source, code_file.name)
                if result.get("sandbox") == "not_configured":
                    st.warning(
                        f"任務 #{result['job_id']} 已隔離保存，但執行沙箱尚未配置，因此不會執行。",
                        icon=":material/shield_locked:",
                    )
                else:
                    st.success(f"任務 #{result['job_id']} 已送入隔離服務：{result.get('sandbox')}。")
            except UnicodeDecodeError:
                st.error("Python 檔案必須使用 UTF-8 編碼。", icon=":material/error:")
            except (PermissionError, RuntimeError, ValueError) as exc:
                st.error(f"策略代碼匯入失敗：{exc}", icon=":material/error:")
        if not can(plan, "code_import"):
            st.caption("策略代碼與 API 信號匯入從專業版開放。")

        section_label("信號 API", "ciclotrade.signal.v1 · Bearer Token · 每批最多 500 筆")
        if can(plan, "api_signal_import"):
            st.code("POST /api/v1/import/signals\nGET  /api/v1/export/signals?limit=500", language="text")
            exported = service.export(user_id)
            st.download_button(
                "匯出已驗證信號 JSON",
                json.dumps({"schema": "ciclotrade.signal.v1", "items": exported}, ensure_ascii=False, indent=2),
                "ciclotrade-signals.json", "application/json", icon=":material/download:",
                disabled=not exported,
            )
        else:
            st.info("API 信號匯入與匯出僅限專業版和定制版。", icon=":material/lock:")

        section_label("最近匯入", "僅顯示目前帳戶")
        jobs = get_database().fetch_all(
            """SELECT id,import_type,filename,status,row_count,created_at
               FROM signal_import_jobs WHERE user_id=? ORDER BY id DESC LIMIT 20""",
            (user_id,),
        )
        if jobs:
            st.dataframe(jobs, hide_index=True, width="stretch")
        else:
            st.info("尚無匯入記錄。", icon=":material/history:")
