# -*- coding: utf-8 -*-
"""CicloTrade transactional email and Telegram message templates."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from core.plans import plan_display_name
from notification.channel_content import (
    ChannelRenderPolicy,
    MISSING_FIELD,
    RecommendationChange,
    RecommendationContent,
    recommendation_from_event,
    recommendation_render_payload,
)


def email_message(
    subject: str,
    title: str,
    intro: str,
    lines: Iterable[str] = (),
    *,
    code: str | None = None,
    action_url: str | None = None,
    action_label: str = "開啟 CicloTrade",
) -> tuple[str, str, str]:
    details = [str(line).strip() for line in lines if str(line).strip()]
    text_parts = [title, "", intro, *details]
    if code:
        text_parts.extend(("", f"驗證碼：{code}"))
    if action_url:
        text_parts.extend(("", action_url))
    text_parts.extend(("", "CicloTrade 安全與服務團隊", "此郵件由系統自動發送，請勿回覆。"))
    detail_html = "".join(
        f'<tr><td style="padding:7px 0;color:#a6abb4;font-size:14px;line-height:1.6">{escape(line)}</td></tr>'
        for line in details
    )
    code_html = (
        '<div style="margin:24px 0;padding:18px 20px;background:#15171b;border:1px solid #31353d;'
        'border-radius:10px;color:#f3f5f7;font:700 28px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;'
        f'letter-spacing:4px;text-align:center">{escape(code)}</div>'
        if code else ""
    )
    action_html = (
        f'<a href="{escape(action_url, quote=True)}" style="display:inline-block;margin-top:22px;padding:12px 18px;'
        'background:#d8ff3e;color:#0a0b0d;text-decoration:none;border-radius:8px;font:700 14px/1.2 Arial,sans-serif">'
        f'{escape(action_label)}</a>'
        if action_url else ""
    )
    html = f"""<!doctype html>
<html lang="zh-Hant"><body style="margin:0;background:#0b0c0f;color:#f3f5f7;font-family:Arial,'Noto Sans TC',sans-serif">
<div style="display:none;max-height:0;overflow:hidden">{escape(intro)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0b0c0f;padding:32px 14px">
<tr><td align="center"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:620px;background:#111318;border:1px solid #292d35;border-radius:12px;overflow:hidden">
<tr><td style="padding:22px 28px;border-bottom:1px solid #292d35"><strong style="font-size:18px;letter-spacing:0;color:#f3f5f7">CicloTrade</strong><span style="float:right;color:#d8ff3e;font-size:12px;font-weight:700">QUANT SYSTEM</span></td></tr>
<tr><td style="padding:34px 28px"><p style="margin:0 0 9px;color:#d8ff3e;font-size:12px;font-weight:700">SECURE NOTICE</p><h1 style="margin:0 0 18px;font-size:26px;line-height:1.25;letter-spacing:0">{escape(title)}</h1><p style="margin:0 0 14px;color:#c7cbd2;font-size:15px;line-height:1.7">{escape(intro)}</p><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{detail_html}</table>{code_html}{action_html}</td></tr>
<tr><td style="padding:18px 28px;background:#0d0f12;color:#777e89;font-size:12px;line-height:1.6">CicloTrade 安全與服務團隊<br>此郵件由系統自動發送，請勿回覆。投資研究資料不構成投資建議或收益承諾。</td></tr>
</table></td></tr></table></body></html>"""
    return subject, "\n".join(text_parts), html


def auth_email(kind: str, code: str, base_url: str) -> tuple[str, str, str]:
    if kind == "verify":
        return email_message(
            "CicloTrade 註冊電郵驗證",
            "完成電郵驗證",
            "輸入以下一次性驗證碼以啟用帳戶；驗證碼將在 30 分鐘後失效。",
            ("如非本人操作，請忽略此郵件。",),
            code=code,
            action_url=base_url,
        )
    if kind == "reset":
        return email_message(
            "CicloTrade 密碼重設",
            "重設你的登入密碼",
            "輸入以下 8 位英數驗證碼以設定新密碼；完成後所有舊工作階段會立即失效。",
            ("驗證碼將在 30 分鐘後失效。", "請勿向任何人透露驗證碼；如非本人操作，請立即檢查帳戶安全。"),
            code=code,
            action_url=base_url,
        )
    raise ValueError("unknown authentication email kind")


def telegram_order_message(mode: str, side: str, quantity: int, symbol: str, price: float, status: str) -> str:
    live = str(mode).lower() == "live"
    icon = "🟢" if str(side).upper() == "BUY" else "🔴"
    return "\n".join(
        (
            f"{icon} CicloTrade · {'實盤' if live else '模擬盤'}訂單",
            f"{symbol} · {str(side).upper()} · {int(quantity):,} 股",
            f"限價　USD {float(price):,.2f}",
            f"狀態　{status}",
            "已送出券商訂單。" if live else "已寫入模擬交易帳本，未動用真實資金。",
            "研究與風控提示，不構成投資建議。",
        )
    )


def _telegram_contract(row: dict) -> str:
    symbol = escape(str(row.get("symbol") or "--"))
    market = "大A" if str(row.get("market") or "").upper() == "CN" or symbol.isdigit() else "美股"
    if row.get("instrument_type") != "option":
        return f"{market} {symbol}"
    right = str(row.get("option_right") or row.get("right") or "").upper()
    icon = "🟢" if right == "CALL" else "🔴"
    label = "Call" if right == "CALL" else "Put"
    expiry = escape(str(row.get("option_expiry") or row.get("expiry") or "--"))
    strike = float(row.get("option_strike") or row.get("strike") or 0)
    return f"{market} {icon} {symbol} {expiry} {strike:g} {label}"


def _telegram_contract_lines(row: dict) -> list[str]:
    """Keep each instrument readable without forcing a long single line."""
    symbol = escape(str(row.get("symbol") or "--"))
    market = "大A" if str(row.get("market") or "").upper() == "CN" or symbol.isdigit() else "美股"
    if row.get("instrument_type") != "option":
        return [f"{market} {symbol}"]
    right = str(row.get("option_right") or row.get("right") or "").upper()
    icon = "🟢" if right == "CALL" else "🔴"
    label = "Call" if right == "CALL" else "Put"
    expiry = escape(str(row.get("option_expiry") or row.get("expiry") or "--"))
    strike = float(row.get("option_strike") or row.get("strike") or 0)
    return [f"{market} {icon} {symbol} {label}", f"到期 {expiry} · 行權 {strike:g}"]


def _telegram_unit(row: dict) -> str:
    return "張" if row.get("instrument_type") == "option" else "股"


def _telegram_quantity(row: dict, value: object) -> str:
    return f"{float(value or 0):g} {_telegram_unit(row)}"


def _telegram_trade_action(row: dict, delta: float) -> tuple[str, str]:
    target = float(row.get("target_quantity") or 0)
    previous = target - delta
    if delta > 0:
        return ("補倉", "➕") if previous > 0 else ("開倉", "📥")
    return ("平倉", "📤") if target == 0 else ("減倉", "📉")


def _telegram_asset_block(
    row: dict,
    *,
    action: str | None = None,
    action_icon: str | None = None,
    quantity: object | None = None,
    price: object | None = None,
    current_quantity: object | None = None,
    occurred_at: object | None = None,
    risk_levels: dict | None = None,
    metadata: dict | None = None,
) -> list[str]:
    """Render one instrument as a separated, narrow Telegram block."""
    risk_levels = risk_levels if isinstance(risk_levels, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    title = _telegram_contract_lines(row)
    heading = f"{action_icon} <b>{escape(action)}</b> " if action and action_icon else ""
    lines = ["<blockquote>", f"{heading}{title[0]}"]
    lines.extend(f"　{line}" for line in title[1:])
    if occurred_at is not None:
        lines.append(f"🕒 {_telegram_time(occurred_at)}")
    if quantity is not None:
        lines.append(f"📦 數量　{_telegram_quantity(row, quantity)}")
    if price is not None:
        lines.append(f"💵 成交　{_money(row.get('currency'), price)}")
    if current_quantity is not None:
        lines.append(f"💼 現持　{_telegram_quantity(row, current_quantity)}")
    if float(current_quantity or row.get("target_quantity") or 0) != 0:
        if risk_line := _risk_line(row, risk_levels, metadata):
            lines.append(risk_line)
    lines.append("</blockquote>")
    return lines


def _telegram_time(value: object) -> str:
    if value is None or not str(value).strip():
        return MISSING_FIELD
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.strftime("%m-%d %H:%M")
        return f"香港 {parsed.astimezone(ZoneInfo('Asia/Hong_Kong')):%m-%d %H:%M}"
    except (TypeError, ValueError):
        return escape(str(value))


def _money(currency: object, value: object) -> str:
    symbol = "¥" if str(currency).upper() == "CNY" else "$"
    return f"{symbol}{float(value):,.2f}"


def _risk_line(row: dict, risk_levels: dict, metadata: dict) -> str | None:
    risk = risk_levels.get(row.get("instrument_key"), {}) if isinstance(risk_levels, dict) else {}
    if not isinstance(risk, dict):
        risk = {}
    stop = risk.get("stop_loss", row.get("stop_loss", metadata.get("stop_loss")))
    target = risk.get("target_price", row.get("target_price", metadata.get("target_price")))
    parts = []
    if stop is not None:
        parts.append(f"🛡️ 止損 {_money(row.get('currency'), stop)}")
    if target is not None:
        parts.append(f"🏆 目標 {_money(row.get('currency'), target)}")
    return " · ".join(parts) or None


def _telegram_text(lines: list[str]) -> str:
    selected: list[str] = []
    for line in lines:
        candidate = "\n".join((*selected, line))
        if len(candidate) > 4040:
            selected.append("其餘內容請到網站查看。")
            break
        selected.append(line)
    return "\n".join(selected)


def _recorded_text(value: object) -> str:
    cleaned = str(value or "").strip()
    return escape(cleaned) if cleaned else MISSING_FIELD


def _recorded_money(currency: object, value: object) -> str:
    return _money(currency, value) if value is not None else MISSING_FIELD


def _recommendation_instrument(content: RecommendationContent) -> str:
    market = "大A" if content.market == "CN" else "美股" if content.market == "US" else MISSING_FIELD
    symbol = _recorded_text(content.symbol)
    if content.instrument_type != "option":
        return f"{market} {symbol} · 正股"
    right = {"CALL": "Call", "PUT": "Put"}.get(str(content.option_right or "").upper(), MISSING_FIELD)
    expiry = _recorded_text(content.option_expiry)
    strike = f"{content.option_strike:g}" if content.option_strike is not None else MISSING_FIELD
    return f"{market} {symbol} · 期權 {right} · 到期 {expiry} · 行權 {strike}"


def _telegram_recommendation_block(
    content: RecommendationContent,
    change: RecommendationChange | str | None,
    *,
    render_policy: ChannelRenderPolicy,
) -> list[str]:
    unit = "張" if content.instrument_type == "option" else "股"
    quantity = f"{content.quantity:g} {unit}" if content.quantity is not None else MISSING_FIELD
    fallback = (
        f"是 · 來自 {_recorded_text(content.quote.fallback_from)}"
        if content.quote.fallback_from
        else "否"
        if content.quote.fallback_recorded
        else MISSING_FIELD
    )
    realtime = "是" if content.quote.is_realtime is True else "否" if content.quote.is_realtime is False else MISSING_FIELD
    actionable = (
        "是"
        if content.quote.actionable_quote is True
        else "否"
        if content.quote.actionable_quote is False
        else MISSING_FIELD
    )
    render_payload = recommendation_render_payload(content, change, render_policy)
    safety_text = str(render_payload["quote"]["safety_text"])
    lines = [
        "<blockquote>",
        f"🔔 <b>{escape(str(render_payload['change_label']))}</b>",
        f"<b>{escape(content.state_label)} · {escape(content.direction_label)}</b>",
        _recommendation_instrument(content),
        f"事件時間　{_telegram_time(content.event_time)}",
        f"報價時間　{_telegram_time(content.quote.quote_at)}",
        f"數據來源　{_recorded_text(content.quote.source)}",
        f"新鮮度　{_recorded_text(content.quote.freshness)}",
        f"即時報價　{realtime} · 可行動報價　{actionable}",
        f"是否回退　{fallback}",
        f"報價安全　<b>{escape(safety_text)}</b>",
        f"入場　{_recorded_money(content.currency, content.entry_price)}",
        f"數量　{quantity}",
        f"止損　{_recorded_money(content.currency, content.stop_price)}",
        f"目標　{_recorded_money(content.currency, content.target_price)}",
        f"最大風險　{_recorded_money(content.currency, content.max_risk)}",
        f"失效條件　{_recorded_text(content.invalidation_condition)}",
    ]
    if content.rationale:
        lines.append(f"理由　{_recorded_text(content.rationale)}")
    lines.append("</blockquote>")
    return lines


def telegram_quant_message(
    event: dict,
    legs: list[dict],
    metadata: dict,
    *,
    positions: list[dict] = (),
    delay_note: str | None = None,
    source_label: str = "CicloTrade 官方模擬帳戶",
    change_kinds: dict[str, RecommendationChange | str] | None = None,
    contents: dict[str, RecommendationContent] | None = None,
    rendered_at: object = None,
    delivery_delay_minutes: int = 0,
    immediate_action_allowed: bool = False,
) -> str:
    label = {"signal": "正式事件", "correction": "更正事件", "reversal": "失效事件"}[event["event_type"]]
    lines = [f"📊 <b>CicloTrade · 官方模擬帳戶{label}</b>"]
    if delay_note:
        lines.append(f"⏱ <b>{escape(delay_note)}</b>")
    lines.extend((f"🕒 事件時間 {_telegram_time(event.get('occurred_at'))}", "<b>本次正式結論</b>"))
    risk_levels = metadata.get("risk_levels") if isinstance(metadata.get("risk_levels"), dict) else {}
    effective_delay = int(delivery_delay_minutes)
    if delay_note and effective_delay <= 0:
        effective_delay = 1
    render_policy = ChannelRenderPolicy(
        reference_time=str(rendered_at or datetime.now(timezone.utc).isoformat(timespec="seconds")),
        immediate_action_allowed=immediate_action_allowed,
        delivery_delay_minutes=effective_delay,
    )
    for leg in legs:
        lines.append("")
        instrument_key = str(leg.get("instrument_key") or "")
        content = (contents or {}).get(instrument_key) or recommendation_from_event(event, leg, metadata)
        change = (change_kinds or {}).get(
            instrument_key, RecommendationChange.NEW_OPPORTUNITY
        )
        lines.extend(
            _telegram_recommendation_block(
                content,
                change,
                render_policy=render_policy,
            )
        )
    lines.append("<b>最新官方模擬持倉</b>")
    if positions:
        for position in positions[:8]:
            lines.append("")
            lines.extend(
                _telegram_asset_block(
                    position,
                    quantity=position.get("quantity") or 0,
                    price=position.get("average_cost") or 0,
                    current_quantity=position.get("quantity") or 0,
                    risk_levels=risk_levels,
                    metadata=metadata,
                )
            )
    else:
        lines.append("• 暫無持倉")
    lines.extend(
        (
            "⚡ 正式方向與數量只讀取不可變量化事件，不會被回退行情改寫。",
            "⚠️ 僅在 is_realtime=true 且 actionable_quote=true 的已驗證報價下，才可核對即時行動描述。",
            f"<i>{escape(source_label)} · 事件 #{int(event['id'])}</i>",
        )
    )
    if delay_note:
        lines.append("💎 升級會員可取得即時建議資料。")
    return _telegram_text(lines)


def telegram_daily_summary(
    items: list[tuple[dict, dict]],
    action_count: int,
    *,
    trades: list[dict] = (),
    positions: list[dict] = (),
    risk_levels: dict | None = None,
    audience: str = "免費頻道",
    delay_note: str | None = None,
) -> str:
    total_pnl = sum(float(snapshot["total_pnl"]) for snapshot, _ in items)
    icon = "🟢" if total_pnl >= 0 else "🔴"
    lines = [f"{icon} <b>CicloTrade · 每日建議總結</b>"]
    if delay_note:
        lines.append(f"⏱ <b>{escape(delay_note)}</b>")
    risks = risk_levels if isinstance(risk_levels, dict) else {}
    lines.append(f"{escape(audience)} · 今日建議成交 {int(action_count)} 筆")
    if trades:
        lines.append("<b>今日建議記錄</b>")
        for trade in trades[:8]:
            delta = float(trade["quantity_delta"])
            action, action_icon = _telegram_trade_action(trade, delta)
            lines.append("")
            lines.extend(
                _telegram_asset_block(
                    trade,
                    action=action,
                    action_icon=action_icon,
                    quantity=abs(delta),
                    price=trade.get("price"),
                    current_quantity=trade.get("target_quantity"),
                    occurred_at=trade.get("time"),
                    risk_levels=risks,
                    metadata={},
                )
            )
    lines.append("<b>收盤持倉</b>")
    if positions:
        for position in positions[:8]:
            lines.append("")
            lines.extend(
                _telegram_asset_block(
                    position,
                    quantity=position.get("quantity") or 0,
                    price=position.get("average_cost") or 0,
                    current_quantity=position.get("quantity") or 0,
                    risk_levels=risks,
                    metadata={},
                )
            )
    else:
        lines.append("• 暫無持倉")
    for snapshot, windows in items:
        equity = float(snapshot["total_equity"])
        pnl = float(snapshot["total_pnl"])
        initial = float(snapshot["initial_cash"])
        lines.extend(
            (
                f"<b>{'美元' if snapshot['currency'] == 'USD' else '人民幣'}資產 {_money(snapshot['currency'], equity)}</b>",
                f"盈虧 {_money(snapshot['currency'], pnl)} ({pnl / initial:+.2%})" if initial else f"盈虧 {_money(snapshot['currency'], pnl)}",
                f"現金 {_money(snapshot['currency'], snapshot['cash'])} · 持倉 {_money(snapshot['currency'], snapshot['market_value'])}",
            )
        )
        for label in ("1周", "1个月", "3个月", "6个月", "1年"):
            value = windows.get(label, {})
            if value.get("available"):
                lines.append(
                    f"{label.replace('个月', '個月')} {_money(snapshot['currency'], value['pnl'])} "
                    f"({float(value['return']):+.2%})"
                )
    latest_at = max(str(snapshot["captured_at"]) for snapshot, _ in items)
    lines.extend(
        (
            f"🕒 {_telegram_time(latest_at)}",
            "⚡ 經量化系統數據分析建議",
            "⚠️ 可能提前止盈或止損，請留意最新建議推送。",
            "🔎 盈利／虧損明細，請私聊機器人查詢。",
        )
    )
    if delay_note:
        lines.append("💎 升級會員可取得即時建議資料。")
    return _telegram_text(lines)


def telegram_price_alert(content: str) -> str:
    return (
        f"⚠️ <b>CicloTrade · 價格預警</b>\n"
        f"<blockquote>{escape(str(content))}</blockquote>\n"
        "只提醒，不會自動買賣。請先核對即時行情與風險限制。"
    )


def telegram_binding(code: str) -> str:
    return f"🔐 CicloTrade · Telegram 綁定\n一次性驗證碼　{code}\n15 分鐘內回到網站完成確認。"


def telegram_membership(plan: str, expiry: str, reason: str) -> str:
    return f"💎 CicloTrade · 會員權益已更新\n會員　{plan_display_name(plan)}\n有效期至　{expiry}\n說明　{reason}\n登入網站即可使用已開放功能。"


def telegram_live_service_paused() -> str:
    return (
        "⛔ CicloTrade · 實盤自動交易已暫停\n"
        "平台已停止新的實盤自動操作，並關閉你的個人實盤開關。\n"
        "現有券商綁定資料仍會保留，但系統不會代你平倉。\n\n"
        "請立即登入券商帳戶核對持倉、未完成訂單與風險，並按需要自行撤單或平倉。\n"
        "恢復服務後不會自動重啟；請等待下一則通知，再回到網站手動開啟。"
    )


def telegram_live_service_resumed() -> str:
    return (
        "✅ CicloTrade · 實盤自動交易可重新申請\n"
        "平台服務已恢復，但你的個人實盤開關仍保持關閉。\n"
        "券商綁定資料無需重新填寫；請登入網站核對帳戶與風控後，手動開啟實盤自動交易。\n"
        "系統不會在未經你再次確認時自動恢復任何實盤操作。"
    )


def telegram_incident(incident_id: str, incident_type: str) -> str:
    return f"🚨 CicloTrade · 系統異常\n事件編號　{incident_id}\n類型　{incident_type}\n請前往管理後台檢查日誌。"
