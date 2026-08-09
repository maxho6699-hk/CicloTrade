# -*- coding: utf-8 -*-
"""Private Telegram queries for completed trade P&L."""

from __future__ import annotations

from html import escape
from typing import Any

from core.plans import effective_plan, plan_display_name, telegram_suggestion_name, telegram_timeline_limits
from core.trade_timeline import filter_closed_trade_cycles, summarize_trade_cycles
from notification import telegram_timeline as _timeline
from notification.telegram_models import TelegramDeskResponse
from notification.telegram_security import consume_telegram_timeline_quota
from notification.telegram_timeline import (
    _delay_label,
    _home_row,
    _instrument,
    _money,
    _plan_visible_cycles,
    _time,
)


_PNL_EXECUTIONS_PER_PAGE = 8
_PERIOD_LABELS = {
    "today": "今日（香港）",
    "yesterday": "昨日（香港）",
    "7d": "近 7 日（含今日）",
}


def _period_rows() -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "今日", "callback_data": "timeline:pnl:today:0"},
            {"text": "昨日", "callback_data": "timeline:pnl:yesterday:0"},
        ],
        [{"text": "近 7 日", "callback_data": "timeline:pnl:7d:0"}],
    ]


def _pnl_picker(account: dict[str, Any] | None) -> TelegramDeskResponse:
    if not account:
        return TelegramDeskResponse(
            "🔒 <b>已平倉盈虧需要綁定帳戶</b>\n\n綁定後才可按會員權限查詢。",
            [[{"text": "🔗 綁定帳戶", "callback_data": "desk:account"}], _home_row()],
        )
    plan = effective_plan(account)
    limits = telegram_timeline_limits(plan)
    coverage = "正股與期權" if limits["pnl_option"] else "正股"
    delays = []
    for kind in ("stock", "option"):
        if limits[f"pnl_{kind}"] > 0 and limits[f"pnl_{kind}_delay_minutes"]:
            delays.append(
                f"{telegram_suggestion_name(kind)}延遲 {_delay_label(limits[f'pnl_{kind}_delay_minutes'])}"
            )
    delay_text = " · ".join(delays) if delays else "即時記錄"
    return TelegramDeskResponse(
        "📅 <b>CicloTrade · 已平倉盈虧</b>\n\n"
        f"<blockquote>{escape(plan_display_name(plan))} · {coverage}\n{escape(delay_text)}\n"
        "按香港自然日，以完整平倉時間歸類；盈虧已扣除交易費用。</blockquote>\n"
        "請選擇查詢期間。",
        [
            *_period_rows(),
            [{"text": "📜 完整交易時間線", "callback_data": "desk:timeline"}],
            [{"text": "⬅️ 查詢中心", "callback_data": "desk:queries"}],
            _home_row(),
        ],
    )


def _execution_lines(cycle: dict[str, Any], executions: list[dict[str, Any]], start: int, total: int) -> list[str]:
    unit = "張" if cycle.get("instrument_type") == "option" else "股"
    roles = {
        "open": ("📥", "開倉"),
        "add": ("➕", "補倉"),
        "reduce": ("📉", "減倉"),
        "close": ("📤", "平倉"),
    }
    lines = [f"<b>成交明細 {start + 1}-{start + len(executions)}／{total}</b>"]
    for execution in executions:
        icon, label = roles.get(str(execution.get("role")), ("•", "成交"))
        quantity = abs(float(execution.get("quantity") or 0))
        price = _money(cycle.get("currency"), execution.get("price"))
        lines.append(f"{icon} {label}　{_time(execution.get('occurred_at'))} · {quantity:g} {unit} × {price}")
        commission = float(execution.get("commission") or 0)
        if commission > 0:
            lines.append(f"　交易費用　{_money(cycle.get('currency'), commission)}")
    return lines


def _closed_cycle_block(
    cycle: dict[str, Any],
    executions: list[dict[str, Any]],
    execution_start: int,
    execution_total: int,
) -> str:
    pnl = float(cycle.get("realized_pnl") or 0)
    icon = "🏆" if pnl > 0 else "🔻" if pnl < 0 else "➖"
    asset = "期權" if cycle.get("instrument_type") == "option" else "正股"
    direction = "做多" if cycle.get("direction") == "long" else "做空"
    unit = "張" if cycle.get("instrument_type") == "option" else "股"
    lines = [
        "<blockquote>",
        f"{icon} <b>#{int(cycle['sequence'])} [{asset}] {_instrument(cycle)}</b>",
        f"{direction} · 已平倉",
    ]
    if execution_total:
        lines.extend(_execution_lines(cycle, executions, execution_start, execution_total))
    else:
        lines.extend(
            (
                f"📥 開倉　{_time(cycle.get('opened_at'))}",
                f"📤 平倉　{_time(cycle.get('closed_at'))}",
            )
        )
    lines.extend(
        (
            f"累計開倉　{float(cycle.get('opened_quantity') or 0):g} {unit}",
            f"累計平倉　{float(cycle.get('closed_quantity') or 0):g} {unit}",
            f"開倉均價　{_money(cycle.get('currency'), cycle.get('average_cost'))}",
            f"交易費用　{_money(cycle.get('currency'), cycle.get('commission'))}",
            f"已實現淨盈虧　<b>{_money(cycle.get('currency'), pnl, signed=True)}</b>",
            (
                f"交易回報　<b>{float(cycle['return']):+.2%}</b>"
                if cycle.get("return") is not None
                else "交易回報　--"
            ),
            "</blockquote>",
        )
    )
    return "\n".join(lines)


def _closed_pages(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for cycle_index, cycle in enumerate(cycles):
        raw = cycle.get("executions")
        executions = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        chunks = [
            executions[index : index + _PNL_EXECUTIONS_PER_PAGE]
            for index in range(0, len(executions), _PNL_EXECUTIONS_PER_PAGE)
        ] or [[]]
        for execution_page, chunk in enumerate(chunks):
            pages.append(
                {
                    "cycle": cycle,
                    "cycle_index": cycle_index,
                    "executions": chunk,
                    "execution_start": execution_page * _PNL_EXECUTIONS_PER_PAGE,
                    "execution_total": len(executions),
                }
            )
    return pages


def _closed_query_cycles(database, plan: str, period: str) -> tuple[list[dict[str, Any]], int, list[str]]:
    limits = telegram_timeline_limits(plan)
    selected: list[dict[str, Any]] = []
    total = 0
    delay_notes: list[str] = []
    for kind in ("stock", "option"):
        maximum = limits[f"pnl_{kind}"]
        if maximum <= 0:
            continue
        cycles, _ = _timeline._cycles(database, kind, include_marks=False)
        visible, delay = _plan_visible_cycles(
            cycles,
            plan,
            kind,
            delay_minutes=limits[f"pnl_{kind}_delay_minutes"],
        )
        closed = filter_closed_trade_cycles(visible, period)
        total += len(closed)
        selected.extend(closed[:maximum])
        if delay:
            delay_notes.append(f"{telegram_suggestion_name(kind)}延遲 {_delay_label(delay)}")
    return (
        sorted(selected, key=lambda row: (str(row["closed_at"]), int(row["sequence"])), reverse=True),
        total,
        delay_notes,
    )


def _render_closed_pnl(
    database,
    chat_id: str,
    account: dict[str, Any],
    period: str,
    page: int,
) -> TelegramDeskResponse:
    if period not in _PERIOD_LABELS or page < 0 or page > 999:
        raise ValueError("盈虧查詢條件無效。")
    plan = effective_plan(account)
    limits = telegram_timeline_limits(plan)
    if not consume_telegram_timeline_quota(
        database,
        chat_id,
        per_minute=limits["per_minute"],
        per_day=limits["per_day"],
        count_daily=page == 0,
    ):
        raise PermissionError("查詢過於頻繁或已達今日上限，請稍後再試。")
    if not _timeline._RENDER_SEMAPHORE.acquire(blocking=False):
        raise RuntimeError("查詢服務目前繁忙，請稍後再試。")
    try:
        cycles, total, delay_notes = _closed_query_cycles(database, plan, period)
    finally:
        _timeline._RENDER_SEMAPHORE.release()
    label = _PERIOD_LABELS[period]
    if not cycles:
        delay = f"\n⏱ {escape(' · '.join(delay_notes))}" if delay_notes else ""
        return TelegramDeskResponse(
            f"📅 <b>已平倉盈虧 · {label}</b>{delay}\n\n"
            "所選香港日期範圍內，暫無可見的已平倉建議記錄。",
            [*_period_rows(), [{"text": "⬅️ 查詢中心", "callback_data": "desk:queries"}], _home_row()],
        )
    pages = _closed_pages(cycles)
    if page >= len(pages):
        raise ValueError("查詢頁碼已超出結果範圍。")
    current = pages[page]
    summary = summarize_trade_cycles(cycles)
    lines = [
        f"📅 <b>已平倉盈虧 · {label}</b>",
        f"{escape(plan_display_name(plan))} · 第 {page + 1}/{len(pages)} 頁",
    ]
    if delay_notes:
        lines.append(f"⏱ <b>{escape(' · '.join(delay_notes))}</b>")
    lines.extend(
        (
            f"已平倉 {len(cycles)} 筆 · 獲利 {summary['profitable']} · 虧損 {summary['losing']} · 平盤 {summary['breakeven']}",
            (
                f"範圍內共 {total} 筆；依 {escape(plan_display_name(plan))} 上限顯示最近 {len(cycles)} 筆。"
                if total > len(cycles)
                else ""
            ),
        )
    )
    for currency, values in summary["currencies"].items():
        ratio = values.get("return")
        lines.append(
            f"{escape(currency)} 已實現　{_money(currency, values['realized_pnl'], signed=True)}"
            f"{f' ({float(ratio):+.2%})' if ratio is not None else ''}"
        )
    lines.extend(
        (
            "",
            f"第 {current['cycle_index'] + 1}/{len(cycles)} 筆交易",
            _closed_cycle_block(
                current["cycle"],
                current["executions"],
                current["execution_start"],
                current["execution_total"],
            ),
            "<i>量化研究記錄，不構成個別投資建議。</i>",
        )
    )
    lines = [line for line in lines if line]
    navigation = []
    if page > 0:
        navigation.append({"text": "⬅️ 上一頁", "callback_data": f"timeline:pnl:{period}:{page - 1}"})
    if page + 1 < len(pages):
        navigation.append({"text": "下一頁 ➡️", "callback_data": f"timeline:pnl:{period}:{page + 1}"})
    buttons = [navigation] if navigation else []
    buttons.extend((*_period_rows(), [{"text": "⬅️ 查詢中心", "callback_data": "desk:queries"}], _home_row()))
    return TelegramDeskResponse("\n".join(lines), buttons)


def handle_closed_pnl_action(
    database,
    chat_id: str,
    account: dict[str, Any] | None,
    command: str,
) -> TelegramDeskResponse:
    value = str(command or "").strip()
    if value == "desk:pnl":
        return _pnl_picker(account)
    if not value.startswith("timeline:pnl:"):
        raise ValueError("盈虧查詢指令無效。")
    if not account:
        return _pnl_picker(None)
    parts = value.split(":")
    if len(parts) != 4:
        raise ValueError("盈虧查詢指令無效。")
    _, _, period, page = parts
    if not page.isdigit():
        raise ValueError("盈虧查詢頁碼無效。")
    return _render_closed_pnl(database, chat_id, account, period, int(page))
