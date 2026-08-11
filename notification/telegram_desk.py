# -*- coding: utf-8 -*-
"""Private Telegram service desk cards and plan-aware navigation."""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
from html import escape
import os
import threading
import time
from typing import Any

from core.plans import (
    PLANS,
    TELEGRAM_CHANNEL_NAMES,
    can,
    effective_plan,
    plan_display_name,
    telegram_suggestion_name,
    telegram_timeline_limits,
)
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
    claim_telegram_callback as claim_telegram_callback,
    claim_telegram_update as claim_telegram_update,
    consume_telegram_quota as consume_telegram_quota,
)
from notification.telegram_timeline import handle_timeline_action
from payment.order_service import (
    MANUAL_PAYMENT_METHODS,
    PAYMENT_METHOD_LABELS,
    ManualPaymentMethod,
    OrderService,
)
from payment.receiving_profile import ReceivingProfileService


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
    return [{"text": "⬅️ 主選單", "callback_data": "desk:home"}]


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


def _delay_label(minutes: object) -> str:
    """Render a plan-owned delay without duplicating the membership matrix."""
    value = max(0, int(minutes or 0))
    if value == 0:
        return "即時"
    hours, remainder = divmod(value, 60)
    if hours and remainder:
        return f"{hours} 小時 {remainder} 分鐘"
    if hours:
        return f"{hours} 小時"
    return f"{remainder} 分鐘"


def _home_card(chat_id: str, account: dict[str, Any] | None) -> str:
    if account:
        plan = effective_plan(account)
        identity = f"{escape(str(account.get('display_name') or account['email']))} · {escape(plan_display_name(plan))}"
    else:
        identity = f"尚未绑定账户 · Chat ID <code>{escape(str(chat_id))}</code>"
    return (
        "🤖 <b>CicloTrade · 量化服務台</b>\n\n"
        f"<blockquote>{identity}</blockquote>\n"
        "請選擇服務。"
    )


def _opening_paused(database, user_id: int) -> bool:
    row = database.fetch_one(
        "SELECT opening_paused FROM user_controls WHERE user_id=?",
        (int(user_id),),
    )
    return bool(row and row.get("opening_paused"))


def _platform_opening_paused(database) -> bool:
    row = database.fetch_one(
        "SELECT control_value FROM platform_controls WHERE control_key='opening_paused'"
    )
    return bool(
        row
        and str(row.get("control_value") or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _account_card(database, chat_id: str, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    if account:
        plan = effective_plan(account)
        paused = _opening_paused(database, int(account["id"])) or _platform_opening_paused(database)
        message = (
            "🔗 <b>CicloTrade · 账户状态</b>\n\n"
            f"<blockquote>已验证 Chat ID：<code>{escape(str(chat_id))}</code>\n"
            f"账户：{escape(str(account['email']))}\n会员：{escape(plan_display_name(plan))}\n"
            f"新开仓：{'已暂停' if paused else '风控允许'}</blockquote>\n"
            "网站与 Bot 使用同一份会员权限和通知设置。\n"
            "TG 只能暂停新开仓；恢复自动交易、提高风险或授权券商必须回网站重新验证。"
        )
        buttons: TelegramKeyboard = []
        if not paused:
            buttons.append([{"text": "⛔ 一鍵暫停新開倉", "callback_data": "desk:pause_opening"}])
        buttons.append([{"text": "🔐 前往網站帳戶頁", "url": _app_url("account")}])
        buttons.append(_home_row())
        return message, buttons
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


def _pause_opening(database, chat_id: str, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    if not account:
        return _account_card(database, chat_id, None)
    user_id = int(account["id"])
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with database.transaction() as conn:
        claimed = conn.execute(
            """INSERT INTO user_controls(user_id,opening_paused,updated_at) VALUES (?,1,?)
               ON CONFLICT(user_id) DO UPDATE SET opening_paused=1,updated_at=excluded.updated_at
               WHERE COALESCE(user_controls.opening_paused,0)=0""",
            (user_id, now),
        ).rowcount
        if claimed == 1:
            conn.execute(
                "INSERT INTO user_action_logs(user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (user_id, "PAUSE_OPENING", "opening_paused=True;source=telegram", now),
            )
    return (
        "⛔ <b>CicloTrade · 新开仓已暂停</b>\n\n"
        "<blockquote>新的正股或期权仓位会被风控拒绝；减仓、退出、行情监控与审计继续运行。</blockquote>\n"
        "TG 不提供恢复按钮。恢复自动交易、提高风险或授权券商，请回到账户页重新验证并明确确认。",
        [[{"text": "🔐 前往網站帳戶頁", "url": _app_url("account")}], _home_row()],
    )


def _plans_card(account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    current = plan_display_name(effective_plan(account)) if account else "未绑定"
    lines = [
        "💎 <b>CicloTrade · 会员方案</b>",
        "",
        f"当前：<b>{escape(current)}</b>",
        "",
    ]
    buttons: TelegramKeyboard = []
    for slug, name in _PLAN_SLUGS.items():
        plan = PLANS[name]
        display_name = plan_display_name(name)
        monthly = plan["prices"].get("monthly")
        price = "HKD 30,000 起" if monthly is None else f"HKD {float(monthly):,.0f}/月"
        lines.append(f"<b>{escape(display_name)}</b> · {price}")
        lines.append(f"{escape(str(plan['summary']))}")
        buttons.append([{"text": f"查看 {display_name}", "callback_data": f"buy:plan:{slug}"}])
    buttons.append(_home_row())
    return "\n".join(lines), buttons


def _plan_detail(slug: str, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    name = _PLAN_SLUGS[slug]
    display_name = plan_display_name(name)
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
    current = plan_display_name(effective_plan(account)) if account else "未绑定"
    return (
        f"💎 <b>{escape(display_name)}</b>\n\n"
        f"<blockquote>当前：{escape(current)}\n{escape(str(plan['summary']))}</blockquote>\n"
        f"{features}\n\n选择付款周期继续。",
        buttons,
    )


def _cycle_card(database, slug: str, cycle: str, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    name = _PLAN_SLUGS[slug]
    display_name = plan_display_name(name)
    if cycle not in PLANS[name]["prices"]:
        raise ValueError("此方案不支持该付款周期。")
    amount = float(PLANS[name]["prices"][cycle])
    if not account:
        message, buttons = _account_card(database, "--", None)
        return message, buttons
    availability = ReceivingProfileService(database).availability()
    methods = [
        (method.value, f"{PAYMENT_METHOD_LABELS[method.value]} · 人工核对")
        for method in ManualPaymentMethod
        if availability[method.value]["available"]
    ]
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
        f"<blockquote>{escape(display_name)} · {_CYCLE_LABELS[cycle]}\n"
        f"应付：HKD {amount:,.0f}</blockquote>\n"
        "数字服务付款后不支持主动退款。建立订单前，你需要再次确认同意用户协议、"
        f"风险披露与不退款政策。\n\n{availability}",
        buttons,
    )


def _method_card(database, slug: str, cycle: str, method: str) -> tuple[str, TelegramKeyboard]:
    name = _PLAN_SLUGS[slug]
    display_name = plan_display_name(name)
    if cycle not in PLANS[name]["prices"] or method not in MANUAL_PAYMENT_METHODS:
        raise ValueError("订单选项无效。")
    if not ReceivingProfileService(database).current(method)["available"]:
        raise ValueError(f"{PAYMENT_METHOD_LABELS[method]}收款资料尚未配置。")
    amount = float(PLANS[name]["prices"][cycle])
    label = PAYMENT_METHOD_LABELS[method]
    return (
        f"✅ <b>最后确认</b>\n\n"
        f"<blockquote>{escape(display_name)} · {_CYCLE_LABELS[cycle]}\n"
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
        return _account_card(database, "--", None)
    orders = OrderService(database).list_orders(int(account["id"]))[:6]
    lines = ["🧾 <b>CicloTrade · 我的订单</b>", ""]
    if not orders:
        lines.append("尚无订阅订单。")
    for order in orders:
        method = str(order["pay_method"])
        lines.extend(
            (
                f"<b>{escape(plan_display_name(str(order['plan_type'])))}</b> · {_CYCLE_LABELS.get(str(order['billing_cycle']), '--')}",
                f"<code>{escape(str(order['order_no']))}</code> · {_STATUS_LABELS.get(str(order['status']), escape(str(order['status'])))} · "
                f"{escape(str(order['currency']))} {float(order['amount']):,.0f} · "
                f"{escape(PAYMENT_METHOD_LABELS.get(method, method))}",
                "",
            )
        )
    return "\n".join(lines).rstrip(), [
        [{"text": "💎 开通会员", "callback_data": "desk:plans"}],
        _home_row(),
    ]


def _membership_card(account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    if not account:
        return (
            "💎 <b>CicloTrade · 會員與訂單</b>\n\n綁定帳戶後，可查看會員權益與訂單。",
            [
                [{"text": "🔗 綁定帳戶", "callback_data": "desk:account"}],
                [{"text": "💎 查看方案", "callback_data": "desk:plans"}],
                _home_row(),
            ],
        )
    plan = effective_plan(account)
    if can(plan, "tg_option_signal"):
        channel = TELEGRAM_CHANNEL_NAMES["professional"]
        access = "即時正股建議\n即時期權建議"
    elif can(plan, "tg_stock_signal"):
        channel = TELEGRAM_CHANNEL_NAMES["advanced"]
        access = "即時正股建議\n期權建議需具備專業權限"
    else:
        channel = TELEGRAM_CHANNEL_NAMES["daily"]
        stock_delay = _delay_label(telegram_timeline_limits(plan).get("stock_delay_minutes"))
        access = f"私人即時建議未開放\n正股建議延遲 {stock_delay}"
    return (
        "💎 <b>CicloTrade · 會員與訂單</b>\n\n"
        f"<blockquote>會員　{escape(plan_display_name(plan))}\n"
        f"頻道　{escape(channel)}\n{escape(access)}</blockquote>",
        [
            [{"text": "💎 會員方案", "callback_data": "desk:plans"}],
            [{"text": "🧾 我的訂單", "callback_data": "desk:orders"}],
            _home_row(),
        ],
    )


def _actions_card(database, account: dict[str, Any] | None) -> tuple[str, TelegramKeyboard]:
    if not account:
        return (
            "🔒 <b>今日建議需要綁定帳戶</b>\n\n綁定後，Bot 會按會員等級顯示量化建議。",
            [[{"text": "🔗 绑定账户", "callback_data": "desk:account"}], [{"text": "💎 查看方案", "callback_data": "desk:plans"}], _home_row()],
        )
    plan = effective_plan(account)
    if not can(plan, "tg_stock_signal"):
        return (
            "🔒 <b>即時建議 · 會員功能</b>\n\n"
            "高級會員可查看即時正股建議；專業會員增加即時期權建議。",
            [[{"text": "💎 升级会员", "callback_data": "desk:plans"}], _home_row()],
        )
    journal = QuantJournal(database)
    events = journal.list_events(_SYSTEM_LEDGER)
    include_options = can(plan, "tg_option_signal")
    lines = ["📈 <b>CicloTrade · 今日建議</b>", ""]
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
            action = "買入建議" if delta > 0 else "賣出建議"
            symbol = escape(str(leg.get("symbol") or "--"))
            instrument = telegram_suggestion_name(str(leg.get("instrument_type")))
            lines.append(
                f"{'🟢' if delta > 0 else '🔴'} <b>{action} · {symbol}</b>\n{instrument} · {abs(delta):g} · "
                f"{_money(leg.get('currency'), leg.get('price'))}"
            )
            lines.append(f"目標持倉 {float(leg.get('target_quantity') or 0):g} · {escape(str(event['strategy_name']))}")
            visible += 1
            if visible >= 5:
                break
    if not visible:
        lines.append("目前沒有可展示的已驗證量化建議。")
    if hidden_options:
        lines.append(f"\n🔒 另有 {hidden_options} 條期權建議，升級專業會員後查看。")
    lines.extend(("", "⚡ 經量化系統數據分析建議", "⚠️ 系統可能提前止盈或止損，請留意最新建議。"))
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
        [{"text": "📈 今日建議", "callback_data": "desk:actions"}],
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
        "3. 今日建議：按會員權限查看量化建議\n"
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
        timeline = handle_timeline_action(database, chat_id, account, command)
        if timeline is not None:
            return timeline
        if command in {"desk:home", "menu:home"}:
            message, keyboard = _home_card(chat_id, account), telegram_main_keyboard()
            from notification.telegram_payment_receivers import is_billing_admin

            if is_billing_admin(database, account):
                keyboard.insert(-1, [{"text": "🏦 收款资料管理", "callback_data": "desk:receiving"}])
        elif command == "desk:account":
            message, keyboard = _account_card(database, chat_id, account)
        elif command == "desk:pause_opening":
            message, keyboard = _pause_opening(database, chat_id, account)
        elif command == "desk:plans":
            message, keyboard = _plans_card(account)
        elif command == "desk:membership":
            message, keyboard = _membership_card(account)
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
            message, keyboard = _cycle_card(database, slug, cycle, account)
        elif command.startswith("buy:method:"):
            _, _, slug, cycle, method = command.split(":")
            message, keyboard = _method_card(database, slug, cycle, method)
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
