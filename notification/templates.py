# -*- coding: utf-8 -*-
"""CicloTrade transactional email and Telegram message templates."""

from __future__ import annotations

from html import escape
from typing import Iterable


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
            "━━━━━━━━━━━━━━",
            f"{symbol} · {str(side).upper()} · {int(quantity):,} 股",
            f"限價　USD {float(price):,.2f}",
            f"狀態　{status}",
            "━━━━━━━━━━━━━━",
            "已送出券商訂單。" if live else "已寫入模擬交易帳本，未動用真實資金。",
            "研究與風控提示，不構成投資建議。",
        )
    )


def telegram_quant_message(event: dict, legs: list[dict], metadata: dict) -> str:
    kind = "正股" if legs[0]["instrument_type"] == "stock" else "期權"
    label = {"signal": "新操作", "correction": "操作更正", "reversal": "撤銷操作"}[event["event_type"]]
    icon = {"signal": "📊", "correction": "⚠️", "reversal": "⛔"}[event["event_type"]]
    lines = [
        f"{icon} CicloTrade · {kind}{label}",
        "━━━━━━━━━━━━━━",
        f"策略　{event['strategy_name']} · {event['strategy_version']}",
        f"時間　{event['occurred_at']}",
    ]
    if event["event_type"] == "reversal":
        lines.append(f"撤銷　事件 #{event['corrects_event_id']}")
    for leg in legs:
        delta = float(leg["quantity_delta"])
        action = "持有" if abs(delta) < 1e-12 else "買入 / 增持" if delta > 0 else "賣出 / 減持"
        price = f" · {leg['currency']} {float(leg['price']):,.2f}" if leg.get("price") is not None else ""
        lines.extend(
            (
                "━━━━━━━━━━━━━━",
                f"{leg['instrument_key']}",
                f"動作　{action}{price}",
                f"變化　{delta:+g}　→　目標持倉 {float(leg['target_quantity']):g}",
            )
        )
    for label_name, key in (("入場", "entry_price"), ("止損", "stop_loss"), ("目標", "target_price")):
        if metadata.get(key) is not None:
            lines.append(f"{label_name}　{metadata[key]}")
    if reason := metadata.get("reason") or metadata.get("rationale"):
        lines.append(f"依據　{str(reason)[:420]}")
    if risk := metadata.get("risk_level"):
        lines.append(f"風險　{risk}")
    lines.extend(("━━━━━━━━━━━━━━", f"事件 #{event['id']} · 連續量化帳本", "研究信號，不代表券商已自動下單；不構成投資建議。"))
    return "\n".join(lines)[:4096]


def telegram_daily_summary(items: list[tuple[dict, dict]], action_count: int) -> str:
    total_pnl = sum(float(snapshot["total_pnl"]) for snapshot, _ in items)
    icon = "🟢" if total_pnl >= 0 else "🔴"
    lines = [
        f"{icon} CicloTrade · 每日量化總結",
        "━━━━━━━━━━━━━━",
        f"今日量化動作　{int(action_count)} 筆",
    ]
    for snapshot, windows in items:
        equity = float(snapshot["total_equity"])
        pnl = float(snapshot["total_pnl"])
        initial = float(snapshot["initial_cash"])
        lines.extend(
            (
                "━━━━━━━━━━━━━━",
                f"{snapshot['currency']} 總資產　{equity:,.2f}",
                f"累計盈虧　{pnl:+,.2f} ({pnl / initial:+.2%})" if initial else f"累計盈虧　{pnl:+,.2f}",
                f"現金 / 持倉　{float(snapshot['cash']):,.2f} / {float(snapshot['market_value']):,.2f}",
            )
        )
        for label in ("1周", "1个月", "3个月", "6个月", "1年"):
            value = windows.get(label, {})
            if value.get("available"):
                lines.append(f"{label.replace('个月', '個月')}　{float(value['pnl']):+,.2f} ({float(value['return']):+.2%})")
    latest_at = max(str(snapshot["captured_at"]) for snapshot, _ in items)
    lines.extend(("━━━━━━━━━━━━━━", f"數據時間　{latest_at}", "已審計連續帳本 · 歷史表現不代表未來收益", "僅供研究參考，不構成投資建議。"))
    return "\n".join(lines)[:4096]


def telegram_price_alert(content: str) -> str:
    return f"⚠️ CicloTrade · 價格預警\n━━━━━━━━━━━━━━\n{content}\n━━━━━━━━━━━━━━\n請先核對即時行情與風險限制。"


def telegram_binding(code: str) -> str:
    return f"🔐 CicloTrade · Telegram 綁定\n━━━━━━━━━━━━━━\n一次性驗證碼　{code}\n15 分鐘內回到網站完成確認。"


def telegram_membership(plan: str, expiry: str, reason: str) -> str:
    return f"💎 CicloTrade · 會員權益已更新\n━━━━━━━━━━━━━━\n方案　{plan}\n有效期至　{expiry}\n說明　{reason}\n━━━━━━━━━━━━━━\n登入網站即可使用已開放功能。"


def telegram_live_service_paused() -> str:
    return (
        "⛔ CicloTrade · 實盤自動交易已暫停\n"
        "━━━━━━━━━━━━━━\n"
        "平台已停止新的實盤自動操作，並關閉你的個人實盤開關。\n"
        "現有券商綁定資料仍會保留，但系統不會代你平倉。\n\n"
        "請立即登入券商帳戶核對持倉、未完成訂單與風險，並按需要自行撤單或平倉。\n"
        "━━━━━━━━━━━━━━\n"
        "恢復服務後不會自動重啟；請等待下一則通知，再回到網站手動開啟。"
    )


def telegram_live_service_resumed() -> str:
    return (
        "✅ CicloTrade · 實盤自動交易可重新申請\n"
        "━━━━━━━━━━━━━━\n"
        "平台服務已恢復，但你的個人實盤開關仍保持關閉。\n"
        "券商綁定資料無需重新填寫；請登入網站核對帳戶與風控後，手動開啟實盤自動交易。\n"
        "━━━━━━━━━━━━━━\n"
        "系統不會在未經你再次確認時自動恢復任何實盤操作。"
    )


def telegram_incident(incident_id: str, incident_type: str) -> str:
    return f"🚨 CicloTrade · 系統異常\n━━━━━━━━━━━━━━\n事件編號　{incident_id}\n類型　{incident_type}\n請前往管理後台檢查日誌。"
