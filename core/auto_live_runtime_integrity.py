"""Fail-closed runtime projection, heartbeat, freshness, and receipt primitives."""

from __future__ import annotations

import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Mapping

from core.auto_live_control_common import (
    AutoLiveConflict,
    AutoLiveControlError,
    HEARTBEAT_FRESHNESS_SECONDS,
    MAX_CLOCK_SKEW_SECONDS,
    RUNTIME_FRESHNESS_SECONDS,
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
        uncertain = conn.execute(
            "SELECT 1 FROM auto_live_order_receipt_projections WHERE mandate_public_id=? AND submission_state='submission_unknown' LIMIT 1",
            (row["public_id"],),
        ).fetchone()
        gates = [
            _gate("runtime_state", runtime_ok, runtime_reason),
            _gate("order_submission", uncertain is None, "submission_unknown" if uncertain else "submission_known"),
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
