"""Fail-closed runtime projection, heartbeat, freshness, and receipt primitives."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from core.auto_live_control_common import (
    AutoLiveConflict,
    HEARTBEAT_FRESHNESS_SECONDS,
    MAX_CLOCK_SKEW_SECONDS,
    RUNTIME_FRESHNESS_SECONDS,
    _gate,
    _opaque,
    canonical_json,
    sha256_json,
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
