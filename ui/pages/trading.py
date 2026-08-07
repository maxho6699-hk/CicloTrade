# -*- coding: utf-8 -*-
"""模拟盘下单与受控实盘入口。"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from core.database import get_database
from core.plans import can, effective_plan, trading_limits
from core.strategy_registry import StrategyRegistry
from core.user_settings import load_user_settings
from notification.telegram_bot import send_telegram, telegram_configured, verified_user_target
from trading.order_manager import OrderManager
from trading.tiger_api import TigerAPI
from ui.components import page_heading, section_label


def _execute_order(
    *,
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    strategy: str,
    mode: str,
    live_confirmed: bool = False,
) -> bool:
    user = st.session_state.user
    db = get_database()
    try:
        order = OrderManager(db).submit(
            user_id=user["id"],
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            strategy=strategy,
            mode=mode,
            risk_config=st.session_state.risk,
            paused=st.session_state.paused,
            live_confirmed=live_confirmed,
        )
        st.success(
            f"订单 {order['order_id']} 已进入{('模拟成交' if mode == 'paper' else '券商通道')}。",
            icon=":material/check_circle:",
        )
        event = "order_filled" if order["status"] == "FILLED" else "order_submitted"
        telegram_target = verified_user_target(load_user_settings(user["id"], db), event) if can(effective_plan(user), "tg_stock_signal") else None
        if telegram_target and telegram_configured(telegram_target):
            try:
                send_telegram(
                    f"CicloTrade 订单\n{mode.upper()} {side} {quantity} {symbol} @ {price:.2f}\n状态：{order['status']}",
                    chat_id=telegram_target,
                )
            except RuntimeError:
                st.warning("订单已记录，但 Telegram 推送失败。")
        return True
    except ValueError as exc:
        st.error(f"订单未提交：{exc}", icon=":material/gpp_bad:")
    except Exception as exc:
        db.log_system_event("ERROR", "TRADING", "订单通道异常", str(exc)[:1000])
        st.error("订单通道暂时不可用，订单未提交。", icon=":material/gpp_bad:")
    return False


@st.dialog("确认实盘限价订单")
def _confirm_live_order(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    strategy: str,
) -> None:
    notional = quantity * price
    st.warning("实盘订单会发送到已配置的共享 Tiger 账户，提交后可能立即产生真实资金风险。")
    with st.container(horizontal=True):
        st.metric("标的 / 方向", f"{symbol} · {side}", border=True)
        st.metric("数量", f"{quantity:,}", border=True)
        st.metric("限价", f"USD {price:,.2f}", border=True)
        st.metric("名义金额", f"USD {notional:,.2f}", border=True)
    st.caption(f"订单来源：{strategy} · 限价单 · 美股实盘")
    confirmed = st.checkbox("我已核对标的、方向、数量、限价与名义金额，并确认发送实盘订单")
    if st.button(
        "确认并发送实盘订单",
        type="primary",
        icon=":material/order_approve:",
        disabled=not confirmed,
        width="stretch",
    ) and _execute_order(
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        strategy=strategy,
        mode="live",
        live_confirmed=True,
    ):
        st.rerun()


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    db = get_database()
    tiger = TigerAPI()
    registry = StrategyRegistry(db)
    registry.sync_catalog()
    allowed_strategies = registry.list_for_plan(plan, family="option")
    page_heading(
        "EXECUTION / ORDERS",
        "交易执行",
        "模拟盘用于验证订单与风控流程。实盘必须具备套餐权限、老虎凭证、额外签约与总开关。",
        "RISK FIRST · NO FAKE FILLS",
    )
    market = st.segmented_control("市场", ["美股", "A股"], default="美股", key="trading_market")
    mode = st.segmented_control("账户模式", ["paper", "live"], default="paper", format_func=lambda value: "模拟盘" if value == "paper" else "实盘")
    contract_users = {value.strip() for value in os.getenv("TRADEAI_STOCK_AUTO_CONTRACT_USER_IDS", "").split(",") if value.strip()}
    operator_id = os.getenv("TRADEAI_LIVE_OPERATOR_USER_ID", "").strip()
    live_entitled = (
        can(plan, "real_trade")
        and str(user["id"]) in contract_users
        and str(user["id"]) == operator_id
    )
    live_enabled = (
        tiger.configured
        and tiger.environment == "live"
        and os.getenv("TIGER_REAL_TRADING_ENABLED", "false").lower() == "true"
        and live_entitled
    )
    if mode == "live" and not live_enabled:
        st.error(
            "实盘不可用：高级版需额外签约；专业版及以上仍需后台白名单、独立操作员配置、券商凭证与模拟盘联调。",
            icon=":material/lock:",
        )
    if mode == "live" and market == "A股":
        st.error("A 股实盘通道尚未接入；当前只允许 A 股模拟订单。", icon=":material/lock:")
    with st.container(border=True):
        st.markdown("**限价订单**")
        with st.form("order_form"):
            columns = st.columns(3, gap="small")
            with columns[0]:
                symbol = st.text_input(
                    "标的",
                    value="AAPL" if market == "美股" else "600519",
                    max_chars=12,
                    key=f"order_symbol_{market}",
                ).strip().upper()
                side = st.segmented_control("方向", ["BUY", "SELL"], default="BUY")
            with columns[1]:
                quantity = st.number_input("数量", 1, 10_000, 1, 1)
                currency = "USD" if market == "美股" else "CNY"
                price = st.number_input(f"限价（{currency}）", 0.01, 1_000_000.0, 100.0, 0.5)
            with columns[2]:
                strategy_options = [item["name"] for item in allowed_strategies] or ["手动下单"]
                strategy = st.selectbox("订单来源", strategy_options)
                st.metric("名义金额", f"{currency} {int(quantity) * float(price):,.2f}", border=True)
            submitted = st.form_submit_button("提交订单", type="primary", icon=":material/send:")
        if submitted:
            valid_symbol = (len(symbol) == 6 and symbol.isdigit()) if market == "A股" else bool(symbol) and not symbol.isdigit()
            if not valid_symbol:
                st.error("请输入符合当前市场的股票代码。", icon=":material/error:")
            elif mode == "live" and market == "A股":
                st.error("A 股实盘通道尚未接入，订单未发送。", icon=":material/error:")
            elif mode == "live" and not live_enabled:
                st.error("实盘条件未满足，订单未发送。", icon=":material/error:")
            elif mode == "live":
                _confirm_live_order(symbol, side, int(quantity), float(price), strategy)
            else:
                _execute_order(
                    symbol=symbol,
                    side=side,
                    quantity=int(quantity),
                    price=float(price),
                    strategy=strategy,
                    mode="paper",
                )

    section_label("订单记录", "模拟记录与券商订单明确区分")
    orders = db.fetch_all("SELECT * FROM orders WHERE reason=? ORDER BY created_at DESC LIMIT 100", (f"user={user['id']}",))
    if orders:
        safe = pd.DataFrame(orders)
        safe["currency"] = safe["symbol"].map(lambda value: "CNY" if str(value).isdigit() and len(str(value)) == 6 else "USD")
        safe = safe[["order_id", "symbol", "side", "quantity", "price", "currency", "status", "strategy_name", "account_mode", "created_at"]]
        safe.columns = ["订单号", "标的", "方向", "数量", "限价", "币种", "状态", "策略", "账户", "建立时间"]
        st.dataframe(safe, hide_index=True, width="stretch")
    else:
        st.info("尚无订单。建议先用模拟盘验证订单与风控。", icon=":material/receipt_long:")
    limits = trading_limits(plan)
    if limits["brokers"]:
        account_limit = limits["brokers"]
        allowed_providers = {"标准版": ["Tiger"], "高级版": ["Tiger", "Alpaca"], "专业版": ["Tiger", "Alpaca", "IBKR"], "定制版": ["Tiger", "Alpaca", "IBKR", "Futu", "QMT", "PTrade"]}[plan]
        section_label(
            "券商账户",
            "定制版不限数量；这里只保存非敏感账户标识"
            if account_limit is None
            else f"当前方案最多 {account_limit} 家；这里只保存非敏感账户标识",
        )
        accounts = db.fetch_all("SELECT * FROM broker_accounts WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
        with st.form("broker_account"):
            columns = st.columns(3, gap="small")
            with columns[0]:
                provider = st.selectbox("券商", allowed_providers)
            with columns[1]:
                alias = st.text_input("账户别名", max_chars=50)
            with columns[2]:
                external_id = st.text_input("券商账户 ID", max_chars=80, autocomplete="off")
            account_mode = st.segmented_control("环境", ["paper", "live"], default="paper")
            add_account = st.form_submit_button("添加券商账户", icon=":material/add:")
        if add_account:
            if account_limit is not None and len(accounts) >= account_limit:
                st.error(f"当前方案最多登记 {account_limit} 个账户。")
            elif not alias.strip() or not external_id.strip():
                st.error("账户别名和券商账户 ID 不能为空。")
            else:
                try:
                    OrderManager(db).add_broker_account(
                        int(user["id"]), provider, alias, external_id, account_mode
                    )
                    st.rerun()
                except Exception as exc:
                    db.log_system_event("ERROR", "TRADING", "券商账户登记失败", str(exc)[:1000])
                    st.error("账户未添加；请检查是否已经登记过相同的券商账户。")
        if accounts:
            st.dataframe(pd.DataFrame(accounts)[["provider", "account_alias", "external_account_id", "mode", "is_active", "created_at"]], hide_index=True, width="stretch")
    st.caption("期权自动交易只对定制版开放；当前页面仅提供正股限价单入口。")
