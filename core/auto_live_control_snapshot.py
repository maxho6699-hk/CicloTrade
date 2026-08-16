"""Owner-scoped, source-anonymous auto-live projections and broker refs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from core.auto_live_control_common import (
    AutoLiveConflict,
    AutoLiveControlError,
    _ID_RE,
    _iso,
    _now,
    _opaque,
    _row_public,
    _text,
    canonical_json,
    sha256_json,
    sha256_text,
)


class AutoLiveSnapshotMixin:
    def broker_account_public_ref(self, user_id: int, broker_account_id: int, *, now: datetime | None = None) -> str:
        with self._tx() as conn:
            self._user(conn, user_id)
            row = conn.execute(
                "SELECT id,user_id FROM broker_accounts WHERE id=? AND user_id=?", (broker_account_id, user_id)
            ).fetchone()
            if not row:
                raise AutoLiveControlError("券商账户引用不存在。", 404)
            return self._ensure_broker_ref(conn, int(user_id), int(broker_account_id), _iso(now))

    def _ensure_broker_ref(self, conn: Any, user_id: int, broker_account_id: int, now: str) -> str:
        row = conn.execute(
            "SELECT public_id FROM auto_live_broker_refs WHERE user_id=? AND broker_account_id=?",
            (user_id, broker_account_id),
        ).fetchone()
        if row:
            return str(row["public_id"])
        public_id = _opaque("broker")
        try:
            conn.execute(
                "INSERT INTO auto_live_broker_refs(public_id,user_id,broker_account_id,created_at) VALUES(?,?,?,?)",
                (public_id, user_id, broker_account_id, now),
            )
        except Exception:
            row = conn.execute(
                "SELECT public_id FROM auto_live_broker_refs WHERE user_id=? AND broker_account_id=?",
                (user_id, broker_account_id),
            ).fetchone()
            if row:
                return str(row["public_id"])
            raise
        return public_id

    def _broker_id_for_ref(self, conn: Any, user_id: int, public_id: str) -> int:
        row = conn.execute(
            "SELECT broker_account_id FROM auto_live_broker_refs WHERE user_id=? AND public_id=?", (user_id, public_id)
        ).fetchone()
        if not row:
            raise AutoLiveControlError("券商账户引用不存在。", 404)
        return int(row["broker_account_id"])

    def create_mandate_from_public_ref(
        self,
        user_id: int,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "broker_account_public_id",
            "strategy_version",
            "risk_version",
            "capital_limit_minor",
            "frequency_limit",
            "valid_from",
            "valid_until",
        }
        if not isinstance(payload, Mapping) or set(payload) != allowed:
            raise AutoLiveControlError("mandate public-ref 字段不完整或包含未知字段。")
        with self._tx() as conn:
            broker_id = self._broker_id_for_ref(
                conn, user_id, _text(payload["broker_account_public_id"], "券商账户引用")
            )
        internal = {key: payload[key] for key in allowed if key != "broker_account_public_id"}
        internal["broker_account_id"] = broker_id
        return self.create_mandate(
            user_id, internal, idempotency_key=idempotency_key, now=now
        )

    def request_pause_public_ref(
        self, user_id: int, broker_account_public_id: str, *, idempotency_key: str, now: datetime | None = None
    ) -> dict[str, Any]:
        with self._tx() as conn:
            broker_id = self._broker_id_for_ref(conn, user_id, _text(broker_account_public_id, "券商账户引用"))
        return self.request_pause(
            user_id, {"scope": "broker", "broker_account_id": broker_id}, idempotency_key=idempotency_key, now=now
        )

    def list_snapshot(self, user_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        moment_dt = _now(now)
        moment = _iso(moment_dt)
        with self._tx() as conn:
            self._user(conn, user_id)
            broker_rows = conn.execute(
                "SELECT r.public_id,b.provider,b.status FROM auto_live_broker_refs r JOIN broker_accounts b ON b.id=r.broker_account_id WHERE r.user_id=? ORDER BY r.public_id",
                (user_id,),
            ).fetchall()
            mandates = conn.execute(
                "SELECT * FROM auto_live_mandates WHERE user_id=? ORDER BY created_at,public_id", (user_id,)
            ).fetchall()
            refs = {
                int(r["broker_account_id"]): str(r["public_id"])
                for r in conn.execute(
                    "SELECT broker_account_id,public_id FROM auto_live_broker_refs WHERE user_id=?", (user_id,)
                ).fetchall()
            }
            mandate_public = {str(row["public_id"]) for row in mandates}
            mandate_items = []
            for row in mandates:
                item = _row_public(row)
                item["broker_account_public_id"] = refs.get(int(row["broker_account_id"]))
                mandate_items.append(item)
            runtime = [
                {
                    **dict(row),
                    "can_reduce_exposure": bool(row["can_reduce_exposure"]),
                }
                for row in conn.execute(
                    "SELECT r.mandate_public_id,r.state,r.can_reduce_exposure,r.last_error_code,r.observed_at FROM auto_live_runtime_projections r JOIN auto_live_mandates m ON m.public_id=r.mandate_public_id WHERE m.user_id=? ORDER BY r.mandate_public_id",
                    (user_id,),
                ).fetchall()
            ]
            heartbeat = [
                dict(row)
                for row in conn.execute(
                    "SELECT h.mandate_public_id,h.heartbeat_state,h.heartbeat_at,h.observed_at FROM auto_live_heartbeat_projections h JOIN auto_live_mandates m ON m.public_id=h.mandate_public_id WHERE m.user_id=? ORDER BY h.mandate_public_id",
                    (user_id,),
                ).fetchall()
            ]
            pause_receipts = [
                {
                    "public_id": str(row["request_public_id"]),
                    "status": str(row["status"]),
                    "receipt": self._safe_receipt(json.loads(str(row["receipt_json"]))),
                    "receipt_sha256": str(row["receipt_sha256"]),
                    "created_at": str(row["created_at"]),
                }
                for row in conn.execute(
                    "SELECT p.request_public_id,p.status,p.receipt_json,p.receipt_sha256,p.created_at FROM auto_live_pause_receipts p JOIN auto_live_pause_requests q ON q.public_id=p.request_public_id WHERE q.user_id=? ORDER BY p.created_at,p.receipt_id",
                    (user_id,),
                ).fetchall()
            ]
            start_receipts = [
                self._safe_receipt(json.loads(str(row["receipt_json"])))
                | {"receipt_sha256": str(row["receipt_sha256"]), "created_at": str(row["created_at"])}
                for row in conn.execute(
                    "SELECT s.receipt_json,s.receipt_sha256,s.created_at FROM auto_live_start_requests s JOIN auto_live_mandates m ON m.public_id=s.mandate_public_id WHERE s.user_id=? ORDER BY s.created_at,s.public_id",
                    (user_id,),
                ).fetchall()
            ]
            order_receipts = [
                dict(row)
                for row in conn.execute(
                    "SELECT o.public_id,o.mandate_public_id,o.client_order_id,o.submission_state,o.observed_at,o.receipt_sha256 FROM auto_live_order_receipt_projections o JOIN auto_live_mandates m ON m.public_id=o.mandate_public_id WHERE m.user_id=? ORDER BY o.observed_at,o.public_id",
                    (user_id,),
                ).fetchall()
            ]
            for item in runtime + heartbeat + order_receipts:
                if item.get("mandate_public_id") not in mandate_public:
                    raise AutoLiveControlError("auto-live projection owner binding invalid。")
            return {
                "snapshot_at": moment,
                "broker_accounts": [dict(row) for row in broker_rows],
                "mandates": mandate_items,
                "runtime_projections": runtime,
                "heartbeat_projections": heartbeat,
                "pause_receipts": pause_receipts,
                "start_receipts": start_receipts,
                "order_receipts": order_receipts,
            }

    @staticmethod
    def _safe_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Return receipt facts without actor identity, idempotency keys, or raw payloads."""
        allowed = {
            "public_id",
            "mandate_public_id",
            "status",
            "state",
            "runtime_state",
            "fencing_epoch",
            "created_at",
            "idempotency_key_sha256",
            "request_fingerprint",
            "scope",
            "confirmed",
            "total",
            "unconfirmed",
            "target_details",
            "can_reduce_exposure",
            "all_ok",
            "gates",
        }
        safe = {key: receipt[key] for key in allowed if key in receipt}
        if "actor" in receipt:
            safe["actor"] = "owner"
        return safe

    def request_pause(
        self, user_id: int, payload: Mapping[str, Any], *, idempotency_key: str, now: datetime | None = None
    ) -> dict[str, Any]:
        allowed = {"scope", "broker_account_id", "mandate_public_id"}
        if not isinstance(payload, Mapping) or set(payload) - allowed or "scope" not in payload:
            raise AutoLiveControlError("暂停请求字段不完整或包含未知字段。")
        scope = _text(payload["scope"], "暂停范围", 16).casefold()
        if scope not in {"aggregate", "broker", "mandate"}:
            raise AutoLiveControlError("暂停范围无效。")
        expected_keys = (
            {"scope"}
            if scope == "aggregate"
            else {"scope", "broker_account_id"}
            if scope == "broker"
            else {"scope", "mandate_public_id"}
        )
        if set(payload) != expected_keys:
            raise AutoLiveControlError("暂停请求字段与范围不匹配。")
        key = _text(idempotency_key, "Idempotency-Key", 128)
        if not _ID_RE.fullmatch(key):
            raise AutoLiveControlError("Idempotency-Key 格式无效。")
        body = {k: payload[k] for k in sorted(payload)}
        fingerprint = sha256_json(body)
        moment_dt = _now(now)
        moment = _iso(moment_dt)
        with self._tx() as conn:
            self._user(conn, user_id)
            existing = conn.execute(
                "SELECT * FROM auto_live_pause_requests WHERE user_id=? AND idempotency_key=?", (user_id, key)
            ).fetchone()
            if existing:
                if str(existing["request_fingerprint"]) != fingerprint:
                    raise AutoLiveConflict("Idempotency-Key 已用于不同暂停请求。")
                immutable = conn.execute(
                    "SELECT status,receipt_json,receipt_sha256 FROM auto_live_pause_receipts WHERE request_public_id=?",
                    (existing["public_id"],),
                ).fetchone()
                if immutable:
                    receipt = json.loads(str(immutable["receipt_json"]))
                    return {
                        "public_id": str(existing["public_id"]),
                        "scope": str(existing["scope"]),
                        "status": str(immutable["status"]),
                        "confirmed": int(receipt["confirmed"]),
                        "total": int(receipt["total"]),
                        "unconfirmed": list(receipt["unconfirmed"]),
                        "can_reduce_exposure": True,
                        "receipt_sha256": str(immutable["receipt_sha256"]),
                        "created_at": str(existing["created_at"]),
                        "updated_at": str(existing["updated_at"]),
                    }
                return self._pause_public(existing)
            broker_id = payload.get("broker_account_id") if scope == "broker" else None
            mandate_id = payload.get("mandate_public_id") if scope == "mandate" else None
            if scope == "broker" and (
                isinstance(broker_id, bool) or not isinstance(broker_id, int) or broker_id <= 0
            ):
                raise AutoLiveControlError("券商账户绑定无效。")
            if scope == "broker":
                owner = conn.execute(
                    "SELECT id FROM broker_accounts WHERE id=? AND user_id=?", (broker_id, user_id)
                ).fetchone()
                if not owner:
                    raise AutoLiveControlError("暂停券商账户不存在。", 404)
            if scope == "mandate":
                mandate_id = _text(mandate_id, "mandate public id")
                owner = conn.execute(
                    "SELECT public_id FROM auto_live_mandates WHERE public_id=? AND user_id=?",
                    (mandate_id, user_id),
                ).fetchone()
                if not owner:
                    raise AutoLiveControlError("暂停 mandate 不存在。", 404)
            where, params = "user_id=? AND state='active'", [user_id]
            if scope == "broker":
                where += " AND broker_account_id=?"
                params.append(broker_id)
            if scope == "mandate":
                where += " AND public_id=?"
                params.append(mandate_id)
            targets = conn.execute(
                f"SELECT * FROM auto_live_mandates WHERE {where} ORDER BY public_id", tuple(params)
            ).fetchall()
            request_id = _opaque("pause")
            total = len(targets)
            empty = canonical_json([])
            initial = {"public_id": request_id, "status": "pausing", "confirmed": 0, "total": total, "unconfirmed": []}
            conn.execute(
                """INSERT INTO auto_live_pause_requests
                (public_id,user_id,scope,broker_account_id,mandate_public_id,idempotency_key,request_fingerprint,status,confirmed,total,unconfirmed_json,receipt_json,receipt_sha256,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id,
                    user_id,
                    scope,
                    broker_id,
                    mandate_id,
                    key,
                    fingerprint,
                    "pausing",
                    0,
                    total,
                    empty,
                    canonical_json(initial),
                    sha256_json(initial),
                    moment,
                    moment,
                ),
            )
            if scope == "aggregate":
                conn.execute(
                    "INSERT INTO user_controls(user_id,opening_paused,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET opening_paused=1,updated_at=excluded.updated_at",
                    (user_id, 1, moment),
                )
            confirmed, unconfirmed = 0, []
            target_details = []
            for target in targets:
                target_id = str(target["public_id"])
                old_epoch = int(target["fencing_epoch"])
                next_epoch = old_epoch + 1
                runtime = conn.execute(
                    "SELECT * FROM auto_live_runtime_projections WHERE mandate_public_id=?",
                    (target_id,),
                ).fetchone()
                runtime_state = str(runtime["state"]) if runtime else None
                runtime_epoch = int(runtime["fencing_epoch"]) if runtime else None
                failed = runtime is None
                detail = "runtime_missing" if failed else "runtime_unconfirmed"
                changed = conn.execute(
                    "UPDATE auto_live_mandates SET state='paused',fencing_epoch=?,updated_at=?,confirmed_at=NULL,confirmation_digest=NULL WHERE public_id=? AND state='active' AND fencing_epoch=?",
                    (next_epoch, moment, target_id, old_epoch),
                ).rowcount
                if changed == 1:
                    self._event(
                        conn,
                        target,
                        "MANDATE_PAUSED",
                        "paused",
                        {"pause_request_public_id": request_id, "fencing_epoch": next_epoch},
                        moment,
                        "active",
                    )
                    if runtime:
                        if runtime_epoch != old_epoch:
                            failed = True
                            detail = "runtime_fencing_conflict"
                        else:
                            confirmed_runtime = runtime_state == "paused" and self._runtime_projection_valid(
                                runtime,
                                mandate_public_id=target_id,
                                fencing_epoch=old_epoch,
                                now=moment_dt,
                            )
                            detail = (
                                None
                                if confirmed_runtime
                                else "runtime_paused_ack_invalid"
                                if runtime_state == "paused"
                                else "runtime_paused_ack_missing"
                            )
                            body = {
                                "mandate_public_id": target_id,
                                "runtime_state": "paused" if confirmed_runtime else "pausing",
                                "can_reduce_exposure": 1,
                                "fencing_epoch": next_epoch,
                                "last_error_code": None if confirmed_runtime else "PAUSE_UNCONFIRMED",
                                "observed_at": moment,
                            }
                            runtime_state_after = body["runtime_state"]
                            changed_runtime = conn.execute(
                                "UPDATE auto_live_runtime_projections SET state=?,can_reduce_exposure=1,fencing_epoch=?,last_error_code=?,observed_at=?,projection_sha256=? WHERE mandate_public_id=? AND fencing_epoch=?",
                                (
                                    runtime_state_after,
                                    next_epoch,
                                    None if confirmed_runtime else "PAUSE_UNCONFIRMED",
                                    moment,
                                    sha256_json(body),
                                    target_id,
                                    old_epoch,
                                ),
                            ).rowcount
                            if changed_runtime != 1:
                                failed = True
                                detail = "runtime_fencing_conflict"
                            else:
                                failed = not confirmed_runtime
                                self._record_runtime_receipt(
                                    conn,
                                    mandate_public_id=target_id,
                                    event_type="pause_requested",
                                    state=runtime_state_after,
                                    fencing_epoch=next_epoch,
                                    observed_at=moment,
                                    detail=detail,
                                )
                else:
                    failed = True
                    detail = "mandate_fencing_conflict"
                conn.execute(
                    "INSERT INTO auto_live_pause_request_targets VALUES(?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        "mandate",
                        target_id,
                        0 if failed else 1,
                        "failed" if failed else "paused",
                        next_epoch,
                        detail if failed else None,
                        moment,
                    ),
                )
                target_details.append(
                    {
                        "target_type": "mandate",
                        "target_public_id": target_id,
                        "confirmed": not failed,
                        "status": "failed" if failed else "paused",
                        "fencing_epoch": next_epoch,
                        "detail": detail if failed else None,
                        "created_at": moment,
                    }
                )
                if failed:
                    unconfirmed.append(target_id)
                else:
                    confirmed += 1
            status = "paused" if not unconfirmed else "partial"
            receipt = {
                "public_id": request_id,
                "status": status,
                "confirmed": confirmed,
                "total": total,
                "unconfirmed": unconfirmed,
                "scope": scope,
                "can_reduce_exposure": True,
                "actor": {"type": "user", "user_id": int(user_id)},
                "created_at": moment,
                "idempotency_key_sha256": sha256_text(key),
                "request_fingerprint": fingerprint,
                "target_details": target_details,
            }
            conn.execute(
                "UPDATE auto_live_pause_requests SET status=?,confirmed=?,unconfirmed_json=?,receipt_json=?,receipt_sha256=?,updated_at=? WHERE public_id=?",
                (
                    status,
                    confirmed,
                    canonical_json(unconfirmed),
                    canonical_json(receipt),
                    sha256_json(receipt),
                    moment,
                    request_id,
                ),
            )
            conn.execute(
                "INSERT INTO auto_live_pause_receipts(receipt_id,request_public_id,status,receipt_json,receipt_sha256,created_at) VALUES(?,?,?,?,?,?)",
                (_opaque("receipt"), request_id, status, canonical_json(receipt), sha256_json(receipt), moment),
            )
            row = conn.execute("SELECT * FROM auto_live_pause_requests WHERE public_id=?", (request_id,)).fetchone()
            return self._pause_public(row)

    @staticmethod
    def _pause_public(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "public_id": str(row["public_id"]),
            "scope": str(row["scope"]),
            "status": str(row["status"]),
            "confirmed": int(row["confirmed"]),
            "total": int(row["total"]),
            "unconfirmed": json.loads(str(row["unconfirmed_json"])),
            "can_reduce_exposure": True,
            "receipt_sha256": str(row["receipt_sha256"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
