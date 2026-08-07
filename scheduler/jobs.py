# -*- coding: utf-8 -*-
"""订阅降级与流失提醒任务。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os

import pandas as pd

from core.alerts import AlertService
from core.database import get_database
from core.plans import can, effective_plan
from core.quant_journal import QuantJournal
from core.user_settings import load_user_settings
from core.strategy_evaluation import score_daily_catalog, update_saved_strategy_performance
from core.user_profiles import UserProfileService
from data.datasource import get_data_source
from notification.email_sender import send_email, smtp_configured
from notification.telegram_bot import (
    TelegramDeliveryUncertain,
    entitled_user_target,
    send_telegram,
    telegram_configured,
    verified_user_target,
)


def _settings_json(value) -> dict:
    try:
        result = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _watches(settings: dict, symbol: str, market: str) -> bool:
    watchlists = settings.get("watchlists")
    if not isinstance(watchlists, dict):
        return False
    values = watchlists.get("a_share" if market == "CN" else "us")
    return isinstance(values, list) and symbol in {str(value).strip().upper() for value in values}


def _signal_capability(instrument_type: str) -> tuple[str, str]:
    return (
        ("stock_signal_telegram", "stock_signal")
        if instrument_type == "stock"
        else ("option_signal_telegram", "option_signal")
    )


def _newer_than_eligibility(recorded_at: str, user: dict) -> bool:
    try:
        event_time = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        for name in ("created_at", "settings_updated_at", "paid_at", "reward_at", "admin_plan_at"):
            if not user.get(name):
                continue
            cutoff = datetime.fromisoformat(str(user[name]).replace("Z", "+00:00"))
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=UTC)
            if event_time <= cutoff.astimezone(UTC):
                return False
        return True
    except (TypeError, ValueError):
        return False


def enqueue_quant_signal_deliveries(database=None) -> int:
    """Create deduplicated Telegram outbox rows from immutable model events."""
    db = database or get_database()
    ledger_key = os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
    event_targets = db.fetch_all(
        """SELECT DISTINCT e.id event_id,e.event_type,e.recorded_at,l.instrument_type,l.symbol,l.market
           FROM quant_events e JOIN quant_event_legs l ON l.event_id=e.id
           WHERE e.ledger_key=?
             AND NOT EXISTS (SELECT 1 FROM quant_events newer WHERE newer.corrects_event_id=e.id)
           UNION
           SELECT DISTINCT e.id event_id,e.event_type,e.recorded_at,l.instrument_type,l.symbol,l.market
           FROM quant_events e
           JOIN quant_event_legs l ON l.event_id=e.corrects_event_id
           WHERE e.ledger_key=? AND e.event_type IN ('correction','reversal')
             AND NOT EXISTS (SELECT 1 FROM quant_events newer WHERE newer.corrects_event_id=e.id)
           ORDER BY event_id""",
        (ledger_key, ledger_key),
    )
    if not event_targets:
        return 0
    users = db.fetch_all(
        """SELECT u.id,u.plan_type,u.subscription_expire,u.created_at,s.settings_json,
                  s.updated_at settings_updated_at,
                  (SELECT MAX(o.paid_at) FROM subscription_orders o
                   WHERE o.user_id=u.id AND o.status='paid') paid_at,
                  (SELECT MAX(r.created_at) FROM rewards r WHERE r.user_id=u.id) reward_at,
                  (SELECT MAX(l.created_at) FROM user_action_logs l
                   WHERE l.action_type='ADMIN_SUBSCRIPTION_ADJUST'
                     AND json_extract(CASE WHEN json_valid(l.details) THEN l.details ELSE '{}' END,
                                      '$.user_id')=u.id) admin_plan_at
           FROM users u LEFT JOIN user_settings s ON s.user_id=u.id WHERE u.is_active=1"""
    )
    now = datetime.now(UTC).isoformat(timespec="seconds")
    queued = 0
    with db.transaction() as conn:
        for target in event_targets:
            capability, event_name = _signal_capability(target["instrument_type"])
            for user in users:
                settings = _settings_json(user.get("settings_json"))
                if (
                    not can(effective_plan(user), capability)
                    or not _watches(settings, target["symbol"], target["market"])
                    or not entitled_user_target(user, settings, event_name)
                    or not _newer_than_eligibility(target["recorded_at"], user)
                ):
                    continue
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO quant_event_deliveries
                       (event_id,user_id,channel,instrument_type,symbol,status,attempts,
                        next_attempt_at,last_error,created_at,updated_at,sent_at)
                       VALUES (?,?,'telegram',?,?, 'pending',0,?,NULL,?,?,NULL)""",
                    (
                        target["event_id"], user["id"], target["instrument_type"], target["symbol"],
                        now, now, now,
                    ),
                )
                queued += cursor.rowcount
    return queued


def _quant_message(database, delivery: dict) -> str:
    event = database.fetch_one(
        """SELECT id,event_type,strategy_name,strategy_version,corrects_event_id,
                  occurred_at,metadata_json FROM quant_events WHERE id=?""",
        (delivery["event_id"],),
    )
    if not event:
        raise RuntimeError("量化事件不存在")
    legs = [
        leg for leg in QuantJournal(database).execution_legs(event["id"])
        if leg["instrument_type"] == delivery["instrument_type"] and leg["symbol"] == delivery["symbol"]
    ]
    if not legs:
        raise RuntimeError("量化事件没有匹配的交易腿")
    kind = "正股" if delivery["instrument_type"] == "stock" else "期权"
    event_label = {"signal": "操作", "correction": "更正", "reversal": "撤销"}[event["event_type"]]
    lines = [
        f"CicloTrade 量化{kind}{event_label} #{event['id']}",
        f"策略：{event['strategy_name']} · {event['strategy_version']}",
        f"时间：{event['occurred_at']}",
    ]
    if event["event_type"] == "reversal":
        lines.append(f"前序事件 #{event['corrects_event_id']} 已撤销，请以网页连续账本为准。")
    for leg in legs:
        delta = float(leg["quantity_delta"])
        if abs(delta) < 1e-12:
            lines.append(f"仓位不变 {leg['instrument_key']}；目标仓位 {float(leg['target_quantity']):g}")
            continue
        action = "买入 / 增持" if delta > 0 else "卖出 / 减持"
        price = (
            f" @ {leg['currency']} {float(leg['price']):,.2f}"
            if event["event_type"] == "signal" and leg.get("price") is not None
            else ""
        )
        lines.append(
            f"{action} {leg['instrument_key']} {delta:+g}{price}；目标仓位 {float(leg['target_quantity']):g}"
        )
    metadata = _settings_json(event["metadata_json"])
    if reason := metadata.get("reason") or metadata.get("rationale"):
        lines.append(f"原因：{str(reason)[:500]}")
    lines.append("研究信号通知，不代表券商已自动下单；不构成投资建议或收益承诺。")
    return "\n".join(lines)


def dispatch_quant_signal_deliveries(database=None, limit: int = 100) -> int:
    """Claim, re-authorize, send, and persist retry state for signal notifications."""
    db = database or get_database()
    now = datetime.now(UTC)
    due = now.isoformat(timespec="seconds")
    deliveries = db.fetch_all(
        """SELECT * FROM quant_event_deliveries
           WHERE status IN ('pending','failed','sending') AND next_attempt_at<=?
           ORDER BY id LIMIT ?""",
        (due, max(1, min(int(limit), 500))),
    )
    sent = 0
    for delivery in deliveries:
        lease_until = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
        claimed = db.execute(
            """UPDATE quant_event_deliveries
               SET status='sending',attempts=attempts+1,next_attempt_at=?,updated_at=?
               WHERE id=? AND status IN ('pending','failed','sending') AND next_attempt_at<=?""",
            (lease_until, due, delivery["id"], due),
        )
        if not claimed:
            continue
        user = db.fetch_one(
            "SELECT id,is_active,plan_type,subscription_expire FROM users WHERE id=?", (delivery["user_id"],)
        )
        settings = load_user_settings(delivery["user_id"], db) if user else {}
        capability, event_name = _signal_capability(delivery["instrument_type"])
        target = entitled_user_target(user, settings, event_name)
        market = "CN" if str(delivery["symbol"]).isdigit() else "US"
        if (
            not user
            or not user["is_active"]
            or not can(effective_plan(user), capability)
            or not target
            or not _watches(settings, delivery["symbol"], market)
        ):
            db.execute(
                """UPDATE quant_event_deliveries SET status='skipped',last_error='entitlement_or_consent',
                   updated_at=? WHERE id=? AND status='sending'""",
                (due, delivery["id"]),
            )
            continue
        try:
            if not telegram_configured(target):
                raise RuntimeError("Telegram Bot 尚未配置")
            send_telegram(_quant_message(db, delivery), chat_id=target)
        except TelegramDeliveryUncertain as exc:
            db.execute(
                """UPDATE quant_event_deliveries SET status='skipped',last_error=?,updated_at=?
                   WHERE id=? AND status='sending'""",
                (f"delivery_uncertain_manual_retry: {exc}"[:300], due, delivery["id"]),
            )
            continue
        except RuntimeError as exc:
            attempts = int(delivery["attempts"]) + 1
            retry_at = (now + timedelta(minutes=min(2 ** min(attempts, 6), 60))).isoformat(timespec="seconds")
            token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            error = str(exc).replace(token, "[redacted]") if token else str(exc)
            db.execute(
                """UPDATE quant_event_deliveries SET status='failed',next_attempt_at=?,last_error=?,
                   updated_at=? WHERE id=? AND status='sending'""",
                (retry_at, error[:300], due, delivery["id"]),
            )
            continue
        db.execute(
            """UPDATE quant_event_deliveries SET status='sent',sent_at=?,updated_at=?,last_error=NULL
               WHERE id=? AND status='sending'""",
            (due, due, delivery["id"]),
        )
        sent += 1
    return sent


def process_quant_signal_notifications(database=None) -> dict[str, int]:
    db = database or get_database()
    return {
        "queued": enqueue_quant_signal_deliveries(db),
        "sent": dispatch_quant_signal_deliveries(db),
    }


def dispatch_price_alert_deliveries(database=None, limit: int = 100) -> int:
    """Retry definite Telegram failures while preserving one-time alert history."""
    db = database or get_database()
    now = datetime.now(UTC)
    due = now.isoformat(timespec="seconds")
    rows = db.fetch_all(
        """SELECT d.*,n.content FROM price_alert_deliveries d
           JOIN notifications n ON n.id=d.notification_id
           WHERE d.status IN ('pending','failed','sending') AND d.next_attempt_at<=?
           ORDER BY d.id LIMIT ?""",
        (due, max(1, min(int(limit), 500))),
    )
    sent = 0
    for delivery in rows:
        lease_until = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
        claimed = db.execute(
            """UPDATE price_alert_deliveries
               SET status='sending',attempts=attempts+1,next_attempt_at=?,updated_at=?
               WHERE id=? AND status IN ('pending','failed','sending') AND next_attempt_at<=?""",
            (lease_until, due, delivery["id"], due),
        )
        if not claimed:
            continue
        user = db.fetch_one(
            "SELECT id,plan_type,subscription_expire,is_active FROM users WHERE id=?",
            (delivery["user_id"],),
        )
        settings = load_user_settings(delivery["user_id"], db) if user else {}
        target = entitled_user_target(user or {}, settings, "price_alert")
        if not user or not user["is_active"] or not target:
            db.execute(
                "UPDATE price_alert_deliveries SET status='skipped',last_error='entitlement_or_consent',updated_at=? WHERE id=? AND status='sending'",
                (due, delivery["id"]),
            )
            db.execute("UPDATE notifications SET push_status='skipped' WHERE id=?", (delivery["notification_id"],))
            continue
        try:
            if not telegram_configured(target):
                raise RuntimeError("Telegram Bot 尚未配置")
            send_telegram(f"CicloTrade 价格预警\n{delivery['content']}", chat_id=target)
        except TelegramDeliveryUncertain as exc:
            db.execute(
                "UPDATE price_alert_deliveries SET status='skipped',last_error=?,updated_at=? WHERE id=? AND status='sending'",
                (f"delivery_uncertain_manual_retry: {exc}"[:300], due, delivery["id"]),
            )
            db.execute("UPDATE notifications SET push_status='failed' WHERE id=?", (delivery["notification_id"],))
            continue
        except RuntimeError as exc:
            attempts = int(delivery["attempts"]) + 1
            retry_at = (now + timedelta(minutes=min(2 ** min(attempts, 6), 60))).isoformat(timespec="seconds")
            token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            error = str(exc).replace(token, "[redacted]") if token else str(exc)
            db.execute(
                """UPDATE price_alert_deliveries SET status='failed',next_attempt_at=?,last_error=?,updated_at=?
                   WHERE id=? AND status='sending'""",
                (retry_at, error[:300], due, delivery["id"]),
            )
            db.execute("UPDATE notifications SET push_status='failed' WHERE id=?", (delivery["notification_id"],))
            continue
        db.execute(
            """UPDATE price_alert_deliveries SET status='sent',sent_at=?,updated_at=?,last_error=NULL
               WHERE id=? AND status='sending'""",
            (due, due, delivery["id"]),
        )
        db.execute("UPDATE notifications SET push_status='sent' WHERE id=?", (delivery["notification_id"],))
        sent += 1
    return sent


def scan_price_alerts(database=None, data_source=None) -> int:
    db = database or get_database()
    dispatch_price_alert_deliveries(db)
    alerts = db.fetch_all(
        """SELECT a.user_id,a.symbol FROM price_alerts a JOIN users u ON u.id=a.user_id
           WHERE a.is_active=1 AND u.is_active=1 ORDER BY a.user_id,a.id"""
    )
    if not alerts:
        return 0
    symbols = tuple(dict.fromkeys(str(row["symbol"]).upper() for row in alerts))
    try:
        closes, volumes = (data_source or get_data_source()).history(symbols, period="3mo")
        if not isinstance(closes, pd.DataFrame) or not isinstance(volumes, pd.DataFrame):
            raise ValueError("行情源必须返回收盘价与成交量表格")
    except Exception as exc:
        db.log_system_event("ERROR", "ALERTS", "后台价格预警行情请求失败", str(exc)[:1000])
        return 0
    prices: dict[str, float] = {}
    metrics: dict[str, dict[str, object]] = {}
    volume_columns = {str(column).upper(): volumes[column] for column in volumes.columns}
    for raw_symbol in closes:
        symbol = str(raw_symbol).upper()
        close_column = closes[raw_symbol]
        if not isinstance(close_column, pd.Series):
            continue
        series = pd.to_numeric(close_column, errors="coerce").dropna()
        if series.empty:
            continue
        prices[symbol] = float(series.iloc[-1])
        volume_source = volume_columns.get(symbol)
        volume = (
            pd.to_numeric(volume_source.reindex(series.index), errors="coerce")
            if isinstance(volume_source, pd.Series)
            else pd.Series(index=series.index, dtype=float)
        )
        daily_change = series.pct_change(fill_method=None).dropna()
        price_delta = series.diff().dropna()
        gains = price_delta.clip(lower=0).rolling(14).mean()
        losses = (-price_delta.clip(upper=0)).rolling(14).mean()
        if gains.empty or pd.isna(gains.iloc[-1]) or pd.isna(losses.iloc[-1]):
            rsi = 50.0
        elif losses.iloc[-1] == 0:
            rsi = 100.0 if gains.iloc[-1] > 0 else 50.0
        elif gains.iloc[-1] == 0:
            rsi = 0.0
        else:
            rsi = float(100 - (100 / (1 + gains.iloc[-1] / losses.iloc[-1])))
        ema12, ema26 = series.ewm(span=12, adjust=False).mean(), series.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal_line = macd.ewm(span=9, adjust=False).mean()
        has_previous = len(series) >= 2
        current_volume = float(volume.iloc[-1]) if not volume.empty and pd.notna(volume.iloc[-1]) else 0.0
        prior_volume = volume.iloc[:-1].dropna().tail(5)
        mean_volume = float(prior_volume.mean()) if not prior_volume.empty else 0.0
        ma20 = series.rolling(20).mean()
        ma50 = series.rolling(50).mean()
        golden_cross = bool(has_previous and macd.iloc[-2] <= signal_line.iloc[-2] and macd.iloc[-1] > signal_line.iloc[-1])
        death_cross = bool(has_previous and macd.iloc[-2] >= signal_line.iloc[-2] and macd.iloc[-1] < signal_line.iloc[-1])
        ma20_breakout = bool(len(series) >= 21 and series.iloc[-2] <= ma20.iloc[-2] and series.iloc[-1] > ma20.iloc[-1])
        ma50_breakout = bool(len(series) >= 51 and series.iloc[-2] <= ma50.iloc[-2] and series.iloc[-1] > ma50.iloc[-1])
        metrics[symbol] = {
            "volume": current_volume,
            "volume_ratio": current_volume / mean_volume if mean_volume > 0 else 0.0,
            "rsi": rsi,
            "change": float(daily_change.iloc[-1] * 100) if not daily_change.empty else 0.0,
            "golden_cross": golden_cross,
            "death_cross": death_cross,
            # Keep the prefixed keys used by early alert records/callers.
            "macd_golden_cross": golden_cross,
            "macd_death_cross": death_cross,
            "ma_ma20_breakout": ma20_breakout,
            "ma_ma50_breakout": ma50_breakout,
            "ma20_breakout": ma20_breakout,
            "ma50_breakout": ma50_breakout,
        }
    triggered_count = 0
    service = AlertService(db)
    for user_id in dict.fromkeys(int(row["user_id"]) for row in alerts):
        user = db.fetch_one("SELECT plan_type,subscription_expire FROM users WHERE id=?", (user_id,)) or {}
        for alert in service.evaluate(user_id, prices, metrics):
            triggered_count += 1
    dispatch_price_alert_deliveries(db)
    return triggered_count


def downgrade_expired_subscriptions() -> int:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return get_database().execute(
        """UPDATE users SET plan_type='免费版',subscription_expire=NULL
           WHERE plan_type!='免费版' AND subscription_expire IS NOT NULL AND subscription_expire<=?""",
        (now,),
    )


def notify_expiring_subscriptions(database=None) -> int:
    if not smtp_configured():
        return 0
    db = database or get_database()
    now = datetime.now(UTC)
    users = db.fetch_all(
        """SELECT id,email,display_name,plan_type,subscription_expire FROM users
           WHERE is_active=1 AND plan_type!='免费版' AND subscription_expire>? AND subscription_expire<=?""",
        (now.isoformat(timespec="seconds"), (now + timedelta(days=7)).isoformat(timespec="seconds")),
    )
    sent = 0
    for user in users:
        marker = f"expiry={user['subscription_expire']}"
        if db.fetch_one(
            "SELECT 1 FROM user_action_logs WHERE user_id=? AND action_type='RENEWAL_REMINDER' AND details=?",
            (user["id"], marker),
        ):
            continue
        try:
            send_email(
                user["email"],
                "CicloTrade 订阅即将到期",
                f"{user.get('display_name') or '您好'}：\n\n您的 {user['plan_type']} 将于 {user['subscription_expire']} 到期。"
                "请登录 CicloTrade 查看续费选项；到期后系统会自动降级为免费版。",
            )
        except RuntimeError:
            continue
        db.execute(
            "INSERT INTO user_action_logs (user_id,action_type,details,created_at) VALUES (?,?,?,?)",
            (user["id"], "RENEWAL_REMINDER", marker, datetime.now(UTC).isoformat(timespec="seconds")),
        )
        sent += 1
    return sent


def notify_inactive_users() -> int:
    if not smtp_configured():
        return 0
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
    users = get_database().fetch_all(
        "SELECT email,display_name FROM users WHERE is_active=1 AND last_login IS NOT NULL AND last_login<=?", (cutoff,)
    )
    sent = 0
    for user in users:
        try:
            send_email(
                user["email"],
                "CicloTrade 账户提醒",
                f"{user.get('display_name') or '您好'}：\n\n您的 CicloTrade 账户已连续 7 天未登录。请登录检查预警、订阅和账户安全状态。",
            )
            sent += 1
        except RuntimeError:
            continue
    return sent


def evaluate_strategy_catalog() -> int:
    """Run the real-data catalog evaluation after market close."""
    return len(score_daily_catalog())


def aggregate_user_profiles(database=None) -> int:
    """Refresh internal recommendation labels from persisted behaviour."""
    return UserProfileService(database or get_database()).aggregate_all()


def refresh_saved_strategy_performance(database=None) -> dict[str, int]:
    """Append today's auditable performance snapshot for eligible strategies."""
    return update_saved_strategy_performance(database or get_database())
