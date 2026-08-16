"""Canonical owner-scoped notification items, delivery and read events."""

from __future__ import annotations

from contextlib import contextmanager
import re
from typing import Any, Iterator, Mapping

from core.account_center import (
    AccountCenterError, AccountCenterNotFound, DELIVERY_STATES,
    IdempotencyConflict, SEVERITIES, TARGET_KINDS,
    _int, _key, _now, _public, _public_id, request_sha256,
    OPAQUE_ID_RE, _entitlement_result,
    STATIC_ROUTE_TARGET_KINDS, route_public_id,
)
from core.database import DatabaseManager


class NotificationInboxService:
    def __init__(self, database: DatabaseManager | Any, *, appearance_entitlement_resolver: Any = None):
        self.db = database
        self.appearance_entitlement_resolver = appearance_entitlement_resolver

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        if isinstance(self.db, DatabaseManager):
            with self.db.transaction() as conn:
                yield conn
        else:
            try:
                yield self.db
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def _fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if isinstance(self.db, DatabaseManager):
            return self.db.fetch_all(sql, params)
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    @staticmethod
    def _require_owner(conn: Any, owner_id: int) -> None:
        if not conn.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (owner_id,)).fetchone():
            raise AccountCenterNotFound("账户不存在或已停用。")

    @staticmethod
    def _replay(conn: Any, table: str, owner_id: int, key: str, digest: str) -> dict[str, Any] | None:
        row = conn.execute(f"SELECT * FROM {table} WHERE owner_id=? AND idempotency_key=?", (owner_id, key)).fetchone()
        if not row:
            return None
        if row["request_sha256"] != digest:
            raise IdempotencyConflict("相同 Idempotency-Key 不得提交不同请求。")
        return dict(row)

    def create_notification(self, owner_id: int, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        owner_id, key = _int(owner_id, "owner_id"), _key(idempotency_key)
        if not isinstance(payload, Mapping):
            raise AccountCenterError("通知字段无效。")
        source_kind = str(payload.get("source_kind") or "").strip()
        source_public_id = str(payload.get("source_public_id") or "").strip()
        source_version = payload.get("source_version")
        kind, title, body = str(payload.get("kind") or "").strip(), str(payload.get("title") or "").strip(), str(payload.get("body") or "").strip()
        severity = str(payload.get("severity") or "info").lower()
        if (
            not 2 <= len(source_kind) <= 64
            or not OPAQUE_ID_RE.fullmatch(source_public_id)
            or not isinstance(source_version, int)
            or isinstance(source_version, bool)
            or source_version < 1
            or not 2 <= len(kind) <= 64
            or not 1 <= len(title) <= 160
            or not 1 <= len(body) <= 2_000
            or severity not in SEVERITIES
        ):
            raise AccountCenterError("通知字段无效。")
        target = payload.get("target")
        if target is not None:
            if not isinstance(target, Mapping) or set(target) != {"target_kind", "public_id", "version"}:
                raise AccountCenterError("通知 deep link 字段无效。")
            target_kind, target_id, version = target["target_kind"], target["public_id"], target["version"]
            if target_kind not in TARGET_KINDS or not isinstance(target_id, str) or not target_id or not isinstance(version, int) or version < 1:
                raise AccountCenterError("通知 deep link 不受支持。")
            self.resolve_deep_link(owner_id, target_kind, target_id, version, allow_fallback=False)
        normalized = {"source_kind": source_kind, "source_public_id": source_public_id, "source_version": source_version, "kind": kind, "title": title, "body": body, "severity": severity, "target": dict(target) if target is not None else None}
        payload_sha256 = request_sha256(normalized)
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "notification_items", owner_id, key, request_sha256(normalized))
            if replay:
                return _public(replay)
            existing = conn.execute("SELECT public_id,payload_sha256 FROM notification_items WHERE owner_id=? AND source_kind=? AND source_public_id=? AND source_version=?", (owner_id, source_kind, source_public_id, source_version)).fetchone()
            if existing:
                if existing["payload_sha256"] != payload_sha256:
                    raise IdempotencyConflict("通知来源版本已绑定不同 payload。")
                return {"public_id": existing["public_id"]}
            target = target or {}
            public_id = _public_id("ntf")
            conn.execute("""INSERT INTO notification_items
                (public_id,owner_id,source_kind,source_public_id,source_version,payload_sha256,kind,title,body,severity,target_kind,target_public_id,target_version,idempotency_key,request_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (public_id, owner_id, source_kind, source_public_id, source_version, payload_sha256, kind, title, body, severity, target.get("target_kind"), target.get("public_id"), target.get("version"), key, payload_sha256, _now()))
            return {"public_id": public_id}

    create_item = create_notification
    create_notification_item = create_notification

    def create_delivery(self, owner_id: int, item_public_id: str, channel: str, idempotency_key: str) -> dict[str, Any]:
        owner_id, key, channel = _int(owner_id, "owner_id"), _key(idempotency_key), str(channel).strip().lower()
        if not channel or len(channel) > 32:
            raise AccountCenterError("通知渠道无效。")
        digest = request_sha256({"item_public_id": item_public_id, "channel": channel})
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "notification_deliveries", owner_id, key, digest)
            if replay:
                return _public(replay, "item_public_id", "channel")
            if not conn.execute("SELECT 1 FROM notification_items WHERE owner_id=? AND public_id=?", (owner_id, item_public_id)).fetchone():
                raise AccountCenterNotFound("通知不存在。")
            if conn.execute("SELECT 1 FROM notification_deliveries WHERE owner_id=? AND item_public_id=? AND channel=?", (owner_id, item_public_id, channel)).fetchone():
                raise AccountCenterError("通知渠道投递已存在。")
            public_id = _public_id("dly")
            conn.execute("INSERT INTO notification_deliveries (public_id,owner_id,item_public_id,channel,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?,?)", (public_id, owner_id, item_public_id, channel, key, digest, _now()))
            return {"public_id": public_id, "item_public_id": item_public_id, "channel": channel}

    def record_delivery_event(self, owner_id: int, delivery_public_id: str, status: str, idempotency_key: str, error_code: str | None = None) -> dict[str, Any]:
        owner_id, key, status = _int(owner_id, "owner_id"), _key(idempotency_key), str(status).strip().lower()
        if status not in DELIVERY_STATES:
            raise AccountCenterError("投递状态无效。")
        digest = request_sha256({"delivery_public_id": delivery_public_id, "status": status, "error_code": error_code})
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "notification_delivery_events", owner_id, key, digest)
            if replay:
                return _public(replay, "delivery_public_id", "status")
            if not conn.execute("SELECT 1 FROM notification_deliveries WHERE owner_id=? AND public_id=?", (owner_id, delivery_public_id)).fetchone():
                raise AccountCenterNotFound("投递不存在。")
            public_id = _public_id("dle")
            conn.execute("INSERT INTO notification_delivery_events (public_id,owner_id,delivery_public_id,status,error_code,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?,?,?)", (public_id, owner_id, delivery_public_id, status, error_code, key, digest, _now()))
            return {"public_id": public_id, "delivery_public_id": delivery_public_id, "status": status}

    create_delivery_event = record_delivery_event

    def mark_read(self, owner_id: int, item_public_id: str, idempotency_key: str) -> dict[str, Any]:
        owner_id, key = _int(owner_id, "owner_id"), _key(idempotency_key)
        digest = request_sha256({"item_public_id": item_public_id})
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "notification_read_events", owner_id, key, digest)
            if replay:
                return _public(replay, "item_public_id")
            if not conn.execute("SELECT 1 FROM notification_items WHERE owner_id=? AND public_id=?", (owner_id, item_public_id)).fetchone():
                raise AccountCenterNotFound("通知不存在。")
            public_id = _public_id("nrd")
            conn.execute("INSERT INTO notification_read_events (public_id,owner_id,item_public_id,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?)", (public_id, owner_id, item_public_id, key, digest, _now()))
            return {"public_id": public_id, "item_public_id": item_public_id}

    create_read_event = mark_read
    read_notification = mark_read

    def _delivery_state(self, owner_id: int, item_public_id: str) -> list[dict[str, Any]]:
        rows = self._fetch("""SELECT d.public_id,d.channel,e.status,e.error_code FROM notification_deliveries d
            LEFT JOIN notification_delivery_events e ON e.id=(SELECT x.id FROM notification_delivery_events x WHERE x.owner_id=d.owner_id AND x.delivery_public_id=d.public_id ORDER BY x.created_at DESC,x.id DESC LIMIT 1)
            WHERE d.owner_id=? AND d.item_public_id=? ORDER BY d.created_at,d.id""", (owner_id, item_public_id))
        return [{"public_id": r["public_id"], "channel": r["channel"], "status": r["status"] or "queued", "error_code": r["error_code"]} for r in rows]

    def list_notifications(self, owner_id: int) -> list[dict[str, Any]]:
        owner_id = _int(owner_id, "owner_id")
        rows = self._fetch("SELECT public_id,source_kind,source_public_id,source_version,kind,title,body,severity,target_kind,target_public_id,target_version,created_at FROM notification_items WHERE owner_id=? ORDER BY created_at DESC,id DESC", (owner_id,))
        for row in rows:
            target_kind, target_id, version = row.pop("target_kind"), row.pop("target_public_id"), row.pop("target_version")
            row["target"] = {"target_kind": target_kind, "public_id": target_id, "version": version} if target_kind else None
            row["read"] = bool(self._fetch("SELECT 1 FROM notification_read_events WHERE owner_id=? AND item_public_id=? LIMIT 1", (owner_id, row["public_id"])))
            row["delivery"] = self._delivery_state(owner_id, row["public_id"])
        return rows

    def resolve_deep_link(self, owner_id: int, target_kind: str, public_id: str, version: int, *, allow_fallback: bool = True) -> dict[str, Any]:
        owner_id, version = _int(owner_id, "owner_id"), _int(version, "version")
        if target_kind not in TARGET_KINDS or not isinstance(public_id, str) or not OPAQUE_ID_RE.fullmatch(public_id):
            raise AccountCenterNotFound("deep link 不受支持。")
        now = _now()
        if target_kind == "content":
            valid = bool(self._fetch("SELECT 1 FROM account_content_index WHERE owner_id=? AND public_id=? AND content_version=? AND (expires_at IS NULL OR julianday(expires_at)>julianday(?))", (owner_id, public_id, version, now)))
        elif target_kind == "memory":
            valid = version == 1 and bool(self._fetch("""SELECT 1 FROM account_memory_entries m WHERE m.owner_id=? AND m.public_id=? AND (m.expires_at IS NULL OR julianday(m.expires_at)>julianday(?)) AND NOT EXISTS (SELECT 1 FROM account_memory_tombstone_events t WHERE t.owner_id=m.owner_id AND t.memory_public_id=m.public_id)""", (owner_id, public_id, now)))
        elif target_kind == "appearance":
            valid = bool(self._fetch("""SELECT 1 FROM account_appearance_manifests
                WHERE public_id=? AND (owner_id IS NULL OR owner_id=?)
                  AND (asset_version=? OR asset_version=?)""", (public_id, owner_id, str(version), f"v{version}")))
            if valid:
                resolver = self.appearance_entitlement_resolver
                if resolver is None:
                    valid = False
                else:
                    row = self._fetch("SELECT skin_id,asset_version,manifest_sha256 FROM account_appearance_manifests WHERE public_id=?", (public_id,))[0]
                    valid = _entitlement_result(resolver(owner_id, row["skin_id"], row["asset_version"], row["manifest_sha256"]))[0]
        elif target_kind == "notifications":
            valid = bool(self._fetch("SELECT 1 FROM notification_items WHERE owner_id=? AND public_id=? AND source_version=?", (owner_id, public_id, version)))
        elif target_kind in STATIC_ROUTE_TARGET_KINDS:
            valid = version == 1 and public_id == route_public_id(target_kind)
        else:
            valid = False
        if valid:
            return {"target_kind": target_kind, "public_id": public_id, "version": version, "stale": False}
        if allow_fallback:
            return {"target_kind": "notifications", "public_id": None, "version": 1, "stale": True}
        raise AccountCenterNotFound("deep link 已失效或不属于当前账户。")

    def resolve_notification(self, owner_id: int, notification_public_id: str) -> dict[str, Any]:
        """Resolve the immutable target stored on an owner-scoped notification.

        The caller supplies only the notification id.  Target fields are never
        trusted from the browser; they are read from the owner-scoped row and
        revalidated against the current account state.
        """
        owner_id = _int(owner_id, "owner_id")
        if not isinstance(notification_public_id, str) or not re.fullmatch(r"ntf_[A-Za-z0-9_-]{24}", notification_public_id):
            raise AccountCenterNotFound("通知不存在。")
        rows = self._fetch(
            "SELECT target_kind,target_public_id,target_version FROM notification_items WHERE owner_id=? AND public_id=?",
            (owner_id, notification_public_id),
        )
        if not rows:
            raise AccountCenterNotFound("通知不存在。")
        row = rows[0]
        target_kind, public_id, version = row["target_kind"], row["target_public_id"], row["target_version"]
        if not target_kind or public_id is None or version is None:
            return {"route": "/notifications", "locator": None, "stale": True}
        try:
            resolved = self.resolve_deep_link(owner_id, target_kind, public_id, version, allow_fallback=False)
        except AccountCenterNotFound:
            return {"route": "/notifications", "locator": None, "stale": True}
        route = {
            "account": "/account", "settings": "/account", "content": "/account", "memory": "/account", "appearance": "/account",
            "membership": "/membership", "orders": "/membership", "payments": "/membership", "payment": "/membership",
            "notifications": "/notifications", "today": "/today", "discover": "/discover", "research": "/research",
            "paper": "/paper", "portfolio": "/portfolio", "reports": "/reports", "trade": "/trade",
        }.get(resolved["target_kind"])
        if route is None:
            return {"route": "/notifications", "locator": None, "stale": True}
        return {
            "route": route,
            "locator": {"kind": resolved["target_kind"], "public_id": resolved["public_id"], "version": resolved["version"]},
            "stale": False,
        }


__all__ = ["NotificationInboxService"]
