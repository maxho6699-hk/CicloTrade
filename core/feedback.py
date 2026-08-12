"""Authenticated product feedback with bounded abuse controls and delayed notices."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from html import escape
import re
import secrets
from typing import Any

from core.admin_service import AdminService
from core.compat import UTC
from core.database import DatabaseManager, get_database


_CATEGORIES = {"bug", "suggestion", "data", "experience", "other"}
_CONTACT_PREFERENCES = {"none", "email", "telegram"}
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9._:-]{8,128}")
_PATH_RE = re.compile(r"/(?:[A-Za-z0-9][A-Za-z0-9._~/-]*)?")


class FeedbackError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _safe_row(row: dict[str, Any]) -> dict[str, Any]:
    summary = str(row["message"]).replace("\n", " ").replace("\t", " ")[:160]
    return {
        "id": str(row["public_id"]),
        "category": str(row["category"]),
        "summary": summary,
        "context_path": row.get("context_path"),
        "contact_preference": str(row["contact_preference"]),
        "created_at": str(row["created_at"]),
        "status": str(row["status"]),
    }


class FeedbackService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def _validate(payload: Any, key: Any) -> tuple[str, str, str | None, str, str]:
        required = {"category", "message", "contact_preference"}
        if not isinstance(payload, dict) or set(payload) - (required | {"context_path"}) or not required.issubset(payload):
            raise FeedbackError("反馈字段不完整或包含未知字段。")
        category, message = payload.get("category"), payload.get("message")
        context_path, preference = payload.get("context_path"), payload.get("contact_preference")
        if category not in _CATEGORIES or preference not in _CONTACT_PREFERENCES or not isinstance(message, str):
            raise FeedbackError("反馈资料格式无效。")
        if message != message.strip() or not 1 <= len(message) <= 2000 or "\x00" in message or any(ord(char) < 32 and char not in "\n\t" for char in message):
            raise FeedbackError("反馈内容必须为 1 至 2000 个纯文本字符。")
        if context_path is not None and (not isinstance(context_path, str) or len(context_path) > 300 or not _PATH_RE.fullmatch(context_path) or "//" in context_path or "/../" in f"/{context_path}/"):
            raise FeedbackError("反馈页面路径无效。")
        if not isinstance(key, str) or not _IDEMPOTENCY_RE.fullmatch(key):
            raise FeedbackError("Idempotency-Key 必须为 8 至 128 个安全字符。")
        return category, message, context_path, preference, key

    @staticmethod
    def _admin_targets(connection: Any) -> list[str]:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='admin_roles'"
        ).fetchone():
            return []
        rows = connection.execute(
            """SELECT t.chat_id,r.role FROM telegram_accounts t
               JOIN users u ON u.id=t.user_id JOIN admin_roles r ON r.user_id=u.id
               WHERE u.is_active=1 AND u.is_admin=1 AND t.is_active=1 AND t.revoked_at IS NULL"""
        ).fetchall()
        return [str(row["chat_id"]) for row in rows if AdminService.has_permission(str(row["role"]), "audit")]

    @staticmethod
    def _notice(row: dict[str, Any], email: str) -> str:
        summary = escape(str(row["message"]).replace("\n", " ")[:180])
        page = escape(str(row.get("context_path") or "—"))
        masked_email = "***" if "@" not in email else f"{email[:1]}***@{email.rsplit('@', 1)[1]}"
        return (
            "<b>CicloTrade · 新反馈</b>\n"
            f"编号：<code>{escape(str(row['public_id']))}</code>\n"
            f"类别：{escape(str(row['category']))} · 页面：<code>{page}</code>\n"
            f"用户：{escape(masked_email)} · 摘要：{summary}"
        )

    def create(self, user_id: int, payload: Any, key: Any) -> tuple[dict[str, Any], bool]:
        category, message, context_path, preference, key = self._validate(payload, key)
        now = _now()
        with self.db.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM user_feedback WHERE user_id=? AND idempotency_key=?", (int(user_id), key)).fetchone()
            if existing:
                row = dict(existing)
                if (row["category"], row["message"], row["context_path"], row["contact_preference"]) != (category, message, context_path, preference):
                    raise FeedbackError("Idempotency-Key 已用于不同反馈。", 409)
                return _safe_row(row), True
            digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
            duplicate = connection.execute(
                """SELECT * FROM user_feedback WHERE user_id=? AND message_sha256=? AND created_at>=?
                   ORDER BY id DESC LIMIT 1""", (int(user_id), digest, _iso(now - timedelta(hours=24)))
            ).fetchone()
            if duplicate:
                return _safe_row(dict(duplicate)), True
            recent = connection.execute("SELECT COUNT(*) count FROM user_feedback WHERE user_id=? AND created_at>=?", (int(user_id), _iso(now - timedelta(minutes=10)))).fetchone()["count"]
            daily = connection.execute("SELECT COUNT(*) count FROM user_feedback WHERE user_id=? AND created_at>=?", (int(user_id), _iso(now - timedelta(days=1)))).fetchone()["count"]
            if int(recent) >= 3 or int(daily) >= 10:
                raise FeedbackError("反馈提交过于频繁，请稍后再试。", 429)
            public_id = "fb_" + secrets.token_urlsafe(18)
            connection.execute(
                """INSERT INTO user_feedback(public_id,user_id,idempotency_key,category,message,message_sha256,context_path,contact_preference,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""", (public_id, int(user_id), key, category, message, digest, context_path, preference, _iso(now))
            )
            row = dict(connection.execute("SELECT * FROM user_feedback WHERE public_id=?", (public_id,)).fetchone())
            targets = self._admin_targets(connection)
            user = connection.execute("SELECT email FROM users WHERE id=?", (int(user_id),)).fetchone()
            notice = self._notice(row, str(user["email"]) if user else "")
            for chat_id in targets:
                connection.execute(
                    """INSERT OR IGNORE INTO telegram_service_outbox
                       (dedupe_key,chat_id,message,buttons_json,copy_from_chat_id,copy_message_id,status,attempts,next_attempt_at,last_error,message_sent_at,copy_sent_at,created_at,updated_at,sent_at)
                       VALUES (?,?,?,NULL,NULL,NULL,'pending',0,?,NULL,NULL,NULL,?,?,NULL)""",
                    (f"feedback:{row['public_id']}:{chat_id}", chat_id, notice, _iso(now), _iso(now), _iso(now)),
                )
        return _safe_row(row), False

    def list_for_user(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.fetch_all("""SELECT public_id,category,message,context_path,contact_preference,created_at,status
                                  FROM user_feedback WHERE user_id=? ORDER BY id DESC LIMIT ?""", (int(user_id), max(1, min(int(limit), 100))))
        return [_safe_row(row) for row in rows]
