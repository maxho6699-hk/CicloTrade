# -*- coding: utf-8 -*-
"""订阅降级与流失提醒任务。"""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import json
import os

import pandas as pd

from core.alerts import AlertService
from core.database import get_database
from core.membership import authoritative_membership_user, resolve_membership
from core.plans import TELEGRAM_CHANNEL_NAMES, can, effective_plan, plan_display_name
from core.official_paper_consumers import (
    LEGACY,
    OFFICIAL_PAPER_V2,
    active_events as official_consumer_events,
    journal_for as official_consumer_journal,
)
from core.quant_journal import QuantJournal
from core.user_settings import load_user_settings
from core.strategy_evaluation import run_system_quant_cycle, update_saved_strategy_performance
from core.user_profiles import UserProfileService
from data.datasource import get_resilient_data_source
from notification.email_sender import send_email, smtp_configured
from notification.channel_content import (
    RecommendationChange,
    classify_recommendation_change,
    normalize_recommendation_revision,
    recommendation_from_event,
)
from notification.templates import (
    email_message,
    telegram_daily_summary,
    telegram_price_alert,
    telegram_quant_message,
)
from notification.telegram_bot import (
    TelegramDeliveryUncertain,
    entitled_user_target,
    remove_group_member,
    send_telegram,
    telegram_configured,
    telegram_group,
    telegram_token,
    verified_user_target,
)
from notification.telegram_outbox import (
    dispatch_telegram_service_outbox as dispatch_telegram_service_outbox,
)


class NoMaterialRecommendationChange(RuntimeError):
    """The event changed timestamps or bookkeeping only, so no channel message is due."""


FREE_GROUP_SIGNAL_DELAY_MINUTES = {"stock": 60, "option": 15}
FREE_GROUP_MAX_DELAY_MINUTES = max(FREE_GROUP_SIGNAL_DELAY_MINUTES.values())


_DELIVERY_TABLES = {
    LEGACY: "quant_event_deliveries",
    OFFICIAL_PAPER_V2: "official_paper_event_deliveries_v2",
}


def _consumer_store(delivery: dict) -> str:
    return str(delivery.get("_consumer_store") or LEGACY)


def _consumer_delivery_table(delivery: dict) -> str:
    return _DELIVERY_TABLES[_consumer_store(delivery)]


def _consumer_event_targets(database) -> list[dict]:
    """Active official events plus terminal reversals, across v2 and history."""
    targets: list[dict] = []
    for event in official_consumer_events(database):
        store = str(event["_consumer_store"])
        # An active signal/correction changes the official position.  A terminal
        # reversal is inactive by definition but must still be communicated.
        if not event.get("active") and event.get("event_type") != "reversal":
            continue
        journal = official_consumer_journal(database, store)
        for leg in journal.execution_legs(int(event["id"])):
            # Corrections are themselves deliverable audit records.  Preserve
            # a zero net-delta correction so rendering can explicitly decide
            # it is a no-material-change, rather than silently losing it.
            if not float(leg.get("quantity_delta") or 0) and event.get("event_type") != "correction":
                continue
            targets.append({
                "event_id": int(event["id"]),
                "event_type": event["event_type"],
                "recorded_at": event["recorded_at"],
                "instrument_type": leg["instrument_type"],
                "symbol": leg["symbol"],
                "market": leg["market"],
                "_consumer_store": store,
            })
    return targets


def _due_consumer_deliveries(database, *, group: bool = False, delayed: bool = False, due: str, limit: int) -> list[dict]:
    if delayed:
        legacy, v2 = "telegram_delayed_group_deliveries", "official_paper_delayed_group_deliveries_v2"
    elif group:
        legacy, v2 = "telegram_group_deliveries", "official_paper_group_deliveries_v2"
    else:
        legacy, v2 = "quant_event_deliveries", "official_paper_event_deliveries_v2"
    # A copied legacy event may already have an unsent row when v2 is adopted.
    # Prefer the v2 immutable source identity at claim time as well as enqueue
    # time, so deployment timing cannot create a duplicate Telegram delivery.
    legacy_filter = """ AND NOT EXISTS (
            SELECT 1 FROM official_paper_events_v2 v2
            JOIN quant_events legacy_event ON legacy_event.id=legacy.event_id
            WHERE v2.ledger_key=? AND v2.source=legacy_event.source
              AND v2.external_event_id=legacy_event.external_event_id
         )"""
    params: tuple = (
        due,
        os.getenv("TRADEAI_OFFICIAL_PAPER_V2_LEDGER_KEY", "tradeai-official-paper-v2"),
        due,
    )
    return database.fetch_all(
        f"""SELECT legacy.*, '{LEGACY}' AS _consumer_store FROM {legacy} legacy
             WHERE status IN ('pending','failed','sending') AND next_attempt_at<=?{legacy_filter}
             UNION ALL
             SELECT *, '{OFFICIAL_PAPER_V2}' AS _consumer_store FROM {v2}
             WHERE status IN ('pending','failed','sending') AND next_attempt_at<=?
             ORDER BY next_attempt_at,_consumer_store,id LIMIT ?""",
        params + (max(1, min(int(limit), 500)),),
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
    event_targets = _consumer_event_targets(db)
    if not event_targets:
        return 0
    users = [
        authoritative_membership_user(db, user)
        for user in db.fetch_all(
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
    ]
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
                table = _DELIVERY_TABLES[str(target["_consumer_store"])]
                cursor = conn.execute(
                    f"""INSERT OR IGNORE INTO {table}
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


def _recommendation_identity(metadata: dict) -> str | None:
    value = (
        metadata.get("recommendation_id")
        or metadata.get("opportunity_id")
        or metadata.get("decision_id")
    )
    cleaned = str(value or "").strip()
    return cleaned or None


def _previous_recommendation_content(
    journal: QuantJournal,
    event: dict,
    leg: dict,
    metadata: dict,
):
    instrument_key = str(leg.get("instrument_key") or "")
    if not instrument_key:
        return None
    events = journal.list_events(str(event["ledger_key"]))
    if event.get("event_type") in {"correction", "reversal"} and event.get("corrects_event_id"):
        candidates = [
            item for item in events
            if int(item["id"]) == int(event["corrects_event_id"])
        ]
    else:
        identity = _recommendation_identity(metadata)
        if not identity:
            return None
        candidates = [
            item for item in events
            if int(item["id"]) < int(event["id"])
            and _recommendation_identity(item.get("metadata") or {}) == identity
        ]
    for previous in reversed(candidates):
        try:
            previous_legs = journal.execution_legs(int(previous["id"]))
        except (KeyError, TypeError, ValueError, RuntimeError):
            continue
        previous_leg = next(
            (item for item in previous_legs if str(item.get("instrument_key") or "") == instrument_key),
            None,
        )
        if previous_leg is not None:
            return recommendation_from_event(previous, previous_leg, previous.get("metadata") or {})
    return None


def _quant_message(
    database,
    delivery: dict,
    *,
    audience: str | None = None,
    delay_minutes: int | None = None,
) -> str:
    store = _consumer_store(delivery)
    journal = official_consumer_journal(database, store)
    event = next((item for item in journal.list_events(
        os.getenv("TRADEAI_OFFICIAL_PAPER_V2_LEDGER_KEY", "tradeai-official-paper-v2")
        if store == OFFICIAL_PAPER_V2 else os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
    ) if int(item["id"]) == int(delivery["event_id"])), None)
    if event is None:
        raise RuntimeError("量化事件不存在")
    all_legs = journal.execution_legs(event["id"])
    if audience == "professional":
        allowed_types = {"stock", "option"}
        legs = all_legs
    elif audience == "advanced":
        allowed_types = {"stock"}
        legs = [leg for leg in all_legs if leg["instrument_type"] == "stock"]
    elif audience == "daily":
        allowed_types = {delivery["instrument_type"]}
        legs = [leg for leg in all_legs if leg["instrument_type"] in allowed_types]
    else:
        allowed_types = {delivery["instrument_type"]}
        legs = [
            leg for leg in all_legs
            if leg["instrument_type"] == delivery["instrument_type"] and leg["symbol"] == delivery["symbol"]
        ]
    if not legs:
        raise RuntimeError("量化事件没有匹配的交易腿")
    metadata = event.get("metadata") or {}
    changed_legs: list[dict] = []
    change_kinds: dict[str, RecommendationChange] = {}
    contents = {}
    for leg in legs:
        current_content = recommendation_from_event(event, leg, metadata)
        previous_content = _previous_recommendation_content(journal, event, leg, metadata)
        current_content = normalize_recommendation_revision(previous_content, current_content)
        change = classify_recommendation_change(previous_content, current_content)
        if change is None:
            continue
        changed_legs.append(leg)
        instrument_key = str(leg.get("instrument_key") or "")
        change_kinds[instrument_key] = change
        contents[instrument_key] = current_content
    if not changed_legs:
        raise NoMaterialRecommendationChange("no_material_change")
    positions = [
        position for position in journal.replay(event["ledger_key"])["positions"].values()
        if position["instrument_type"] in allowed_types
        and (audience is not None or position["symbol"] == delivery["symbol"])
    ]
    source_label = "CicloTrade 官方模擬帳戶"
    delay_note = None
    if delay_minutes:
        delay_note = "期權建議延遲 15 分鐘" if delivery["instrument_type"] == "option" else "正股建議延遲 1 小時"
    return telegram_quant_message(
        event,
        changed_legs,
        metadata,
        positions=positions,
        delay_note=delay_note,
        source_label=source_label,
        change_kinds=change_kinds,
        contents=contents,
        delivery_delay_minutes=int(delay_minutes or 0),
        immediate_action_allowed=not bool(delay_minutes),
    )


def _send_quant_card(message: str, target: str, *, upgrade: bool = False) -> None:
    base = os.getenv("APP_BASE_URL", "https://ciclotrade.com").rstrip("/")
    buttons = (
        [
            [
                {"text": "📈 今日建議", "url": f"{base}/opportunities"},
                {"text": "💼 官方模擬持倉", "url": f"{base}/portfolio"},
            ],
            [
                {"text": "📊 市場行情", "url": f"{base}/markets"},
                {"text": "🔔 通知設定", "url": f"{base}/notifications"},
            ],
        ]
        if not upgrade
        else [
            [
                {"text": "📈 延遲建議", "url": f"{base}/opportunities"},
                {"text": "💎 升級會員", "url": f"{base}/membership"},
            ],
            [
                {"text": "📊 市場行情", "url": f"{base}/markets"},
                {"text": "❓ 方案說明", "url": f"{base}/membership"},
            ],
        ]
    )
    send_telegram(
        message,
        chat_id=target,
        parse_mode="HTML",
        buttons=buttons,
        protect_content=True,
    )


def dispatch_quant_signal_deliveries(database=None, limit: int = 100) -> int:
    """Claim, re-authorize, send, and persist retry state for signal notifications."""
    db = database or get_database()
    now = datetime.now(UTC)
    due = now.isoformat(timespec="seconds")
    deliveries = _due_consumer_deliveries(db, due=due, limit=limit)
    sent = 0
    for delivery in deliveries:
        table = _consumer_delivery_table(delivery)
        lease_until = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
        claimed = db.execute(
            f"""UPDATE {table}
               SET status='sending',attempts=attempts+1,next_attempt_at=?,updated_at=?
               WHERE id=? AND channel='telegram'
                 AND status IN ('pending','failed','sending') AND next_attempt_at<=?""",
            (lease_until, due, delivery["id"], due),
        )
        if not claimed:
            continue
        user = db.fetch_one(
            "SELECT id,is_active,plan_type,subscription_expire FROM users WHERE id=?", (delivery["user_id"],)
        )
        if user:
            user = authoritative_membership_user(db, user)
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
                f"""UPDATE {table} SET status='skipped',last_error='entitlement_or_consent',
                   updated_at=? WHERE id=? AND status='sending'""",
                (due, delivery["id"]),
            )
            continue
        try:
            if not telegram_configured(target):
                raise RuntimeError("Telegram Bot 尚未配置")
            _send_quant_card(_quant_message(db, delivery), target)
        except NoMaterialRecommendationChange:
            db.execute(
                f"""UPDATE {table} SET status='skipped',last_error='no_material_change',
                   updated_at=? WHERE id=? AND status='sending'""",
                (due, delivery["id"]),
            )
            continue
        except TelegramDeliveryUncertain as exc:
            db.execute(
                f"""UPDATE {table} SET status='skipped',last_error=?,updated_at=?
                   WHERE id=? AND status='sending'""",
                (f"delivery_uncertain_manual_retry: {exc}"[:300], due, delivery["id"]),
            )
            continue
        except RuntimeError as exc:
            attempts = int(delivery["attempts"]) + 1
            retry_at = (now + timedelta(minutes=min(2 ** min(attempts, 6), 60))).isoformat(timespec="seconds")
            token = telegram_token()
            error = str(exc).replace(token, "[redacted]") if token else str(exc)
            db.execute(
                f"""UPDATE {table} SET status='failed',next_attempt_at=?,last_error=?,
                   updated_at=? WHERE id=? AND status='sending'""",
                (retry_at, error[:300], due, delivery["id"]),
            )
            continue
        db.execute(
            f"""UPDATE {table} SET status='sent',sent_at=?,updated_at=?,last_error=NULL
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
        "group_queued": enqueue_quant_group_deliveries(db),
        "group_sent": dispatch_quant_group_deliveries(db),
        "free_queued": enqueue_delayed_free_group_deliveries(db),
        "free_sent": dispatch_delayed_free_group_deliveries(db),
    }


def enqueue_quant_group_deliveries(database=None) -> int:
    if os.getenv("TELEGRAM_GROUP_SIGNALS_ENABLED", "false").strip().lower() != "true":
        return 0
    db = database or get_database()
    targets = _consumer_event_targets(db)
    event_types: dict[tuple[str, int], set[str]] = {}
    for target in targets:
        event_types.setdefault((str(target["_consumer_store"]), int(target["event_id"])), set()).add(str(target["instrument_type"]))
    now = datetime.now(UTC).isoformat(timespec="seconds")
    queued = 0
    with db.transaction() as conn:
        for (store, event_id), instrument_types in event_types.items():
            table = "official_paper_group_deliveries_v2" if store == OFFICIAL_PAPER_V2 else "telegram_group_deliveries"
            routes = []
            if "stock" in instrument_types:
                routes.append(("advanced", "stock"))
            routes.append(("professional", "stock" if "stock" in instrument_types else "option"))
            for group, instrument_type in routes:
                chat_id = telegram_group(group)
                if not chat_id:
                    continue
                if conn.execute(
                    f"SELECT 1 FROM {table} WHERE event_id=? AND group_name=? LIMIT 1",
                    (event_id, group),
                ).fetchone():
                    continue
                cursor = conn.execute(
                    f"""INSERT OR IGNORE INTO {table}
                       (event_id,group_name,chat_id,instrument_type,symbol,status,attempts,
                        next_attempt_at,last_error,created_at,updated_at,sent_at)
                       VALUES (?,?,?,?,?,'pending',0,?,NULL,?,?,NULL)""",
                    (event_id, group, chat_id, instrument_type, "*", now, now, now),
                )
                queued += cursor.rowcount
    return queued


def dispatch_quant_group_deliveries(database=None, limit: int = 100) -> int:
    if os.getenv("TELEGRAM_GROUP_SIGNALS_ENABLED", "false").strip().lower() != "true":
        return 0
    db = database or get_database()
    now = datetime.now(UTC)
    due = now.isoformat(timespec="seconds")
    rows = _due_consumer_deliveries(db, group=True, due=due, limit=limit)
    sent = 0
    for delivery in rows:
        table = "official_paper_group_deliveries_v2" if _consumer_store(delivery) == OFFICIAL_PAPER_V2 else "telegram_group_deliveries"
        lease_until = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
        if not db.execute(
            f"""UPDATE {table}
               SET status='sending',attempts=attempts+1,next_attempt_at=?,updated_at=?
               WHERE id=? AND status IN ('pending','failed','sending') AND next_attempt_at<=?""",
            (lease_until, due, delivery["id"], due),
        ):
            continue
        target = telegram_group(delivery["group_name"])
        if not target:
            db.execute(
                f"UPDATE {table} SET status='skipped',last_error='group_not_configured',updated_at=? WHERE id=?",
                (due, delivery["id"]),
            )
            continue
        try:
            if not telegram_configured(target):
                raise RuntimeError("Telegram 群组通知尚未启用")
            _send_quant_card(_quant_message(db, delivery, audience=delivery["group_name"]), target)
        except NoMaterialRecommendationChange:
            db.execute(
                f"UPDATE {table} SET status='skipped',last_error='no_material_change',updated_at=? WHERE id=?",
                (due, delivery["id"]),
            )
            continue
        except TelegramDeliveryUncertain as exc:
            db.execute(
                f"UPDATE {table} SET status='skipped',last_error=?,updated_at=? WHERE id=?",
                (f"delivery_uncertain_manual_retry: {exc}"[:300], due, delivery["id"]),
            )
            continue
        except RuntimeError as exc:
            retry_at = (now + timedelta(minutes=min(2 ** min(int(delivery["attempts"]) + 1, 6), 60))).isoformat(timespec="seconds")
            token = telegram_token()
            error = str(exc).replace(token, "[redacted]") if token else str(exc)
            db.execute(
                f"""UPDATE {table} SET status='failed',next_attempt_at=?,last_error=?,updated_at=?
                   WHERE id=?""",
                (retry_at, error[:300], due, delivery["id"]),
            )
            continue
        db.execute(
            f"UPDATE {table} SET status='sent',sent_at=?,updated_at=?,last_error=NULL WHERE id=?",
            (due, due, delivery["id"]),
        )
        sent += 1
    return sent


def enqueue_delayed_free_group_deliveries(database=None) -> int:
    if os.getenv("TELEGRAM_FREE_DELAYED_SIGNALS_ENABLED", "false").strip().lower() != "true":
        return 0
    db = database or get_database()
    target = telegram_group("daily")
    if not target:
        return 0
    rows = _consumer_event_targets(db)
    now = datetime.now(UTC)
    queued = 0
    with db.transaction() as conn:
        for row in rows:
            table = "official_paper_delayed_group_deliveries_v2" if row["_consumer_store"] == OFFICIAL_PAPER_V2 else "telegram_delayed_group_deliveries"
            delay = FREE_GROUP_SIGNAL_DELAY_MINUTES.get(
                str(row["instrument_type"]), FREE_GROUP_MAX_DELAY_MINUTES
            )
            recorded = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=UTC)
            release = recorded.astimezone(UTC) + timedelta(minutes=delay)
            cursor = conn.execute(
                f"""INSERT OR IGNORE INTO {table}
                   (event_id,chat_id,instrument_type,delay_minutes,status,attempts,next_attempt_at,
                    last_error,created_at,updated_at,sent_at)
                   VALUES (?,?,?,?,'pending',0,?,NULL,?,?,NULL)""",
                (
                    row["event_id"], target, row["instrument_type"], delay,
                    release.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds"),
                ),
            )
            queued += cursor.rowcount
    return queued


def dispatch_delayed_free_group_deliveries(database=None, limit: int = 100) -> int:
    if os.getenv("TELEGRAM_FREE_DELAYED_SIGNALS_ENABLED", "false").strip().lower() != "true":
        return 0
    db = database or get_database()
    now = datetime.now(UTC)
    due = now.isoformat(timespec="seconds")
    rows = _due_consumer_deliveries(db, delayed=True, due=due, limit=limit)
    sent = 0
    for delivery in rows:
        table = "official_paper_delayed_group_deliveries_v2" if _consumer_store(delivery) == OFFICIAL_PAPER_V2 else "telegram_delayed_group_deliveries"
        lease_until = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
        if not db.execute(
            f"""UPDATE {table}
               SET status='sending',attempts=attempts+1,next_attempt_at=?,updated_at=?
               WHERE id=? AND status IN ('pending','failed','sending') AND next_attempt_at<=?""",
            (lease_until, due, delivery["id"], due),
        ):
            continue
        try:
            if not telegram_configured(delivery["chat_id"]):
                raise RuntimeError("Telegram 免費頻道尚未啟用")
            message = _quant_message(
                db,
                delivery,
                audience="daily",
                delay_minutes=int(delivery["delay_minutes"]),
            )
            _send_quant_card(message, delivery["chat_id"], upgrade=True)
        except NoMaterialRecommendationChange:
            db.execute(
                f"UPDATE {table} SET status='skipped',last_error='no_material_change',updated_at=? WHERE id=?",
                (due, delivery["id"]),
            )
            continue
        except TelegramDeliveryUncertain as exc:
            db.execute(
                f"UPDATE {table} SET status='skipped',last_error=?,updated_at=? WHERE id=?",
                (f"delivery_uncertain_manual_retry: {exc}"[:300], due, delivery["id"]),
            )
            continue
        except RuntimeError as exc:
            retry_at = (now + timedelta(minutes=min(2 ** min(int(delivery["attempts"]) + 1, 6), 60))).isoformat(timespec="seconds")
            token = telegram_token()
            error = str(exc).replace(token, "[redacted]") if token else str(exc)
            db.execute(
                f"""UPDATE {table} SET status='failed',next_attempt_at=?,
                   last_error=?,updated_at=? WHERE id=?""",
                (retry_at, error[:300], due, delivery["id"]),
            )
            continue
        db.execute(
            f"UPDATE {table} SET status='sent',sent_at=?,updated_at=?,last_error=NULL WHERE id=?",
            (due, due, delivery["id"]),
        )
        sent += 1
    return sent


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
        if user:
            user = authoritative_membership_user(db, user)
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
            send_telegram(
                telegram_price_alert(delivery["content"]),
                chat_id=target,
                parse_mode="HTML",
                protect_content=True,
            )
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
            token = telegram_token()
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
        closes, volumes = (data_source or get_resilient_data_source()).history(symbols, period="3mo")
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
        for alert in service.evaluate(user_id, prices, metrics):
            triggered_count += 1
    dispatch_price_alert_deliveries(db)
    return triggered_count


def downgrade_expired_subscriptions(database=None) -> int:
    db = database or get_database()
    moment = datetime.now(UTC)
    now = moment.isoformat(timespec="seconds")
    users = db.fetch_all(
        """SELECT id,email,display_name,plan_type,subscription_expire FROM users
           WHERE plan_type!='免费版' AND subscription_expire IS NOT NULL AND subscription_expire<=?""",
        (now,),
    )
    if not users:
        return 0
    transitions = []
    with db.transaction() as conn:
        for user in users:
            resolved = resolve_membership(
                conn, int(user["id"]), moment, sync_cache=True
            )
            new_plan = str(resolved["plan_type"])
            action = (
                "SUBSCRIPTION_EXPIRED"
                if new_plan == "免费版"
                else "SUBSCRIPTION_TIER_FALLBACK"
            )
            conn.execute(
                "INSERT INTO user_action_logs(user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                (
                    user["id"],
                    action,
                    json.dumps(
                        {
                            "previous_plan": user["plan_type"],
                            "previous_expiry": user["subscription_expire"],
                            "effective_plan": new_plan,
                            "effective_expiry": resolved["subscription_expire"],
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            transitions.append(
                {
                    **user,
                    "effective_plan": new_plan,
                    "effective_expiry": resolved["subscription_expire"],
                }
            )
    membership_sync = os.getenv("TELEGRAM_MEMBERSHIP_SYNC_ENABLED", "false").strip().lower() == "true"
    for user in transitions:
        settings = load_user_settings(user["id"], db)
        telegram_user = verified_user_target(settings)
        if membership_sync and telegram_user:
            previous_groups = set(
                ("advanced", "professional")
                if user["plan_type"] in {"专业版", "定制版"}
                else ("advanced",) if user["plan_type"] == "高级版" else ()
            )
            effective_groups = set(
                ("advanced", "professional")
                if user["effective_plan"] in {"专业版", "定制版"}
                else ("advanced",) if user["effective_plan"] == "高级版" else ()
            )
            for group in sorted(previous_groups - effective_groups):
                if target := telegram_group(group):
                    try:
                        remove_group_member(target, telegram_user)
                    except RuntimeError as exc:
                        db.log_system_event("WARN", "TELEGRAM", "会员到期群组移除失败", f"user={user['id']};group={group};{str(exc)[:180]}")
        if smtp_configured():
            fell_back = user["effective_plan"] != "免费版"
            member_message = (
                f"{user.get('display_name') or '您好'}，你的 {plan_display_name(user['plan_type'])} 已到期，"
                f"现已回落为 {plan_display_name(user['effective_plan'])}。"
                if fell_back
                else f"{user.get('display_name') or '您好'}，你的 {plan_display_name(user['plan_type'])} 已到期并自动转为免费会员。"
            )
            subject, text, html = email_message(
                "CicloTrade 會員等級已更新" if fell_back else "CicloTrade 會員已到期",
                "會員等級已回落" if fell_back else "會員權益已到期",
                member_message,
                (
                    f"原等级到期时间：{user['subscription_expire']}",
                    f"当前等级有效期：{user['effective_expiry'] or '免费版长期有效'}",
                    "已保存的研究记录不会删除。",
                ),
                action_url=os.getenv("APP_BASE_URL", "https://ciclotrade.com"),
            )
            notices = (
                (user["email"], (subject, text, html)),
                (
                    os.getenv("SUPPORT_EMAIL", "support@ciclotrade.com"),
                    email_message(
                        f"CicloTrade 会员到期 · 用户 #{user['id']}",
                        "会员到期处理完成",
                        "系统已重新计算用户权益；请核对付费群成员状态。",
                        (
                            f"用户 ID：{user['id']}",
                            f"原方案：{user['plan_type']}",
                            f"当前方案：{user['effective_plan']}",
                            f"原到期时间：{user['subscription_expire']}",
                        ),
                    ),
                ),
            )
            for recipient, message in notices:
                try:
                    send_email(recipient, *message)
                except RuntimeError:
                    db.log_system_event("WARN", "EMAIL", "会员到期邮件发送失败", f"user={user['id']};recipient={recipient}")
    return len(transitions)


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
                *email_message(
                    "CicloTrade 會員即將到期",
                    "你的會員權益即將到期",
                        f"{user.get('display_name') or '您好'}，你的 {plan_display_name(user['plan_type'])} 將於指定時間到期。",
                    (
                        f"到期時間：{user['subscription_expire']}",
                        "到期後系統會自動降級為免費版，已保存的研究記錄不會被刪除。",
                    ),
                    action_url=os.getenv("APP_BASE_URL", "https://ciclotrade.com"),
                ),
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
                *email_message(
                    "CicloTrade 帳戶提醒",
                    "你的研究工作區正在等待",
                    f"{user.get('display_name') or '您好'}，你的 CicloTrade 帳戶已連續 7 天未登入。",
                    ("請登入檢查預警、量化日誌、會員狀態與帳戶安全。",),
                    action_url=os.getenv("APP_BASE_URL", "https://ciclotrade.com"),
                ),
            )
            sent += 1
        except RuntimeError:
            continue
    return sent


def _daily_summary_payload(database, ledger_key: str, snapshots: list[dict]) -> dict:
    journal = QuantJournal(database)
    latest_at = max(datetime.fromisoformat(str(row["captured_at"]).replace("Z", "+00:00")) for row in snapshots)
    earliest = latest_at - timedelta(days=1)
    events = []
    risk_levels: dict = {}
    for event in journal.list_events(ledger_key):
        values = event.get("metadata", {}).get("risk_levels") if isinstance(event.get("metadata"), dict) else None
        if event.get("active") and isinstance(values, dict):
            risk_levels.update(values)
        occurred = datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=UTC)
        if earliest <= occurred <= latest_at:
            events.append(event)
    trades = []
    for event in events:
        for leg in journal.execution_legs(int(event["id"])):
            trades.append({**leg, "time": event["occurred_at"]})
    state = journal.replay(ledger_key)
    return {
        "items": [
            (snapshot, (journal.performance_windows(ledger_key, snapshot["currency"]) or {}).get("windows", {}))
            for snapshot in snapshots
        ],
        "trades": trades,
        "positions": list(state["positions"].values()),
        "risk_levels": risk_levels,
    }


def _free_summary_release_at(database, ledger_key: str, snapshots: list[dict]) -> datetime | None:
    """Return the latest safe release time for every datum exposed by a free summary."""
    release_times: list[datetime] = []
    for snapshot in snapshots:
        try:
            captured_at = datetime.fromisoformat(
                str(snapshot["captured_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            return None
        if captured_at.tzinfo is None:
            return None
        release_times.append(
            captured_at.astimezone(UTC)
            + timedelta(minutes=FREE_GROUP_MAX_DELAY_MINUTES)
        )
    event_rows = database.fetch_all(
        """SELECT e.recorded_at,l.instrument_type
           FROM quant_events e JOIN quant_event_legs l ON l.event_id=e.id
           WHERE e.ledger_key=?""",
        (ledger_key,),
    )
    for row in event_rows:
        try:
            recorded_at = datetime.fromisoformat(
                str(row["recorded_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            return None
        if recorded_at.tzinfo is None:
            return None
        delay = FREE_GROUP_SIGNAL_DELAY_MINUTES.get(
            str(row["instrument_type"]), FREE_GROUP_MAX_DELAY_MINUTES
        )
        release_times.append(
            recorded_at.astimezone(UTC) + timedelta(minutes=delay)
        )
    return max(release_times) if release_times else None


def publish_daily_group_summary(database=None, *, free_group: bool = False) -> int:
    """Publish entitlement-specific summaries from the immutable simulation ledger."""
    if os.getenv("TELEGRAM_DAILY_SUMMARY_ENABLED", "false").strip().lower() != "true":
        return 0
    db = database or get_database()
    ledger_key = os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
    snapshots = db.fetch_all(
        """SELECT s.* FROM quant_equity_snapshots s
           JOIN (SELECT currency,MAX(id) id FROM quant_equity_snapshots
                 WHERE ledger_key=? GROUP BY currency) latest ON latest.id=s.id
           ORDER BY s.currency""",
        (ledger_key,),
    )
    if not snapshots:
        return 0
    if free_group:
        release_at = _free_summary_release_at(db, ledger_key, snapshots)
        if release_at is None or datetime.now(UTC) < release_at:
            return 0
    snapshot_marker = ",".join(str(row["id"]) for row in snapshots)
    payload = _daily_summary_payload(db, ledger_key, snapshots)
    routes = (("daily", TELEGRAM_CHANNEL_NAMES["daily"], {"stock", "option"}, "正股建議延遲 1 小時 · 期權建議延遲 15 分鐘", True),) if free_group else (
        ("advanced", TELEGRAM_CHANNEL_NAMES["advanced"], {"stock"}, None, False),
        ("professional", TELEGRAM_CHANNEL_NAMES["professional"], {"stock", "option"}, None, False),
    )
    now = datetime.now(UTC).isoformat(timespec="seconds")
    sent = 0
    for group, audience, allowed, delay_note, upgrade in routes:
        target = telegram_group(group)
        if not target or not telegram_configured(target):
            continue
        control_key = f"telegram_daily_summary_snapshot_{group}"
        saved = db.fetch_one("SELECT control_value FROM platform_controls WHERE control_key=?", (control_key,))
        if saved and saved["control_value"] == snapshot_marker:
            continue
        trades = [row for row in payload["trades"] if row["instrument_type"] in allowed]
        positions = [row for row in payload["positions"] if row["instrument_type"] in allowed]
        message = telegram_daily_summary(
            payload["items"],
            len(trades),
            trades=trades,
            positions=positions,
            risk_levels=payload["risk_levels"],
            audience=audience,
            delay_note=delay_note,
        )
        _send_quant_card(message, target, upgrade=upgrade)
        db.execute(
            """INSERT INTO platform_controls(control_key,control_value,updated_by,updated_at)
               VALUES (?,?,NULL,?)
               ON CONFLICT(control_key) DO UPDATE SET control_value=excluded.control_value,
               updated_by=NULL,updated_at=excluded.updated_at""",
            (control_key, snapshot_marker, now),
        )
        sent += 1
    return sent


def publish_free_daily_group_summary(database=None) -> int:
    """Publish the free summary after the longest free-group signal delay."""
    return publish_daily_group_summary(database, free_group=True)


def evaluate_strategy_catalog(cycle_slot: str = "after_close") -> int:
    """Run one idempotent adaptive cycle for a US-market checkpoint."""
    return int(run_system_quant_cycle(cycle_slot=cycle_slot)["event_created"])


def aggregate_user_profiles(database=None) -> int:
    """Refresh internal recommendation labels from persisted behaviour."""
    return UserProfileService(database or get_database()).aggregate_all()


def refresh_saved_strategy_performance(database=None) -> dict[str, int]:
    """Append today's auditable performance snapshot for eligible strategies."""
    return update_saved_strategy_performance(database or get_database())
