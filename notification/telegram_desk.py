# -*- coding: utf-8 -*-
"""Private Telegram service desk cards and plan-aware navigation."""

from __future__ import annotations

from html import escape
import os
import threading
import time
from typing import Any

from core.plans import PLANS, can, effective_plan
from core.quant_journal import QuantJournal
from notification.telegram_bot import (
    TelegramKeyboard,
    telegram_main_keyboard,
    telegram_notification_keyboard,
    update_notification_preference,
    verified_account_for_chat,
)
from notification.telegram_models import TelegramDeskResponse
from notification.telegram_security import (
    claim_telegram_callback,
    claim_telegram_update,
    consume_telegram_quota,
)
from payment.order_service import OrderService


_SYSTEM_LEDGER = os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
_PLAN_SLUGS = {
    "standard": "标准版",
    "advanced": "高级版",
    "professional": "专业版",
    "custom": "定制版",
}
_CYCLE_LABELS = {
    "monthly": "月付",
    "quarterly": "季付",
    "yearly": "年付",
    "project": "项目",
}
_STATUS_LABELS = {
    "pending": "待付款",
    "paid": "已开通",
    "failed": "付款失败",
    "cancelled": "已取消",
    "refunded": "已逆转",
}
_MARKET_CACHE: tuple[float, str] | None = None
_MARKET_CACHE_LOCK = threading.Lock()


def _app_url(path: str) -> str:
    base = os.getenv("APP_BASE_URL", "https://ciclotrade.com").strip().rstrip("/")
    if not base.startswith("https://"):
        base = "https://ciclotrade.com"
    return f"{base}/{path.strip('/')}"


def _home_row() -> list[dict[str, str]]:
    return [{"text": "⬅️ 主选单", "callback_data": "desk:home"}]


def _account(database, chat_id: str) -> dict[str, Any] | None:
    """Resolve one verified private chat to one active CicloTrade account."""
    return verified_account_for_chat(database, chat_id)


def _money(currency: object, value: object) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "--"
    symbol = "¥" if str(currency).upper() == "CNY" else "$"
    return f"{symbol}{amount:,.2f}"


def _home_card(chat_id: str, account: dict[str, Any] | None) -> str:
    if account:
        plan = effective_plan(account)
        identity = f"{escape(str(account.get('display_name') or account['email']))} · {escape(plan)}"
    else:
        identity = f"尚未绑定账户 · Chat ID <code>{escape(str(chat_id))}</code>"
    return (
        "🤖 <b>CicloTrade · 量化服务台</b>\n\n"
        f"<blockquote>{identity}\n行情、模拟持仓、量化行动、会员与通知都可在这里完成。</blockquote>\n"
        "请选择需要查看的功能。"
    )


def _account_card(chat_id: str, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    if account:
        plan = effective_plan(account)
        message = (
            "🔗 <b>CicloTrade · 账户状态</b>\n\n"
            f"<blockquote>已验证 Chat ID：<code>{escape(str(chat_id))}</code>\n"
            f"账户：{escape(str(account['email']))}\n会员：{escape(plan)}</blockquote>\n"
            "网站与 Bot 使用同一份会员权限和通知设置。"
        )
        return message, [_home_row()]
    message = (
        "🔗 <b>CicloTrade · 绑定账户</b>\n\n"
        f"<blockquote>你的 Chat ID：<code>{escape(str(chat_id))}</code></blockquote>\n"
        "为防止他人冒领账户，首次绑定必须在网站登录后完成一次验证。绑定完成后，"
        "行情、持仓、订单和通知都可直接在 Bot 内使用。"
    )
    return message, [
        [{"text": "🔐 安全绑定", "url": _app_url("account")}],
        _home_row(),
    ]


def _plans_card(account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    current = effective_plan(account) if account else "未绑定"
    lines = [
        "💎 <b>CicloTrade · 会员方案</b>",
        "",
        f"当前：<b>{escape(current)}</b>",
        "",
    ]
    buttons: TelegramKeyboard = []
    for slug, name in _PLAN_SLUGS.items():
        plan = PLANS[name]
        monthly = plan["prices"].get("monthly")
        price = "HKD 30,000 起" if monthly is None else f"HKD {float(monthly):,.0f}/月"
        lines.append(f"<b>{escape(name)}</b> · {price}")
        lines.append(f"{escape(str(plan['summary']))}")
        buttons.append([{"text": f"查看 {name}", "callback_data": f"buy:plan:{slug}"}])
    buttons.append(_home_row())
    return "\n".join(lines), buttons


def _plan_detail(slug: str, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    name = _PLAN_SLUGS[slug]
    plan = PLANS[name]
    features = "\n".join(f"• {escape(str(item))}" for item in plan["features"])
    annual_bonus = OrderService().annual_bonus_enabled()
    buttons: TelegramKeyboard = []
    for cycle, amount in plan["prices"].items():
        label = _CYCLE_LABELS[cycle]
        if cycle == "yearly" and annual_bonus:
            label = "年付 · 15个月"
        buttons.append(
            [{"text": f"{label} · HKD {float(amount):,.0f}", "callback_data": f"buy:cycle:{slug}:{cycle}"}]
        )
    buttons.extend(
        ([{"text": "⬅️ 返回方案", "callback_data": "desk:plans"}], _home_row())
    )
    current = effective_plan(account) if account else "未绑定"
    return (
        f"💎 <b>{escape(name)}</b>\n\n"
        f"<blockquote>当前：{escape(current)}\n{escape(str(plan['summary']))}</blockquote>\n"
        f"{features}\n\n选择付款周期继续。",
        buttons,
    )


def _cycle_card(slug: str, cycle: str, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    name = _PLAN_SLUGS[slug]
    if cycle not in PLANS[name]["prices"]:
        raise ValueError("此方案不支持该付款周期。")
    amount = float(PLANS[name]["prices"][cycle])
    if not account:
        message, buttons = _account_card("--", None)
        return message, buttons
    methods: list[tuple[str, str]] = []
    if os.getenv("FPS_PAYMENT_INSTRUCTIONS", "").strip():
        methods.append(("fps", "FPS 转数快 · 人工核对"))
    try:
        from payment.paypal_client import PayPalClient

        if PayPalClient().configured:
            methods.append(("paypal", "PayPal 安全付款"))
    except Exception:
        pass
    try:
        from payment.paddle_client import PaddleClient

        if PaddleClient().configured:
            methods.append(("paddle", "Paddle 安全付款"))
    except Exception:
        pass
    buttons = [
        [{"text": label, "callback_data": f"buy:method:{slug}:{cycle}:{method}"}]
        for method, label in methods
    ]
    buttons.extend(
        ([{"text": "⬅️ 返回方案", "callback_data": f"buy:plan:{slug}"}], _home_row())
    )
    availability = "选择付款方式继续。" if methods else "当前付款通道尚未开放，请联系客户服务。"
    return (
        f"🧾 <b>确认订单</b>\n\n"
        f"<blockquote>{escape(name)} · {_CYCLE_LABELS[cycle]}\n"
        f"应付：HKD {amount:,.0f}</blockquote>\n"
        "数字服务付款后不支持主动退款。建立订单前，你需要再次确认同意用户协议、"
        f"风险披露与不退款政策。\n\n{availability}",
        buttons,
    )


def _method_card(slug: str, cycle: str, method: str) -> tuple[str, TelegramKeyboard]:
    name = _PLAN_SLUGS[slug]
    if cycle not in PLANS[name]["prices"] or method not in {"fps", "paypal", "paddle"}:
        raise ValueError("订单选项无效。")
    amount = float(PLANS[name]["prices"][cycle])
    label = {"fps": "FPS 转数快", "paypal": "PayPal", "paddle": "Paddle"}[method]
    return (
        f"✅ <b>最后确认</b>\n\n"
        f"<blockquote>{escape(name)} · {_CYCLE_LABELS[cycle]}\n"
        f"{label} · HKD {amount:,.0f}</blockquote>\n"
        "点击下方按钮即表示你已阅读并同意用户协议、隐私政策、风险披露与付款后不退款政策。",
        [
            [{"text": "同意并建立订单", "callback_data": f"buy:create:{slug}:{cycle}:{method}"}],
            [{"text": "⬅️ 返回", "callback_data": f"buy:cycle:{slug}:{cycle}"}],
            _home_row(),
        ],
    )


def _orders_card(database, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    if not account:
        return _account_card("--", None)
    orders = OrderService(database).list_orders(int(account["id"]))[:6]
    lines = ["🧾 <b>CicloTrade · 我的订单</b>", ""]
    if not orders:
        lines.append("尚无订阅订单。")
    for order in orders:
        lines.extend(
            (
                f"<b>{escape(str(order['plan_type']))}</b> · {_CYCLE_LABELS.get(str(order['billing_cycle']), '--')}",
                f"<code>{escape(str(order['order_no']))}</code> · {_STATUS_LABELS.get(str(order['status']), escape(str(order['status'])))} · "
                f"{escape(str(order['currency']))} {float(order['amount']):,.0f}",
                "",
            )
        )
    return "\n".join(lines).rstrip(), [
        [{"text": "💎 开通会员", "callback_data": "desk:plans"}],
        _home_row(),
    ]


def _actions_card(database, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    if not account:
        return (
            "🔒 <b>今日行动需要绑定账户</b>\n\n绑定后，Bot 会按你的会员等级展示量化交易行动。",
            [[{"text": "🔗 绑定账户", "callback_data": "desk:account"}], [{"text": "💎 查看方案", "callback_data": "desk:plans"}], _home_row()],
        )
    plan = effective_plan(account)
    if not can(plan, "signal_web"):
        return (
            "🔒 <b>今日行动 · 会员功能</b>\n\n"
            "标准版及以上可查看量化系统已执行的正股行动与时间线；高级版增加期权研究。",
            [[{"text": "💎 升级会员", "callback_data": "desk:plans"}], _home_row()],
        )
    journal = QuantJournal(database)
    events = journal.list_events(_SYSTEM_LEDGER)
    include_options = can(plan, "option_chain")
    lines = ["📈 <b>CicloTrade · 今日行动</b>", ""]
    visible = 0
    hidden_options = 0
    for event in reversed(events):
        if visible >= 5:
            break
        try:
            legs = journal.execution_legs(int(event["id"]))
        except (KeyError, TypeError, ValueError, RuntimeError, StopIteration):
            continue
        for leg in legs:
            if leg.get("instrument_type") == "option" and not include_options:
                hidden_options += 1
                continue
            delta = float(leg.get("quantity_delta") or 0)
            action = "买入" if delta > 0 else "卖出"
            symbol = escape(str(leg.get("symbol") or "--"))
            instrument = "期权" if leg.get("instrument_type") == "option" else "正股"
            lines.append(
                f"{'🟢' if delta > 0 else '🔴'} <b>{action} {symbol}</b> · {instrument} · {abs(delta):g} · "
                f"{_money(leg.get('currency'), leg.get('price'))}"
            )
            lines.append(f"目标持仓 {float(leg.get('target_quantity') or 0):g} · {escape(str(event['strategy_name']))}")
            visible += 1
            if visible >= 5:
                break
    if not visible:
        lines.append("当前没有可展示的已验证量化操作。")
    if hidden_options:
        lines.append(f"\n🔒 另有 {hidden_options} 条期权行动，升级高级版后查看。")
    lines.extend(("", "⚡ 经量化系统数据分析建议", "⚠️ 系统可能提前止盈或止损，请留意最新推送。"))
    return "\n".join(lines), [
        [{"text": "💼 查看模拟持仓", "callback_data": "desk:portfolio"}],
        [{"text": "💎 会员方案", "callback_data": "desk:plans"}],
        _home_row(),
    ]


def _portfolio_card(database) -> tuple[str, TelegramKeyboard]:
    try:
        from trading.tiger_api import TigerAPI

        snapshot = TigerAPI().paper_snapshot()
    except Exception as exc:
        database.log_system_event("WARN", "TELEGRAM", "Bot 模拟持仓读取失败", type(exc).__name__)
        return (
            "💼 <b>CicloTrade · 模拟持仓</b>\n\n模拟账户正在同步，请稍后再试。",
            [[{"text": "🔄 重新载入", "callback_data": "desk:portfolio"}], _home_row()],
        )
    account = snapshot.get("account") or {}
    currency = str(account.get("currency") or "USD")
    lines = [
        "💼 <b>CicloTrade · Tiger 模拟账户</b>",
        "",
        f"<blockquote>总资产 {_money(currency, account.get('total_assets'))}\n"
        f"现金 {_money(currency, account.get('cash'))} · 持仓 {_money(currency, account.get('market_value'))}\n"
        f"今日盈亏 {_money(currency, account.get('today_pnl'))}</blockquote>",
        "<b>最新持仓</b>",
    ]
    positions = snapshot.get("positions") or []
    if not positions:
        lines.append("• 暂无持仓")
    for position in positions[:8]:
        kind = "期权" if position.get("instrument_type") == "option" else "正股"
        lines.append(
            f"• <b>{escape(str(position.get('symbol') or '--'))}</b> · {kind} · "
            f"{float(position.get('quantity') or 0):g} · {_money(position.get('currency'), position.get('market_price'))}"
        )
    lines.extend(("", "公开模拟组合，仅用于展示量化系统实际记录，不代表客户个人资产。"))
    return "\n".join(lines), [
        [{"text": "📈 今日行动", "callback_data": "desk:actions"}],
        _home_row(),
    ]


def _market_card(database) -> tuple[str, TelegramKeyboard]:
    global _MARKET_CACHE
    now = time.monotonic()
    ttl = max(15, min(int(os.getenv("TELEGRAM_MARKET_CACHE_SECONDS", "60")), 300))
    with _MARKET_CACHE_LOCK:
        if _MARKET_CACHE and now - _MARKET_CACHE[0] < ttl:
            return _MARKET_CACHE[1], [[{"text": "🔄 更新行情", "callback_data": "desk:market"}], _home_row()]
        try:
            from data.datasource import get_resilient_data_source, public_market_status

            us_source = get_resilient_data_source(os.getenv("DATA_SOURCE", "yfinance"))
            us_closes, _ = us_source.history(("AAPL", "NVDA", "MSFT"), period="5d", interval="1d")
            cn_source = get_resilient_data_source("yfinance")
            cn_closes, _ = cn_source.history(("000001", "600519"), period="5d", interval="1d")
            lines = ["📊 <b>CicloTrade · 市场行情</b>", "", "<b>美股</b>"]
            for symbol in ("AAPL", "NVDA", "MSFT"):
                series = us_closes[symbol].dropna()
                latest = float(series.iloc[-1])
                previous = float(series.iloc[-2]) if len(series) > 1 else latest
                change = latest / previous - 1 if previous else 0
                lines.append(f"{'🟢' if change >= 0 else '🔴'} {symbol} · ${latest:,.2f} · {change:+.2%}")
            lines.extend(("", "<b>大A</b>"))
            for symbol in ("000001", "600519"):
                series = cn_closes[symbol].dropna()
                latest = float(series.iloc[-1])
                previous = float(series.iloc[-2]) if len(series) > 1 else latest
                change = latest / previous - 1 if previous else 0
                lines.append(f"{'🟢' if change >= 0 else '🔴'} {symbol} · ¥{latest:,.2f} · {change:+.2%}")
            status = public_market_status(os.getenv("DATA_SOURCE", "yfinance"), "美股")
            lines.extend(("", f"<i>{escape(str(status['display_source']))} · {escape(str(status['freshness']))}</i>"))
            message = "\n".join(lines)
            _MARKET_CACHE = (now, message)
        except Exception as exc:
            database.log_system_event("WARN", "TELEGRAM", "Bot 行情读取失败", type(exc).__name__)
            message = "📊 <b>CicloTrade · 市场行情</b>\n\n行情正在重新连接，请稍后再试。"
    return message, [[{"text": "🔄 更新行情", "callback_data": "desk:market"}], _home_row()]


def _help_card() -> tuple[str, TelegramKeyboard]:
    return (
        "❓ <b>CicloTrade · 使用帮助</b>\n\n"
        "<blockquote>1. 行情：查看美股与大A快照\n"
        "2. 模拟持仓：查看平台公开模拟组合\n"
        "3. 今日行动：按会员权限查看量化操作\n"
        "4. 开通会员：选择方案、周期和付款方式\n"
        "5. 通知设置：网站与 Bot 自动同步</blockquote>\n"
        "Bot 不会要求券商密码、API Secret 或付款密码。",
        [[{"text": "联系客服", "url": "https://t.me/Maxooo8"}], _home_row()],
    )


def telegram_desk_response(
    database,
    chat_id: str,
    value: str,
    *,
    callback: bool = False,
    message_id: int | None = None,
    update_id: str | int | None = None,
    photo: dict[str, str] | None = None,
) -> TelegramDeskResponse:
    command = str(value or "").strip()
    head = command.lower().split(maxsplit=1)[0].split("@", 1)[0] if command else "/start"
    if not callback:
        command = {
            "/start": "desk:home",
            "/plans": "desk:plans",
            "/orders": "desk:orders",
            "/help": "desk:help",
            "/id": "desk:account",
        }.get(head, command)
    account = _account(database, chat_id)
    try:
        from notification.telegram_billing import handle_billing_action

        billing = handle_billing_action(
            database,
            chat_id,
            account,
            command,
            message_id=message_id,
            update_id=update_id,
            photo=photo,
        )
        if billing is not None:
            return billing
        if command in {"desk:home", "menu:home"}:
            message, keyboard = _home_card(chat_id, account), telegram_main_keyboard()
        elif command == "desk:account":
            message, keyboard = _account_card(chat_id, account)
        elif command == "desk:plans":
            message, keyboard = _plans_card(account)
        elif command == "desk:orders":
            message, keyboard = _orders_card(database, account)
        elif command == "desk:actions":
            message, keyboard = _actions_card(database, account)
        elif command == "desk:portfolio":
            message, keyboard = _portfolio_card(database)
        elif command == "desk:market":
            message, keyboard = _market_card(database)
        elif command == "desk:help":
            message, keyboard = _help_card()
        elif (
            command in {"desk:settings", "menu:settings"}
            or command.startswith("notify:")
            or head in {"/settings", "/notify"}
        ):
            preference_command = command
            if command.startswith("notify:"):
                _, event_name, action = command.split(":")
                preference_command = f"/notify {event_name} {action}"
            elif head not in {"/settings", "/notify"}:
                preference_command = "/settings"
            message = update_notification_preference(
                database,
                chat_id,
                preference_command,
            )
            keyboard = telegram_notification_keyboard(database, chat_id)
        elif command.startswith("buy:plan:"):
            message, keyboard = _plan_detail(command.split(":")[2], account)
        elif command.startswith("buy:cycle:"):
            _, _, slug, cycle = command.split(":")
            message, keyboard = _cycle_card(slug, cycle, account)
        elif command.startswith("buy:method:"):
            _, _, slug, cycle, method = command.split(":")
            message, keyboard = _method_card(slug, cycle, method)
        else:
            message, keyboard = _help_card()
    except (KeyError, ValueError, PermissionError) as exc:
        message = f"⚠️ <b>无法完成操作</b>\n\n{escape(str(exc))}"
        keyboard = [_home_row()]
    except RuntimeError:
        database.log_system_event("WARN", "TELEGRAM", "Bot 服务操作暂时失败", command[:120])
        message = "⚠️ <b>服务暂时不可用</b>\n\n请求已安全停止，请稍后再试。"
        keyboard = [_home_row()]
    return TelegramDeskResponse(message, keyboard)
