# -*- coding: utf-8 -*-
"""Protected, plan-aware private Telegram timeline queries."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
from html import escape
import math
import os
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from core.plans import effective_plan, plan_display_name, telegram_suggestion_name, telegram_timeline_limits
from core.official_paper_consumers import active_events as official_consumer_events
from core.trade_timeline import project_trade_cycles, summarize_trade_cycles
from notification.telegram_models import TelegramDeskResponse
from notification.telegram_security import consume_telegram_timeline_quota


_SYSTEM_LEDGER = os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, list[dict[str, Any]], str | None]] = {}
_RENDER_SEMAPHORE = threading.BoundedSemaphore(
    max(1, min(int(os.getenv("TELEGRAM_TIMELINE_CONCURRENCY", "2")), 4))
)
_PAGE_SIZE = 5


def _home_row() -> list[dict[str, str]]:
    return [{"text": "⬅️ 主選單", "callback_data": "desk:home"}]


def _money(currency: object, value: object, *, signed: bool = False) -> str:
    amount = float(value or 0)
    symbol = "¥" if str(currency).upper() == "CNY" else "$"
    prefix = "+" if signed and amount > 0 else ""
    return f"{prefix}{symbol}{amount:,.2f}"


def _delay_label(minutes: int) -> str:
    return "1 小時" if int(minutes) == 60 else f"{int(minutes)} 分鐘"


def _time(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return f"{parsed.astimezone(ZoneInfo('Asia/Hong_Kong')):%m/%d %H:%M}"
    except (TypeError, ValueError):
        return "--"


def _instrument(cycle: dict[str, Any]) -> str:
    symbol = escape(str(cycle.get("symbol") or "--"))
    if cycle.get("instrument_type") != "option":
        return symbol
    right = "Call" if str(cycle.get("option_right")).upper() == "CALL" else "Put"
    expiry = escape(str(cycle.get("option_expiry") or "--"))
    strike = float(cycle.get("option_strike") or 0)
    icon = "🟢" if right == "Call" else "🔴"
    return f"{icon} {symbol} {expiry} {strike:g} {right}"


def _position_key(position: dict[str, Any]) -> str | None:
    symbol = str(position.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    if position.get("instrument_type") != "option":
        return f"US:STOCK:{symbol}"
    expiry = str(position.get("expiry") or "").strip()
    right = {"C": "CALL", "CALL": "CALL", "P": "PUT", "PUT": "PUT"}.get(
        str(position.get("right") or "").strip().upper()
    )
    try:
        strike = format(float(position.get("strike")), ".12g")
    except (TypeError, ValueError):
        return None
    return f"US:OPTION:{symbol}:{expiry}:{right}:{strike}" if expiry and right else None


def _fresh_marks(database) -> tuple[dict[str, float], str | None]:
    try:
        from trading.tiger_api import TigerAPI

        snapshot = TigerAPI().paper_snapshot()
        marks = {}
        for position in snapshot.get("positions") or ():
            key = _position_key(position)
            price = float(position.get("market_price") or 0)
            if key and math.isfinite(price) and price > 0:
                marks[key] = price
        return marks, datetime.now(UTC).isoformat(timespec="seconds")
    except Exception as exc:
        database.log_system_event("WARN", "TELEGRAM", "交易時間線估值暫不可用", type(exc).__name__)
        return {}, None


def _cycles(database, kind: str, *, include_marks: bool = True) -> tuple[list[dict[str, Any]], str | None]:
    ttl = max(15, min(int(os.getenv("TELEGRAM_TIMELINE_CACHE_SECONDS", "30")), 120))
    now = time.monotonic()
    cache_key = f"{kind}:{'marked' if include_marks else 'closed'}"
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] < ttl:
            return cached[1], cached[2]
    events = official_consumer_events(database)
    max_events = max(500, min(int(os.getenv("TELEGRAM_TIMELINE_MAX_EVENTS", "5000")), 20_000))
    if len(events) > max_events:
        raise RuntimeError("交易記錄正在建立索引，請稍後再試。")
    marks, marked_at = _fresh_marks(database) if include_marks else ({}, None)
    result = project_trade_cycles(events, kind, marks=marks)
    with _CACHE_LOCK:
        _CACHE[cache_key] = (now, result, marked_at)
    return result, marked_at


def _plan_visible_cycles(
    cycles: list[dict[str, Any]],
    plan: str,
    kind: str,
    *,
    delay_minutes: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Prevent private Bot queries from bypassing channel delay entitlements."""
    delay = telegram_timeline_limits(plan)[f"{kind}_delay_minutes"] if delay_minutes is None else int(delay_minutes)
    if delay <= 0:
        return cycles, 0
    cutoff = datetime.now(UTC) - timedelta(minutes=delay)
    visible: list[dict[str, Any]] = []
    for cycle in cycles:
        try:
            visible_at = datetime.fromisoformat(
                str(cycle.get("recorded_at") or cycle.get("updated_at")).replace("Z", "+00:00")
            )
            if visible_at.tzinfo is None:
                visible_at = visible_at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            continue
        if visible_at > cutoff:
            continue
        delayed = dict(cycle)
        delayed["entitlement_redacted"] = True
        delayed["mark_price"] = None
        delayed["unrealized_pnl"] = None
        visible.append(delayed)
    return visible, delay


def _cycle_block(cycle: dict[str, Any]) -> str:
    if cycle.get("entitlement_redacted") is True:
        return "\n".join(
            (
                "<blockquote>",
                f"🔒 <b>#{int(cycle['sequence'])} 延遲研究記錄</b>",
                "標的、交易方向、入場價格、數量、止盈與止損已隱藏",
                "升級對應會員後查看完整可執行資訊",
                "</blockquote>",
            )
        )
    closed = bool(cycle.get("closed_at"))
    direction = "做多" if cycle.get("direction") == "long" else "做空"
    status = "已平倉" if closed else "持倉中"
    icon = "✅" if closed else "⏳"
    unit = "張" if cycle.get("instrument_type") == "option" else "股"
    quantity = float(cycle.get("opened_quantity") if closed else cycle.get("current_quantity") or 0)
    lines = [
        "<blockquote>",
        f"{icon} <b>#{int(cycle['sequence'])} {_instrument(cycle)}</b>",
        f"{direction} · {status}",
        f"開倉　{_time(cycle.get('opened_at'))}",
    ]
    if closed:
        pnl = float(cycle.get("realized_pnl") or 0)
        ratio = cycle.get("return")
        lines.extend(
            (
                f"平倉　{_time(cycle.get('closed_at'))}",
                f"累計開倉　{quantity:g} {unit}",
                f"已實現損益　<b>{_money(cycle.get('currency'), pnl, signed=True)}</b>",
                f"交易回報　<b>{float(ratio):+.2%}</b>" if ratio is not None else "交易回報　--",
            )
        )
    else:
        lines.extend(
            (
                f"開倉均價　{_money(cycle.get('currency'), cycle.get('average_cost'))}",
                f"目前持倉　{abs(quantity):g} {unit}",
                (
                    f"浮動損益　<b>{_money(cycle.get('currency'), cycle.get('unrealized_pnl'), signed=True)}</b>"
                    if cycle.get("unrealized_pnl") is not None
                    else "浮動損益　暫無可信即時估值"
                ),
            )
        )
    lines.append("</blockquote>")
    return "\n".join(lines)


def _summary_lines(cycles: list[dict[str, Any]]) -> list[str]:
    summary = summarize_trade_cycles(cycles)
    lines = [
        "<b>本次查詢彙總</b>",
        f"獲利 {summary['profitable']} 筆 · 虧損 {summary['losing']} 筆 · 平盤 {summary['breakeven']} 筆",
        f"持倉中 {summary['open']} 筆",
    ]
    for currency, values in summary["currencies"].items():
        ratio = values.get("return")
        realized = f"{_money(currency, values['realized_pnl'], signed=True)}"
        lines.append(f"{escape(currency)} 已實現　{realized}{f' ({float(ratio):+.2%})' if ratio is not None else ''}")
        if summary["open"]:
            if values["open_missing_marks"]:
                lines.append(f"{escape(currency)} 浮動　部分持倉暫無可信即時估值")
            else:
                lines.append(f"{escape(currency)} 浮動　{_money(currency, values['unrealized_pnl'], signed=True)}")
    return lines


def _query_center() -> TelegramDeskResponse:
    return TelegramDeskResponse(
        "🔎 <b>CicloTrade · 查詢中心</b>\n\n"
        "請選擇查詢項目。",
        [
            [{"text": "📅 已平倉盈虧", "callback_data": "desk:pnl"}],
            [{"text": "📜 交易時間線", "callback_data": "desk:timeline"}],
            [{"text": "📈 今日建議", "callback_data": "desk:actions"}],
            [{"text": "💼 模擬持倉", "callback_data": "desk:portfolio"}],
            [{"text": "📊 市場行情", "callback_data": "desk:market"}],
            [{"text": "🔔 預警與通知", "callback_data": "desk:settings"}],
            _home_row(),
        ],
    )


def _timeline_home(account: dict[str, Any] | None) -> TelegramDeskResponse:
    if not account:
        return TelegramDeskResponse(
            "🔒 <b>交易時間線需要綁定帳戶</b>\n\n綁定後才可按會員權限查詢。",
            [[{"text": "🔗 綁定帳戶", "callback_data": "desk:account"}], _home_row()],
        )
    plan = effective_plan(account)
    limits = telegram_timeline_limits(plan)
    option_label = "期權建議時間線" if limits["option"] else "🔒 期權建議 · 專業會員"
    if limits["option"] and limits["option_delay_minutes"]:
        option_label += f" · 延遲 {_delay_label(limits['option_delay_minutes'])}"
    return TelegramDeskResponse(
        "📜 <b>CicloTrade · 交易時間線</b>\n\n"
        f"<blockquote>目前等級　{escape(plan_display_name(plan))}\n"
        "一筆交易由開倉開始，完整平倉後結束；再次開倉會建立新的一筆。</blockquote>\n"
        "請先選擇交易種類。",
        [
            [{"text": "📈 正股建議時間線", "callback_data": "timeline:choose:stock"}],
            [{"text": f"🧾 {option_label}", "callback_data": "timeline:choose:option"}],
            [{"text": "⬅️ 查詢中心", "callback_data": "desk:queries"}],
            _home_row(),
        ],
    )


def _count_picker(account: dict[str, Any], kind: str) -> TelegramDeskResponse:
    plan = effective_plan(account)
    maximum = telegram_timeline_limits(plan)[kind]
    label = telegram_suggestion_name(kind)
    if maximum <= 0:
        return TelegramDeskResponse(
            f"🔒 <b>{label}時間線</b>\n\n{label}時間線需要升級會員後查看。",
            [[{"text": "💎 查看會員方案", "callback_data": "desk:plans"}], [{"text": "⬅️ 返回", "callback_data": "desk:timeline"}], _home_row()],
        )
    buttons = [[{"text": "最近 10 筆", "callback_data": f"timeline:show:{kind}:10:0"}]]
    if maximum >= 30:
        buttons.append([{"text": "最近 30 筆", "callback_data": f"timeline:show:{kind}:30:0"}])
        buttons.append([{"text": f"自訂筆數 · 最多 {maximum}", "callback_data": f"timeline:custom:{kind}"}])
    buttons.extend(([{"text": "⬅️ 返回", "callback_data": "desk:timeline"}], _home_row()))
    return TelegramDeskResponse(
        f"📜 <b>{label}時間線</b>\n\n"
        f"<blockquote>{escape(plan_display_name(plan))} · 最多查詢 {maximum} 筆\n"
        "每頁顯示 5 筆，頁尾彙總本次查詢的全部交易。</blockquote>\n"
        "請選擇查詢筆數。",
        buttons,
    )


def _custom_prompt(account: dict[str, Any], kind: str) -> TelegramDeskResponse:
    plan = effective_plan(account)
    maximum = telegram_timeline_limits(plan)[kind]
    label = "stock" if kind == "stock" else "option"
    if maximum <= 10:
        raise PermissionError("目前會員等級不提供自訂筆數。")
    return TelegramDeskResponse(
        "⌨️ <b>自訂查詢筆數</b>\n\n"
        f"請直接發送：\n<code>/timeline {label} 50</code>\n\n"
        f"可輸入 1 至 {maximum}；超出會員上限的請求會被拒絕。",
        [[{"text": "⬅️ 返回", "callback_data": f"timeline:choose:{kind}"}], _home_row()],
    )


def _render_timeline(database, chat_id: str, account: dict[str, Any], kind: str, count: int, page: int) -> TelegramDeskResponse:
    plan = effective_plan(account)
    limits = telegram_timeline_limits(plan)
    maximum = limits[kind]
    if maximum <= 0:
        raise PermissionError("目前會員等級無法查看此交易種類。")
    if not 1 <= count <= maximum:
        raise PermissionError(f"{plan_display_name(plan)}最多查詢 {maximum} 筆。")
    if page < 0 or page > 999:
        raise ValueError("查詢頁碼無效。")
    if not consume_telegram_timeline_quota(
        database,
        chat_id,
        per_minute=limits["per_minute"],
        per_day=limits["per_day"],
        count_daily=page == 0,
    ):
        raise PermissionError("查詢過於頻繁或已達今日上限，請稍後再試。")
    if not _RENDER_SEMAPHORE.acquire(blocking=False):
        raise RuntimeError("查詢服務目前繁忙，請稍後再試。")
    try:
        all_cycles, marked_at = _cycles(database, kind)
    finally:
        _RENDER_SEMAPHORE.release()
    all_cycles, delay_minutes = _plan_visible_cycles(all_cycles, plan, kind)
    if delay_minutes:
        marked_at = None
    selected = all_cycles[:count]
    if not selected:
        detail = (
            f"目前沒有已達 {_delay_label(delay_minutes)} 延遲時間的建議記錄。"
            if delay_minutes
            else "目前尚無可展示的完整量化建議記錄。"
        )
        return TelegramDeskResponse(
            f"📜 <b>交易時間線</b>\n\n{detail}",
            [[{"text": "⬅️ 變更條件", "callback_data": f"timeline:choose:{kind}"}], _home_row()],
        )
    start = page * _PAGE_SIZE
    if start >= len(selected):
        raise ValueError("查詢頁碼已超出結果範圍。")
    current = selected[start : start + _PAGE_SIZE]
    total_pages = math.ceil(len(selected) / _PAGE_SIZE)
    label = telegram_suggestion_name(kind)
    lines = [
        f"📜 <b>交易時間線 · {label}</b>",
        f"最近 {len(selected)} 筆 · 第 {start + 1}-{start + len(current)} 筆",
        "",
    ]
    if delay_minutes:
        lines[2:2] = [f"⏱ <b>{label}延遲 {_delay_label(delay_minutes)}</b>", ""]
    lines.extend(_cycle_block(cycle) for cycle in current)
    lines.extend(("", *_summary_lines(selected)))
    if marked_at:
        lines.extend(("", f"<i>持倉估值更新　{_time(marked_at)}</i>"))
    navigation = []
    if page > 0:
        navigation.append({"text": "⬅️ 上一頁", "callback_data": f"timeline:show:{kind}:{count}:{page - 1}"})
    if page + 1 < total_pages:
        navigation.append({"text": "下一頁 ➡️", "callback_data": f"timeline:show:{kind}:{count}:{page + 1}"})
    buttons = [navigation] if navigation else []
    buttons.extend(([{"text": "🔢 變更筆數", "callback_data": f"timeline:choose:{kind}"}], [{"text": "⬅️ 查詢中心", "callback_data": "desk:queries"}], _home_row()))
    return TelegramDeskResponse("\n".join(lines), buttons)


def handle_timeline_action(
    database,
    chat_id: str,
    account: dict[str, Any] | None,
    command: str,
) -> TelegramDeskResponse | None:
    value = str(command or "").strip()
    lower = value.lower()
    if value == "desk:queries":
        return _query_center()
    if value == "desk:pnl" or value.startswith("timeline:pnl:"):
        from notification.telegram_pnl import handle_closed_pnl_action

        return handle_closed_pnl_action(database, chat_id, account, value)
    if value == "desk:timeline" or lower == "/timeline":
        return _timeline_home(account)
    if lower.startswith("/timeline "):
        parts = lower.split()
        if len(parts) != 3 or parts[1] not in {"stock", "option"} or not parts[2].isdigit():
            raise ValueError("格式：/timeline stock 50 或 /timeline option 30")
        if not account:
            return _timeline_home(None)
        return _render_timeline(database, chat_id, account, parts[1], int(parts[2]), 0)
    if value.startswith("timeline:choose:"):
        if not account:
            return _timeline_home(None)
        return _count_picker(account, value.rsplit(":", 1)[1])
    if value.startswith("timeline:custom:"):
        if not account:
            return _timeline_home(None)
        return _custom_prompt(account, value.rsplit(":", 1)[1])
    if value.startswith("timeline:show:"):
        if not account:
            return _timeline_home(None)
        _, _, kind, count, page = value.split(":")
        return _render_timeline(database, chat_id, account, kind, int(count), int(page))
    return None
