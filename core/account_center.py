"""Owner-scoped append-only Account and Notification inbox services."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import re
import secrets
from typing import Any, Iterator, Mapping

from core.compat import UTC
from core.database import DatabaseManager


class AccountCenterError(ValueError):
    pass


class AccountCenterNotFound(AccountCenterError):
    pass


class IdempotencyConflict(AccountCenterError):
    pass


TARGET_KINDS = frozenset({
    "account", "membership", "settings", "notifications", "today", "discover",
    "research", "paper", "portfolio", "reports", "trade", "orders", "payments", "payment", "content", "memory", "appearance",
})
STATIC_ROUTE_TARGET_KINDS = frozenset({
    "account", "membership", "settings", "today", "discover", "research",
    "paper", "portfolio", "reports", "trade", "orders", "payments", "payment",
})
SEVERITIES = frozenset({"info", "success", "warning", "error"})
DELIVERY_STATES = frozenset({"queued", "sending", "sent", "delivered", "failed", "skipped"})
OPAQUE_ID_RE = re.compile(r"[a-z][a-z0-9]{1,15}_[A-Za-z0-9_-]{24}\Z")


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AccountCenterError("请求必须是有限 JSON。") from exc


def request_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def route_public_id(target_kind: str) -> str:
    """Return the immutable opaque id for a static authenticated route."""
    kind = str(target_kind).strip().lower()
    if kind not in STATIC_ROUTE_TARGET_KINDS:
        raise AccountCenterError("通知页面目标不受支持。")
    return "route_" + hashlib.sha256(f"ciclotrade-route:{kind}:v1".encode()).hexdigest()[:24]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _public(row: Mapping[str, Any], *extra: str) -> dict[str, Any]:
    result = {"public_id": row["public_id"]}
    for name in extra:
        if name in row:
            result[name] = row[name]
    return result


def _entitlement_result(result: Any) -> tuple[bool, int]:
    if isinstance(result, dict):
        return bool(result.get("allowed")), int(result.get("rank", 1))
    if isinstance(result, (tuple, list)) and len(result) == 2:
        return bool(result[0]), int(result[1])
    if isinstance(result, int) and not isinstance(result, bool):
        return result > 0, result
    return bool(result), 1


def _key(value: Any) -> str:
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 128:
        raise AccountCenterError("Idempotency-Key 必须为 8 至 128 个字符。")
    return value.strip()


def _int(value: Any, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AccountCenterError(f"{name} 无效。")
    return value


def _json(value: Any, name: str) -> str:
    if not isinstance(value, (dict, list)):
        raise AccountCenterError(f"{name} 必须是 JSON 对象或数组。")
    return canonical_json(value)


class AccountCenterService:
    """Persistence boundary.  It never returns SQLite integer primary keys."""

    def __init__(self, database: DatabaseManager | Any | None = None, *, appearance_entitlement_resolver: Any = None):
        if database is None:
            raise AccountCenterError("AccountCenterService 需要显式 database。")
        self.db = database
        self.appearance_entitlement_resolver = appearance_entitlement_resolver

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        if isinstance(self.db, DatabaseManager):
            with self.db.transaction() as conn:
                yield conn
        else:
            # Useful for migration tests that supply a raw sqlite connection.
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
    def _table(conn: Any, name: str) -> bool:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    @staticmethod
    def _require_owner(conn: Any, owner_id: int) -> None:
        if not conn.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (owner_id,)).fetchone():
            raise AccountCenterNotFound("账户不存在或已停用。")

    @staticmethod
    def _replay(conn: Any, table: str, owner_col: str | None, owner_id: int | None, key: str, digest: str) -> dict[str, Any] | None:
        where = "idempotency_key=?"
        args: list[Any] = [key]
        if owner_col:
            if owner_id is None:
                where = f"{owner_col} IS NULL AND {where}"
            else:
                where = f"{owner_col}=? AND {where}"
                args.insert(0, owner_id)
        row = conn.execute(f"SELECT * FROM {table} WHERE {where}", tuple(args)).fetchone()
        if not row:
            return None
        if row["request_sha256"] != digest:
            raise IdempotencyConflict("相同 Idempotency-Key 不得提交不同请求。")
        return dict(row)

    @staticmethod
    def _public(row: Mapping[str, Any], *extra: str) -> dict[str, Any]:
        result = {"public_id": row["public_id"]}
        for name in extra:
            if name in row:
                result[name] = row[name]
        return result

    def register_manifest(self, manifest: Mapping[str, Any], idempotency_key: str, actor_id: int | None = None) -> dict[str, Any]:
        key = _key(idempotency_key)
        if not isinstance(manifest, Mapping):
            raise AccountCenterError("外观 manifest 无效。")
        raw = dict(manifest)
        manifest_key = str(raw.get("manifest_key") or "ciclo").strip()
        skin_id = str(raw.get("skin_id") or "").strip()
        asset_version = str(raw.get("asset_version") or "").strip()
        supplied = str(raw.get("manifest_sha256") or "").lower()
        if not manifest_key or not skin_id or not asset_version or len(supplied) != 64:
            raise AccountCenterError("外观 manifest 三元组或 hash 不完整。")
        material = dict(raw)
        material.pop("manifest_sha256", None)
        digest = request_sha256(material)
        if supplied != digest:
            raise AccountCenterError("外观 manifest hash 不匹配。")
        raw["manifest_sha256"] = supplied
        req_digest = request_sha256(raw)
        encoded = canonical_json(raw)
        with self._transaction() as conn:
            replay = self._replay(conn, "account_appearance_manifests", "created_by", actor_id, key, req_digest)
            if replay:
                return self._public(replay, "skin_id", "asset_version", "manifest_sha256")
            existing = conn.execute(
                "SELECT * FROM account_appearance_manifests WHERE skin_id=? AND asset_version=?",
                (skin_id, asset_version),
            ).fetchone()
            if existing:
                if existing["manifest_sha256"] != supplied:
                    raise AccountCenterError("同一外观三元组的历史 manifest 已冻结。")
                return self._public(dict(existing), "skin_id", "asset_version", "manifest_sha256")
            public_id = _public_id("man")
            conn.execute(
                """INSERT INTO account_appearance_manifests
                   (public_id,manifest_key,skin_id,asset_version,manifest_json,manifest_sha256,idempotency_key,request_sha256,created_by,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (public_id, manifest_key, skin_id, asset_version, encoded, supplied, key, req_digest, actor_id, _now()),
            )
            return {"public_id": public_id, "skin_id": skin_id, "asset_version": asset_version, "manifest_sha256": supplied}

    create_manifest = register_manifest

    def select_appearance(self, owner_id: int, manifest_public_id: str, idempotency_key: str) -> dict[str, Any]:
        owner_id = _int(owner_id, "owner_id")
        key = _key(idempotency_key)
        payload = {"manifest_public_id": manifest_public_id}
        digest = request_sha256(payload)
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "account_appearance_selection_events", "owner_id", owner_id, key, digest)
            if replay:
                return self._public(replay, "manifest_public_id", "skin_id", "asset_version")
            manifest = conn.execute("SELECT * FROM account_appearance_manifests WHERE public_id=?", (manifest_public_id,)).fetchone()
            if not manifest:
                raise AccountCenterNotFound("外观 manifest 不存在。")
            resolver = self.appearance_entitlement_resolver
            if resolver is None or not _entitlement_result(resolver(owner_id, manifest["skin_id"], manifest["asset_version"], manifest["manifest_sha256"]))[0]:
                raise AccountCenterError("外观 entitlement 尚未配置，当前选择已锁定。")
            public_id = _public_id("sel")
            conn.execute(
                """INSERT INTO account_appearance_selection_events
                   (public_id,owner_id,manifest_public_id,skin_id,asset_version,manifest_sha256,idempotency_key,request_sha256,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (public_id, owner_id, manifest_public_id, manifest["skin_id"], manifest["asset_version"], manifest["manifest_sha256"], key, digest, _now()),
            )
            return {"public_id": public_id, "manifest_public_id": manifest_public_id, "skin_id": manifest["skin_id"], "asset_version": manifest["asset_version"]}

    select_manifest = select_appearance

    def current_appearance(self, owner_id: int) -> dict[str, Any]:
        owner_id = _int(owner_id, "owner_id")
        rows = self._fetch(
            """SELECT s.public_id selection_public_id,m.public_id,m.skin_id,m.asset_version,m.manifest_sha256,m.owner_id
               FROM account_appearance_selection_events s JOIN account_appearance_manifests m ON m.public_id=s.manifest_public_id
               WHERE s.owner_id=? ORDER BY s.created_at DESC,s.id DESC""", (owner_id,),
        )
        resolver = self.appearance_entitlement_resolver
        if resolver is not None:
            for row in rows:
                if _entitlement_result(resolver(owner_id, row["skin_id"], row["asset_version"], row["manifest_sha256"]))[0]:
                    return {"public_id": row["public_id"], "skin_id": row["skin_id"], "asset_version": row["asset_version"], "manifest_sha256": row["manifest_sha256"], "source": "selected"}
            manifests = self._fetch("SELECT public_id,skin_id,asset_version,manifest_sha256 FROM account_appearance_manifests WHERE owner_id IS NULL OR owner_id=? ORDER BY created_at DESC,id DESC", (owner_id,))
            best: tuple[int, dict[str, Any]] | None = None
            for row in manifests:
                allowed, rank = _entitlement_result(resolver(owner_id, row["skin_id"], row["asset_version"], row["manifest_sha256"]))
                if allowed and (best is None or rank > best[0]):
                    best = (rank, row)
            if best is not None:
                row = best[1]
                return {**row, "source": "fallback"}
        return {"public_id": None, "skin_id": None, "asset_version": None, "manifest_sha256": None, "source": "unavailable"}

    def list_appearances(self, owner_id: int) -> list[dict[str, Any]]:
        """List only manifests visible to this owner with server-resolved entitlement."""
        owner_id = _int(owner_id, "owner_id")
        rows = self._fetch(
            """SELECT public_id,skin_id,asset_version,manifest_sha256,manifest_json,created_at
               FROM account_appearance_manifests
               WHERE owner_id IS NULL OR owner_id=? ORDER BY created_at,id""",
            (owner_id,),
        )
        resolver = self.appearance_entitlement_resolver
        result: list[dict[str, Any]] = []
        for row in rows:
            allowed, rank = _entitlement_result(
                resolver(owner_id, row["skin_id"], row["asset_version"], row["manifest_sha256"])
                if resolver is not None else False
            )
            manifest = json.loads(str(row["manifest_json"]))
            result.append({
                "public_id": row["public_id"],
                "skin_id": row["skin_id"],
                "asset_version": row["asset_version"],
                "manifest_sha256": row["manifest_sha256"],
                "assets": manifest.get("assets", {}),
                "entitled": allowed,
                "rank": rank,
                "created_at": row["created_at"],
            })
        return result

    def index_content(self, owner_id: int, content_key: str, content_version: int, content: Any, idempotency_key: str, expires_at: str | None = None) -> dict[str, Any]:
        owner_id, version, key = _int(owner_id, "owner_id"), _int(content_version, "content_version"), _key(idempotency_key)
        content_key = str(content_key).strip()
        if not content_key or len(content_key) > 128:
            raise AccountCenterError("content_key 无效。")
        if not isinstance(content, Mapping):
            raise AccountCenterError("content 必须是 JSON 对象。")
        encoded = canonical_json(content)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        req = request_sha256({"content_key": content_key, "content_version": version, "content": json.loads(encoded), "expires_at": expires_at})
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "account_content_index", "owner_id", owner_id, key, req)
            if replay:
                return self._public(replay, "content_key", "content_version")
            existing = conn.execute("SELECT public_id FROM account_content_index WHERE owner_id=? AND content_key=? AND content_version=?", (owner_id, content_key, version)).fetchone()
            if existing:
                raise AccountCenterError("同一 content_key 的版本号已存在。")
            public_id = _public_id("cnt")
            conn.execute("""INSERT INTO account_content_index
                (public_id,owner_id,content_key,content_version,content_json,content_sha256,expires_at,idempotency_key,request_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (public_id, owner_id, content_key, version, encoded, digest, expires_at, key, req, _now()))
            return {"public_id": public_id, "content_key": content_key, "content_version": version}

    create_content = index_content

    def list_content(self, owner_id: int, now: str | None = None) -> list[dict[str, Any]]:
        owner_id = _int(owner_id, "owner_id")
        now = now or _now()
        rows = self._fetch(
            """SELECT public_id,content_key,content_version,content_json,content_sha256,expires_at,created_at
               FROM account_content_index
               WHERE owner_id=? AND (expires_at IS NULL OR julianday(expires_at)>julianday(?))
               ORDER BY created_at DESC,id DESC""",
            (owner_id, now),
        )
        return [
            {
                **{key: value for key, value in row.items() if key != "content_json"},
                "content": json.loads(str(row["content_json"])),
            }
            for row in rows
        ]

    def put_memory(self, owner_id: int, memory_key: str, value: Any, idempotency_key: str, expires_at: str | None = None) -> dict[str, Any]:
        owner_id, key = _int(owner_id, "owner_id"), _key(idempotency_key)
        memory_key = str(memory_key).strip()
        if not memory_key or len(memory_key) > 128:
            raise AccountCenterError("memory_key 无效。")
        encoded = _json(value, "memory")
        req = request_sha256({"memory_key": memory_key, "value": json.loads(encoded), "expires_at": expires_at})
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "account_memory_entries", "owner_id", owner_id, key, req)
            if replay:
                return self._public(replay, "memory_key", "expires_at")
            existing = conn.execute("""SELECT m.public_id FROM account_memory_entries m
                WHERE m.owner_id=? AND m.memory_key=? AND NOT EXISTS (
                  SELECT 1 FROM account_memory_tombstone_events t
                  WHERE t.owner_id=m.owner_id AND t.memory_public_id=m.public_id
                ) ORDER BY m.created_at DESC,m.id DESC LIMIT 1""", (owner_id, memory_key)).fetchone()
            if existing:
                raise AccountCenterError("同一 memory_key 只允许一个可控记忆版本。")
            public_id = _public_id("mem")
            conn.execute("""INSERT INTO account_memory_entries
                (public_id,owner_id,memory_key,memory_json,expires_at,idempotency_key,request_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?)""", (public_id, owner_id, memory_key, encoded, expires_at, key, req, _now()))
            return {"public_id": public_id, "memory_key": memory_key, "expires_at": expires_at}

    create_memory = put_memory

    def list_memories(self, owner_id: int, now: str | None = None) -> list[dict[str, Any]]:
        owner_id = _int(owner_id, "owner_id")
        now = now or _now()
        return self._fetch("""SELECT m.public_id,m.memory_key,m.memory_json,m.expires_at,m.created_at
            FROM account_memory_entries m WHERE m.owner_id=? AND (m.expires_at IS NULL OR julianday(m.expires_at)>julianday(?))
            AND NOT EXISTS (SELECT 1 FROM account_memory_tombstone_events t WHERE t.owner_id=m.owner_id AND t.memory_public_id=m.public_id)
            ORDER BY m.created_at DESC,m.id DESC""", (owner_id, now))

    def tombstone_memory(self, owner_id: int, memory_public_id: str, reason: str, idempotency_key: str) -> dict[str, Any]:
        owner_id, key = _int(owner_id, "owner_id"), _key(idempotency_key)
        reason = str(reason).strip()
        if not reason or len(reason) > 256:
            raise AccountCenterError("记忆撤销原因无效。")
        req = request_sha256({"memory_public_id": memory_public_id, "reason": reason})
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            replay = self._replay(conn, "account_memory_tombstone_events", "owner_id", owner_id, key, req)
            if replay:
                return self._public(replay, "memory_public_id")
            memory = conn.execute("SELECT public_id FROM account_memory_entries WHERE owner_id=? AND public_id=?", (owner_id, memory_public_id)).fetchone()
            if not memory:
                raise AccountCenterNotFound("记忆不存在。")
            public_id = _public_id("mtr")
            conn.execute("""INSERT INTO account_memory_tombstone_events
                (public_id,owner_id,memory_public_id,reason,idempotency_key,request_sha256,created_at)
                VALUES (?,?,?,?,?,?,?)""", (public_id, owner_id, memory_public_id, reason, key, req, _now()))
            return {"public_id": public_id, "memory_public_id": memory_public_id}

    delete_memory = tombstone_memory

    def authorize_data(self, owner_id: int, data_kind: str, scope: Any, action: str, idempotency_key: str, *, policy_type: str = "account_data", policy_version: int = 1, policy_sha256: str | None = None, request_identity: str | None = None) -> dict[str, Any]:
        owner_id, key = _int(owner_id, "owner_id"), _key(idempotency_key)
        data_kind, action = str(data_kind).strip(), str(action).strip().lower()
        action = {"grant": "granted", "revoke": "revoked"}.get(action, action)
        requested_policy_type = str(policy_type).strip()
        request_identity = str(request_identity or f"user:{owner_id}").strip()
        requested_policy_version = _int(policy_version, "policy_version")
        if policy_sha256 is not None:
            raise AccountCenterError("数据授权政策由服务端发布，客户端不得覆盖 hash。")
        if not data_kind or requested_policy_type != "account_data" or action not in {"granted", "revoked"} or not request_identity:
            raise AccountCenterError("数据授权字段无效。")
        with self._transaction() as conn:
            self._require_owner(conn, owner_id)
            if not self._table(conn, "account_data_policy_versions"):
                raise AccountCenterError("数据授权政策尚未发布。")
            policy = conn.execute(
                """SELECT policy_type,data_kind,policy_version,policy_json,policy_sha256
                   FROM account_data_policy_versions
                   WHERE policy_type='account_data' AND data_kind=? AND status='published'
                   ORDER BY policy_version DESC LIMIT 1""",
                (data_kind,),
            ).fetchone()
            if not policy or int(policy["policy_version"]) != requested_policy_version:
                raise AccountCenterError("数据授权政策尚未发布或版本已变化。")
            policy_body = json.loads(str(policy["policy_json"]))
            schema = policy_body.get("scope_schema") if isinstance(policy_body, dict) else None
            allowed_pages = schema.get("allowed_pages") if isinstance(schema, dict) else None
            if not isinstance(scope, Mapping) or set(scope) != {"pages"} or not isinstance(scope["pages"], list):
                raise AccountCenterError("数据授权范围必须只包含 pages。")
            pages = scope["pages"]
            if (
                not pages
                or len(pages) > 20
                or not isinstance(allowed_pages, list)
                or any(not isinstance(page, str) or page not in allowed_pages for page in pages)
                or len(set(pages)) != len(pages)
            ):
                raise AccountCenterError("数据授权页面范围无效。")
            encoded = canonical_json({"pages": pages})
            policy_type = str(policy["policy_type"])
            policy_version = int(policy["policy_version"])
            resolved_policy_sha256 = str(policy["policy_sha256"])
            if request_sha256(policy_body) != resolved_policy_sha256:
                raise AccountCenterError("数据授权政策完整性校验失败。")
            req = request_sha256({"data_kind": data_kind, "policy_type": policy_type, "policy_version": policy_version, "policy_sha256": resolved_policy_sha256, "scopes": json.loads(encoded), "action": action, "request_identity": request_identity})
            replay = self._replay(conn, "account_data_authorization_receipts", "owner_id", owner_id, key, req)
            if replay:
                return self._public(replay, "data_kind", "policy_type", "policy_version", "action")
            public_id = _public_id("auth")
            conn.execute("""INSERT INTO account_data_authorization_receipts
                (public_id,owner_id,data_kind,policy_type,policy_version,policy_sha256,scope_json,scopes_json,action,request_identity,idempotency_key,request_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (public_id, owner_id, data_kind, policy_type, policy_version, resolved_policy_sha256, encoded, encoded, action, request_identity, key, req, _now()))
            return {"public_id": public_id, "data_kind": data_kind, "policy_type": policy_type, "policy_version": policy_version, "action": action}

    record_authorization = authorize_data

    def authorization_status(self, owner_id: int, data_kind: str) -> dict[str, Any]:
        owner_id = _int(owner_id, "owner_id")
        policy_table = self._fetch(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_data_policy_versions'"
        )
        current = self._fetch(
            """SELECT policy_version,policy_json,policy_sha256 FROM account_data_policy_versions
               WHERE policy_type='account_data' AND data_kind=? AND status='published'
               ORDER BY policy_version DESC LIMIT 1""",
            (data_kind,),
        ) if policy_table else []
        rows = self._fetch("SELECT public_id,action,scope_json,policy_version,policy_sha256,created_at FROM account_data_authorization_receipts WHERE owner_id=? AND data_kind=? ORDER BY created_at DESC,id DESC LIMIT 1", (owner_id, data_kind))
        if not rows:
            return {"data_kind": data_kind, "authorized": False, "policy_state": "configured" if current else "not_configured"}
        policy_integrity = False
        if current:
            try:
                policy_integrity = request_sha256(json.loads(str(current[0]["policy_json"]))) == current[0]["policy_sha256"]
            except (TypeError, ValueError, json.JSONDecodeError):
                policy_integrity = False
        configured = (
            policy_integrity
            and int(current[0]["policy_version"]) == int(rows[0]["policy_version"])
            and current[0]["policy_sha256"] == rows[0]["policy_sha256"]
        )
        return {"data_kind": data_kind, "authorized": configured and rows[0]["action"] == "granted", "policy_state": "configured" if configured else "compatibility_only", "receipt_public_id": rows[0]["public_id"]}

    @property
    def notification_inbox(self):
        if not hasattr(self, "_notification_inbox"):
            from core.notification_inbox import NotificationInboxService
            self._notification_inbox = NotificationInboxService(
                self.db, appearance_entitlement_resolver=self.appearance_entitlement_resolver,
            )
        return self._notification_inbox

    def create_notification(self, owner_id: int, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        return self.notification_inbox.create_notification(owner_id, payload, idempotency_key)

    def create_delivery(self, owner_id: int, item_public_id: str, channel: str, idempotency_key: str) -> dict[str, Any]:
        return self.notification_inbox.create_delivery(owner_id, item_public_id, channel, idempotency_key)

    def record_delivery_event(self, owner_id: int, delivery_public_id: str, status: str, idempotency_key: str, error_code: str | None = None) -> dict[str, Any]:
        return self.notification_inbox.record_delivery_event(owner_id, delivery_public_id, status, idempotency_key, error_code)

    def mark_read(self, owner_id: int, item_public_id: str, idempotency_key: str) -> dict[str, Any]:
        return self.notification_inbox.mark_read(owner_id, item_public_id, idempotency_key)

    def list_notifications(self, owner_id: int) -> list[dict[str, Any]]:
        return self.notification_inbox.list_notifications(owner_id)

    def resolve_deep_link(self, owner_id: int, target_kind: str, public_id: str, version: int, *, allow_fallback: bool = True) -> dict[str, Any]:
        return self.notification_inbox.resolve_deep_link(owner_id, target_kind, public_id, version, allow_fallback=allow_fallback)

    def resolve_notification(self, owner_id: int, notification_public_id: str) -> dict[str, Any]:
        return self.notification_inbox.resolve_notification(owner_id, notification_public_id)

    def account_overview(self, owner_id: int) -> dict[str, Any]:
        owner_id = _int(owner_id, "owner_id")
        with self._transaction() as conn:
            user = conn.execute("SELECT plan_type,subscription_expire,display_name FROM users WHERE id=? AND is_active=1", (owner_id,)).fetchone()
            if not user:
                raise AccountCenterNotFound("账户不存在。")
            membership = {"state": "available", "plan": user["plan_type"], "subscription_expire": user["subscription_expire"]}
            if not self._table(conn, "membership_entitlements"):
                membership["state"] = "compatibility_only"
            telegram = {"state": "unavailable", "policy_state": "not_configured"}
            if self._table(conn, "telegram_accounts"):
                row = conn.execute("SELECT 1 FROM telegram_accounts WHERE user_id=? AND is_active=1 AND revoked_at IS NULL LIMIT 1", (owner_id,)).fetchone()
                telegram = {"state": "configured" if row else "not_configured", "policy_state": "configured"}
            brokers = {"state": "unavailable", "items": []}
            if self._table(conn, "broker_accounts"):
                rows = conn.execute("SELECT provider,account_alias,mode,status,is_active FROM broker_accounts WHERE user_id=? ORDER BY created_at,id", (owner_id,)).fetchall()
                brokers = {"state": "configured" if rows else "not_configured", "items": [{"provider": r["provider"], "account_alias": r["account_alias"], "mode": r["mode"], "status": r["status"], "active": bool(r["is_active"])} for r in rows]}
        levels = {f"L{i}": {"level": None, "policy_state": "not_configured"} for i in range(5)}
        return {"account": {"public_id": "usr_" + hashlib.sha256(f"user:{owner_id}".encode()).hexdigest()[:24], "display_name": user["display_name"] or "CicloTrade 用户"}, "membership": membership, "telegram": telegram, "brokers": brokers, "agent_levels": levels, "runtime": {"auto_live": "not_ready"}}


__all__ = [
    "AccountCenterError", "AccountCenterNotFound", "IdempotencyConflict",
    "AccountCenterService", "canonical_json", "request_sha256", "route_public_id",
    "TARGET_KINDS", "STATIC_ROUTE_TARGET_KINDS",
]
