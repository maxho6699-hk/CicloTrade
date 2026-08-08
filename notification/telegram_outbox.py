# -*- coding: utf-8 -*-
"""Persistent, retryable delivery for Telegram service-desk notices."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import json
from typing import Any

from core.database import get_database
from notification.telegram_bot import (
    TelegramDeliveryUncertain,
    copy_telegram_message,
    send_telegram,
    telegram_token,
)
from notification.telegram_models import TelegramOutbound


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="seconds")


def enqueue_telegram_outbound(database, item: TelegramOutbound, dedupe_key: str) -> bool:
    """Persist one private notice before attempting network delivery."""
    key = str(dedupe_key).strip()
    if not 1 <= len(key) <= 160:
        raise ValueError("Telegram 通知幂等键无效。")
    buttons = json.dumps(item.buttons, ensure_ascii=False, separators=(",", ":")) if item.buttons else None
    now = _iso()
    with database.transaction() as conn:
        inserted = conn.execute(
            """INSERT OR IGNORE INTO telegram_service_outbox
               (dedupe_key,chat_id,message,buttons_json,copy_from_chat_id,copy_message_id,
                status,attempts,next_attempt_at,last_error,message_sent_at,copy_sent_at,
                created_at,updated_at,sent_at)
               VALUES (?,?,?,?,?,?,'pending',0,?,NULL,NULL,NULL,?,?,NULL)""",
            (
                key,
                str(item.chat_id),
                str(item.message),
                buttons,
                str(item.copy_from_chat_id) if item.copy_from_chat_id else None,
                item.copy_message_id,
                now,
                now,
                now,
            ),
        )
    return bool(inserted.rowcount)


def _retry(database, row: dict[str, Any], now: datetime, error: str) -> None:
    attempts = int(row["attempts"]) + 1
    retry_at = _iso(now + timedelta(minutes=min(2 ** min(attempts, 6), 60)))
    token = telegram_token()
    detail = error.replace(token, "[redacted]") if token else error
    database.execute(
        """UPDATE telegram_service_outbox
           SET status='failed',next_attempt_at=?,last_error=?,updated_at=?
           WHERE id=? AND status='sending'""",
        (retry_at, detail[:300], _iso(now), row["id"]),
    )


def dispatch_telegram_service_outbox(database=None, limit: int = 50) -> int:
    """Deliver due notices with a lease and bounded exponential retry."""
    db = database or get_database()
    now = datetime.now(UTC)
    due = _iso(now)
    rows = db.fetch_all(
        """SELECT * FROM telegram_service_outbox
           WHERE status IN ('pending','failed','sending') AND next_attempt_at<=?
           ORDER BY id LIMIT ?""",
        (due, max(1, min(int(limit), 200))),
    )
    sent = 0
    for row in rows:
        lease_until = _iso(now + timedelta(minutes=10))
        if not db.execute(
            """UPDATE telegram_service_outbox
               SET status='sending',attempts=attempts+1,next_attempt_at=?,updated_at=?
               WHERE id=? AND status IN ('pending','failed','sending') AND next_attempt_at<=?""",
            (lease_until, due, row["id"], due),
        ):
            continue
        current = db.fetch_one("SELECT * FROM telegram_service_outbox WHERE id=?", (row["id"],)) or row
        try:
            buttons = json.loads(current["buttons_json"]) if current.get("buttons_json") else None
            if buttons is not None and not isinstance(buttons, list):
                raise ValueError("Telegram 按钮资料无效。")
            if not current.get("message_sent_at"):
                send_telegram(
                    current["message"],
                    chat_id=current["chat_id"],
                    parse_mode="HTML",
                    buttons=buttons,
                )
                db.execute(
                    "UPDATE telegram_service_outbox SET message_sent_at=?,updated_at=? WHERE id=?",
                    (due, due, current["id"]),
                )
            if (
                current.get("copy_from_chat_id")
                and current.get("copy_message_id")
                and not current.get("copy_sent_at")
            ):
                copy_telegram_message(
                    current["chat_id"],
                    current["copy_from_chat_id"],
                    int(current["copy_message_id"]),
                )
                db.execute(
                    "UPDATE telegram_service_outbox SET copy_sent_at=?,updated_at=? WHERE id=?",
                    (due, due, current["id"]),
                )
        except TelegramDeliveryUncertain as exc:
            db.execute(
                """UPDATE telegram_service_outbox
                   SET status='skipped',last_error=?,updated_at=? WHERE id=? AND status='sending'""",
                (f"delivery_uncertain_manual_check: {str(exc)[:240]}", due, current["id"]),
            )
            continue
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            _retry(db, current, now, str(exc))
            continue
        db.execute(
            """UPDATE telegram_service_outbox
               SET status='sent',sent_at=?,updated_at=?,last_error=NULL
               WHERE id=? AND status='sending'""",
            (due, due, current["id"]),
        )
        sent += 1
    return sent
