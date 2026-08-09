"""Versioned manual-payment receiver configuration and immutable order snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from typing import Any
import unicodedata

from core.admin_service import AdminService
from core.compat import UTC
from core.database import DatabaseManager, get_database
from payment.receiver_storage import StoredReceiverQr, read_receiver_qr


METHODS = frozenset({"fps", "alipay", "wechat"})
METHOD_LABELS = {"fps": "FPS 转数快", "alipay": "支付宝", "wechat": "微信支付"}
INSTRUCTION_ENVS = {
    "fps": "FPS_PAYMENT_INSTRUCTIONS",
    "alipay": "ALIPAY_PAYMENT_INSTRUCTIONS",
    "wechat": "WECHAT_PAYMENT_INSTRUCTIONS",
}


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="seconds")


def _method(value: object) -> str:
    method = str(value or "").strip().lower()
    if method not in METHODS:
        raise ValueError("人工付款方式无效。")
    return method


def _text(value: object) -> str:
    normalized = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").strip()
    if not 1 <= len(normalized) <= 500 or any(ord(char) < 32 and char != "\n" for char in normalized):
        raise ValueError("收款 ID 或说明必须为 1 至 500 个有效字符。")
    return normalized


def _environment_text(method: str) -> str:
    value = os.getenv(INSTRUCTION_ENVS[method], "")
    return value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").strip()


class ReceivingProfileService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def current_from_connection(conn: Any, method: str) -> dict[str, Any]:
        method = _method(method)
        row = conn.execute("SELECT * FROM manual_payment_receivers WHERE method=?", (method,)).fetchone()
        if row:
            profile = dict(row)
            profile["available"] = bool(
                profile["enabled"] and (profile.get("receiver_text") or profile.get("qr_storage_key"))
            )
            profile["source"] = "telegram_admin"
            return profile
        fallback = _environment_text(method)
        return {
            "method": method,
            "enabled": int(bool(fallback)),
            "receiver_text": fallback or None,
            "qr_storage_key": None,
            "qr_sha256": None,
            "qr_telegram_file_id": None,
            "qr_telegram_file_unique_id": None,
            "version": 0,
            "updated_by": None,
            "updated_at": None,
            "available": bool(fallback),
            "source": "environment" if fallback else "unconfigured",
        }

    def current(self, method: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return self.current_from_connection(conn, method)

    def availability(self) -> dict[str, dict[str, bool]]:
        return {
            method: {
                "available": bool(profile["available"]),
                "has_text": bool(profile.get("receiver_text")),
                "has_qr": bool(profile.get("qr_storage_key")),
            }
            for method in sorted(METHODS)
            for profile in [self.current(method)]
        }

    def require_billing_admin(self, actor_id: int) -> None:
        service = AdminService(self.db)
        if not service.has_permission(service.role_for(int(actor_id)), "billing"):
            raise PermissionError("当前后台角色无权管理收款资料。")

    @staticmethod
    def _write_audit(conn: Any, actor_id: int, action: str, details: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO user_action_logs(user_id,action_type,details,created_at) VALUES (?,?,?,?)",
            (int(actor_id), action, json.dumps(details, ensure_ascii=False), _iso()),
        )

    def set_receiver_text(self, actor_id: int, method: str, value: object) -> dict[str, Any]:
        self.require_billing_admin(actor_id)
        method, receiver_text = _method(method), _text(value)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            AdminService._require_billing_in_transaction(conn, int(actor_id))
            current = self.current_from_connection(conn, method)
            version = int(current.get("version") or 0) + 1
            conn.execute(
                """INSERT INTO manual_payment_receivers
                   (method,enabled,receiver_text,qr_storage_key,qr_sha256,qr_telegram_file_id,
                    qr_telegram_file_unique_id,version,updated_by,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(method) DO UPDATE SET enabled=1,receiver_text=excluded.receiver_text,
                       version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (method, 1, receiver_text, current.get("qr_storage_key"), current.get("qr_sha256"),
                 current.get("qr_telegram_file_id"), current.get("qr_telegram_file_unique_id"),
                 version, int(actor_id), _iso()),
            )
            self._write_audit(conn, actor_id, "ADMIN_PAYMENT_RECEIVER_TEXT_UPDATED", {"method": method, "version": version})
            return self.current_from_connection(conn, method)

    def set_receiver_qr(
        self,
        actor_id: int,
        method: str,
        stored: StoredReceiverQr,
        telegram_file_id: str,
        telegram_file_unique_id: str,
    ) -> dict[str, Any]:
        self.require_billing_admin(actor_id)
        method = _method(method)
        file_id, unique_id = str(telegram_file_id).strip(), str(telegram_file_unique_id).strip()
        if not 1 <= len(file_id) <= 256 or not 1 <= len(unique_id) <= 256:
            raise ValueError("Telegram 二维码文件标识无效。")
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            AdminService._require_billing_in_transaction(conn, int(actor_id))
            current = self.current_from_connection(conn, method)
            version = int(current.get("version") or 0) + 1
            conn.execute(
                """INSERT INTO manual_payment_receivers
                   (method,enabled,receiver_text,qr_storage_key,qr_sha256,qr_telegram_file_id,
                    qr_telegram_file_unique_id,version,updated_by,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(method) DO UPDATE SET enabled=1,qr_storage_key=excluded.qr_storage_key,
                       qr_sha256=excluded.qr_sha256,qr_telegram_file_id=excluded.qr_telegram_file_id,
                       qr_telegram_file_unique_id=excluded.qr_telegram_file_unique_id,
                       version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (method, 1, current.get("receiver_text"), stored.storage_key, stored.sha256,
                 file_id, unique_id, version, int(actor_id), _iso()),
            )
            self._write_audit(conn, actor_id, "ADMIN_PAYMENT_RECEIVER_QR_UPDATED", {"method": method, "version": version, "sha256": stored.sha256})
            return self.current_from_connection(conn, method)

    def clear_field(self, actor_id: int, method: str, field: str) -> dict[str, Any]:
        self.require_billing_admin(actor_id)
        method = _method(method)
        if field not in {"receiver_text", "qr"}:
            raise ValueError("收款资料字段无效。")
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            AdminService._require_billing_in_transaction(conn, int(actor_id))
            current = self.current_from_connection(conn, method)
            text = None if field == "receiver_text" else current.get("receiver_text")
            qr_key = None if field == "qr" else current.get("qr_storage_key")
            qr_hash = None if field == "qr" else current.get("qr_sha256")
            qr_file = None if field == "qr" else current.get("qr_telegram_file_id")
            qr_unique = None if field == "qr" else current.get("qr_telegram_file_unique_id")
            version = int(current.get("version") or 0) + 1
            enabled = int(bool(text or qr_key))
            conn.execute(
                """INSERT INTO manual_payment_receivers
                   (method,enabled,receiver_text,qr_storage_key,qr_sha256,qr_telegram_file_id,
                    qr_telegram_file_unique_id,version,updated_by,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(method) DO UPDATE SET enabled=excluded.enabled,
                       receiver_text=excluded.receiver_text,qr_storage_key=excluded.qr_storage_key,
                       qr_sha256=excluded.qr_sha256,qr_telegram_file_id=excluded.qr_telegram_file_id,
                       qr_telegram_file_unique_id=excluded.qr_telegram_file_unique_id,
                       version=excluded.version,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                (method, enabled, text, qr_key, qr_hash, qr_file, qr_unique, version, int(actor_id), _iso()),
            )
            self._write_audit(conn, actor_id, "ADMIN_PAYMENT_RECEIVER_FIELD_CLEARED", {"method": method, "field": field, "version": version})
            return self.current_from_connection(conn, method)

    @staticmethod
    def snapshot_order(conn: Any, order_no: str, method: str) -> dict[str, Any]:
        existing = conn.execute(
            "SELECT * FROM subscription_order_payment_receivers WHERE order_no=?", (order_no,)
        ).fetchone()
        if existing:
            return dict(existing)
        profile = ReceivingProfileService.current_from_connection(conn, method)
        if not profile["available"]:
            raise ValueError(f"{METHOD_LABELS[_method(method)]}收款资料尚未配置，请联系客服。")
        conn.execute(
            """INSERT INTO subscription_order_payment_receivers
               (order_no,method,receiver_version,receiver_text,qr_storage_key,qr_sha256,
                qr_telegram_file_id,created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (order_no, method, int(profile.get("version") or 0), profile.get("receiver_text"),
             profile.get("qr_storage_key"), profile.get("qr_sha256"),
             profile.get("qr_telegram_file_id"), _iso()),
        )
        return dict(conn.execute(
            "SELECT * FROM subscription_order_payment_receivers WHERE order_no=?", (order_no,)
        ).fetchone())

    def for_order(self, order_no: str, user_id: int | None = None, pending_only: bool = False) -> dict[str, Any]:
        params: list[Any] = [str(order_no).strip()]
        clauses = ["o.order_no=?"]
        if user_id is not None:
            clauses.append("o.user_id=?")
            params.append(int(user_id))
        if pending_only:
            clauses.append("o.status='pending'")
            clauses.append("(o.expires_at IS NULL OR datetime(o.expires_at)>datetime(?))")
            params.append(_iso())
        with self.db.transaction() as conn:
            order = conn.execute(
                f"SELECT o.* FROM subscription_orders o WHERE {' AND '.join(clauses)}", tuple(params)
            ).fetchone()
            if not order:
                raise PermissionError("订单不存在、已结束或不属于当前用户。")
            snapshot = conn.execute(
                "SELECT * FROM subscription_order_payment_receivers WHERE order_no=?", (order_no,)
            ).fetchone()
            if not snapshot:
                snapshot = self.snapshot_order(conn, str(order_no), str(order["pay_method"]))
            return dict(snapshot)

    def qr_for_order(self, order_no: str, user_id: int, pending_only: bool = True) -> bytes:
        snapshot = self.for_order(order_no, user_id=user_id, pending_only=pending_only)
        if not snapshot.get("qr_storage_key") or not snapshot.get("qr_sha256"):
            raise ValueError("此订单没有收款二维码。")
        return read_receiver_qr(str(snapshot["qr_storage_key"]), str(snapshot["qr_sha256"]))

    def begin_session(self, actor_id: int, chat_id: str, method: str, action: str) -> None:
        self.require_billing_admin(actor_id)
        method = _method(method)
        if action not in {"receiver_text", "qr"}:
            raise ValueError("收款资料操作无效。")
        now = datetime.now(UTC)
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            AdminService._require_billing_in_transaction(conn, int(actor_id))
            conn.execute(
                """INSERT INTO telegram_payment_receiver_sessions
                   (chat_id,user_id,method,action,expires_at,created_at) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(chat_id) DO UPDATE SET user_id=excluded.user_id,method=excluded.method,
                       action=excluded.action,expires_at=excluded.expires_at,created_at=excluded.created_at""",
                (str(chat_id), int(actor_id), method, action, _iso(now + timedelta(minutes=10)), _iso(now)),
            )

    def session(self, actor_id: int, chat_id: str) -> dict[str, Any] | None:
        self.require_billing_admin(actor_id)
        row = self.db.fetch_one(
            """SELECT * FROM telegram_payment_receiver_sessions
               WHERE chat_id=? AND user_id=? AND datetime(expires_at)>datetime(?)""",
            (str(chat_id), int(actor_id), _iso()),
        )
        if row:
            return row
        self.cancel_session(actor_id, chat_id)
        return None

    def cancel_session(self, actor_id: int, chat_id: str) -> None:
        self.require_billing_admin(actor_id)
        self.db.execute(
            "DELETE FROM telegram_payment_receiver_sessions WHERE chat_id=? AND user_id=?",
            (str(chat_id), int(actor_id)),
        )


def payment_profile_public(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": str(snapshot.get("method") or ""),
        "payment_instructions": str(snapshot.get("receiver_text") or ""),
        "payment_qr_available": bool(snapshot.get("qr_storage_key")),
    }
