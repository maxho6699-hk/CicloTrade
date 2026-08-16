"""Governed eligibility applications for the five launch US brokers.

Approval grants product eligibility only.  This module deliberately never
creates ``broker_accounts``, enables execution, sends Telegram, or calls a
broker network API.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
import secrets
from typing import Any

from core.admin_service import AdminService
from core.broker_catalog import US_LAUNCH_BROKER_CATALOG
from core.compat import UTC
from core.database import DatabaseManager, get_database
from core.entitlement_consumer import verified_can
from core.membership import authoritative_membership_row


CANONICAL_PROVIDERS = frozenset(entry.key for entry in US_LAUNCH_BROKER_CATALOG)
ELIGIBLE_PLANS = frozenset({"高级版"})
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9._:-]{8,128}")
_PUBLIC_ID_RE = re.compile(r"bra_[A-Za-z0-9_-]{16,48}")
_LIST_STATUSES = frozenset({"submitted", "approved", "rejected", "withdrawn", "revoked", "expired"})


class BrokerAccessApplicationError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat(timespec="seconds")


def _text(value: Any, name: str, maximum: int, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or value != value.strip() or "\x00" in value:
        raise BrokerAccessApplicationError(f"{name}格式无效。")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise BrokerAccessApplicationError(f"{name}格式无效。")
    if (required and not value) or len(value) > maximum:
        raise BrokerAccessApplicationError(f"{name}长度无效。")
    return value or None


def _provider(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or value not in CANONICAL_PROVIDERS:
        raise BrokerAccessApplicationError("当前仅接受首期五家美股券商资格申请。")
    return value


def _fingerprint(provider: str, reason: str | None) -> str:
    body = json.dumps(
        {"provider": provider, "request_reason": reason},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _public(row: Any) -> dict[str, Any]:
    value = dict(row)
    return {
        "id": str(value["public_id"]),
        "provider": str(value["provider"]),
        "status": str(value["status"]),
        "request_reason": value.get("request_reason"),
        "decision_reason": value.get("decision_reason"),
        "created_at": str(value["created_at"]),
        "updated_at": str(value["updated_at"]),
        "reviewed_at": value.get("reviewed_at"),
        "withdrawn_at": value.get("withdrawn_at"),
        "eligibility_only": True,
        "broker_account_created": False,
        "execution_enabled": False,
    }


def _admin_public(row: Any) -> dict[str, Any]:
    item = _public(row)
    value = dict(row)
    item.update(
        user_id=int(value["user_id"]),
        user_email=str(value.get("user_email") or ""),
        user_display_name=str(value.get("user_display_name") or ""),
        reviewed_by=int(value["reviewed_by"]) if value.get("reviewed_by") is not None else None,
        reviewer_email=str(value.get("reviewer_email") or "") or None,
    )
    return item


class BrokerAccessApplicationService:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    @staticmethod
    def _readiness(conn: Any, user_id: int) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id,plan_type,subscription_expire,is_active FROM users WHERE id=?",
            (int(user_id),),
        ).fetchone()
        if not row or not bool(row["is_active"]):
            return {
                "can_apply": False,
                "membership_eligible": False,
                "telegram_ready": False,
                "requires_telegram": True,
                "providers": sorted(CANONICAL_PROVIDERS),
                "reason": "账户不可用。",
                "eligibility_only": True,
                "broker_account_created": False,
                "execution_enabled": False,
            }
        membership = authoritative_membership_row(conn, row)
        membership_eligible = verified_can(
            conn,
            str(membership.get("plan_type") or "免费版"),
            "broker_access_apply",
        )
        telegram = conn.execute(
            """SELECT 1
               FROM telegram_accounts t
               JOIN user_settings s ON s.user_id=t.user_id
               WHERE t.user_id=?
                 AND t.is_active=1
                 AND t.revoked_at IS NULL
                 AND json_extract(s.settings_json,'$.telegram.verified')=1
                 AND json_extract(s.settings_json,'$.telegram.consent')=1""",
            (int(user_id),),
        ).fetchone()
        telegram_ready = bool(telegram)
        reason = None
        if not membership_eligible:
            reason = "当前会员策略不允许申请实盘券商资格。"
        elif not telegram_ready:
            reason = "申请前必须绑定、验证并同意通知的 Telegram 账户。"
        return {
            "can_apply": membership_eligible and telegram_ready,
            "membership_eligible": membership_eligible,
            "telegram_ready": telegram_ready,
            "requires_telegram": True,
            "providers": sorted(CANONICAL_PROVIDERS),
            "reason": reason,
            "eligibility_only": True,
            "broker_account_created": False,
            "execution_enabled": False,
        }

    @staticmethod
    def _eligible_user(conn: Any, user_id: int) -> None:
        readiness = BrokerAccessApplicationService._readiness(conn, user_id)
        if not readiness["can_apply"]:
            raise BrokerAccessApplicationError(str(readiness["reason"]), 403)

    def readiness(self, user_id: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            return self._readiness(conn, user_id)

    def create(self, user_id: int, payload: Any, idempotency_key: Any) -> tuple[dict[str, Any], bool]:
        if not isinstance(payload, dict) or set(payload) - {"provider", "request_reason"} or "provider" not in payload:
            raise BrokerAccessApplicationError("申请字段不完整或包含未知字段。")
        provider = _provider(payload.get("provider"))
        reason = _text(payload.get("request_reason"), "申请原因", 500)
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise BrokerAccessApplicationError("Idempotency-Key 必须为 8 至 128 个安全字符。")
        fingerprint = _fingerprint(provider, reason)
        now = _iso()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._eligible_user(conn, user_id)
            existing = conn.execute(
                "SELECT * FROM broker_access_applications WHERE user_id=? AND idempotency_key=?",
                (int(user_id), idempotency_key),
            ).fetchone()
            if existing:
                if str(existing["request_fingerprint"]) != fingerprint:
                    raise BrokerAccessApplicationError("Idempotency-Key 已用于不同申请。", 409)
                return _public(existing), True
            pending = conn.execute(
                """SELECT * FROM broker_access_applications
                   WHERE user_id=? AND provider=? AND status IN ('submitted','approved')""",
                (int(user_id), provider),
            ).fetchone()
            if pending:
                message = (
                    "该券商资格已经审核通过。"
                    if str(pending["status"]) == "approved"
                    else "该券商已有待审核资格申请。"
                )
                raise BrokerAccessApplicationError(message, 409)
            public_id = "bra_" + secrets.token_urlsafe(18)
            conn.execute(
                """INSERT INTO broker_access_applications
                   (public_id,user_id,provider,status,idempotency_key,request_fingerprint,
                    request_reason,created_at,updated_at)
                   VALUES (?,?,?,'submitted',?,?,?,?,?)""",
                (public_id, int(user_id), provider, idempotency_key, fingerprint, reason, now, now),
            )
            row = conn.execute(
                "SELECT * FROM broker_access_applications WHERE public_id=?", (public_id,)
            ).fetchone()
        return _public(row), False

    def list_for_user(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT * FROM broker_access_applications
               WHERE user_id=? ORDER BY id DESC LIMIT ?""",
            (int(user_id), max(1, min(int(limit), 100))),
        )
        return [_public(row) for row in rows]

    def withdraw(self, user_id: int, public_id: Any) -> dict[str, Any]:
        if not isinstance(public_id, str) or not _PUBLIC_ID_RE.fullmatch(public_id):
            raise BrokerAccessApplicationError("资格申请编号无效。", 404)
        now = _iso()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """UPDATE broker_access_applications
                   SET status='withdrawn',withdrawn_at=?,updated_at=?
                   WHERE public_id=? AND user_id=? AND status='submitted'""",
                (now, now, public_id, int(user_id)),
            )
            row = conn.execute(
                "SELECT * FROM broker_access_applications WHERE public_id=? AND user_id=?",
                (public_id, int(user_id)),
            ).fetchone()
            if not row:
                raise BrokerAccessApplicationError("资格申请不存在。", 404)
            if changed.rowcount != 1:
                if str(row["status"]) == "withdrawn":
                    return _public(row)
                raise BrokerAccessApplicationError("仅待审核申请可以撤回。", 409)
        return _public(row)

    def list_for_admin(self, actor_id: int, status: Any = "submitted", limit: int = 100) -> list[dict[str, Any]]:
        actor = self.db.fetch_one(
            """SELECT u.is_admin,u.is_active,r.role FROM users u
               LEFT JOIN admin_roles r ON r.user_id=u.id WHERE u.id=?""",
            (int(actor_id),),
        )
        if (
            not actor
            or not bool(actor["is_admin"])
            or not bool(actor["is_active"])
            or actor.get("role") != "super_admin"
        ):
            raise BrokerAccessApplicationError("仅超级管理员可查看资格申请。", 403)
        if status not in _LIST_STATUSES:
            raise BrokerAccessApplicationError("申请状态筛选无效。")
        rows = self.db.fetch_all(
            """SELECT a.*,u.email user_email,u.display_name user_display_name,
                      reviewer.email reviewer_email
               FROM broker_access_applications a JOIN users u ON u.id=a.user_id
               LEFT JOIN users reviewer ON reviewer.id=a.reviewed_by
               WHERE a.status=? ORDER BY a.id LIMIT ?""",
            (status, max(1, min(int(limit), 200))),
        )
        return [_admin_public(row) for row in rows]

    def review(self, actor_id: int, public_id: Any, payload: Any) -> dict[str, Any]:
        if not isinstance(public_id, str) or not _PUBLIC_ID_RE.fullmatch(public_id):
            raise BrokerAccessApplicationError("资格申请编号无效。", 404)
        if not isinstance(payload, dict) or set(payload) != {"decision", "reason"}:
            raise BrokerAccessApplicationError("审核字段必须只包含 decision 与 reason。")
        decision = payload.get("decision")
        if decision not in {"approved", "rejected"}:
            raise BrokerAccessApplicationError("审核决定必须为 approved 或 rejected。")
        reason = _text(payload.get("reason"), "审核原因", 500, required=True)
        now = _iso()
        with self.db.transaction() as conn:
            conn.execute("BEGIN IMMEDIATE")
            AdminService._require_super_admin_in_transaction(conn, int(actor_id))
            row = conn.execute(
                "SELECT * FROM broker_access_applications WHERE public_id=?", (public_id,)
            ).fetchone()
            if not row:
                raise BrokerAccessApplicationError("资格申请不存在。", 404)
            if int(row["user_id"]) == int(actor_id):
                raise BrokerAccessApplicationError("管理员不能审核自己的资格申请。", 403)
            if decision == "approved":
                self._eligible_user(conn, int(row["user_id"]))
            changed = conn.execute(
                """UPDATE broker_access_applications
                   SET status=?,decision_reason=?,reviewed_by=?,reviewed_at=?,updated_at=?
                   WHERE public_id=? AND status='submitted'""",
                (decision, reason, int(actor_id), now, now, public_id),
            )
            if changed.rowcount != 1:
                raise BrokerAccessApplicationError("该资格申请已由其他管理员处理。", 409)
            AdminService._audit(
                conn,
                int(actor_id),
                "ADMIN_BROKER_ACCESS_APPLICATION_REVIEW",
                {"application_id": public_id, "provider": str(row["provider"]), "decision": decision, "reason": reason},
            )
            updated = conn.execute(
                """SELECT a.*,u.email user_email,u.display_name user_display_name,
                          reviewer.email reviewer_email
                   FROM broker_access_applications a JOIN users u ON u.id=a.user_id
                   LEFT JOIN users reviewer ON reviewer.id=a.reviewed_by
                   WHERE a.public_id=?""",
                (public_id,),
            ).fetchone()
        return _admin_public(updated)
__all__ = [
    "BrokerAccessApplicationError",
    "BrokerAccessApplicationService",
    "CANONICAL_PROVIDERS",
    "ELIGIBLE_PLANS",
]
