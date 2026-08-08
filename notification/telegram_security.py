# -*- coding: utf-8 -*-
"""Persistent rate limiting and delivery deduplication for Telegram updates."""

from __future__ import annotations

from datetime import datetime, timedelta
from core.compat import UTC
import hashlib


def _action_bucket(value: str) -> tuple[str, int, int]:
    if value.startswith(("buy:create:", "pay:claimed:")):
        return "payment", 4, 600
    if value.startswith("admin:"):
        return "admin", 12, 60
    if value.startswith("desk:market"):
        return "market", 8, 60
    if value == "photo":
        return "photo", 6, 60
    if value.startswith("notify:"):
        return "settings", 12, 60
    if value.startswith(("desk:", "buy:", "menu:")):
        return "navigation", 30, 60
    if value.startswith("/"):
        return "command", 20, 60
    return "text", 12, 60


def consume_telegram_quota(database, chat_id: str, action: str) -> bool:
    """Apply one global and one fixed action bucket for a private chat."""
    bucket, limit, window_seconds = _action_bucket(action)
    now = datetime.now(UTC)
    subject = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:20]
    limits = (("all", 60, 60), (bucket, limit, window_seconds))
    with database.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for name, bucket_limit, bucket_window in limits:
            key = f"telegram-chat:{subject}:{name}"
            row = conn.execute(
                "SELECT attempts,window_started,blocked_until FROM auth_rate_limits WHERE rate_key=?",
                (key,),
            ).fetchone()
            attempts, started = 0, now
            if row and row["blocked_until"]:
                try:
                    blocked = datetime.fromisoformat(row["blocked_until"])
                    blocked = blocked.replace(tzinfo=UTC) if blocked.tzinfo is None else blocked
                    if blocked > now:
                        return False
                except (TypeError, ValueError):
                    pass
            if row:
                try:
                    saved = datetime.fromisoformat(row["window_started"])
                    saved = saved.replace(tzinfo=UTC) if saved.tzinfo is None else saved
                    if now - saved < timedelta(seconds=bucket_window):
                        attempts, started = max(0, int(row["attempts"])), saved
                except (TypeError, ValueError):
                    pass
            if attempts >= bucket_limit:
                conn.execute(
                    """INSERT INTO auth_rate_limits(rate_key,attempts,window_started,blocked_until)
                       VALUES (?,?,?,?) ON CONFLICT(rate_key) DO UPDATE SET
                       attempts=excluded.attempts,window_started=excluded.window_started,
                       blocked_until=excluded.blocked_until""",
                    (key, attempts, started.isoformat(), (now + timedelta(seconds=60)).isoformat()),
                )
                return False
            conn.execute(
                """INSERT INTO auth_rate_limits(rate_key,attempts,window_started,blocked_until)
                   VALUES (?,?,?,NULL) ON CONFLICT(rate_key) DO UPDATE SET
                   attempts=excluded.attempts,window_started=excluded.window_started,blocked_until=NULL""",
                (key, attempts + 1, started.isoformat()),
            )
    return True


def _claim_receipt(database, receipt_id: str, chat_id: str, payload: str) -> bool:
    now = datetime.now(UTC)
    fingerprint = hashlib.sha256(f"{chat_id}:{payload}".encode("utf-8")).hexdigest()
    with database.transaction() as conn:
        conn.execute(
            "DELETE FROM telegram_callback_receipts WHERE datetime(received_at)<datetime(?)",
            ((now - timedelta(days=7)).isoformat(timespec="seconds"),),
        )
        claimed = conn.execute(
            """INSERT OR IGNORE INTO telegram_callback_receipts
               (update_id,chat_id,payload_fingerprint,received_at) VALUES (?,?,?,?)""",
            (receipt_id, str(chat_id), fingerprint, now.isoformat(timespec="seconds")),
        )
    return bool(claimed.rowcount)


def claim_telegram_callback(database, callback_id: str, chat_id: str) -> bool:
    return _claim_receipt(database, f"callback:{callback_id}", chat_id, callback_id)


def claim_telegram_update(database, update_id: int, chat_id: str, payload: str) -> bool:
    return _claim_receipt(database, f"update:{int(update_id)}", chat_id, payload)
