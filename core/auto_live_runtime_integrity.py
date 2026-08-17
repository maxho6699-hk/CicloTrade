"""Fail-closed runtime projection, heartbeat, freshness, and receipt primitives."""

from __future__ import annotations

import hmac
import json
import math
import secrets
from datetime import datetime, timedelta
from typing import Any, Mapping

from core.auto_live_control_common import (
    AutoLiveConflict,
    AutoLiveControlError,
    HEARTBEAT_FRESHNESS_SECONDS,
    MAX_CLOCK_SKEW_SECONDS,
    RUNTIME_FRESHNESS_SECONDS,
    SUPPORTED_BROKERS,
    _HEX64_RE,
    _ID_RE,
    _gate,
    _iso,
    _now,
    _opaque,
    _text,
    canonical_json,
    sha256_json,
    sha256_text,
)


class RuntimeIntegrityMixin:
    def _initialize_confirmed_runtime(self, conn: Any, row: Any, observed_at: str) -> None:
        self._write_runtime_projection(
            conn,
            mandate_public_id=str(row["public_id"]),
            state="stopped",
            fencing_epoch=int(row["fencing_epoch"]),
            observed_at=observed_at,
            event_type="initialized",
            expected_fencing_epoch=int(row["fencing_epoch"]),
            allow_exact_replay=True,
            insert_only=True,
        )

    @staticmethod
    def _aware_time(value: Any) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None

    @staticmethod
    def _fresh(timestamp: datetime | None, now: datetime, maximum_age_seconds: int) -> bool:
        if timestamp is None:
            return False
        age = (now - timestamp).total_seconds()
        return -MAX_CLOCK_SKEW_SECONDS <= age <= maximum_age_seconds

    @staticmethod
    def _runtime_body(row: Any) -> dict[str, Any]:
        return {
            "mandate_public_id": str(row["mandate_public_id"]),
            "runtime_state": str(row["state"]),
            "can_reduce_exposure": int(row["can_reduce_exposure"]),
            "fencing_epoch": int(row["fencing_epoch"]),
            "last_error_code": row["last_error_code"],
            "observed_at": str(row["observed_at"]),
        }

    @classmethod
    def _runtime_projection_valid(
        cls,
        runtime: Any,
        *,
        mandate_public_id: str,
        fencing_epoch: int,
        now: datetime,
    ) -> bool:
        try:
            return bool(
                runtime
                and str(runtime["mandate_public_id"]) == mandate_public_id
                and int(runtime["fencing_epoch"]) == fencing_epoch
                and int(runtime["can_reduce_exposure"]) == 1
                and runtime["last_error_code"] is None
                and sha256_json(cls._runtime_body(runtime)) == str(runtime["projection_sha256"])
                and cls._fresh(cls._aware_time(runtime["observed_at"]), now, RUNTIME_FRESHNESS_SECONDS)
            )
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _heartbeat_body(row: Any) -> dict[str, Any]:
        return {
            "mandate_public_id": str(row["mandate_public_id"]),
            "heartbeat_state": str(row["heartbeat_state"]),
            "heartbeat_at": row["heartbeat_at"],
            "fencing_epoch": int(row["fencing_epoch"]),
            "observed_at": str(row["observed_at"]),
        }

    @staticmethod
    def _record_runtime_receipt(
        conn: Any,
        *,
        mandate_public_id: str,
        event_type: str,
        state: str,
        fencing_epoch: int,
        observed_at: str,
        detail: str | None = None,
    ) -> None:
        body = {
            "mandate_public_id": mandate_public_id,
            "event_type": event_type,
            "runtime_state": state,
            "fencing_epoch": fencing_epoch,
            "observed_at": observed_at,
            "detail": detail,
        }
        conn.execute(
            """INSERT INTO auto_live_runtime_receipts
            (receipt_id,mandate_public_id,event_type,state,fencing_epoch,payload_json,payload_sha256,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                _opaque("runtime"),
                mandate_public_id,
                event_type,
                state,
                fencing_epoch,
                canonical_json(body),
                sha256_json(body),
                observed_at,
            ),
        )

    @classmethod
    def _running_ack_matches_projection(cls, conn: Any, runtime: Any) -> bool:
        if runtime is None:
            return False
        receipt = conn.execute(
            "SELECT * FROM auto_live_runtime_receipts WHERE mandate_public_id=? ORDER BY rowid DESC LIMIT 1",
            (runtime["mandate_public_id"],),
        ).fetchone()
        if receipt is None:
            return False
        try:
            payload = json.loads(str(receipt["payload_json"]))
            expected = {
                "mandate_public_id": str(runtime["mandate_public_id"]),
                "event_type": "running_ack",
                "runtime_state": "running",
                "fencing_epoch": int(runtime["fencing_epoch"]),
                "observed_at": str(runtime["observed_at"]),
                "detail": None,
            }
            return bool(
                isinstance(payload, Mapping)
                and dict(payload) == expected
                and sha256_json(payload) == str(receipt["payload_sha256"])
                and str(receipt["mandate_public_id"]) == expected["mandate_public_id"]
                and str(receipt["event_type"]) == "running_ack"
                and str(receipt["state"]) == "running"
                and int(receipt["fencing_epoch"]) == expected["fencing_epoch"]
                and str(receipt["created_at"]) == expected["observed_at"]
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    @classmethod
    def _write_runtime_projection(
        cls,
        conn: Any,
        *,
        mandate_public_id: str,
        state: str,
        fencing_epoch: int,
        observed_at: str,
        event_type: str,
        last_error_code: str | None = None,
        expected_fencing_epoch: int | None = None,
        allow_exact_replay: bool = False,
        insert_only: bool = False,
    ) -> None:
        body = {
            "mandate_public_id": mandate_public_id,
            "runtime_state": state,
            "can_reduce_exposure": 1,
            "fencing_epoch": fencing_epoch,
            "last_error_code": last_error_code,
            "observed_at": observed_at,
        }
        digest = sha256_json(body)
        existing = conn.execute(
            "SELECT * FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate_public_id,)
        ).fetchone()
        if existing:
            existing_epoch = int(existing["fencing_epoch"])
            if expected_fencing_epoch is not None and existing_epoch != expected_fencing_epoch:
                raise AutoLiveConflict("runtime fencing epoch 已过期。")
            exact = (
                str(existing["state"]) == state
                and int(existing["can_reduce_exposure"]) == 1
                and existing["last_error_code"] == last_error_code
                and int(existing["fencing_epoch"]) == fencing_epoch
                and str(existing["observed_at"]) == observed_at
                and str(existing["projection_sha256"]) == digest
            )
            if allow_exact_replay and exact:
                return
            if insert_only and str(existing["state"]) not in {"paused", "stopped"}:
                raise AutoLiveConflict("confirmed runtime projection 已存在且不一致。")
            changed = conn.execute(
                """UPDATE auto_live_runtime_projections
                SET state=?,can_reduce_exposure=1,fencing_epoch=?,last_error_code=?,observed_at=?,projection_sha256=?
                WHERE mandate_public_id=? AND fencing_epoch=?""",
                (state, fencing_epoch, last_error_code, observed_at, digest, mandate_public_id, existing_epoch),
            ).rowcount
            if changed != 1:
                raise AutoLiveConflict("runtime fencing CAS 失败。")
        else:
            conn.execute(
                """INSERT INTO auto_live_runtime_projections
                (mandate_public_id,state,can_reduce_exposure,fencing_epoch,last_error_code,observed_at,projection_sha256)
                VALUES(?,?,?,?,?,?,?)""",
                (mandate_public_id, state, 1, fencing_epoch, last_error_code, observed_at, digest),
            )
        cls._record_runtime_receipt(
            conn,
            mandate_public_id=mandate_public_id,
            event_type=event_type,
            state=state,
            fencing_epoch=fencing_epoch,
            observed_at=observed_at,
            detail=last_error_code,
        )

    @staticmethod
    def _lease_body(row: Any) -> dict[str, Any]:
        return {
            "mandate_public_id": str(row["mandate_public_id"]),
            "worker_id": str(row["worker_id"]),
            "lease_token_sha256": str(row["lease_token_sha256"]),
            "fencing_epoch": int(row["fencing_epoch"]),
            "lease_expires_at": str(row["lease_expires_at"]),
            "heartbeat_at": row["heartbeat_at"],
            "observed_at": str(row["observed_at"]),
        }

    @classmethod
    def _valid_lease(
        cls,
        lease: Any,
        *,
        mandate_public_id: str,
        worker_id: str,
        lease_token: str,
        fencing_epoch: int,
        now: datetime,
    ) -> bool:
        try:
            expires_at = datetime.fromisoformat(str(lease["lease_expires_at"]))
            return bool(
                str(lease["mandate_public_id"]) == mandate_public_id
                and str(lease["worker_id"]) == worker_id
                and hmac.compare_digest(str(lease["lease_token_sha256"]), sha256_text(lease_token))
                and int(lease["fencing_epoch"]) == fencing_epoch
                and expires_at.tzinfo is not None
                and now < expires_at
                and sha256_json(cls._lease_body(lease)) == str(lease["projection_sha256"])
            )
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _runtime_worker_row(conn: Any, mandate_public_id: str) -> Any:
        row = conn.execute(
            "SELECT * FROM auto_live_mandates WHERE public_id=?", (mandate_public_id,)
        ).fetchone()
        if not row:
            raise AutoLiveControlError("runtime mandate 不存在。", 404)
        return row

    @staticmethod
    def _lease_seconds(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 120:
            raise AutoLiveControlError("runtime 租约秒数必须介于 5 与 120。")
        return value

    @classmethod
    def _record_lease_event(cls, conn: Any, lease: Any, event_type: str, observed_at: str) -> None:
        payload = {**cls._lease_body(lease), "event_type": event_type}
        conn.execute(
            """INSERT INTO auto_live_runtime_lease_events
               (event_id,mandate_public_id,worker_id,event_type,fencing_epoch,lease_token_sha256,
                lease_expires_at,payload_json,payload_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                _opaque("leaseevt"),
                lease["mandate_public_id"],
                lease["worker_id"],
                event_type,
                lease["fencing_epoch"],
                lease["lease_token_sha256"],
                lease["lease_expires_at"],
                canonical_json(payload),
                sha256_json(payload),
                observed_at,
            ),
        )

    @staticmethod
    def _write_heartbeat_projection(
        conn: Any,
        *,
        mandate_public_id: str,
        fencing_epoch: int,
        observed_at: str,
    ) -> None:
        body = {
            "mandate_public_id": mandate_public_id,
            "heartbeat_state": "fresh",
            "heartbeat_at": observed_at,
            "fencing_epoch": fencing_epoch,
            "observed_at": observed_at,
        }
        conn.execute(
            """INSERT INTO auto_live_heartbeat_projections
               (mandate_public_id,heartbeat_state,heartbeat_at,fencing_epoch,observed_at,projection_sha256)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(mandate_public_id) DO UPDATE SET
                 heartbeat_state=excluded.heartbeat_state,
                 heartbeat_at=excluded.heartbeat_at,
                 fencing_epoch=excluded.fencing_epoch,
                 observed_at=excluded.observed_at,
                 projection_sha256=excluded.projection_sha256""",
            (mandate_public_id, "fresh", observed_at, fencing_epoch, observed_at, sha256_json(body)),
        )

    def claim_runtime_lease(
        self,
        mandate_public_id: str,
        *,
        worker_id: str,
        expected_fencing_epoch: int,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        public_id = _text(mandate_public_id, "runtime mandate public id")
        worker = _text(worker_id, "runtime worker id", 128)
        seconds = self._lease_seconds(lease_seconds)
        if isinstance(expected_fencing_epoch, bool) or not isinstance(expected_fencing_epoch, int) or expected_fencing_epoch < 0:
            raise AutoLiveControlError("runtime fencing epoch 无效。")
        moment_dt, moment = _now(now), _iso(now)
        expires_at = _iso(moment_dt + timedelta(seconds=seconds))
        with self._tx() as conn:
            mandate = self._runtime_worker_row(conn, public_id)
            runtime = conn.execute(
                "SELECT * FROM auto_live_runtime_projections WHERE mandate_public_id=?", (public_id,)
            ).fetchone()
            if str(mandate["state"]) != "active" or not runtime or str(runtime["state"]) != "starting":
                raise AutoLiveControlError("runtime 只有 active/starting mandate 可领取。", 409)
            if int(mandate["fencing_epoch"]) != expected_fencing_epoch or int(runtime["fencing_epoch"]) != expected_fencing_epoch:
                raise AutoLiveConflict("runtime fencing epoch 已过期。")
            existing = conn.execute(
                "SELECT * FROM auto_live_runtime_leases WHERE mandate_public_id=?", (public_id,)
            ).fetchone()
            event_type = "claimed"
            if existing:
                try:
                    existing_expiry = datetime.fromisoformat(str(existing["lease_expires_at"]))
                    existing_valid = sha256_json(self._lease_body(existing)) == str(existing["projection_sha256"])
                except (TypeError, ValueError):
                    existing_valid, existing_expiry = False, moment_dt
                if not existing_valid:
                    raise AutoLiveControlError("runtime 租约投影无效。", 409)
                if moment_dt < existing_expiry:
                    raise AutoLiveConflict("runtime 租约当前由其他 Worker 持有。")
                event_type = "reclaimed"
            token = secrets.token_urlsafe(32)
            token_sha256 = sha256_text(token)
            body = {
                "mandate_public_id": public_id,
                "worker_id": worker,
                "lease_token_sha256": token_sha256,
                "fencing_epoch": expected_fencing_epoch,
                "lease_expires_at": expires_at,
                "heartbeat_at": None,
                "observed_at": moment,
            }
            conn.execute(
                """INSERT INTO auto_live_runtime_leases
                   (mandate_public_id,worker_id,lease_token_sha256,fencing_epoch,lease_expires_at,
                    heartbeat_at,observed_at,projection_sha256)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(mandate_public_id) DO UPDATE SET
                     worker_id=excluded.worker_id,
                     lease_token_sha256=excluded.lease_token_sha256,
                     fencing_epoch=excluded.fencing_epoch,
                     lease_expires_at=excluded.lease_expires_at,
                     heartbeat_at=NULL,
                     observed_at=excluded.observed_at,
                     projection_sha256=excluded.projection_sha256""",
                (public_id, worker, token_sha256, expected_fencing_epoch, expires_at, None, moment, sha256_json(body)),
            )
            lease = conn.execute(
                "SELECT * FROM auto_live_runtime_leases WHERE mandate_public_id=?", (public_id,)
            ).fetchone()
            self._record_lease_event(conn, lease, event_type, moment)
            return {
                "mandate_public_id": public_id,
                "worker_id": worker,
                "lease_token": token,
                "fencing_epoch": expected_fencing_epoch,
                "lease_expires_at": expires_at,
            }

    def _require_runtime_lease(
        self,
        conn: Any,
        *,
        mandate_public_id: str,
        worker_id: str,
        lease_token: str,
        expected_fencing_epoch: int,
        now: datetime,
    ) -> tuple[Any, Any, Any]:
        mandate = self._runtime_worker_row(conn, mandate_public_id)
        runtime = conn.execute(
            "SELECT * FROM auto_live_runtime_projections WHERE mandate_public_id=?", (mandate_public_id,)
        ).fetchone()
        lease = conn.execute(
            "SELECT * FROM auto_live_runtime_leases WHERE mandate_public_id=?", (mandate_public_id,)
        ).fetchone()
        if int(mandate["fencing_epoch"]) != expected_fencing_epoch:
            raise AutoLiveConflict("runtime fencing epoch 已过期。")
        if not self._valid_lease(
            lease,
            mandate_public_id=mandate_public_id,
            worker_id=worker_id,
            lease_token=lease_token,
            fencing_epoch=expected_fencing_epoch,
            now=now,
        ):
            raise AutoLiveControlError("runtime 租约 token 无效或已过期。", 409)
        return mandate, runtime, lease

    def ack_runtime_running(
        self,
        mandate_public_id: str,
        *,
        worker_id: str,
        lease_token: str,
        expected_fencing_epoch: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        public_id = _text(mandate_public_id, "runtime mandate public id")
        worker = _text(worker_id, "runtime worker id", 128)
        token = _text(lease_token, "runtime lease token", 256)
        moment_dt, moment = _now(now), _iso(now)
        with self._tx() as conn:
            mandate, runtime, lease = self._require_runtime_lease(
                conn,
                mandate_public_id=public_id,
                worker_id=worker,
                lease_token=token,
                expected_fencing_epoch=expected_fencing_epoch,
                now=moment_dt,
            )
            if str(mandate["state"]) != "active" or not runtime or str(runtime["state"]) != "starting":
                raise AutoLiveControlError("runtime 当前不可确认 running。", 409)
            self._write_runtime_projection(
                conn,
                mandate_public_id=public_id,
                state="running",
                fencing_epoch=expected_fencing_epoch,
                observed_at=moment,
                event_type="running_ack",
                expected_fencing_epoch=expected_fencing_epoch,
            )
            self._write_heartbeat_projection(
                conn,
                mandate_public_id=public_id,
                fencing_epoch=expected_fencing_epoch,
                observed_at=moment,
            )
            conn.execute(
                "UPDATE auto_live_runtime_leases SET heartbeat_at=?,observed_at=?,projection_sha256=? WHERE mandate_public_id=?",
                (
                    moment,
                    moment,
                    sha256_json({**self._lease_body(lease), "heartbeat_at": moment, "observed_at": moment}),
                    public_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM auto_live_runtime_leases WHERE mandate_public_id=?", (public_id,)
            ).fetchone()
            self._record_lease_event(conn, updated, "running_ack", moment)
            return {
                "mandate_public_id": public_id,
                "runtime_state": "running",
                "heartbeat_state": "fresh",
                "fencing_epoch": expected_fencing_epoch,
                "observed_at": moment,
            }

    def renew_runtime_heartbeat(
        self,
        mandate_public_id: str,
        *,
        worker_id: str,
        lease_token: str,
        expected_fencing_epoch: int,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        public_id = _text(mandate_public_id, "runtime mandate public id")
        worker = _text(worker_id, "runtime worker id", 128)
        token = _text(lease_token, "runtime lease token", 256)
        seconds = self._lease_seconds(lease_seconds)
        moment_dt, moment = _now(now), _iso(now)
        expires_at = _iso(moment_dt + timedelta(seconds=seconds))
        with self._tx() as conn:
            mandate, runtime, lease = self._require_runtime_lease(
                conn,
                mandate_public_id=public_id,
                worker_id=worker,
                lease_token=token,
                expected_fencing_epoch=expected_fencing_epoch,
                now=moment_dt,
            )
            if str(mandate["state"]) != "active" or not runtime or str(runtime["state"]) != "running":
                raise AutoLiveControlError("runtime 当前不可续约 heartbeat。", 409)
            body = {
                **self._lease_body(lease),
                "lease_expires_at": expires_at,
                "heartbeat_at": moment,
                "observed_at": moment,
            }
            conn.execute(
                """UPDATE auto_live_runtime_leases SET lease_expires_at=?,heartbeat_at=?,observed_at=?,projection_sha256=?
                   WHERE mandate_public_id=? AND fencing_epoch=? AND lease_token_sha256=?""",
                (expires_at, moment, moment, sha256_json(body), public_id, expected_fencing_epoch, sha256_text(token)),
            )
            self._write_heartbeat_projection(
                conn,
                mandate_public_id=public_id,
                fencing_epoch=expected_fencing_epoch,
                observed_at=moment,
            )
            updated = conn.execute(
                "SELECT * FROM auto_live_runtime_leases WHERE mandate_public_id=?", (public_id,)
            ).fetchone()
            self._record_lease_event(conn, updated, "heartbeat", moment)
            return {
                "mandate_public_id": public_id,
                "worker_id": worker,
                "heartbeat_state": "fresh",
                "fencing_epoch": expected_fencing_epoch,
                "heartbeat_at": moment,
                "lease_expires_at": expires_at,
            }

    def append_shadow_intents(
        self,
        mandate_public_id: str,
        intents: list[Mapping[str, Any]],
        *,
        worker_id: str,
        lease_token: str,
        expected_fencing_epoch: int,
        now: datetime | None = None,
    ) -> dict[str, int | str]:
        public_id = _text(mandate_public_id, "runtime mandate public id")
        worker = _text(worker_id, "runtime worker id", 128)
        token = _text(lease_token, "runtime lease token", 256)
        if not isinstance(intents, list) or not intents or len(intents) > 100:
            raise AutoLiveControlError("shadow intents 必须包含 1 至 100 项。")
        moment_dt, moment = _now(now), _iso(now)
        allowed = {
            "client_order_id", "action", "instrument_type", "symbol", "side", "quantity",
            "limit_price", "currency", "quote_at", "quote_sha256", "evidence_sha256",
        }
        with self._tx() as conn:
            mandate, runtime, _ = self._require_runtime_lease(
                conn,
                mandate_public_id=public_id,
                worker_id=worker,
                lease_token=token,
                expected_fencing_epoch=expected_fencing_epoch,
                now=moment_dt,
            )
            if str(mandate["state"]) != "active" or not runtime or str(runtime["state"]) != "running":
                raise AutoLiveControlError("runtime 只有 active/running 可生成 shadow intent。", 409)
            gates = self._gates(conn, mandate, moment_dt, runtime_mode="opening")
            paused = conn.execute(
                "SELECT opening_paused FROM user_controls WHERE user_id=?", (mandate["user_id"],)
            ).fetchone()
            if not gates["all_ok"] or int(paused[0] if paused else 1):
                raise AutoLiveControlError("runtime opening gate 未通过。", 409)

            normalized: list[tuple[dict[str, Any], str, Any | None]] = []
            seen_client_ids: set[str] = set()
            for raw in intents:
                if not isinstance(raw, Mapping) or set(raw) != allowed:
                    raise AutoLiveControlError("shadow intent 字段不完整或包含未知字段。")
                client_order_id = _text(raw["client_order_id"], "client_order_id", 128)
                if not _ID_RE.fullmatch(client_order_id) or client_order_id in seen_client_ids:
                    raise AutoLiveControlError("client_order_id 无效或重复。")
                seen_client_ids.add(client_order_id)
                action = _text(raw["action"], "action", 32).casefold()
                instrument_type = _text(raw["instrument_type"], "instrument_type", 16).casefold()
                if action not in {"open", "reduce_exposure", "close_position"}:
                    raise AutoLiveControlError("shadow intent action 无效。")
                if instrument_type != "stock":
                    raise AutoLiveControlError("shadow 第一阶段只允许正股 intent。")
                symbol = _text(raw["symbol"], "symbol", 32).upper()
                if not symbol.replace(".", "").replace("-", "").isalnum():
                    raise AutoLiveControlError("shadow intent symbol 无效。")
                side = _text(raw["side"], "side", 8).upper()
                if side not in {"BUY", "SELL"}:
                    raise AutoLiveControlError("shadow intent side 无效。")
                quantity = raw["quantity"]
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                    raise AutoLiveControlError("shadow intent quantity 无效。")
                try:
                    limit_price = float(raw["limit_price"])
                except (TypeError, ValueError) as exc:
                    raise AutoLiveControlError("shadow intent limit price 无效。") from exc
                if not math.isfinite(limit_price) or limit_price <= 0:
                    raise AutoLiveControlError("shadow intent limit price 无效。")
                currency = _text(raw["currency"], "currency", 3).upper()
                if len(currency) != 3 or not currency.isalpha():
                    raise AutoLiveControlError("shadow intent currency 无效。")
                try:
                    quote_at = datetime.fromisoformat(_text(raw["quote_at"], "quote_at", 64).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise AutoLiveControlError("shadow intent quote_at 无效。") from exc
                age = (moment_dt - quote_at).total_seconds() if quote_at.tzinfo is not None else float("inf")
                if not -MAX_CLOCK_SKEW_SECONDS <= age <= 60:
                    raise AutoLiveControlError("shadow intent 行情陈旧或来自未来。")
                quote_sha256 = _text(raw["quote_sha256"], "quote_sha256", 64)
                evidence_sha256 = _text(raw["evidence_sha256"], "evidence_sha256", 64)
                if not _HEX64_RE.fullmatch(quote_sha256) or not _HEX64_RE.fullmatch(evidence_sha256):
                    raise AutoLiveControlError("shadow intent 证据哈希无效。")
                payload = {
                    "schema_version": 1,
                    "mandate_public_id": public_id,
                    "client_order_id": client_order_id,
                    "execution_mode": "shadow",
                    "fencing_epoch": expected_fencing_epoch,
                    "strategy_version": str(mandate["strategy_version"]),
                    "risk_version": str(mandate["risk_version"]),
                    "action": action,
                    "instrument_type": instrument_type,
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "currency": currency,
                    "quote_at": quote_at.isoformat(),
                    "quote_sha256": quote_sha256,
                    "evidence_sha256": evidence_sha256,
                }
                digest = sha256_json(payload)
                existing = conn.execute(
                    "SELECT * FROM auto_live_order_intents WHERE client_order_id=?", (client_order_id,)
                ).fetchone()
                if existing and str(existing["intent_sha256"]) != digest:
                    raise AutoLiveConflict("client_order_id 已绑定不同 intent。")
                normalized.append((payload, digest, existing))

            new_items = [(payload, digest) for payload, digest, existing in normalized if existing is None]
            current_count = int(conn.execute(
                "SELECT COUNT(*) FROM auto_live_order_intents WHERE mandate_public_id=? AND date(created_at)=date(?)",
                (public_id, moment),
            ).fetchone()[0])
            if current_count + len(new_items) > int(mandate["frequency_limit"]):
                raise AutoLiveControlError("shadow intent 超过 mandate 频率限制。", 409)
            total_notional_minor = sum(round(item["quantity"] * item["limit_price"] * 100) for item, _ in new_items)
            if total_notional_minor > int(mandate["capital_limit_minor"]):
                raise AutoLiveControlError("shadow intent 超过 mandate 资本限制。", 409)

            for payload, digest in new_items:
                intent_id = _opaque("intent")
                conn.execute(
                    """INSERT INTO auto_live_order_intents
                       (public_id,mandate_public_id,client_order_id,execution_mode,fencing_epoch,
                        strategy_version,risk_version,action,instrument_type,symbol,side,quantity,
                        limit_price,currency,quote_at,quote_sha256,evidence_sha256,intent_json,intent_sha256,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        intent_id, public_id, payload["client_order_id"], "shadow", expected_fencing_epoch,
                        payload["strategy_version"], payload["risk_version"], payload["action"],
                        payload["instrument_type"], payload["symbol"], payload["side"], payload["quantity"],
                        payload["limit_price"], payload["currency"], payload["quote_at"],
                        payload["quote_sha256"], payload["evidence_sha256"], canonical_json(payload), digest, moment,
                    ),
                )
                event = {
                    "intent_public_id": intent_id,
                    "mandate_public_id": public_id,
                    "event_type": "shadowed",
                    "fencing_epoch": expected_fencing_epoch,
                    "intent_sha256": digest,
                }
                conn.execute(
                    """INSERT INTO auto_live_order_intent_events
                       (event_id,intent_public_id,mandate_public_id,event_type,fencing_epoch,payload_json,payload_sha256,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (_opaque("intentevt"), intent_id, public_id, "shadowed", expected_fencing_epoch, canonical_json(event), sha256_json(event), moment),
                )
            return {"status": "shadowed", "intents": len(new_items), "reused": len(normalized) - len(new_items)}

    @staticmethod
    def _broker_receipt_public(row: Any, *, reconciled: bool) -> dict[str, Any]:
        return {
            "public_id": str(row["receipt_id"]),
            "mandate_public_id": str(row["mandate_public_id"]),
            "client_order_id": str(row["client_order_id"]),
            "provider": str(row["provider"]),
            "submission_state": str(row["submission_state"]),
            "broker_status": str(row["broker_status"]),
            "observed_at": str(row["observed_at"]),
            "receipt_sha256": str(row["payload_sha256"]),
            "reconciled": bool(reconciled),
        }

    def broker_binding_for_order_intent(self, mandate_public_id: str, client_order_id: str) -> dict[str, str]:
        mandate_id = _text(mandate_public_id, "mandate public id")
        client_id = _text(client_order_id, "client_order_id")
        with self._tx() as conn:
            row = conn.execute(
                """SELECT b.provider,b.external_account_id
                   FROM auto_live_order_intents i
                   JOIN auto_live_mandates m ON m.public_id=i.mandate_public_id
                   JOIN broker_accounts b ON b.id=m.broker_account_id
                   WHERE i.mandate_public_id=? AND i.client_order_id=?""",
                (mandate_id, client_id),
            ).fetchone()
            if row is None:
                raise AutoLiveControlError("broker reconciliation intent 不存在。", 404)
            provider = str(row["provider"]).strip().casefold()
            if provider not in SUPPORTED_BROKERS:
                raise AutoLiveControlError("broker reconciliation provider 不受支持。")
            return {
                "provider": provider,
                "broker_account_sha256": sha256_text(str(row["external_account_id"])),
            }

    def pending_broker_reconciliations(
        self, *, limit: int = 100, providers: tuple[str, ...] = ("tiger",)
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise AutoLiveControlError("broker reconciliation limit 必须为 1 至 500。")
        if not isinstance(providers, tuple) or not providers:
            raise AutoLiveControlError("broker reconciliation providers 无效。")
        normalized_providers = tuple(sorted({_text(item, "broker reconciliation provider").casefold() for item in providers}))
        if any(item not in SUPPORTED_BROKERS for item in normalized_providers):
            raise AutoLiveControlError("broker reconciliation provider 不受支持。")
        placeholders = ",".join("?" for _ in normalized_providers)
        with self._tx() as conn:
            rows = conn.execute(
                f"""SELECT i.mandate_public_id,i.client_order_id,i.fencing_epoch,b.provider,b.external_account_id
                   FROM auto_live_order_intents i
                   JOIN auto_live_mandates m ON m.public_id=i.mandate_public_id
                   JOIN broker_accounts b ON b.id=m.broker_account_id
                   JOIN auto_live_order_receipt_projections current
                     ON current.mandate_public_id=i.mandate_public_id
                    AND current.client_order_id=i.client_order_id
                   WHERE i.execution_mode IN ('paper','live')
                     AND LOWER(b.provider) IN ({placeholders})
                     AND current.submission_state='submission_unknown'
                     AND current.rowid=(
                       SELECT MAX(latest.rowid) FROM auto_live_order_receipt_projections latest
                       WHERE latest.mandate_public_id=current.mandate_public_id
                         AND latest.client_order_id=current.client_order_id
                     )
                     AND EXISTS(
                       SELECT 1 FROM auto_live_order_intent_events event
                       WHERE event.intent_public_id=i.public_id AND event.event_type='send_claimed'
                     )
                   ORDER BY current.observed_at,current.rowid
                   LIMIT ?""",
                (*normalized_providers, limit),
            ).fetchall()
            pending = []
            for row in rows:
                provider = str(row["provider"]).strip().casefold()
                pending.append(
                    {
                        "mandate_public_id": str(row["mandate_public_id"]),
                        "client_order_id": str(row["client_order_id"]),
                        "provider": provider,
                        "broker_account_sha256": sha256_text(str(row["external_account_id"])),
                        "expected_fencing_epoch": int(row["fencing_epoch"]),
                    }
                )
            return pending

    def record_broker_order_receipt(
        self,
        mandate_public_id: str,
        client_order_id: str,
        observation: Mapping[str, Any],
        *,
        expected_fencing_epoch: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        mandate_id = _text(mandate_public_id, "mandate public id")
        client_id = _text(client_order_id, "client_order_id")
        required = {
            "provider",
            "submission_state",
            "broker_order_id",
            "broker_status",
            "observed_at",
            "evidence_sha256",
            "broker_account_sha256",
        }
        if not isinstance(observation, Mapping) or set(observation) != required:
            raise AutoLiveControlError("broker receipt 字段不完整或包含未知字段。")
        if isinstance(expected_fencing_epoch, bool) or not isinstance(expected_fencing_epoch, int) or expected_fencing_epoch < 0:
            raise AutoLiveControlError("broker receipt fencing epoch 无效。")
        provider = _text(observation["provider"], "broker receipt provider").casefold()
        if provider not in SUPPORTED_BROKERS:
            raise AutoLiveControlError("broker receipt provider 不受支持。")
        state = _text(observation["submission_state"], "broker receipt submission state").casefold()
        if state not in {"accepted", "rejected", "submission_unknown", "cancelled"}:
            raise AutoLiveControlError("broker receipt submission state 无效。")
        broker_order_id = observation["broker_order_id"]
        if broker_order_id is not None:
            broker_order_id = _text(broker_order_id, "broker_order_id")
        if state in {"accepted", "cancelled"} and broker_order_id is None:
            raise AutoLiveControlError("已知 broker receipt 缺少 broker_order_id。")
        broker_status = _text(observation["broker_status"], "broker_status")
        evidence_sha256 = _text(observation["evidence_sha256"], "broker receipt 证据", 64).casefold()
        if not _HEX64_RE.fullmatch(evidence_sha256):
            raise AutoLiveControlError("broker receipt 证据哈希无效。")
        broker_account_sha256 = _text(observation["broker_account_sha256"], "broker account 指纹", 64).casefold()
        if not _HEX64_RE.fullmatch(broker_account_sha256):
            raise AutoLiveControlError("broker account 指纹无效。")
        expected_evidence = sha256_json(
            {
                "provider": provider,
                "client_order_id": client_id,
                "broker_order_id": broker_order_id,
                "broker_status": broker_status,
                "submission_state": state,
                "broker_account_sha256": broker_account_sha256,
            }
        )
        if not hmac.compare_digest(evidence_sha256, expected_evidence):
            raise AutoLiveControlError("broker receipt 证据哈希与观察内容不匹配。")
        try:
            observed_dt = datetime.fromisoformat(str(observation["observed_at"]).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise AutoLiveControlError("broker receipt observed_at 无效。") from exc
        if observed_dt.tzinfo is None or observed_dt.utcoffset() is None:
            raise AutoLiveControlError("broker receipt observed_at 必须带时区。")
        moment_dt = _now(now)
        observed_at = _iso(observed_dt)
        moment = _iso(moment_dt)
        if (observed_dt - moment_dt).total_seconds() > MAX_CLOCK_SKEW_SECONDS:
            raise AutoLiveControlError("broker receipt observed_at 来自未来。")

        with self._tx() as conn:
            intent = conn.execute(
                """SELECT i.*,m.fencing_epoch mandate_fencing_epoch,m.state mandate_state,
                          b.provider broker_provider,b.external_account_id broker_external_account_id
                   FROM auto_live_order_intents i
                   JOIN auto_live_mandates m ON m.public_id=i.mandate_public_id
                   JOIN broker_accounts b ON b.id=m.broker_account_id
                   WHERE i.mandate_public_id=? AND i.client_order_id=?""",
                (mandate_id, client_id),
            ).fetchone()
            if intent is None:
                raise AutoLiveControlError("broker receipt intent 不存在。", 404)
            if str(intent["execution_mode"]) == "shadow":
                raise AutoLiveControlError("shadow intent 不接受 broker receipt。")
            mandate_epoch = int(intent["mandate_fencing_epoch"])
            if int(intent["fencing_epoch"]) != expected_fencing_epoch or mandate_epoch < expected_fencing_epoch:
                raise AutoLiveConflict("broker receipt fencing epoch 不匹配。")
            if mandate_epoch != expected_fencing_epoch and str(intent["mandate_state"]) == "active":
                raise AutoLiveConflict("active mandate 已进入新的 fencing epoch。")
            bound_provider = str(intent["broker_provider"]).strip().casefold()
            if provider != bound_provider:
                raise AutoLiveControlError("broker receipt provider 与 mandate 券商不匹配。")
            expected_account_sha256 = sha256_text(str(intent["broker_external_account_id"]))
            if not hmac.compare_digest(broker_account_sha256, expected_account_sha256):
                raise AutoLiveControlError("broker receipt 账户指纹与 mandate 不匹配。")
            send_claim = conn.execute(
                """SELECT * FROM auto_live_order_intent_events
                   WHERE intent_public_id=? AND event_type='send_claimed' AND fencing_epoch=?
                   ORDER BY rowid DESC LIMIT 1""",
                (intent["public_id"], expected_fencing_epoch),
            ).fetchone()
            if send_claim is None:
                raise AutoLiveControlError("broker receipt intent 缺少 send claim。", 409)
            expected_claim = {
                "intent_public_id": str(intent["public_id"]),
                "mandate_public_id": mandate_id,
                "event_type": "send_claimed",
                "fencing_epoch": expected_fencing_epoch,
                "intent_sha256": str(intent["intent_sha256"]),
            }
            try:
                claim_payload = json.loads(str(send_claim["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AutoLiveControlError("broker receipt send claim 无效。", 409) from exc
            if (
                not isinstance(claim_payload, Mapping)
                or dict(claim_payload) != expected_claim
                or str(send_claim["mandate_public_id"]) != mandate_id
                or int(send_claim["fencing_epoch"]) != expected_fencing_epoch
                or not hmac.compare_digest(str(send_claim["payload_sha256"]), sha256_json(expected_claim))
            ):
                raise AutoLiveControlError("broker receipt send claim 绑定无效。", 409)
            try:
                intent_created = datetime.fromisoformat(str(intent["created_at"]))
            except ValueError as exc:
                raise AutoLiveControlError("broker receipt intent 时间无效。") from exc
            if intent_created.tzinfo is None or intent_created.utcoffset() is None:
                raise AutoLiveControlError("broker receipt intent 时间必须带时区。")
            if (intent_created - observed_dt).total_seconds() > MAX_CLOCK_SKEW_SECONDS:
                raise AutoLiveControlError("broker receipt 早于 order intent。")

            replay = conn.execute(
                "SELECT * FROM auto_live_broker_reconciliation_receipts WHERE intent_public_id=? AND evidence_sha256=?",
                (intent["public_id"], evidence_sha256),
            ).fetchone()
            if replay is not None:
                previous_unknown = conn.execute(
                    """SELECT 1 FROM auto_live_broker_reconciliation_receipts previous
                       WHERE previous.intent_public_id=?
                         AND previous.submission_state='submission_unknown'
                         AND previous.rowid<(
                           SELECT current.rowid FROM auto_live_broker_reconciliation_receipts current
                           WHERE current.receipt_id=?
                         )
                       LIMIT 1""",
                    (intent["public_id"], replay["receipt_id"]),
                ).fetchone()
                return self._broker_receipt_public(
                    replay,
                    reconciled=previous_unknown is not None and str(replay["submission_state"]) != "submission_unknown",
                )

            previous = conn.execute(
                """SELECT * FROM auto_live_order_receipt_projections
                   WHERE mandate_public_id=? AND client_order_id=? ORDER BY rowid DESC LIMIT 1""",
                (mandate_id, client_id),
            ).fetchone()
            previous_state = str(previous["submission_state"]) if previous else None
            if previous_state in {"accepted", "rejected", "cancelled"} and state == "submission_unknown":
                raise AutoLiveConflict("broker receipt 状态不得回退到 submission_unknown。")
            if previous_state in {"rejected", "cancelled"} and state != previous_state:
                raise AutoLiveConflict("broker receipt 终态不得改变。")

            reconciled = previous_state == "submission_unknown" and state != "submission_unknown"
            receipt_id = _opaque("brokerreceipt")
            payload = {
                "schema_version": 1,
                "receipt_id": receipt_id,
                "intent_public_id": str(intent["public_id"]),
                "mandate_public_id": mandate_id,
                "client_order_id": client_id,
                "provider": provider,
                "broker_account_sha256": broker_account_sha256,
                "submission_state": state,
                "broker_order_id": broker_order_id,
                "broker_status": broker_status,
                "observed_at": observed_at,
                "evidence_sha256": evidence_sha256,
            }
            digest = sha256_json(payload)
            conn.execute(
                """INSERT INTO auto_live_broker_reconciliation_receipts
                   (receipt_id,intent_public_id,mandate_public_id,client_order_id,provider,broker_account_sha256,submission_state,
                    broker_order_id,broker_status,observed_at,evidence_sha256,payload_json,payload_sha256,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_id, intent["public_id"], mandate_id, client_id, provider, broker_account_sha256, state,
                    broker_order_id, broker_status, observed_at, evidence_sha256,
                    canonical_json(payload), digest, moment,
                ),
            )
            conn.execute(
                """INSERT INTO auto_live_order_receipt_projections
                   (public_id,mandate_public_id,client_order_id,submission_state,broker_order_id,observed_at,receipt_sha256)
                   VALUES(?,?,?,?,?,?,?)""",
                (receipt_id, mandate_id, client_id, state, broker_order_id, observed_at, digest),
            )
            event_type = "reconciled" if reconciled else state
            event = {
                "intent_public_id": str(intent["public_id"]),
                "mandate_public_id": mandate_id,
                "event_type": event_type,
                "fencing_epoch": expected_fencing_epoch,
                "receipt_id": receipt_id,
                "submission_state": state,
                "broker_status": broker_status,
                "evidence_sha256": evidence_sha256,
                "receipt_sha256": digest,
            }
            conn.execute(
                """INSERT INTO auto_live_order_intent_events
                   (event_id,intent_public_id,mandate_public_id,event_type,fencing_epoch,payload_json,payload_sha256,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    _opaque("intentevt"), intent["public_id"], mandate_id, event_type, expected_fencing_epoch,
                    canonical_json(event), sha256_json(event), moment,
                ),
            )
            stored = conn.execute(
                "SELECT * FROM auto_live_broker_reconciliation_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            return self._broker_receipt_public(stored, reconciled=reconciled)

    @classmethod
    def _runtime_safety_gates(
        cls, conn: Any, row: Any, now: datetime, *, mode: str = "confirm"
    ) -> list[dict[str, Any]]:
        runtime = conn.execute(
            "SELECT * FROM auto_live_runtime_projections WHERE mandate_public_id=?", (row["public_id"],)
        ).fetchone()
        runtime_state = str(runtime["state"]) if runtime else None
        runtime_valid = cls._runtime_projection_valid(
            runtime,
            mandate_public_id=str(row["public_id"]),
            fencing_epoch=int(row["fencing_epoch"]),
            now=now,
        )
        if mode == "opening":
            runtime_ok = runtime_state == "running" and runtime_valid
            runtime_reason = "running_fresh_and_fenced" if runtime_ok else "runtime_not_fresh_running"
        elif mode == "lifecycle":
            runtime_ok = runtime_state in {"stopped", "paused"} and runtime_valid
            runtime_reason = "runtime_startable" if runtime_ok else "runtime_missing_stale_or_not_startable"
        else:
            runtime_ok = True
            runtime_reason = "not_required_for_confirmation"
        blocking_receipt = conn.execute(
            """SELECT CASE
                         WHEN current.submission_state='submission_unknown' THEN 'submission_unknown'
                         ELSE 'missing_reconciliation_receipt'
                       END blocking_reason
               FROM auto_live_order_receipt_projections current
               WHERE current.mandate_public_id=?
                 AND current.rowid=(
                   SELECT MAX(latest.rowid) FROM auto_live_order_receipt_projections latest
                   WHERE latest.mandate_public_id=current.mandate_public_id
                     AND latest.client_order_id=current.client_order_id
                 )
                 AND (
                   current.submission_state='submission_unknown'
                   OR (
                     EXISTS(
                       SELECT 1 FROM auto_live_order_receipt_projections previous
                       WHERE previous.mandate_public_id=current.mandate_public_id
                         AND previous.client_order_id=current.client_order_id
                         AND previous.rowid<current.rowid
                         AND previous.submission_state='submission_unknown'
                     )
                     AND NOT EXISTS(
                       SELECT 1 FROM auto_live_broker_reconciliation_receipts receipt
                       WHERE receipt.receipt_id=current.public_id
                         AND receipt.mandate_public_id=current.mandate_public_id
                         AND receipt.client_order_id=current.client_order_id
                         AND receipt.submission_state=current.submission_state
                         AND receipt.broker_order_id IS current.broker_order_id
                         AND receipt.observed_at=current.observed_at
                         AND receipt.payload_sha256=current.receipt_sha256
                     )
                   )
                 )
               LIMIT 1""",
            (row["public_id"],),
        ).fetchone()
        gates = [
            _gate("runtime_state", runtime_ok, runtime_reason),
            _gate(
                "order_submission",
                blocking_receipt is None,
                str(blocking_receipt["blocking_reason"]) if blocking_receipt else "submission_known",
            ),
        ]
        if mode == "opening":
            running_ack_valid = runtime_ok and cls._running_ack_matches_projection(conn, runtime)
            gates.append(
                _gate(
                    "runtime_running_ack",
                    running_ack_valid,
                    "matching_append_only_receipt" if running_ack_valid else "missing_or_mismatched",
                )
            )
            heartbeat = conn.execute(
                "SELECT * FROM auto_live_heartbeat_projections WHERE mandate_public_id=?", (row["public_id"],)
            ).fetchone()
            try:
                heartbeat_valid = bool(
                    heartbeat
                    and str(heartbeat["heartbeat_state"]) == "fresh"
                    and int(heartbeat["fencing_epoch"]) == int(row["fencing_epoch"])
                    and sha256_json(cls._heartbeat_body(heartbeat)) == str(heartbeat["projection_sha256"])
                    and cls._fresh(cls._aware_time(heartbeat["observed_at"]), now, HEARTBEAT_FRESHNESS_SECONDS)
                    and cls._fresh(cls._aware_time(heartbeat["heartbeat_at"]), now, HEARTBEAT_FRESHNESS_SECONDS)
                )
            except (KeyError, TypeError, ValueError):
                heartbeat_valid = False
            gates.append(
                _gate(
                    "runtime_heartbeat",
                    heartbeat_valid,
                    "fresh_and_fenced" if heartbeat_valid else "missing_stale_or_tampered",
                )
            )
        return gates
